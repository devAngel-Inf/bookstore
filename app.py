import datetime
import os
import re
import secrets
import time

import oracledb
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# Serve the UI from the public/ folder at the site root
app = Flask(__name__, static_folder="public", static_url_path="")

# Login sessions are signed with this key. If SECRET_KEY is not set in .env, a new
# random key is made every time app.py starts, so everyone has to log in again after
# a restart. Set SECRET_KEY in .env if you want logins to survive restarts.
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

pool = oracledb.create_pool(
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    dsn=os.getenv("DB_CONNECT_STRING"),
    min=1,
    max=5,
)

# Whitelist of sort options -> ORDER BY clause (never built from user text)
SORTS = {
    # books has no created date, so the highest book_id is the newest entry
    "recent_added": "b.book_id DESC",
    "recent_viewed": "b.last_viewed DESC NULLS LAST, b.book_id DESC",
    "title_asc": "LOWER(b.title) ASC, b.book_id ASC",
    "title_desc": "LOWER(b.title) DESC, b.book_id DESC",
}
DEFAULT_SORT = "recent_added"
DEFAULT_LOW_STOCK = 5
LOW_STOCK_KEY = "low_stock_threshold"
MAX_TAGS = 10
MAX_TAG_LEN = 40


class Invalid(Exception):
    """Bad input from the browser; message is shown to the user (HTTP 400)."""


class Missing(Exception):
    """Something the user referred to does not exist (HTTP 404)."""


# ---------- Database helpers ----------

def _plain(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


class Tx:
    """One pooled connection used as a transaction.
    Commits when the block ends normally and rolls back if anything is raised,
    so multi-step work (like checkout) is all-or-nothing."""

    def __enter__(self):
        self.conn = pool.acquire()
        self.cur = self.conn.cursor()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.cur.close()
            self.conn.close()
        return False

    def execute(self, sql, binds=None):
        self.cur.execute(sql, binds or {})
        return self.cur.rowcount

    def query(self, sql, binds=None):
        self.cur.execute(sql, binds or {})
        cols = [c[0].lower() for c in self.cur.description]
        return [
            {k: _plain(v) for k, v in zip(cols, row)}
            for row in self.cur.fetchall()
        ]


def run(sql, binds=None, fetch=False):
    """Run one statement in its own transaction. Returns rows (fetch) or rowcount."""
    with Tx() as tx:
        return tx.query(sql, binds) if fetch else tx.execute(sql, binds)


def insert_row(table, id_col, values, raw=None, tx=None):
    """INSERT a row and return its new id.
    If the id column is not auto-generated in your database (ORA-01400), retry
    using MAX(id) + 1 so it works either way.
    `raw` holds columns set from SQL expressions, e.g. {"order_date": "SYSDATE"}.
    """
    if tx is None:
        with Tx() as own:
            return insert_row(table, id_col, values, raw, own)

    raw = raw or {}

    def build(with_id):
        cols = list(values) + list(raw)
        vals = [f":{c}" for c in values] + [raw[c] for c in raw]
        if with_id:
            cols.insert(0, id_col)
            vals.insert(0, f"(SELECT NVL(MAX({id_col}), 0) + 1 FROM {table})")
        return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)}) "
                f"RETURNING {id_col} INTO :new_id")

    def attempt(with_id):
        new_id = tx.cur.var(oracledb.NUMBER)
        tx.execute(build(with_id), {**values, "new_id": new_id})
        got = new_id.getvalue()
        got = got[0] if isinstance(got, (list, tuple)) else got
        return int(got) if got is not None else None

    try:
        return attempt(False)
    except oracledb.DatabaseError as err:
        text_ = str(err).upper()
        if "ORA-01400" in text_ and id_col.upper() in text_:
            return attempt(True)
        raise


# ---------- Input helpers ----------

def num(value, cast, label, required=False, minimum=None):
    if value in (None, ""):
        if required:
            raise Invalid(f"{label} is required")
        return None
    try:
        n = cast(value)
    except (TypeError, ValueError):
        raise Invalid(f"{label} must be a number")
    if minimum is not None and n < minimum:
        raise Invalid(f"{label} can't be less than {minimum}")
    return n


def text(body, key, label, required=False, max_len=None):
    value = str(body.get(key) or "").strip()
    if required and not value:
        raise Invalid(f"{label} is required")
    if max_len and len(value) > max_len:
        raise Invalid(f"{label} can be at most {max_len} characters")
    return value or None


# ---------- Error handling (always answer with JSON) ----------

@app.errorhandler(Invalid)
def handle_invalid(err):
    return jsonify(error=str(err)), 400


@app.errorhandler(Missing)
def handle_missing(err):
    return jsonify(error=str(err)), 404


@app.errorhandler(oracledb.Error)
def handle_db_error(err):
    msg = str(err)
    if "ORA-20001" in msg:
        return jsonify(error="Prices on order lines are locked and cannot be changed"), 409
    if "ORA-00001" in msg:
        return jsonify(error="That value already exists (for example a duplicate ISBN, email or username)"), 409
    if "ORA-02292" in msg:
        return jsonify(error="Can't delete this: other records (such as orders) still use it"), 409
    if "ORA-02291" in msg:
        return jsonify(error="A related record was not found (check the selected author, book or customer)"), 404
    if "ORA-12899" in msg:
        return jsonify(error="One of the values is too long"), 400
    if "ORA-02290" in msg:
        rule = re.search(r"\(([^)]+)\)", msg)
        return jsonify(error="The database rejected a value"
                             + (f" (rule {rule.group(1)})" if rule else "")), 400
    if "ORA-01438" in msg:
        return jsonify(error="A number is larger than the database allows"), 400
    if "ORA-00942" in msg:
        return jsonify(error="A required table is missing. Run setup_cart_stock.sql and "
                             "setup_auth_tags.sql once in VS Code (see README), then try again."), 500
    app.logger.error(msg)
    return jsonify(error=msg), 500


@app.errorhandler(Exception)
def handle_unexpected(err):
    if isinstance(err, HTTPException):
        return err
    app.logger.exception(err)
    return jsonify(error="Unexpected server error"), 500


# ---------- Login ----------

# Pages and endpoints that work without being signed in
PUBLIC_PATHS = {
    "/login", "/login.html", "/login.js", "/theme.js", "/style.css",
    "/planet_drawings.jpg",
    "/api/session", "/api/login", "/api/signup",
}


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please sign in", login_required=True), 401
    return redirect("/login")


# Very small brake on password guessing: 5 wrong tries per IP per 5 minutes
FAILED_LOGINS = {}


def too_many_attempts(ip):
    now = time.time()
    recent = [t for t in FAILED_LOGINS.get(ip, []) if now - t < 300]
    FAILED_LOGINS[ip] = recent
    return len(recent) >= 5


def user_count():
    return run("SELECT COUNT(*) AS n FROM app_users", fetch=True)[0]["n"]


@app.route("/login")
def login_page():
    if session.get("user"):
        return redirect("/")
    return app.send_static_file("login.html")


@app.get("/api/session")
def session_info():
    return jsonify(
        logged_in=bool(session.get("user")),
        username=session.get("user"),
        needs_setup=user_count() == 0,
    )


@app.post("/api/signup")
def signup():
    """Anyone can create an account from the login page."""
    body = request.get_json(silent=True) or {}
    username = (text(body, "username", "Username", required=True, max_len=50) or "").lower()
    if len(username) < 3:
        raise Invalid("Username must be at least 3 characters")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise Invalid("Password must be at least 8 characters")
    if run("SELECT 1 FROM app_users WHERE username = :u", {"u": username}, fetch=True):
        raise Invalid("That username is already taken")
    insert_row("app_users", "user_id",
               {"username": username, "password_hash": generate_password_hash(password)})
    session.clear()
    session["user"] = username
    return jsonify(ok=True, username=username), 201


@app.post("/api/login")
def login():
    ip = request.remote_addr or "?"
    if too_many_attempts(ip):
        return jsonify(error="Too many wrong attempts. Wait a few minutes and try again."), 429
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    rows = run("SELECT password_hash FROM app_users WHERE username = :u",
               {"u": username}, fetch=True)
    if not rows or not check_password_hash(rows[0]["password_hash"], password):
        FAILED_LOGINS.setdefault(ip, []).append(time.time())
        return jsonify(error="Wrong username or password"), 401
    FAILED_LOGINS.pop(ip, None)
    session.clear()
    session["user"] = username
    return jsonify(ok=True, username=username)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------- Tags ----------

def parse_tags(raw):
    """Turn 'Fantasy, magic ,  YA' (or a list) into ['fantasy', 'magic', 'ya'].
    Returns None when no tags were sent at all (meaning: leave tags unchanged)."""
    if raw is None:
        return None
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    tags = []
    for item in items:
        tag = re.sub(r"\s+", " ", str(item or "")).strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise Invalid(f"Each tag can be at most {MAX_TAG_LEN} characters")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise Invalid(f"A book can have at most {MAX_TAGS} tags")
    return tags


def save_tags(tx, book_id, tags):
    """Replace a book's tags with this list (creating new tags as needed)."""
    tx.execute("DELETE FROM book_tags WHERE book_id = :id", {"id": book_id})
    for name in tags:
        found = tx.query("SELECT tag_id FROM tags WHERE tag_name = :n", {"n": name})
        tag_id = found[0]["tag_id"] if found else insert_row(
            "tags", "tag_id", {"tag_name": name}, tx=tx)
        tx.execute("INSERT INTO book_tags (book_id, tag_id) VALUES (:b, :t)",
                   {"b": book_id, "t": tag_id})
    # Keep the tag list tidy: drop tags that no book uses any more
    tx.execute("DELETE FROM tags WHERE tag_id NOT IN (SELECT tag_id FROM book_tags)")


@app.get("/api/tags")
def list_tags():
    rows = run(
        """SELECT t.tag_name, COUNT(*) AS book_count
             FROM tags t
             JOIN book_tags bt ON bt.tag_id = t.tag_id
            GROUP BY t.tag_name
            ORDER BY COUNT(*) DESC, t.tag_name""",
        fetch=True,
    )
    return jsonify(rows)


@app.get("/api/books/<int:book_id>/similar")
def similar_books(book_id):
    """Books that share the most tags with this one."""
    rows = run(
        """SELECT b.book_id, b.title, a.author_name, b.unit_price, b.stock_quantity,
                  COUNT(*) AS shared_count,
                  LISTAGG(t.tag_name, ', ') WITHIN GROUP (ORDER BY t.tag_name) AS shared
             FROM book_tags mine
             JOIN book_tags other ON other.tag_id = mine.tag_id
                                 AND other.book_id <> mine.book_id
             JOIN tags t ON t.tag_id = mine.tag_id
             JOIN books b ON b.book_id = other.book_id
             LEFT JOIN authors a ON a.author_id = b.author_id
            WHERE mine.book_id = :id
            GROUP BY b.book_id, b.title, a.author_name, b.unit_price, b.stock_quantity
            ORDER BY shared_count DESC, LOWER(b.title)
            FETCH FIRST 6 ROWS ONLY""",
        {"id": book_id},
        fetch=True,
    )
    return jsonify(rows)


@app.get("/api/customers/<int:customer_id>/recommendations")
def recommendations(customer_id):
    """Books this customer hasn't bought, ranked by how many of their past
    purchases share tags with them."""
    rows = run(
        """WITH liked AS (
               SELECT bt.tag_id, COUNT(*) AS weight
                 FROM orders o
                 JOIN order_items i ON i.order_id = o.order_id
                 JOIN book_tags bt ON bt.book_id = i.book_id
                WHERE o.customer_id = :cid
                GROUP BY bt.tag_id)
           SELECT b.book_id, b.title, a.author_name, b.unit_price, b.stock_quantity,
                  SUM(l.weight) AS score,
                  LISTAGG(t.tag_name, ', ') WITHIN GROUP (ORDER BY t.tag_name) AS matched
             FROM liked l
             JOIN book_tags bt ON bt.tag_id = l.tag_id
             JOIN tags t ON t.tag_id = l.tag_id
             JOIN books b ON b.book_id = bt.book_id
             LEFT JOIN authors a ON a.author_id = b.author_id
            WHERE b.stock_quantity > 0
              AND NOT EXISTS (SELECT 1 FROM orders o2
                                JOIN order_items i2 ON i2.order_id = o2.order_id
                               WHERE o2.customer_id = :cid AND i2.book_id = b.book_id)
              AND NOT EXISTS (SELECT 1 FROM cart_items c
                               WHERE c.customer_id = :cid AND c.book_id = b.book_id)
            GROUP BY b.book_id, b.title, a.author_name, b.unit_price, b.stock_quantity
            ORDER BY score DESC, LOWER(b.title)
            FETCH FIRST 5 ROWS ONLY""",
        {"cid": customer_id},
        fetch=True,
    )
    return jsonify(rows)


# ---------- Books ----------

def clean_book(body):
    body = body or {}
    return {
        "title": text(body, "title", "Title", required=True, max_len=150),
        "author_id": num(body.get("author_id"), int, "Author", required=True),
        "isbn": text(body, "isbn", "ISBN", required=True, max_len=13),
        "unit_price": num(body.get("unit_price"), float, "Price", required=True, minimum=0),
        "stock_quantity": num(body.get("stock_quantity"), int, "Stock", minimum=0) or 0,
        "publication_year": num(body.get("publication_year"), int, "Year", minimum=0),
    }


@app.get("/api/books")
def list_books():
    q = f"%{request.args.get('q', '').strip().lower()}%"
    tag = request.args.get("tag", "").strip().lower()
    order_by = SORTS.get(request.args.get("sort"), SORTS[DEFAULT_SORT])

    tag_exists = ("SELECT 1 FROM book_tags bt JOIN tags t ON t.tag_id = bt.tag_id "
                  "WHERE bt.book_id = b.book_id AND ")
    where = ("(LOWER(b.title) LIKE :q OR LOWER(a.author_name) LIKE :q OR LOWER(b.isbn) LIKE :q "
             f"OR EXISTS ({tag_exists} t.tag_name LIKE :q))")
    binds = {"q": q}
    if tag:
        where += f" AND EXISTS ({tag_exists} t.tag_name = :tag)"
        binds["tag"] = tag

    rows = run(
        f"""SELECT b.book_id, b.title, b.author_id, a.author_name, b.isbn,
                   b.unit_price, b.stock_quantity, b.publication_year,
                   (SELECT ROUND(AVG(r.rating), 1) FROM reviews r
                     WHERE r.book_id = b.book_id) AS avg_rating,
                   (SELECT COUNT(*) FROM reviews r
                     WHERE r.book_id = b.book_id) AS review_count,
                   (SELECT LISTAGG(t.tag_name, ',') WITHIN GROUP (ORDER BY t.tag_name)
                      FROM book_tags bt JOIN tags t ON t.tag_id = bt.tag_id
                     WHERE bt.book_id = b.book_id) AS tags
              FROM books b
              LEFT JOIN authors a ON a.author_id = b.author_id
             WHERE {where}
             ORDER BY {order_by}""",
        binds,
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/books")
def add_book():
    body = request.get_json(silent=True)
    book = clean_book(body)
    tags = parse_tags((body or {}).get("tags")) or []
    with Tx() as tx:
        book_id = insert_row("books", "book_id", book, tx=tx)
        if tags:
            save_tags(tx, book_id, tags)
    return jsonify(ok=True), 201


@app.put("/api/books/<int:book_id>")
def update_book(book_id):
    # Only the books row changes. Prices already copied into carts and orders
    # are separate values and are never touched here (price immutability).
    body = request.get_json(silent=True)
    b = clean_book(body)
    tags = parse_tags((body or {}).get("tags"))
    with Tx() as tx:
        tx.execute(
            """UPDATE books
                  SET title = :title, author_id = :author_id, isbn = :isbn,
                      unit_price = :unit_price, stock_quantity = :stock_quantity,
                      publication_year = :publication_year
                WHERE book_id = :id""",
            {**b, "id": book_id},
        )
        if tags is not None:
            save_tags(tx, book_id, tags)
    return jsonify(ok=True)


@app.delete("/api/books/<int:book_id>")
def delete_book(book_id):
    # Reviews, tag links and cart lines go with the book (ON DELETE CASCADE). A book
    # that appears in order_items is protected by the database and returns a clear message.
    with Tx() as tx:
        tx.execute("DELETE FROM books WHERE book_id = :id", {"id": book_id})
        tx.execute("DELETE FROM tags WHERE tag_id NOT IN (SELECT tag_id FROM book_tags)")
    return jsonify(ok=True)


@app.post("/api/books/<int:book_id>/view")
def mark_viewed(book_id):
    """Record that a book was just opened (drives the 'recently viewed' sort)."""
    run("UPDATE books SET last_viewed = SYSTIMESTAMP WHERE book_id = :id", {"id": book_id})
    return jsonify(ok=True)


# ---------- Stock ----------

def get_threshold():
    """Returns (threshold, can_save). can_save is False until setup_cart_stock.sql has run."""
    try:
        rows = run(
            "SELECT setting_value FROM app_settings WHERE setting_key = :k",
            {"k": LOW_STOCK_KEY},
            fetch=True,
        )
    except oracledb.DatabaseError as err:
        if "ORA-00942" in str(err):
            return DEFAULT_LOW_STOCK, False
        raise
    if rows:
        try:
            return max(0, int(rows[0]["setting_value"])), True
        except (TypeError, ValueError):
            pass
    return DEFAULT_LOW_STOCK, True


@app.get("/api/stock")
def stock_overview():
    threshold, can_save = get_threshold()
    rows = run(
        """SELECT b.book_id, b.title, a.author_name, b.stock_quantity, b.unit_price,
                  NVL((SELECT SUM(i.quantity)
                         FROM order_items i
                         JOIN orders o ON o.order_id = i.order_id
                        WHERE i.book_id = b.book_id
                          AND o.order_date >= SYSDATE - 30), 0) AS sold_30d
             FROM books b
             LEFT JOIN authors a ON a.author_id = b.author_id
            ORDER BY b.stock_quantity ASC, LOWER(b.title) ASC""",
        fetch=True,
    )
    low = out = units = 0
    for r in rows:
        stock = r["stock_quantity"] or 0
        units += stock
        if stock <= 0:
            r["status"] = "out"
            out += 1
        elif stock <= threshold:
            r["status"] = "low"
            low += 1
        else:
            r["status"] = "ok"
        sold = r["sold_30d"] or 0
        # Rough "how long will it last" from the last 30 days of orders
        r["days_left"] = round(stock / (sold / 30)) if sold > 0 and stock > 0 else None
    return jsonify(
        threshold=threshold,
        can_save=can_save,
        summary={"titles": len(rows), "units": units, "low": low, "out": out},
        books=rows,
    )


@app.put("/api/settings/low-stock")
def set_low_stock():
    body = request.get_json(silent=True) or {}
    n = num(body.get("threshold"), int, "Threshold", required=True, minimum=0)
    run(
        """MERGE INTO app_settings s
           USING (SELECT :k AS k, :v AS v FROM dual) n
              ON (s.setting_key = n.k)
            WHEN MATCHED THEN UPDATE SET s.setting_value = n.v
            WHEN NOT MATCHED THEN INSERT (setting_key, setting_value) VALUES (n.k, n.v)""",
        {"k": LOW_STOCK_KEY, "v": str(n)},
    )
    return jsonify(ok=True, threshold=n)


# ---------- Cart (prices are locked when a book is first added) ----------

@app.get("/api/customers/<int:customer_id>/cart")
def cart_list(customer_id):
    rows = run(
        """SELECT c.book_id, b.title, a.author_name, c.quantity,
                  c.locked_price, b.unit_price AS current_price,
                  b.stock_quantity, c.quantity * c.locked_price AS line_total
             FROM cart_items c
             JOIN books b ON b.book_id = c.book_id
             LEFT JOIN authors a ON a.author_id = b.author_id
            WHERE c.customer_id = :cid
            ORDER BY c.added_at, c.book_id""",
        {"cid": customer_id},
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/customers/<int:customer_id>/cart")
def cart_add(customer_id):
    body = request.get_json(silent=True) or {}
    book_id = num(body.get("book_id"), int, "Book", required=True)
    raw_qty = body.get("quantity")
    qty = num(1 if raw_qty in (None, "") else raw_qty, int, "Quantity", minimum=1)
    # The price is always read from the database here. Any price sent by the
    # browser is ignored, so it can't be tampered with.
    with Tx() as tx:
        book = tx.query(
            "SELECT title, unit_price, stock_quantity FROM books WHERE book_id = :id",
            {"id": book_id},
        )
        if not book:
            raise Missing("That book no longer exists")
        book = book[0]
        existing = tx.query(
            "SELECT quantity, locked_price FROM cart_items "
            "WHERE customer_id = :cid AND book_id = :bid",
            {"cid": customer_id, "bid": book_id},
        )
        have = existing[0]["quantity"] if existing else 0
        if have + qty > book["stock_quantity"]:
            more = f" ({have} already in this cart)" if have else ""
            raise Invalid(f"Only {book['stock_quantity']} of '{book['title']}' in stock{more}")
        if existing:
            # Quantity only. The locked price stays exactly as it was.
            tx.execute(
                "UPDATE cart_items SET quantity = :qty "
                "WHERE customer_id = :cid AND book_id = :bid",
                {"qty": have + qty, "cid": customer_id, "bid": book_id},
            )
            locked = existing[0]["locked_price"]
        else:
            locked = book["unit_price"]
            tx.execute(
                "INSERT INTO cart_items (customer_id, book_id, quantity, locked_price) "
                "VALUES (:cid, :bid, :qty, :price)",
                {"cid": customer_id, "bid": book_id, "qty": qty, "price": locked},
            )
    return jsonify(ok=True, locked_price=locked), 201


@app.put("/api/customers/<int:customer_id>/cart/<int:book_id>")
def cart_set_quantity(customer_id, book_id):
    body = request.get_json(silent=True) or {}
    qty = num(body.get("quantity"), int, "Quantity", required=True, minimum=1)
    with Tx() as tx:
        book = tx.query(
            "SELECT title, stock_quantity FROM books WHERE book_id = :id", {"id": book_id}
        )
        if not book:
            raise Missing("That book no longer exists")
        if qty > book[0]["stock_quantity"]:
            raise Invalid(f"Only {book[0]['stock_quantity']} of '{book[0]['title']}' in stock")
        changed = tx.execute(
            "UPDATE cart_items SET quantity = :qty WHERE customer_id = :cid AND book_id = :bid",
            {"qty": qty, "cid": customer_id, "bid": book_id},
        )
        if not changed:
            raise Missing("That item is not in the cart")
    return jsonify(ok=True)


@app.delete("/api/customers/<int:customer_id>/cart/<int:book_id>")
def cart_remove(customer_id, book_id):
    run(
        "DELETE FROM cart_items WHERE customer_id = :cid AND book_id = :bid",
        {"cid": customer_id, "bid": book_id},
    )
    return jsonify(ok=True)


@app.post("/api/customers/<int:customer_id>/checkout")
def checkout(customer_id):
    """Turn the cart into an order. Each order line gets the price that was
    locked in the cart, not the book's current price. Stock is reduced in the
    same transaction; if anything fails, nothing is saved."""
    body = request.get_json(silent=True) or {}
    status = text(body, "status", "Order status", required=True, max_len=30)
    threshold, _ = get_threshold()
    low_now = []
    total = 0.0

    with Tx() as tx:
        cart = tx.query(
            """SELECT c.book_id, c.quantity, c.locked_price, b.title
                 FROM cart_items c
                 JOIN books b ON b.book_id = c.book_id
                WHERE c.customer_id = :cid
                ORDER BY c.added_at, c.book_id""",
            {"cid": customer_id},
        )
        if not cart:
            raise Invalid("The cart is empty")

        order_id = insert_row(
            "orders", "order_id",
            {"customer_id": customer_id, "order_status": status},
            raw={"order_date": "SYSDATE"}, tx=tx,
        )
        for line in cart:
            changed = tx.execute(
                "UPDATE books SET stock_quantity = stock_quantity - :q "
                "WHERE book_id = :id AND stock_quantity >= :q",
                {"q": line["quantity"], "id": line["book_id"]},
            )
            if not changed:
                raise Invalid(f"Not enough stock for '{line['title']}'. "
                              "Lower the quantity in the cart and try again.")
            insert_row(
                "order_items", "order_item_id",
                {"order_id": order_id, "book_id": line["book_id"],
                 "quantity": line["quantity"], "unit_price": line["locked_price"]},
                tx=tx,
            )
            total += line["quantity"] * float(line["locked_price"])
            left = tx.query(
                "SELECT stock_quantity FROM books WHERE book_id = :id", {"id": line["book_id"]}
            )[0]["stock_quantity"]
            if left <= threshold:
                low_now.append({"title": line["title"], "stock_quantity": left})
        tx.execute("DELETE FROM cart_items WHERE customer_id = :cid", {"cid": customer_id})

    return jsonify(ok=True, order_id=order_id, total=round(total, 2), low_stock=low_now), 201


@app.get("/api/order-statuses")
def order_statuses():
    """Statuses already used in your orders table, so new orders use a value your database accepts."""
    rows = run(
        "SELECT DISTINCT order_status FROM orders WHERE order_status IS NOT NULL ORDER BY order_status",
        fetch=True,
    )
    return jsonify([r["order_status"] for r in rows])


# ---------- Reviews ----------

@app.get("/api/books/<int:book_id>/reviews")
def list_reviews(book_id):
    rows = run(
        """SELECT review_id, reviewer, rating, review_text, created_at
             FROM reviews
            WHERE book_id = :id
            ORDER BY created_at DESC, review_id DESC""",
        {"id": book_id},
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/books/<int:book_id>/reviews")
def add_review(book_id):
    body = request.get_json(silent=True) or {}
    rating = num(body.get("rating"), int, "Rating", required=True)
    if not 1 <= rating <= 5:
        raise Invalid("Rating must be between 1 and 5")
    insert_row(
        "reviews",
        "review_id",
        {
            "book_id": book_id,
            "reviewer": text(body, "reviewer", "Your name", required=True, max_len=80),
            "rating": rating,
            "review_text": text(body, "review_text", "Review", max_len=1000),
        },
    )
    return jsonify(ok=True), 201


@app.delete("/api/reviews/<int:review_id>")
def delete_review(review_id):
    run("DELETE FROM reviews WHERE review_id = :id", {"id": review_id})
    return jsonify(ok=True)


# ---------- Authors ----------

@app.get("/api/authors")
def list_authors():
    rows = run(
        """SELECT a.author_id, a.author_name, a.country,
                  (SELECT COUNT(*) FROM books b WHERE b.author_id = a.author_id) AS book_count
             FROM authors a
            ORDER BY LOWER(a.author_name)""",
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/authors")
def add_author():
    body = request.get_json(silent=True) or {}
    insert_row(
        "authors",
        "author_id",
        {
            "author_name": text(body, "author_name", "Author name", required=True, max_len=100),
            "country": text(body, "country", "Country", max_len=60),
        },
    )
    return jsonify(ok=True), 201


# ---------- Customers ----------

@app.get("/api/customers")
def list_customers():
    q = f"%{request.args.get('q', '').strip().lower()}%"
    rows = run(
        """SELECT c.customer_id, c.first_name, c.last_name, c.email, c.phone,
                  c.registration_date,
                  (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id) AS order_count
             FROM customers c
            WHERE LOWER(c.first_name || ' ' || c.last_name) LIKE :q
               OR LOWER(c.email) LIKE :q
            ORDER BY c.customer_id DESC""",
        {"q": q},
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/customers")
def add_customer():
    body = request.get_json(silent=True) or {}
    digits = re.sub(r"\D", "", str(body.get("phone") or ""))
    insert_row(
        "customers",
        "customer_id",
        {
            "first_name": text(body, "first_name", "First name", required=True, max_len=50),
            "last_name": text(body, "last_name", "Last name", required=True, max_len=50),
            "email": text(body, "email", "Email", required=True, max_len=100),
            "phone": int(digits) if digits else None,
        },
        raw={"registration_date": "SYSDATE"},
    )
    return jsonify(ok=True), 201


# ---------- Orders (view only) ----------

@app.get("/api/orders")
def list_orders():
    q = f"%{request.args.get('q', '').strip().lower()}%"
    rows = run(
        """SELECT o.order_id, o.customer_id,
                  c.first_name || ' ' || c.last_name AS customer_name,
                  o.order_date, o.order_status,
                  (SELECT COUNT(*) FROM order_items i
                    WHERE i.order_id = o.order_id) AS item_count,
                  (SELECT NVL(SUM(i.quantity * i.unit_price), 0) FROM order_items i
                    WHERE i.order_id = o.order_id) AS total
             FROM orders o
             LEFT JOIN customers c ON c.customer_id = o.customer_id
            WHERE LOWER(c.first_name || ' ' || c.last_name) LIKE :q
               OR LOWER(o.order_status) LIKE :q
               OR TO_CHAR(o.order_id) LIKE :q
            ORDER BY o.order_id DESC""",
        {"q": q},
        fetch=True,
    )
    return jsonify(rows)


@app.get("/api/orders/<int:order_id>")
def order_details(order_id):
    header = run(
        """SELECT o.order_id, o.customer_id,
                  c.first_name || ' ' || c.last_name AS customer_name,
                  c.email, o.order_date, o.order_status
             FROM orders o
             LEFT JOIN customers c ON c.customer_id = o.customer_id
            WHERE o.order_id = :id""",
        {"id": order_id},
        fetch=True,
    )
    if not header:
        return jsonify(error="Order not found"), 404
    items = run(
        """SELECT i.order_item_id, i.book_id, b.title, a.author_name,
                  i.quantity, i.unit_price, i.quantity * i.unit_price AS line_total
             FROM order_items i
             LEFT JOIN books b ON b.book_id = i.book_id
             LEFT JOIN authors a ON a.author_id = b.author_id
            WHERE i.order_id = :id
            ORDER BY i.order_item_id""",
        {"id": order_id},
        fetch=True,
    )
    return jsonify({**header[0], "items": items})


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 3000)), debug=True)
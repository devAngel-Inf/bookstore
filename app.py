import datetime
import os
import re

import oracledb
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

load_dotenv()

# Serve the UI from the public/ folder at the site root
app = Flask(__name__, static_folder="public", static_url_path="")

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


class Invalid(Exception):
    """Bad input from the browser; message is shown to the user."""


# ---------- Helpers ----------

def _plain(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def run(sql, binds=None, fetch=False):
    """Run one statement on a pooled connection. Returns rows (fetch) or rowcount."""
    with pool.acquire() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds or {})
            if fetch:
                cols = [c[0].lower() for c in cur.description]
                return [
                    {k: _plain(v) for k, v in zip(cols, row)}
                    for row in cur.fetchall()
                ]
            conn.commit()
            return cur.rowcount


def insert_row(table, id_col, values, raw=None):
    """INSERT a row. If the id column is not auto-generated in your database
    (ORA-01400), retry using MAX(id) + 1 so it works either way.
    `raw` holds columns set from SQL expressions, e.g. {"registration_date": "SYSDATE"}.
    """
    raw = raw or {}

    def build(with_id):
        cols = list(values) + list(raw)
        vals = [f":{c}" for c in values] + [raw[c] for c in raw]
        if with_id:
            cols.insert(0, id_col)
            vals.insert(0, f"(SELECT NVL(MAX({id_col}), 0) + 1 FROM {table})")
        return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"

    try:
        run(build(False), values)
    except oracledb.DatabaseError as err:
        text = str(err).upper()
        if "ORA-01400" in text and id_col.upper() in text:
            run(build(True), values)
        else:
            raise


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


@app.errorhandler(oracledb.Error)
def handle_db_error(err):
    msg = str(err)
    if "ORA-00001" in msg:
        return jsonify(error="That value already exists (for example a duplicate ISBN or email)"), 409
    if "ORA-02292" in msg:
        return jsonify(error="Can't delete this: other records (such as orders) still use it"), 409
    if "ORA-02291" in msg:
        return jsonify(error="A related record was not found (check the selected author or book)"), 404
    if "ORA-12899" in msg:
        return jsonify(error="One of the values is too long"), 400
    if "ORA-02290" in msg or "ORA-01438" in msg:
        return jsonify(error="A number is outside the range the database allows"), 400
    app.logger.error(msg)
    return jsonify(error=msg), 500


@app.errorhandler(Exception)
def handle_unexpected(err):
    if isinstance(err, HTTPException):
        return err
    app.logger.exception(err)
    return jsonify(error="Unexpected server error"), 500


@app.route("/")
def index():
    return app.send_static_file("index.html")


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
    order_by = SORTS.get(request.args.get("sort"), SORTS[DEFAULT_SORT])
    rows = run(
        f"""SELECT b.book_id, b.title, b.author_id, a.author_name, b.isbn,
                   b.unit_price, b.stock_quantity, b.publication_year,
                   (SELECT ROUND(AVG(r.rating), 1) FROM reviews r
                     WHERE r.book_id = b.book_id) AS avg_rating,
                   (SELECT COUNT(*) FROM reviews r
                     WHERE r.book_id = b.book_id) AS review_count
              FROM books b
              LEFT JOIN authors a ON a.author_id = b.author_id
             WHERE LOWER(b.title) LIKE :q
                OR LOWER(a.author_name) LIKE :q
                OR LOWER(b.isbn) LIKE :q
             ORDER BY {order_by}""",
        {"q": q},
        fetch=True,
    )
    return jsonify(rows)


@app.post("/api/books")
def add_book():
    insert_row("books", "book_id", clean_book(request.get_json(silent=True)))
    return jsonify(ok=True), 201


@app.put("/api/books/<int:book_id>")
def update_book(book_id):
    b = clean_book(request.get_json(silent=True))
    run(
        """UPDATE books
              SET title = :title, author_id = :author_id, isbn = :isbn,
                  unit_price = :unit_price, stock_quantity = :stock_quantity,
                  publication_year = :publication_year
            WHERE book_id = :id""",
        {**b, "id": book_id},
    )
    return jsonify(ok=True)


@app.delete("/api/books/<int:book_id>")
def delete_book(book_id):
    # Reviews go with the book (ON DELETE CASCADE). A book that appears in
    # order_items is protected by the database and returns a clear message.
    run("DELETE FROM books WHERE book_id = :id", {"id": book_id})
    return jsonify(ok=True)


@app.post("/api/books/<int:book_id>/view")
def mark_viewed(book_id):
    """Record that a book was just opened (drives the 'recently viewed' sort)."""
    run("UPDATE books SET last_viewed = SYSTIMESTAMP WHERE book_id = :id", {"id": book_id})
    return jsonify(ok=True)


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

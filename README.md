# Book Manager (Python Flask + Oracle)

A book store website that runs on your existing Oracle tables. The browser UI talks to a small Flask server, which reads and writes your database.

```
Browser (public/)  →  Flask API (app.py)  →  Oracle Database
```

## Your tooling

- **Database work:** Oracle SQL Developer extension in **VS Code**: connect, run queries, inspect tables.
- **Website code:** also in VS Code. Open this folder (`File > Open Folder`).

## Setup

1. Install Python 3.9 or newer.
2. **Run `setup_cart_stock.sql` once** in VS Code (Oracle SQL Developer extension), using the same connection/user as your `.env`. Run it as a script. It adds the tables for the cart and the saved low-stock threshold, plus two safety triggers. It does not change your existing tables.
3. **Run `setup_auth_tags.sql` once**, the same way. It adds the tables for logins (`APP_USERS`) and book tags (`TAGS`, `BOOK_TAGS`). It does not change your existing tables.
4. Copy `.env.example` to `.env` and fill in:
   - `DB_USER` and `DB_PASSWORD`: the same login you use in SQL Developer
   - `DB_CONNECT_STRING`: `host:port/service_name`, e.g. `localhost:1521/XEPDB1`
   - `SECRET_KEY` (optional): any long random text. See "Login" below.
5. In the VS Code terminal:
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (Mac/Linux)
   pip install -r requirements.txt
   python app.py
   ```
6. Open http://localhost:3000

The `oracledb` package connects directly (Thin mode), so Oracle Instant Client is not needed for Oracle 12.1 and newer.

## Login

- Every page and every `/api` call requires you to be signed in. Anyone who isn't is sent to the login page.
- **First run:** the login page asks you to create the first account (username + a password of 8 or more characters). After that it is a normal sign-in form.
- Passwords are stored as salted hashes in `APP_USERS`, never as plain text.
- **Each time you start `app.py` you sign in again**, because the session key is created fresh at startup. If you would rather stay signed in across restarts, put a long random `SECRET_KEY=...` in `.env`.
- Five wrong passwords from the same address lock sign-in for 5 minutes.
- Use **Log out** at the top right of the site to end your session.
- Only the first account can be created from the login page. Once an account exists, extra accounts can only be added by a signed-in user (the `/api/signup` endpoint), and there is no screen for it yet.

## Dark and light mode

The **🌙 Dark / ☀️ Light** button in the header (and at the top right of the login page) switches the theme. Your choice is remembered in the browser. The first time, it follows your computer's setting. All colours live in CSS variables at the top of `public/style.css`.

## Tags and recommendations

- **Add tags** on the Books tab: type comma-separated tags (up to 10 per book, 40 characters each) such as `fantasy, magic, young adult`. Tags are lowercased so `Fantasy` and `fantasy` are the same tag. Existing tags show as clickable suggestions under the field.
- **Browse by tag:** the Library card shows every tag with its book count. Click one (or a tag chip in a row) to filter. Search also matches tags.
- **Similar books:** open a book's **Reviews** window to see "Readers might also like", ranked by how many tags they share.
- **Recommended for this customer:** on the Cart tab, books the customer hasn't bought (and that are in stock) ranked by how many tags they share with that customer's past orders, with an **Add to cart** button. Suggestions only appear once the customer has ordered books that have tags.
- A tag that no book uses any more is removed automatically.

## What you can do

**Stock tab**
- See every book's stock with a status: OK, Low, or Out of stock, plus units sold in the last 30 days and an estimate of how many days the stock will last.
- Set the warning threshold ("warn me when a book has N copies or fewer"). It is saved in the database for everyone.
- A yellow warning banner appears at the top of **every** page while any book is at or below the threshold. After an order is placed, the page also tells you which books just dropped to the threshold.
- To add stock, edit the book on the Books tab.

**Cart and price lock**
- On the **Cart** tab, pick a customer and add books. The book's price is **locked when it is first added**.
- If the book's price changes later, the cart keeps the locked price and shows "Price is now X; yours stays Y".
- **Place order** creates the order and its order lines using the locked prices, and reduces stock, all in one step. If anything fails (for example not enough stock), nothing is saved.
- Prices on existing orders never change when a book's price is edited. The database also enforces this: two triggers reject any attempt to edit the price on an order line or a cart line.
- The price is always read from the database when a book is added, never from the browser, so it can't be altered from the page.

**Books:** add, edit, delete; tag; search by title, author, ISBN or tag; sort by recently added, recently viewed, A → Z or Z → A; read and write reviews. A book that appears in an order can't be deleted.

**Orders:** list with customer, date, status, item count and total; **Details** shows each book with the price paid. **Customers** and **Authors:** lists with search and add forms.

Notes:
- New orders use a status that already exists in your `orders` table (you choose it next to **Place order**), so the database accepts it.
- "Recently added" uses the highest `book_id`, because `BOOKS` has no created date.
- "Recently viewed" is the last time anyone opened a book's reviews. It is shared by everyone who signs in.
- The currency symbol is `₹`; change `CURRENCY` at the top of `public/app.js`.
- If an ID column isn't auto-generated in your database, the site uses the next free number (highest ID + 1).

## Tables

| Table | Used for |
|---|---|
| `BOOKS` | `book_id`, `author_id`, `isbn`, `title`, `unit_price`, `stock_quantity`, `publication_year`, `last_viewed` |
| `AUTHORS` | `author_id`, `author_name`, `country` |
| `CUSTOMERS` | `customer_id`, `first_name`, `last_name`, `email`, `phone`, `registration_date` |
| `ORDERS` | `order_id`, `customer_id`, `order_date`, `order_status` |
| `ORDER_ITEMS` | `order_item_id`, `order_id`, `book_id`, `quantity`, `unit_price` (the price paid) |
| `REVIEWS` | `review_id`, `book_id`, `reviewer`, `rating`, `review_text`, `created_at` |
| `CART_ITEMS` (new) | `customer_id`, `book_id`, `quantity`, `locked_price`, `added_at` |
| `APP_SETTINGS` (new) | `setting_key`, `setting_value` (holds the low-stock threshold) |
| `APP_USERS` (new) | `user_id`, `username`, `password_hash`, `created_at` |
| `TAGS` (new) | `tag_id`, `tag_name` |
| `BOOK_TAGS` (new) | `book_id`, `tag_id` (links books to tags) |

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask API, login and sessions |
| `setup_cart_stock.sql` | One-time setup for the cart, settings and price-lock triggers |
| `setup_auth_tags.sql` | One-time setup for logins and tags |
| `export_db.py` | Saves your Oracle database to `database/bookstore.db` |
| `.gitignore` | Keeps `.env` and `venv/` out of Git |
| `requirements.txt` | Python packages |
| `public/index.html`, `style.css`, `app.js` | The user interface |
| `public/login.html`, `login.js` | The login page |
| `public/theme.js` | Dark / light toggle |
| `.env` | Your database credentials (never share or commit this) |

## Save the database to a .db file

Oracle doesn't keep a database in one file, so `export_db.py` copies your tables (structure and data) into a portable SQLite file:

```
python export_db.py
```

This writes `database/bookstore.db`. Run it again any time to refresh the snapshot. Open it in VS Code with the **SQLite Viewer** extension. The website still uses Oracle.

The file contains customer names, emails and phone numbers, and now also the `app_users` table with password hashes. Keep the GitHub repository **private**, or leave the personal data out:

```
python export_db.py --no-data customers,app_users
```

## Put the project on GitHub (from VS Code)

1. Install Git (https://git-scm.com) and create a free GitHub account.
2. In VS Code, open the project folder, then open **Source Control** (the branch icon in the left bar, or Ctrl+Shift+G).
3. Click **Initialize Repository**. The included `.gitignore` keeps `.env` (your password) out of Git.
4. Hover over the file list, click **+** to stage all changes, type a message such as `First version`, and click **Commit**.
5. Click **Publish Branch**, sign in to GitHub when asked, and choose **Private repository** (recommended).

Afterwards, commit as you go: change something, open Source Control, type a short message, press Ctrl+Enter to commit, then click **Sync Changes** to send it to GitHub.

## Troubleshooting

- **"A required table is missing. Run setup_cart_stock.sql and setup_auth_tags.sql":** you haven't run a setup script yet, or ran it as a different user than `.env` uses.
- **The login page shows an error instead of a form:** the same cause. Run `setup_auth_tags.sql`.
- **ORA-00955 name is already used (running a setup script):** a table from the script already exists. Remove that part of the script and run the rest.
- **ORA-02270 no matching unique or primary key (running a setup script):** `customers.customer_id` or `books.book_id` isn't a primary key in your database. Tell me and I'll adjust the script.
- **ORA-01017 invalid username/password:** check `.env`.
- **Connection refused / DPY-6005:** wrong host, port or service name, or the listener isn't running. Use the same details as in SQL Developer.
- **Page looks old after an update:** hard-refresh with Ctrl+F5.
- **Signed out every time you save a file:** Flask's debug mode restarts the server on each change, which creates a new session key. Set `SECRET_KEY` in `.env` while developing.
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
3. Copy `.env.example` to `.env` and fill in:
   - `DB_USER` and `DB_PASSWORD`: the same login you use in SQL Developer
   - `DB_CONNECT_STRING`: `host:port/service_name`, e.g. `localhost:1521/XEPDB1`
4. In the VS Code terminal:
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (Mac/Linux)
   pip install -r requirements.txt
   python app.py
   ```
5. Open http://localhost:3000

The `oracledb` package connects directly (Thin mode), so Oracle Instant Client is not needed for Oracle 12.1 and newer.

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

**Books:** add, edit, delete; search by title, author or ISBN; sort by recently added, recently viewed, A → Z or Z → A; read and write reviews. A book that appears in an order can't be deleted.

**Orders:** list with customer, date, status, item count and total; **Details** shows each book with the price paid. **Customers** and **Authors:** lists with search and add forms.

Notes:
- New orders use a status that already exists in your `orders` table (you choose it next to **Place order**), so the database accepts it.
- "Recently added" uses the highest `book_id`, because `BOOKS` has no created date.
- "Recently viewed" is the last time anyone opened a book's reviews. There is no login, so it is shared by everyone.
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

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask API |
| `setup_cart_stock.sql` | One-time setup for the cart, settings and price-lock triggers |
| `export_db.py` | Saves your Oracle database to `database/bookstore.db` |
| `.gitignore` | Keeps `.env` and `venv/` out of Git |
| `requirements.txt` | Python packages |
| `public/index.html`, `style.css`, `app.js` | The user interface |
| `.env` | Your database credentials (never share or commit this) |

## Save the database to a .db file

Oracle doesn't keep a database in one file, so `export_db.py` copies your tables (structure and data) into a portable SQLite file:

```
python export_db.py
```

This writes `database/bookstore.db`. Run it again any time to refresh the snapshot. Open it in VS Code with the **SQLite Viewer** extension. The website still uses Oracle.

The file contains customer names, emails and phone numbers. Keep the GitHub repository **private**, or leave the personal data out:

```
python export_db.py --no-data customers
```

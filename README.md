# Book Manager (Python Flask + Oracle)

A book store website that runs on your existing Oracle tables. The browser UI talks to a small Flask server, which reads and writes your database.

```
Browser (public/)  →  Flask API (app.py)  →  Oracle Database
```

## Your tooling

- **Database work:** Oracle SQL Developer extension in **VS Code**: connect, run queries, inspect tables.
- **Website code:** also in VS Code. Open this folder (`File > Open Folder`).

## Tables the site uses

| Table | Columns used |
|---|---|
| `BOOKS` | `book_id`, `author_id`, `isbn`, `title`, `unit_price`, `stock_quantity`, `publication_year`, `last_viewed` |
| `AUTHORS` | `author_id`, `author_name`, `country` |
| `CUSTOMERS` | `customer_id`, `first_name`, `last_name`, `email`, `phone`, `registration_date` |
| `ORDERS` | `order_id`, `customer_id`, `order_date`, `order_status` |
| `ORDER_ITEMS` | `order_item_id`, `order_id`, `book_id`, `quantity`, `unit_price` |
| `REVIEWS` | `review_id`, `book_id`, `reviewer`, `rating`, `review_text`, `created_at` |

`REVIEWS` and `BOOKS.LAST_VIEWED` were added for the reviews and "recently viewed" features. If you ever need to recreate them:

```sql
ALTER TABLE books ADD (last_viewed TIMESTAMP);

CREATE TABLE reviews (
  review_id   NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  book_id     NUMBER NOT NULL REFERENCES books (book_id) ON DELETE CASCADE,
  reviewer    VARCHAR2(80)  NOT NULL,
  rating      NUMBER(1)     NOT NULL CHECK (rating BETWEEN 1 AND 5),
  review_text VARCHAR2(1000),
  created_at  TIMESTAMP DEFAULT SYSTIMESTAMP
);
```

## Setup

1. Install Python 3.9 or newer.
2. Copy `.env.example` to `.env` and fill in:
   - `DB_USER` and `DB_PASSWORD`: the same login you use in SQL Developer
   - `DB_CONNECT_STRING`: `host:port/service_name`, e.g. `localhost:1521/XEPDB1`
3. In the VS Code terminal:
   ```
   python -m venv venv
   venv\Scripts\activate        (Windows)
   source venv/bin/activate     (Mac/Linux)
   pip install -r requirements.txt
   python app.py
   ```
4. Open http://localhost:3000

The `oracledb` package connects directly (Thin mode), so Oracle Instant Client is not needed for Oracle 12.1 and newer.

## What you can do

**Books**
- Add, edit and delete books (pick the author from a list)
- Search by title, author or ISBN
- Sort by recently added, recently viewed, title A → Z or title Z → A
- Click a title (or **Reviews**) to read reviews, see the average rating, post a 1-5 star review, or delete one
- A book that appears in an order can't be deleted; the site says so

**Orders:** list with customer, date, status, item count and total; **Details** shows every book in the order with quantity and prices. Searchable by order number, customer or status.

**Customers:** list with order counts, search, and add new customers.

**Authors:** list with book counts, and add new authors.

Notes:
- "Recently added" uses the highest `book_id`, because `BOOKS` has no created date.
- "Recently viewed" is the last time anyone opened a book's reviews. There is no login, so it is shared by everyone, and reviewers type their own name.
- The currency symbol is `₹`; change `CURRENCY` at the top of `public/app.js`.
- New rows: if an ID column isn't auto-generated in your database, the site uses the next free number (highest ID + 1).

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask API for books, reviews, authors, customers and orders |
| `requirements.txt` | Python packages |
| `public/index.html`, `style.css`, `app.js` | The user interface |
| `.env` | Your database credentials (never share or commit this) |

## Troubleshooting

- **ORA-01017 invalid username/password:** check `.env`.
- **Connection refused / DPY-6005:** wrong host, port or service name, or the listener isn't running. Use the same details as in SQL Developer.
- **ORA-00942 table or view does not exist:** `.env` points at a different database user than the one that owns the tables.
- **Page looks old after an update:** hard-refresh with Ctrl+F5.

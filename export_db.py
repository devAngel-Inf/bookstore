"""Copy your Oracle database (tables, structure and data) into one SQLite .db file.

Run from the project folder with your virtual environment active:

    python export_db.py
    python export_db.py --out database/bookstore.db
    python export_db.py --no-data customers          # structure only for these tables

The website keeps using Oracle. The .db file is a portable snapshot you can open
in VS Code (SQLite Viewer extension), share, or commit to GitHub.
Run it again any time to refresh the snapshot (it replaces the old file).
"""
import argparse
import datetime
import decimal
import os
import sqlite3
import sys

import oracledb
from dotenv import load_dotenv

oracledb.defaults.fetch_lobs = False  # return CLOB/BLOB as plain str/bytes

BATCH = 1000
PERSONAL_TABLES = {"CUSTOMERS"}


def sqlite_type(data_type, scale):
    t = (data_type or "").upper()
    if t == "NUMBER":
        if scale is None:
            return "NUMERIC"
        return "INTEGER" if scale == 0 else "REAL"
    if t in ("FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"):
        return "REAL"
    if t in ("BLOB", "RAW", "LONG RAW"):
        return "BLOB"
    return "TEXT"  # VARCHAR2, CHAR, CLOB, DATE, TIMESTAMP...


def convert(value):
    """Turn Oracle values into something SQLite stores faithfully."""
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def copy_table(cur, out, table, structure_only):
    cur.execute(
        "SELECT column_name, data_type, data_scale FROM user_tab_columns "
        "WHERE table_name = :t ORDER BY column_id",
        {"t": table},
    )
    cols = cur.fetchall()
    cur.execute(
        """SELECT cc.column_name
             FROM user_constraints c
             JOIN user_cons_columns cc
               ON cc.constraint_name = c.constraint_name AND cc.table_name = c.table_name
            WHERE c.constraint_type = 'P' AND c.table_name = :t
            ORDER BY cc.position""",
        {"t": table},
    )
    pk = [r[0].lower() for r in cur.fetchall()]

    defs = [f'"{name.lower()}" {sqlite_type(dtype, scale)}' for name, dtype, scale in cols]
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk) + ")")
    out.execute(f'CREATE TABLE "{table.lower()}" ({", ".join(defs)})')
    if structure_only:
        return 0

    cur.execute(f'SELECT * FROM "{table}"')
    marks = ", ".join("?" for _ in cols)
    count = 0
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            break
        out.executemany(
            f'INSERT INTO "{table.lower()}" VALUES ({marks})',
            [tuple(convert(v) for v in row) for row in rows],
        )
        count += len(rows)
    return count


def main():
    ap = argparse.ArgumentParser(description="Export the Oracle database to a SQLite .db file")
    ap.add_argument("--out", default=os.path.join("database", "bookstore.db"),
                    help="where to write the .db file (default: database/bookstore.db)")
    ap.add_argument("--no-data", default="",
                    help="comma-separated table names to copy as structure only (no rows)")
    args = ap.parse_args()

    load_dotenv()
    missing = [k for k in ("DB_USER", "DB_PASSWORD", "DB_CONNECT_STRING") if not os.getenv(k)]
    if missing:
        sys.exit("Missing in your .env file: " + ", ".join(missing))

    no_data = {n.strip().upper() for n in args.no_data.split(",") if n.strip()}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if os.path.exists(args.out):
        os.remove(args.out)

    out = sqlite3.connect(args.out)
    summary = []
    with oracledb.connect(
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dsn=os.getenv("DB_CONNECT_STRING"),
    ) as conn:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        tables = [r[0] for r in cur.fetchall() if "$" not in r[0]]  # skip internal tables
        for table in tables:
            summary.append((table, table in no_data, copy_table(cur, out, table, table in no_data)))
    out.commit()
    out.close()

    print(f"Saved {args.out}")
    for table, structure_only, rows in summary:
        print(f"  {table.lower():<14} {'structure only' if structure_only else f'{rows} rows'}")
    shared = [t for t, skipped, rows in summary if t in PERSONAL_TABLES and not skipped and rows]
    if shared:
        print("\nNote: this file contains customer names, emails and phone numbers. "
              "Keep your GitHub repository private, or re-run with:  --no-data customers")


if __name__ == "__main__":
    main()

import sqlite3
from contextlib import contextmanager
from . import config

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    store TEXT NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT,
    price_paid REAL NOT NULL,
    regular_price REAL,
    quantity_bought INTEGER DEFAULT 1,
    unit TEXT,
    quantity_label TEXT
);
CREATE INDEX IF NOT EXISTS idx_product_name ON purchase_history(product_name);
CREATE INDEX IF NOT EXISTS idx_store ON purchase_history(store);
CREATE INDEX IF NOT EXISTS idx_timestamp ON purchase_history(timestamp);
"""


def init_db():
    config.ensure_storage_dir()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.executescript(CREATE_TABLE_SQL)
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    config.ensure_storage_dir()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

import sqlite3

import pytest

from bizdata.schema import DDL
from bizdata.store import Store

# Small enough to check every expected number by hand. See test_reports.py.
CUSTOMERS = [
    (1, "alice@example.com", "Alice", "Ng", "CA", "2025-12-01 09:00:00"),
    (2, "bob@example.com", "Bob", "Ray", "TX", "2026-01-15 12:00:00"),
    (3, "cara@example.com", "Cara", "Lim", "WA", "2026-02-01 08:00:00"),
]
PRODUCTS = [
    (1, "CMP-001", "Tent", "Camping", 200, 100, 3, 5, 1),
    (2, "HIK-001", "Boots", "Hiking", 100, 50, 50, 5, 1),
    (3, "APP-001", "Jacket", "Apparel", 50, 20, 0, 2, 1),
    (4, "ACC-001", "Old Compass", "Accessories", 10, 4, 0, 5, 0),
]
ORDERS = [
    (1, 1, "2026-01-10 10:00:00", "completed", "web", 0),
    (2, 2, "2026-01-20 15:30:00", "completed", "marketplace", 0),
    (3, 1, "2026-02-05 19:45:00", "completed", "web", 0),
    (4, 2, "2026-02-14 11:00:00", "cancelled", "web", 0),
    (5, 2, "2026-03-01 08:15:00", "completed", "phone", 7.95),
]
ORDER_ITEMS = [
    (1, 1, 1, 1, 200),
    (2, 1, 3, 2, 45),
    (3, 2, 2, 1, 100),
    (4, 3, 2, 2, 90),
    (5, 4, 1, 1, 200),
    (6, 5, 3, 1, 50),
    (7, 5, 2, 1, 100),
]
REFUNDS = [
    (1, 2, "2026-01-25 10:00:00", 1, 45, "wrong size"),
    (2, 4, "2026-02-20 10:00:00", 1, 90, "defective"),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "store.db"
    con = sqlite3.connect(path)
    with con:
        con.executescript(DDL)
        con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", CUSTOMERS)
        con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)", PRODUCTS)
        con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", ORDERS)
        con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", ORDER_ITEMS)
        con.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", REFUNDS)
    con.close()
    return path


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "audit.jsonl"


@pytest.fixture
def store(db_path, audit_path):
    return Store(db_path, audit_path=audit_path, hidden_columns={"customers.email"}, source="test")

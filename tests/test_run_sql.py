import json
import sqlite3

import pytest

from bizdata.store import QueryError, Store


def test_returns_columns_rows_and_flag(store):
    result = store.run_sql("SELECT id, status FROM orders ORDER BY id")
    assert result["columns"] == ["id", "status"]
    assert result["rows"][0] == [1, "completed"]
    assert result["row_count"] == 5
    assert result["truncated"] is False


def test_row_cap(db_path):
    capped = Store(db_path, max_rows=2).run_sql("SELECT * FROM order_items")
    assert capped["row_count"] == 2
    assert capped["truncated"] is True

    exact = Store(db_path, max_rows=7).run_sql("SELECT * FROM order_items")
    assert exact["row_count"] == 7
    assert exact["truncated"] is False


def test_timeout_stops_runaway_query(db_path):
    slow = Store(db_path, timeout_s=0.3)
    endless = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n"
    with pytest.raises(QueryError, match="longer than"):
        slow.run_sql(endless)


@pytest.mark.parametrize("sql", ["DELETE FROM orders", "PRAGMA table_info(orders)", "SELECT 1; SELECT 2"])
def test_rejected_by_guard(store, sql):
    with pytest.raises(QueryError):
        store.run_sql(sql)


# These skip the text guard on purpose, to show the layers underneath hold on their own.
@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "INSERT INTO products (id, sku, name, category, price, cost, stock_on_hand, reorder_point)"
    " VALUES (9, 'X', 'X', 'X', 1, 1, 1, 1)",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE ':memory:' AS scratch",
    "SELECT load_extension('evil')",
    "SELECT length(zeroblob(500000000))",
])
def test_authorizer_blocks_without_text_guard(store, sql):
    with pytest.raises(QueryError):
        store._execute_guarded(sql)


def test_file_is_read_only_even_unguarded(store):
    con = store.connect(guarded=False)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM orders")
    con.close()


def test_hidden_column_reads_as_null(store):
    result = store.run_sql("SELECT id, email FROM customers ORDER BY id")
    assert [row[1] for row in result["rows"]] == [None, None, None]
    filtered = store.run_sql("SELECT id FROM customers WHERE email LIKE '%alice%'")
    assert filtered["row_count"] == 0


def test_sql_errors_come_back_readable(store):
    with pytest.raises(QueryError, match="no such column"):
        store.run_sql("SELECT nope FROM orders")


def test_missing_database_is_explained(tmp_path):
    with pytest.raises(FileNotFoundError, match="make_sample_db"):
        Store(tmp_path / "missing.db").run_sql("SELECT 1")


def test_describe_schema(store):
    info = store.describe_schema()
    tables = {t["name"]: t for t in info["tables"]}
    assert set(tables) == {"customers", "products", "orders", "order_items", "refunds"}
    assert tables["orders"]["row_count"] == 5
    email = next(c for c in tables["customers"]["columns"] if c["name"] == "email")
    assert "Hidden" in email["note"]
    assert "customer_id -> customers.id" in tables["orders"]["foreign_keys"]
    assert "## orders (5 rows)" in store.schema_markdown()


def test_audit_log_records_successes_and_failures(store, audit_path):
    store.run_sql("SELECT id FROM orders")
    with pytest.raises(QueryError):
        store.run_sql("DROP TABLE orders")

    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2
    ok, failed = entries
    assert ok["tool"] == "run_sql"
    assert ok["args"] == {"query": "SELECT id FROM orders"}
    assert ok["rows"] == 5
    assert ok["ok"] is True
    assert ok["source"] == "test"
    assert ok["duration_ms"] >= 0
    assert "ts" in ok
    assert failed["ok"] is False
    assert failed["error"].startswith("QueryError: only SELECT")

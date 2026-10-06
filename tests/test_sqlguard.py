import pytest

from bizdata.sqlguard import UnsafeQuery, validate


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from orders where status = 'completed';",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT 'drop table orders; delete from x' AS harmless_text",
    'SELECT "update" FROM (SELECT 1 AS "update")',
    "SELECT replace(name, 'Tent', 'Shelter') FROM products",
    "SELECT 1 -- trailing comment mentioning DELETE",
    "/* leading comment */ SELECT 1",
    "SELECT 'it''s fine; really' AS s",
    "SELECT updated_total FROM (SELECT 1 AS updated_total)",
])
def test_allows_plain_selects(sql):
    assert validate(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO orders VALUES (1)",
    "UPDATE products SET price = 0",
    "DELETE FROM refunds",
    "DROP TABLE orders",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'other.db' AS other",
    "CREATE TABLE x (id INTEGER)",
    "REPLACE INTO products (id) VALUES (1)",
    "VACUUM",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1 --\n; DROP TABLE orders",
    "SELECT 1; /* sneaky */ DELETE FROM orders",
    "/* x */ PRAGMA writable_schema = 1",
    "WITH t AS (SELECT 1) DELETE FROM orders",
    "WITH t AS (SELECT 1) INSERT INTO orders SELECT * FROM t",
    "SELECT 'a' AS x; ATTACH 'b.db' AS b",
    "SEL/**/ECT 1",
    "SELECT 1 /* unterminated",
    "SELECT 'unterminated",
    "",
    "   ;  ",
])
def test_rejects_everything_else(sql):
    with pytest.raises(UnsafeQuery):
        validate(sql)


def test_strips_trailing_semicolon():
    assert validate("  SELECT 1 ;  ") == "SELECT 1"

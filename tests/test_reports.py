"""Expected numbers are worked out by hand from the fixture in conftest.py.

Revenue by line (cancelled order 4 excluded):
  order 1  Alice  2026-01-10  tent 200, jacket 2 x 45 = 90 (1 jacket refunded, 45)
  order 2  Bob    2026-01-20  boots 100
  order 3  Alice  2026-02-05  boots 2 x 90 = 180 (1 pair refunded, 90)
  order 5  Bob    2026-03-01  jacket 50, boots 100
"""

import json

import pytest


def test_sales_by_month(store):
    result = store.sales_summary(group_by="month")
    assert result["rows"] == [
        {"month": "2026-01", "orders": 2, "units": 4, "revenue": 390, "refunds": 45, "net_revenue": 345},
        {"month": "2026-02", "orders": 1, "units": 2, "revenue": 180, "refunds": 90, "net_revenue": 90},
        {"month": "2026-03", "orders": 1, "units": 2, "revenue": 150, "refunds": 0, "net_revenue": 150},
    ]
    assert result["totals"] == {
        "orders": 4, "units": 8, "revenue": 720, "refunds": 135, "net_revenue": 585, "avg_order_value": 180,
    }


def test_sales_by_category_sorted_by_net(store):
    rows = store.sales_summary(group_by="category")["rows"]
    assert [(r["category"], r["revenue"], r["net_revenue"]) for r in rows] == [
        ("Hiking", 380, 290),
        ("Camping", 200, 200),
        ("Apparel", 140, 95),
    ]


def test_sales_by_week_starts_monday(store):
    rows = store.sales_summary(group_by="week")["rows"]
    assert [r["week"] for r in rows] == ["2026-01-05", "2026-01-19", "2026-02-02", "2026-02-23"]


def test_sales_date_range_is_inclusive(store):
    result = store.sales_summary(start="2026-01-20", end="2026-03-01", group_by="quarter")
    assert result["rows"] == [
        {"quarter": "2026-Q1", "orders": 3, "units": 5, "revenue": 430, "refunds": 90, "net_revenue": 340},
    ]


def test_sales_empty_range(store):
    result = store.sales_summary(start="2030-01-01", end="2030-01-31")
    assert result["rows"] == []
    assert result["totals"]["revenue"] == 0


@pytest.mark.parametrize("kwargs", [
    {"group_by": "customer_email"},
    {"group_by": "month; DROP TABLE orders"},
    {"start": "last tuesday"},
    {"start": "2026-03-01", "end": "2026-01-01"},
])
def test_sales_rejects_bad_input(store, kwargs):
    with pytest.raises(ValueError):
        store.sales_summary(**kwargs)


def test_top_customers(store):
    rows = store.top_customers(n=5)["rows"]
    assert [(r["name"], r["orders"], r["revenue"], r["refunds"], r["net_revenue"]) for r in rows] == [
        ("Alice Ng", 2, 470, 135, 335),
        ("Bob Ray", 2, 250, 0, 250),
    ]
    assert "email" not in rows[0]


def test_top_customers_in_range(store):
    rows = store.top_customers(n=1, start="2026-02-01", end="2026-03-31")["rows"]
    assert [(r["name"], r["net_revenue"]) for r in rows] == [("Bob Ray", 150)]


def test_top_customers_bounds(store):
    with pytest.raises(ValueError):
        store.top_customers(n=0)


def test_refund_rate_by_category(store):
    rows = store.refund_rate(by="category")["rows"]
    assert rows == [
        {"category": "Apparel", "units_sold": 3, "units_refunded": 1, "revenue": 140, "refunded": 45, "refund_rate_pct": 32.14},
        {"category": "Hiking", "units_sold": 4, "units_refunded": 1, "revenue": 380, "refunded": 90, "refund_rate_pct": 23.68},
        {"category": "Camping", "units_sold": 1, "units_refunded": 0, "revenue": 200, "refunded": 0, "refund_rate_pct": 0.0},
    ]


def test_refund_rate_rejects_unknown_grouping(store):
    with pytest.raises(ValueError):
        store.refund_rate(by="reason")


def test_inventory_alerts(store):
    result = store.inventory_alerts()
    assert result["as_of"] == "2026-03-01"
    assert [(r["sku"], r["status"], r["units_last_30d"], r["days_of_cover"]) for r in result["rows"]] == [
        ("APP-001", "out of stock", 1, 0.0),
        ("CMP-001", "at or below reorder point", 0, None),
    ]


def test_inventory_alerts_cover_threshold(store):
    rows = store.inventory_alerts(min_days_of_cover=600)["rows"]
    boots = next(r for r in rows if r["sku"] == "HIK-001")
    assert boots["days_of_cover"] == 500.0
    assert boots["status"] == "under 600 days of cover"


def test_every_business_tool_is_audited(store, audit_path):
    store.sales_summary(group_by="category")
    store.top_customers(n=3)
    store.refund_rate()
    store.inventory_alerts()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [e["tool"] for e in entries] == ["sales_summary", "top_customers", "refund_rate", "inventory_alerts"]
    assert entries[0]["args"] == {"start": None, "end": None, "group_by": "category"}
    assert entries[0]["rows"] == 3
    assert entries[1]["args"]["n"] == 3

"""Fixed SQL behind the business tools.

Values always go in as parameters. Grouping columns can't be parameters in
SQL, so they come from the dicts below and the caller only picks a key.
"""

import sqlite3
from datetime import date, timedelta

# One row per sold line item, with what was later refunded on it. Every report
# starts here so "revenue" means the same thing everywhere.
LINES = """
lines AS (
    SELECT o.id AS order_id, o.customer_id, o.ordered_at, o.channel,
           p.id AS product_id, p.sku, p.name AS product, p.category,
           i.quantity,
           i.quantity * i.unit_price AS revenue,
           COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.order_item_id = i.id), 0) AS refunded,
           COALESCE((SELECT SUM(r.quantity) FROM refunds r WHERE r.order_item_id = i.id), 0) AS refunded_units
    FROM orders o
    JOIN order_items i ON i.order_id = o.id
    JOIN products p ON p.id = i.product_id
    WHERE o.status = 'completed'
      AND (:start IS NULL OR o.ordered_at >= :start)
      AND (:end IS NULL OR o.ordered_at < :end)
)
"""

SALES_GROUPS = {
    "day": "date(ordered_at)",
    "week": "date(ordered_at, '-6 days', 'weekday 1')",  # Monday the week starts on
    "month": "strftime('%Y-%m', ordered_at)",
    "quarter": "strftime('%Y', ordered_at) || '-Q' || ((CAST(strftime('%m', ordered_at) AS INTEGER) + 2) / 3)",
    "category": "category",
    "product": "product",
    "channel": "channel",
}
TIME_GROUPS = {"day", "week", "month", "quarter"}

REFUND_GROUPS = {
    "category": "category",
    "product": "product",
    "month": "strftime('%Y-%m', ordered_at)",
    "channel": "channel",
}


def date_range(start: str | None, end: str | None) -> dict:
    """Turn inclusive YYYY-MM-DD bounds into SQL parameters (end becomes exclusive)."""
    try:
        s = date.fromisoformat(start) if start else None
        e = date.fromisoformat(end) if end else None
    except ValueError:
        raise ValueError("dates must look like YYYY-MM-DD") from None
    if s and e and s > e:
        raise ValueError(f"start {s} is after end {e}")
    return {"start": s.isoformat() if s else None, "end": (e + timedelta(days=1)).isoformat() if e else None}


def _dicts(cur: sqlite3.Cursor) -> list[dict]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def sales_summary(con, start, end, group_by):
    if group_by not in SALES_GROUPS:
        raise ValueError(f"group_by must be one of: {', '.join(SALES_GROUPS)}")
    params = date_range(start, end)
    order = group_by if group_by in TIME_GROUPS else "net_revenue DESC"
    rows = _dicts(con.execute(f"""
        WITH {LINES}
        SELECT {SALES_GROUPS[group_by]} AS {group_by},
               COUNT(DISTINCT order_id) AS orders,
               SUM(quantity) AS units,
               ROUND(SUM(revenue), 2) AS revenue,
               ROUND(SUM(refunded), 2) AS refunds,
               ROUND(SUM(revenue) - SUM(refunded), 2) AS net_revenue
        FROM lines
        GROUP BY 1
        ORDER BY {order}
    """, params))
    totals = _dicts(con.execute(f"""
        WITH {LINES}
        SELECT COUNT(DISTINCT order_id) AS orders,
               COALESCE(SUM(quantity), 0) AS units,
               ROUND(COALESCE(SUM(revenue), 0), 2) AS revenue,
               ROUND(COALESCE(SUM(refunded), 0), 2) AS refunds,
               ROUND(COALESCE(SUM(revenue) - SUM(refunded), 0), 2) AS net_revenue,
               ROUND(SUM(revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value
        FROM lines
    """, params))[0]
    return {"start": start, "end": end, "group_by": group_by, "rows": rows, "totals": totals}


def top_customers(con, n, start, end):
    if not 1 <= n <= 100:
        raise ValueError("n must be between 1 and 100")
    params = {**date_range(start, end), "n": n}
    rows = _dicts(con.execute(f"""
        WITH {LINES}
        SELECT c.id AS customer_id,
               c.first_name || ' ' || c.last_name AS name,
               c.state,
               COUNT(DISTINCT l.order_id) AS orders,
               ROUND(SUM(l.revenue), 2) AS revenue,
               ROUND(SUM(l.refunded), 2) AS refunds,
               ROUND(SUM(l.revenue) - SUM(l.refunded), 2) AS net_revenue,
               MIN(l.ordered_at) AS first_order,
               MAX(l.ordered_at) AS last_order
        FROM lines l
        JOIN customers c ON c.id = l.customer_id
        GROUP BY c.id
        ORDER BY net_revenue DESC, c.id
        LIMIT :n
    """, params))
    return {"start": start, "end": end, "rows": rows}


def refund_rate(con, by, start, end):
    if by not in REFUND_GROUPS:
        raise ValueError(f"by must be one of: {', '.join(REFUND_GROUPS)}")
    order = "month" if by == "month" else "refund_rate_pct DESC, revenue DESC"
    rows = _dicts(con.execute(f"""
        WITH {LINES}
        SELECT {REFUND_GROUPS[by]} AS {by},
               SUM(quantity) AS units_sold,
               SUM(refunded_units) AS units_refunded,
               ROUND(SUM(revenue), 2) AS revenue,
               ROUND(SUM(refunded), 2) AS refunded,
               ROUND(100.0 * SUM(refunded) / SUM(revenue), 2) AS refund_rate_pct
        FROM lines
        GROUP BY 1
        ORDER BY {order}
    """, date_range(start, end)))
    return {"start": start, "end": end, "by": by, "rows": rows}


def inventory_alerts(con, min_days_of_cover):
    as_of = con.execute("SELECT date(MAX(ordered_at)) FROM orders WHERE status = 'completed'").fetchone()[0]
    rows = _dicts(con.execute("""
        WITH recent AS (
            SELECT i.product_id, SUM(i.quantity) AS units
            FROM order_items i
            JOIN orders o ON o.id = i.order_id
            WHERE o.status = 'completed'
              AND o.ordered_at >= date(:as_of, '-29 days')
              AND o.ordered_at < date(:as_of, '+1 day')
            GROUP BY i.product_id
        )
        SELECT p.sku, p.name, p.category, p.stock_on_hand, p.reorder_point,
               COALESCE(r.units, 0) AS units_last_30d,
               CASE WHEN COALESCE(r.units, 0) = 0 THEN NULL
                    ELSE ROUND(p.stock_on_hand / (r.units / 30.0), 1) END AS days_of_cover
        FROM products p
        LEFT JOIN recent r ON r.product_id = p.id
        WHERE p.active = 1
          AND (p.stock_on_hand <= p.reorder_point
               OR (r.units > 0 AND p.stock_on_hand / (r.units / 30.0) < :min_cover))
        ORDER BY p.stock_on_hand > 0, days_of_cover IS NULL, days_of_cover, p.sku
    """, {"as_of": as_of, "min_cover": min_days_of_cover}))
    for row in rows:
        if row["stock_on_hand"] == 0:
            row["status"] = "out of stock"
        elif row["stock_on_hand"] <= row["reorder_point"]:
            row["status"] = "at or below reorder point"
        else:
            row["status"] = f"under {min_days_of_cover} days of cover"
    return {"as_of": as_of, "min_days_of_cover": min_days_of_cover, "rows": rows}

"""Table definitions plus the plain-English notes the model reads before writing SQL.

The notes matter more than they look. Without "revenue excludes cancelled orders"
or "dates are text", a model will write SQL that runs fine and returns the wrong
number. If you point this at your own database, rewrite NOTES for it.
"""

DDL = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE products (
    id             INTEGER PRIMARY KEY,
    sku            TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    price          NUMERIC NOT NULL,
    cost           NUMERIC NOT NULL,
    stock_on_hand  INTEGER NOT NULL,
    reorder_point  INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    ordered_at   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
    channel      TEXT NOT NULL CHECK (channel IN ('web', 'marketplace', 'phone')),
    shipping     NUMERIC NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC NOT NULL
);

CREATE TABLE refunds (
    id             INTEGER PRIMARY KEY,
    order_item_id  INTEGER NOT NULL REFERENCES order_items(id),
    refunded_at    TEXT NOT NULL,
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    amount         NUMERIC NOT NULL,
    reason         TEXT NOT NULL
);

CREATE INDEX idx_orders_ordered_at ON orders(ordered_at);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
CREATE INDEX idx_refunds_item ON refunds(order_item_id);
"""

TABLE_NOTES = {
    "customers": "One row per customer account. Some never ordered.",
    "products": "Catalog. Inactive products are discontinued and should be left out of stock questions.",
    "orders": "One row per checkout. Cancelled orders never shipped and carry no revenue.",
    "order_items": "Line items. Revenue lives here, not on orders.",
    "refunds": "Item-level refunds, possibly partial (quantity < order_items.quantity).",
}

COLUMN_NOTES = {
    ("customers", "state"): "Two-letter US state code.",
    ("customers", "created_at"): "Account creation time, text 'YYYY-MM-DD HH:MM:SS', store local time.",
    ("products", "price"): "Current list price in USD. Use order_items.unit_price for what was actually charged.",
    ("products", "cost"): "Unit cost in USD (what the store pays the supplier).",
    ("products", "reorder_point"): "Reorder when stock_on_hand falls to or below this.",
    ("products", "active"): "1 = sold today, 0 = discontinued.",
    ("orders", "ordered_at"): "Text 'YYYY-MM-DD HH:MM:SS', store local time. Use date(), strftime() to bucket.",
    ("orders", "status"): "'completed' or 'cancelled'. Only completed orders count as sales.",
    ("orders", "channel"): "'web' (own site), 'marketplace' (third-party), 'phone'.",
    ("orders", "shipping"): "Shipping charged to the customer, USD. Not included in revenue figures.",
    ("order_items", "unit_price"): "USD actually charged per unit, after discounts. Revenue = quantity * unit_price.",
    ("refunds", "refunded_at"): "Text 'YYYY-MM-DD HH:MM:SS'.",
    ("refunds", "amount"): "USD refunded for this line. Net revenue = revenue - refunds.amount.",
    ("refunds", "reason"): "Free-ish text from a fixed list: wrong size, defective, changed mind, arrived late, not as described.",
}

CONVENTIONS = [
    "Revenue means SUM(order_items.quantity * order_items.unit_price) for orders with status = 'completed'. Shipping is excluded.",
    "Net revenue subtracts refunds.amount. The business tools attribute a refund to the date of the original order, not the refund date.",
    "Timestamps are TEXT in 'YYYY-MM-DD HH:MM:SS'. Compare with string ranges, e.g. ordered_at >= '2026-07-01' AND ordered_at < '2026-10-01'.",
    "Money columns are USD. Round to 2 decimals for display.",
]

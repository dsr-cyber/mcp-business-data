"""Build the Harbor & Pine Supply sample database.

Everything here is made up. The point is data with enough shape to make the
questions interesting: seasonality that differs by category, growth that
differs by category, a handful of loyal repeat buyers, one product with a
quality problem, and a few items about to run out.

    python scripts/make_sample_db.py                 # data/harbor_pine.db
    python scripts/make_sample_db.py --out /tmp/x.db --seed 7
"""

import argparse
import math
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from bizdata.schema import DDL

START = date(2025, 4, 1)
END = date(2026, 9, 30)

# (name, list price). Order within a category is popularity order.
CATALOG = {
    "Camping": [
        ("Ridgeline 2P Backpacking Tent", 289.00),
        ("Cedar Flat 4P Family Tent", 349.00),
        ("Summit 20F Down Sleeping Bag", 239.00),
        ("Trailhead Insulated Sleeping Pad", 119.00),
        ("Two-Burner Camp Stove", 139.00),
        ("Collapsible Camp Kitchen Set", 64.00),
        ("Folding Camp Chair", 49.00),
        ("LED Lantern 500", 34.00),
    ],
    "Hiking": [
        ("Switchback 45L Pack", 179.00),
        ("Daylight 22L Daypack", 89.00),
        ("Carbon Trekking Poles (pair)", 129.00),
        ("Waterproof Hiking Boot", 169.00),
        ("Trail Runner Low", 134.00),
        ("Merino Hiking Socks 3-Pack", 42.00),
        ("Hydration Reservoir 2L", 36.00),
        ("Trail Map Case", 18.00),
    ],
    "Apparel": [
        ("Stormfront Rain Shell", 199.00),
        ("Harbor Down Jacket", 249.00),
        ("Midweight Fleece Pullover", 79.00),
        ("Merino Base Layer Top", 89.00),
        ("Merino Base Layer Bottom", 84.00),
        ("Convertible Hiking Pant", 74.00),
        ("Sun Hoodie", 54.00),
        ("Wool Beanie", 28.00),
        ("Insulated Gloves", 46.00),
    ],
    "Climbing": [
        ("Granite Climbing Harness", 74.00),
        ("Edge Climbing Shoe", 149.00),
        ("Chalk Bag + Chalk", 29.00),
        ("Locking Carabiner 3-Pack", 39.00),
        ("Dynamic Rope 60m", 219.00),
        ("Crash Pad", 189.00),
        ("Climbing Helmet", 69.00),
    ],
    "Water": [
        ("Inflatable Kayak", 429.00),
        ("Touring SUP Board", 649.00),
        ("Adjustable Paddle", 119.00),
        ("Dry Bag 20L", 32.00),
        ("Type III Life Vest", 79.00),
        ("Water Shoes", 49.00),
    ],
    "Accessories": [
        ("Insulated Bottle 32oz", 34.00),
        ("Headlamp 350", 44.00),
        ("Multi-Tool", 59.00),
        ("First Aid Kit", 39.00),
        ("Gift Card $50", 50.00),
        ("Bear Canister", 84.00),
        ("Compass", 24.00),
        ("Fire Starter Kit", 19.00),
    ],
}

SKU_PREFIX = {"Camping": "CMP", "Hiking": "HIK", "Apparel": "APP", "Climbing": "CLB", "Water": "WTR", "Accessories": "ACC"}

# Relative demand by calendar month (index 0 = January).
SEASON = {
    "Camping":     [0.35, 0.40, 0.65, 0.95, 1.40, 1.75, 1.80, 1.60, 1.05, 0.70, 0.60, 0.80],
    "Hiking":      [0.45, 0.50, 0.80, 1.15, 1.35, 1.40, 1.35, 1.30, 1.25, 1.10, 0.70, 0.75],
    "Apparel":     [1.30, 1.00, 0.85, 0.70, 0.55, 0.45, 0.45, 0.60, 0.95, 1.40, 1.75, 1.95],
    "Climbing":    [0.85, 0.85, 0.95, 1.05, 1.10, 1.05, 1.00, 1.00, 1.05, 1.05, 0.95, 1.10],
    "Water":       [0.10, 0.15, 0.30, 0.65, 1.30, 2.30, 2.60, 2.20, 0.90, 0.30, 0.15, 0.20],
    "Accessories": [0.70, 0.70, 0.80, 0.90, 1.00, 1.05, 1.05, 1.00, 0.95, 1.00, 1.60, 2.40],
}

# Compound monthly growth. Climbing is the breakout category.
GROWTH = {"Camping": 0.012, "Hiking": 0.018, "Apparel": 0.008, "Climbing": 0.045, "Water": 0.004, "Accessories": 0.010}
CATEGORY_SHARE = {"Camping": 0.24, "Hiking": 0.21, "Apparel": 0.22, "Climbing": 0.08, "Water": 0.09, "Accessories": 0.16}
REFUND_RATE = {"Camping": 0.035, "Hiking": 0.045, "Apparel": 0.085, "Climbing": 0.03, "Water": 0.05, "Accessories": 0.02}
BAD_BATCH_SKU = "CMP-002"  # the family tent with a zipper problem

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
         "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Chris", "Karen",
         "Daniel", "Lisa", "Matthew", "Nancy", "Anthony", "Sandra", "Mark", "Ashley", "Steven", "Emily",
         "Andrew", "Megan", "Kevin", "Rachel", "Brian", "Laura", "Tyler", "Hannah", "Luis", "Maria",
         "Wei", "Priya", "Omar", "Aisha", "Diego", "Sofia", "Noah", "Olivia", "Ethan", "Ava"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
        "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
        "Wright", "Scott", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall",
        "Patel", "Chen", "Kim", "Murphy", "Rivera", "Cooper", "Reed", "Bailey", "Ward", "Brooks"]
STATES = {"CA": 14, "WA": 9, "OR": 7, "CO": 8, "TX": 8, "NY": 6, "UT": 4, "MT": 2, "ID": 2, "AZ": 4,
          "NC": 4, "VT": 2, "ME": 2, "MN": 3, "MI": 3, "PA": 4, "MA": 3, "GA": 3, "FL": 4, "IL": 4,
          "WI": 2, "NM": 2, "VA": 3, "TN": 2, "NH": 2}
REASONS = {"wrong size": 4, "defective": 2, "changed mind": 3, "arrived late": 1, "not as described": 1}
CHANNELS = {"web": 70, "marketplace": 22, "phone": 8}


def pick(rng, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def months_since_start(d: date) -> int:
    return (d.year - START.year) * 12 + d.month - START.month


def category_weight(cat: str, d: date) -> float:
    return CATEGORY_SHARE[cat] * SEASON[cat][d.month - 1] * (1 + GROWTH[cat]) ** months_since_start(d)


def poisson(rng, lam: float) -> int:
    # Knuth's method is fine for the small daily rates used here.
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def order_time(rng, d: date) -> datetime:
    hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 6, 7, 6, 5, 5, 6, 7, 8, 9, 9, 8, 5, 2])[0]
    return datetime(d.year, d.month, d.day, hour, rng.randrange(60), rng.randrange(60))


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build(out: Path, seed: int) -> dict:
    rng = random.Random(seed)

    products = []  # (id, sku, name, category, price, cost)
    by_category = {}
    for cat, items in CATALOG.items():
        for i, (name, price) in enumerate(items, start=1):
            pid = len(products) + 1
            cost = round(price * rng.uniform(0.42, 0.58), 2)
            products.append((pid, f"{SKU_PREFIX[cat]}-{i:03d}", name, cat, price, cost))
            by_category.setdefault(cat, []).append(pid)
    product = {p[0]: p for p in products}
    sku_id = {p[1]: p[0] for p in products}
    # Zipf-ish popularity inside each category: first item sells most.
    popularity = {cat: [1 / (rank + 1) ** 0.8 for rank in range(len(ids))] for cat, ids in by_category.items()}

    customers = []  # (id, email, first, last, state, created_at)
    loyalty = []    # weight for being picked as a returning customer

    def new_customer(when: datetime) -> int:
        cid = len(customers) + 1
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first.lower()}.{last.lower()}{cid}@example.com"
        customers.append((cid, email, first, last, pick(rng, STATES), ts(when)))
        loyalty.append(rng.paretovariate(1.6))
        return cid

    orders, items, refunds = [], [], []
    day, n_days = START, (END - START).days + 1
    for _ in range(n_days):
        weights = {cat: category_weight(cat, day) for cat in CATALOG}
        demand = sum(weights.values())
        weekend = 1.15 if day.weekday() >= 5 else 1.0
        black_friday_week = day.month == 11 and 22 <= day.day <= 30
        lam = 6.2 * demand * weekend * (1.8 if black_friday_week else 1.0)

        for _ in range(poisson(rng, lam)):
            when = order_time(rng, day)
            if customers and rng.random() < 0.38:
                cid = rng.choices(range(1, len(customers) + 1), weights=loyalty)[0]
            else:
                cid = new_customer(when - timedelta(minutes=rng.randrange(5, 600)))

            oid = len(orders) + 1
            status = "cancelled" if rng.random() < 0.03 else "completed"
            channel = pick(rng, CHANNELS)
            n_lines = rng.choices([1, 2, 3, 4], weights=[62, 25, 9, 4])[0]
            subtotal = 0.0
            chosen = set()
            for line in range(n_lines):
                cat = pick(rng, weights) if line == 0 or rng.random() < 0.5 else rng.choice(list(CATALOG))
                pid = rng.choices(by_category[cat], weights=popularity[cat])[0]
                if pid in chosen:
                    continue
                chosen.add(pid)
                price = product[pid][4]
                qty = 1 if price > 100 else rng.choices([1, 2, 3], weights=[75, 20, 5])[0]
                if black_friday_week:
                    price = round(price * 0.75, 2)
                elif rng.random() < 0.15:
                    price = round(price * rng.choice([0.9, 0.85, 0.8]), 2)
                item_id = len(items) + 1
                items.append((item_id, oid, pid, qty, price))
                subtotal += qty * price

                if status != "completed":
                    continue
                rate = REFUND_RATE[cat]
                if product[pid][1] == BAD_BATCH_SKU:
                    rate = 0.19
                if rng.random() < rate:
                    refund_at = when + timedelta(days=rng.randint(4, 40), hours=rng.randint(0, 9))
                    if refund_at.date() <= END:
                        rqty = qty if rng.random() < 0.8 else rng.randint(1, qty)
                        reason = "defective" if product[pid][1] == BAD_BATCH_SKU and rng.random() < 0.7 else pick(rng, REASONS)
                        if cat != "Apparel" and reason == "wrong size":
                            reason = "changed mind"
                        refunds.append((len(refunds) + 1, item_id, ts(refund_at), rqty, round(rqty * price, 2), reason))

            shipping = 0.0 if subtotal >= 75 or channel == "marketplace" else 7.95
            orders.append((oid, cid, ts(when), status, channel, shipping))
        day += timedelta(days=1)

    # Some sign-ups never buy anything.
    for _ in range(len(customers) // 9):
        d = START + timedelta(days=rng.randrange(n_days))
        new_customer(order_time(rng, d))

    # Stock levels from the last 30 days of demand, so alerts line up with real velocity.
    cutoff = ts(datetime.combine(END - timedelta(days=29), datetime.min.time()))
    order_at = {o[0]: o[2] for o in orders}
    recent_units = {pid: 0 for pid in product}
    for _, oid, pid, qty, _ in items:
        if order_at[oid] >= cutoff:
            recent_units[pid] += qty

    product_rows = []
    for pid, sku, name, cat, price, cost in products:
        daily = recent_units[pid] / 30
        reorder_point = max(5, math.ceil(daily * 14))
        roll = rng.random()
        if roll < 0.06:
            stock = 0
        elif roll < 0.22:
            stock = rng.randint(1, reorder_point)
        else:
            stock = reorder_point + rng.randint(5, 40) + math.ceil(daily * rng.uniform(10, 60))
        active = 1
        if sku == "ACC-008":  # discontinued, sold through
            active, stock = 0, 0
        product_rows.append((pid, sku, name, cat, price, cost, stock, reorder_point, active))

    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(out)
    with con:
        con.executescript(DDL)
        con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)
        con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)", product_rows)
        con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", orders)
        con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
        con.executemany("INSERT INTO refunds VALUES (?,?,?,?,?,?)", refunds)
    con.close()
    return {"customers": len(customers), "products": len(product_rows), "orders": len(orders),
            "order_items": len(items), "refunds": len(refunds)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "harbor_pine.db")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    counts = build(args.out, args.seed)
    print(f"wrote {args.out}")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>6}")


if __name__ == "__main__":
    main()

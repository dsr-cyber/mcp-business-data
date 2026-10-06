import argparse
import functools
from typing import Any, Literal

import anyio
from mcp.server.fastmcp import FastMCP

from .store import Store

INSTRUCTIONS = """\
Read-only access to the Harbor & Pine Supply store database (outdoor gear, US customers).
Prefer sales_summary, top_customers, refund_rate and inventory_alerts: their numbers use
the store's agreed definitions. For anything they don't cover, call describe_schema first,
then run_sql. Dates are YYYY-MM-DD and ranges are inclusive."""


def build_server(store: Store, **settings) -> FastMCP:
    """settings go straight to FastMCP (host, port, ...)."""
    mcp = FastMCP("harbor-pine-data", instructions=INSTRUCTIONS, **settings)

    async def call(method, **kwargs):
        # SQLite is blocking; keep it off the event loop so one slow query doesn't stall the session.
        return await anyio.to_thread.run_sync(functools.partial(method, **kwargs))

    @mcp.tool()
    async def run_sql(query: str) -> dict[str, Any]:
        """Run one read-only SQLite SELECT (or WITH ... SELECT) and return columns, rows and a truncated flag.

        Results are capped (200 rows by default) and queries are stopped after a few seconds.
        Aggregate in SQL rather than pulling raw rows. Call describe_schema first if you
        haven't seen the tables.
        """
        return await call(store.run_sql, query=query)

    @mcp.tool()
    async def describe_schema() -> dict[str, Any]:
        """List every table with its columns, row counts, foreign keys, and notes on how to use them.

        Read the conventions section before writing SQL: it defines revenue, refunds and date formats.
        """
        return await call(store.describe_schema)

    @mcp.tool()
    async def sales_summary(
        start: str | None = None,
        end: str | None = None,
        group_by: Literal["day", "week", "month", "quarter", "category", "product", "channel"] = "month",
    ) -> dict[str, Any]:
        """Orders, units, revenue, refunds and net revenue, grouped by time period or dimension.

        start/end are inclusive YYYY-MM-DD dates; leave either out for no bound. Only completed
        orders count. Refunds are attributed to the original order's date. Weeks start on Monday.
        Includes a totals row with average order value.
        """
        return await call(store.sales_summary, start=start, end=end, group_by=group_by)

    @mcp.tool()
    async def top_customers(n: int = 10, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        """The n customers with the highest net revenue (after refunds) in an optional date range.

        n is 1-100. Returns customer id, name, state, order count, revenue, refunds, and first/last order time.
        """
        return await call(store.top_customers, n=n, start=start, end=end)

    @mcp.tool()
    async def refund_rate(
        by: Literal["category", "product", "month", "channel"] = "category",
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """Refunded dollars as a percentage of revenue, plus units sold and refunded, grouped by `by`.

        Dates filter on when the order was placed. Small groups can show extreme rates;
        check units_sold before drawing conclusions.
        """
        return await call(store.refund_rate, by=by, start=start, end=end)

    @mcp.tool()
    async def inventory_alerts(min_days_of_cover: int = 14) -> dict[str, Any]:
        """Active products that are out of stock, at or below their reorder point, or running low.

        Days of cover = stock on hand / average daily units sold over the last 30 days, measured
        up to the most recent order in the data. Products with no recent sales show no cover figure.
        """
        return await call(store.inventory_alerts, min_days_of_cover=min_days_of_cover)

    @mcp.resource("schema://tables", name="schema", mime_type="text/markdown")
    def schema_resource() -> str:
        """Tables, columns and usage notes for the store database, as Markdown."""
        return store.schema_markdown()

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for the store database")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    store = Store.from_env(source="mcp")
    if args.http:
        build_server(store, host=args.host, port=args.port).run(transport="streamable-http")
    else:
        build_server(store).run(transport="stdio")


if __name__ == "__main__":
    main()

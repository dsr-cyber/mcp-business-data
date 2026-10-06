import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

from bizdata.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_lists_tools_and_resource(store):
    async with create_connected_server_and_client_session(build_server(store)) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "run_sql", "describe_schema", "sales_summary", "top_customers", "refund_rate", "inventory_alerts",
        }
        sales = next(t for t in tools.tools if t.name == "sales_summary")
        assert "quarter" in sales.inputSchema["properties"]["group_by"]["enum"]

        resources = await client.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["schema://tables"]


async def test_tool_call_over_mcp(store):
    async with create_connected_server_and_client_session(build_server(store)) as client:
        result = await client.call_tool("sales_summary", {"group_by": "category"})
        assert not result.isError
        assert result.structuredContent["totals"]["net_revenue"] == 585
        assert json.loads(result.content[0].text)["rows"][0]["category"] == "Hiking"


async def test_rejected_query_is_a_tool_error(store):
    async with create_connected_server_and_client_session(build_server(store)) as client:
        result = await client.call_tool("run_sql", {"query": "DELETE FROM orders"})
        assert result.isError
        assert "only SELECT" in result.content[0].text


async def test_schema_resource(store):
    async with create_connected_server_and_client_session(build_server(store)) as client:
        result = await client.read_resource(AnyUrl("schema://tables"))
        assert "## order_items" in result.contents[0].text

import os
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def parameters(dsn: str = "postgresql://reader:hidden@127.0.0.1:1/unavailable"):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "pgscope_mcp"],
        cwd=ROOT,
        env={"PGSCOPE_DSN": dsn, "PGSCOPE_ALLOW_ANALYZE": "false", "PYTHONUTF8": "1"},
    )


async def test_stdio_discovery_validation_and_exit_without_database():
    async with Client(parameters(), mode="legacy", read_timeout_seconds=15) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "list_tables",
            "describe_table",
            "query",
            "explain",
            "diagnose",
        }
        result = await client.call_tool("query", {"sql": "DELETE FROM orders"})
        assert result.is_error
        assert result.structured_content["error"]["code"] == "QUERY_REJECTED"
        result = await client.call_tool("explain", {"sql": "SELECT 1", "analyze": True})
        assert result.is_error
        assert result.structured_content["error"]["code"] == "ANALYZE_DISABLED"
        result = await client.call_tool("query", {"sql": "SELECT 1", "max_rows": 1001})
        assert result.is_error
        result = await client.call_tool("query", {"sql": "SELECT 1"})
        assert result.is_error
        assert result.structured_content["error"]["code"] == "CONNECTION_FAILED"
        assert "hidden" not in str(result)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PGSCOPE_TEST_DSN"), reason="需要 PGSCOPE_TEST_DSN")
async def test_all_five_tools_over_real_stdio_and_postgres():
    async with Client(
        parameters(os.environ["PGSCOPE_TEST_DSN"]), read_timeout_seconds=20
    ) as client:
        for name, args, expected_key in [
            ("list_tables", {}, "tables"),
            ("describe_table", {"table": "orders"}, "columns"),
            ("query", {"sql": "SELECT COUNT(*) FROM orders"}, "rows"),
            ("explain", {"sql": "SELECT id FROM orders WHERE id=1"}, "plan"),
            ("diagnose", {"sql": "SELECT * FROM orders WHERE id=1"}, "findings"),
        ]:
            result = await client.call_tool(name, args)
            assert not result.is_error, result
            assert expected_key in result.structured_content
            assert result.content[0].text
            if name == "query":
                assert result.structured_content["rows"] == [[100000]]
            if name == "diagnose":
                assert any(
                    f["rule_id"] == "SELECT_STAR" for f in result.structured_content["findings"]
                )

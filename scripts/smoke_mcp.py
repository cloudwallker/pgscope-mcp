"""A model-free, real stdio MCP client exercising all five tools."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def smoke(output: Path | None = None) -> dict:
    dsn = os.environ.get("PGSCOPE_DSN")
    if not dsn:
        raise SystemExit("请设置只读连接环境变量 PGSCOPE_DSN。")
    # Pass an explicit allowlist; never forward the benchmark admin credential.
    env = {"PGSCOPE_DSN": dsn, "PYTHONUTF8": "1"}
    for key in ("PGSCOPE_ALLOWED_SCHEMAS", "PGSCOPE_ALLOW_ANALYZE"):
        if key in os.environ:
            env[key] = os.environ[key]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pgscope_mcp"],
        env=env,
    )
    calls = [
        ("list_tables", {}),
        ("describe_table", {"table": "orders"}),
        ("query", {"sql": "SELECT COUNT(*) AS order_count FROM orders"}),
        ("explain", {"sql": "SELECT id FROM orders WHERE id = 42"}),
        ("diagnose", {"sql": "SELECT * FROM orders WHERE date(created_at) = DATE '2024-06-15'"}),
    ]
    report = {}
    async with Client(params, read_timeout_seconds=20) as client:
        listed = await client.list_tools()
        if {tool.name for tool in listed.tools} != {name for name, _ in calls}:
            raise RuntimeError("MCP 工具列表不完整")
        for name, arguments in calls:
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(f"{name}: {result.structured_content}")
            report[name] = result.structured_content
            print(f"PASS {name}")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="无模型费用的 PGScope MCP 端到端演示")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    asyncio.run(smoke(args.output_json))


if __name__ == "__main__":
    main()

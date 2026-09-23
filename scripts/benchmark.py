"""Reproducible, tightly scoped benchmarks for the local PGScope demo database."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

MARKER = "pgscope-demo-v1"
EXPECTED_COUNTS = {"customers": 5000, "orders": 100000, "order_items": 200000}
MEASUREMENTS = 5

CUSTOMER_QUERY = (
    "SELECT id, customer_id, created_at, status, total FROM public.orders "
    "WHERE customer_id = 42 ORDER BY id"
)
DATE_FUNCTION_QUERY = (
    "SELECT id, customer_id, created_at, status, total FROM public.orders "
    "WHERE date(created_at) = date '2024-06-15' ORDER BY id"
)
DATE_RANGE_QUERY = (
    "SELECT id, customer_id, created_at, status, total FROM public.orders "
    "WHERE created_at >= timestamp '2024-06-15 00:00:00' "
    "AND created_at < timestamp '2024-06-16 00:00:00' ORDER BY id"
)

BUFFER_FIELDS = {
    "Shared Hit Blocks": "shared_hit",
    "Shared Read Blocks": "shared_read",
    "Shared Dirtied Blocks": "shared_dirtied",
    "Shared Written Blocks": "shared_written",
    "Local Hit Blocks": "local_hit",
    "Local Read Blocks": "local_read",
    "Local Dirtied Blocks": "local_dirtied",
    "Local Written Blocks": "local_written",
    "Temp Read Blocks": "temp_read",
    "Temp Written Blocks": "temp_written",
}


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_target_dsn(dsn: str) -> dict[str, str]:
    """Reject implicit, remote, or non-demo targets before connecting."""
    try:
        target = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise ValueError("演示数据库连接串无效") from exc
    if (
        target.get("dbname") != "pgscope_demo"
        or target.get("user") != "postgres"
        or target.get("port") != "55432"
        or not _is_loopback(target.get("host", ""))
        or (target.get("hostaddr") and not _is_loopback(target["hostaddr"]))
        or target.get("service")
    ):
        raise ValueError("仅允许连接本机 55432 端口的 pgscope_demo 演示数据库")
    return target


def validate_connected_endpoint(hostaddr: str, port: int) -> None:
    """Check the resolved peer too, so a modified localhost DNS entry cannot escape."""
    if not _is_loopback(hostaddr or "") or port != 55432:
        raise ValueError("连接后的实际地址必须是 55432 端口的 loopback")


def validate_marker_rows(rows: list[tuple[str]]) -> None:
    if rows != [(MARKER,)]:
        raise ValueError("演示数据库 marker 缺失、重复或版本不符")


def validate_row_counts(counts: dict[str, int]) -> None:
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"演示数据行数不符：预期 {EXPECTED_COUNTS}，实际 {counts}")


def assert_same_rows(before: list[tuple[Any, ...]], after: list[tuple[Any, ...]]) -> None:
    if before != after:
        raise ValueError("实验前后查询结果不一致，拒绝生成基准报告")


def plan_metrics(document: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(document, list) or len(document) != 1:
        raise ValueError("EXPLAIN JSON 必须包含一个计划")
    result = document[0]
    root = result["Plan"]
    scans = []

    def visit(node: dict[str, Any]) -> None:
        node_type = node["Node Type"]
        if node_type.endswith("Scan"):
            scans.append(
                {
                    "node_type": node_type,
                    "relation": node.get("Relation Name"),
                    "index": node.get("Index Name"),
                }
            )
        for child in node.get("Plans", []):
            visit(child)

    visit(root)
    return {
        "execution_time_ms": float(result["Execution Time"]),
        "root_buffers": {
            short: root[long] for long, short in BUFFER_FIELDS.items() if long in root
        },
        "scans": scans,
    }


def summarize_trials(documents: list[list[dict[str, Any]]]) -> dict[str, Any]:
    if len(documents) != MEASUREMENTS:
        raise ValueError(f"每种查询必须恰好测量 {MEASUREMENTS} 次")
    trials = []
    for document in documents:
        trials.append({**plan_metrics(document), "raw_plan": document})
    return {
        "median_execution_ms": statistics.median(trial["execution_time_ms"] for trial in trials),
        "trials": trials,
    }


def _check_database(cursor: psycopg.Cursor[Any]) -> dict[str, int]:
    cursor.execute("SELECT current_database(), session_user")
    if cursor.fetchone() != ("pgscope_demo", "postgres"):
        raise ValueError("演示数据库名称或管理账号不符")
    cursor.execute("SELECT marker FROM public.pgscope_demo_marker")
    validate_marker_rows(cursor.fetchall())
    counts = {}
    for table in EXPECTED_COUNTS:
        # Names come only from the fixed constant above, never from CLI input.
        cursor.execute(f"SELECT count(*) FROM public.{table}")
        counts[table] = cursor.fetchone()[0]
    validate_row_counts(counts)
    return counts


def _reset_fixed_indexes(cursor: psycopg.Cursor[Any]) -> None:
    """Restore one fixed baseline on every run without touching table rows."""
    cursor.execute("DROP INDEX IF EXISTS public.idx_demo_orders_customer_id")
    cursor.execute("DROP INDEX IF EXISTS public.idx_demo_orders_created_at")
    cursor.execute("CREATE INDEX idx_demo_orders_created_at ON public.orders (created_at)")
    cursor.execute("ANALYZE public.orders")


def _explain(cursor: psycopg.Cursor[Any], query: str) -> list[dict[str, Any]]:
    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query)
    return cursor.fetchone()[0]


def _measure_variant(
    cursor: psycopg.Cursor[Any], label: str, query: str
) -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
    cursor.execute(query)
    rows = cursor.fetchall()
    _explain(cursor, query)  # One warm-up, deliberately omitted from the five samples.
    documents = [_explain(cursor, query) for _ in range(MEASUREMENTS)]
    return (
        {
            "label": label,
            "query": query,
            "result_count": len(rows),
            "summary": summarize_trials(documents),
        },
        rows,
    )


def run_benchmark(dsn: str) -> dict[str, Any]:
    validate_target_dsn(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection:
        validate_connected_endpoint(connection.info.hostaddr, connection.info.port)
        with connection.cursor() as cursor:
            counts = _check_database(cursor)

            _reset_fixed_indexes(cursor)
            customer_before, customer_before_rows = _measure_variant(
                cursor, "customer_id 无索引", CUSTOMER_QUERY
            )
            cursor.execute(
                "CREATE INDEX idx_demo_orders_customer_id ON public.orders (customer_id)"
            )
            cursor.execute("ANALYZE public.orders")
            customer_after, customer_after_rows = _measure_variant(
                cursor, "customer_id 有索引", CUSTOMER_QUERY
            )
            assert_same_rows(customer_before_rows, customer_after_rows)

            _reset_fixed_indexes(cursor)
            date_before, date_before_rows = _measure_variant(
                cursor, "date(created_at) 函数条件", DATE_FUNCTION_QUERY
            )
            date_after, date_after_rows = _measure_variant(
                cursor, "created_at 半开范围", DATE_RANGE_QUERY
            )
            assert_same_rows(date_before_rows, date_after_rows)

    return {
        "database": "pgscope_demo",
        "marker": MARKER,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "row_counts": counts,
        "warmup_runs_per_variant": 1,
        "measured_runs_per_variant": MEASUREMENTS,
        "experiments": [
            {
                "id": "customer_id_index",
                "title": "高选择性 customer_id：无索引与添加索引",
                "results_equal": True,
                "before": customer_before,
                "after": customer_after,
            },
            {
                "id": "created_at_date_range",
                "title": "created_at 日期函数与半开范围（日期索引已存在）",
                "results_equal": True,
                "before": date_before,
                "after": date_after,
            },
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PGScope 演示数据库基准报告",
        "",
        f"数据库：`{report['database']}`。每种查询预热 1 次，随后运行 5 次 "
        "`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`。",
        "",
        "下列倍数仅描述本次实测；受缓存、机器和 PostgreSQL 执行计划影响，不保证固定提升倍数。",
        "",
    ]
    for experiment in report["experiments"]:
        before = experiment["before"]
        after = experiment["after"]
        before_ms = before["summary"]["median_execution_ms"]
        after_ms = after["summary"]["median_execution_ms"]
        ratio = f"{before_ms / after_ms:.2f}×" if after_ms > 0 else "不可计算"
        lines.extend(
            [
                f"## {experiment['title']}",
                "",
                f"完整结果一致：{'是' if experiment['results_equal'] else '否'}。"
                f"执行时间中位数：{before_ms:.3f} ms → {after_ms:.3f} ms；"
                f"本次观测比值：{ratio}。",
                "",
            ]
        )
        for variant in (before, after):
            lines.extend(
                [
                    f"### {variant['label']}",
                    "",
                    f"SQL：`{variant['query']}`",
                    "",
                    "| 次数 | 执行时间 (ms) | 根节点 Shared Hit | 根节点 Shared Read | "
                    "根节点其他 buffers | 扫描类型 |",
                    "| ---: | ---: | ---: | ---: | --- | --- |",
                ]
            )
            for number, trial in enumerate(variant["summary"]["trials"], 1):
                buffers = trial["root_buffers"]
                others = (
                    ", ".join(
                        f"{key}={value}"
                        for key, value in buffers.items()
                        if key not in {"shared_hit", "shared_read"}
                    )
                    or "—"
                )
                scans = ", ".join(scan["node_type"] for scan in trial["scans"]) or "—"
                lines.append(
                    f"| {number} | {trial['execution_time_ms']:.3f} | "
                    f"{buffers.get('shared_hit', 0)} | {buffers.get('shared_read', 0)} | "
                    f"{others} | {scans} |"
                )
            lines.append("")
    lines.append("每次完整原始 JSON 计划保存在同目录的 `benchmark.json`。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行本机 PGScope 演示数据库基准")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/benchmark"), help="报告输出目录"
    )
    args = parser.parse_args()
    dsn = os.environ.get("PGSCOPE_DEMO_ADMIN_DSN")
    if not dsn:
        parser.error("必须设置 PGSCOPE_DEMO_ADMIN_DSN（仅用于演示实验，勿用于 MCP 服务）")
    report = run_benchmark(dsn)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "benchmark.json"
    markdown_path = args.output_dir / "benchmark.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"已生成 {json_path} 和 {markdown_path}")


if __name__ == "__main__":
    main()

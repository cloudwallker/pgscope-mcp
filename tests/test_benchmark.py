"""Benchmark calculations and safety checks without a live database."""

import pytest

from scripts import benchmark


def plan(time_ms, *, node_type="Seq Scan", relation="orders", hit=0, read=0):
    return [
        {
            "Plan": {
                "Node Type": node_type,
                "Relation Name": relation,
                "Shared Hit Blocks": hit,
                "Shared Read Blocks": read,
            },
            "Planning Time": 0.2,
            "Execution Time": time_ms,
        }
    ]


def test_accepts_only_explicit_local_demo_admin_target():
    target = benchmark.validate_target_dsn(
        "postgresql://postgres:example@127.0.0.1:55432/pgscope_demo"
    )
    assert target["dbname"] == "pgscope_demo"
    assert target["user"] == "postgres"
    assert target["port"] == "55432"


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:example@192.168.1.8:55432/pgscope_demo",
        "postgresql://postgres:example@localhost:5432/pgscope_demo",
        "postgresql://postgres:example@localhost:55432/production",
        "postgresql://pgscope_reader:example@localhost:55432/pgscope_demo",
        "dbname=pgscope_demo user=postgres port=55432",
        "host=localhost,remote.test port=55432 dbname=pgscope_demo user=postgres",
        "host=localhost hostaddr=192.168.1.8 port=55432 dbname=pgscope_demo user=postgres",
        "service=demo host=localhost port=55432 dbname=pgscope_demo user=postgres",
    ],
)
def test_rejects_target_that_could_escape_the_demo(dsn):
    with pytest.raises(ValueError, match="演示数据库"):
        benchmark.validate_target_dsn(dsn)


@pytest.mark.parametrize("rows", [[], [("wrong",)], [("pgscope-demo-v1",)] * 2])
def test_rejects_missing_wrong_or_duplicated_marker(rows):
    with pytest.raises(ValueError, match="marker"):
        benchmark.validate_marker_rows(rows)


def test_accepts_exactly_one_expected_marker():
    benchmark.validate_marker_rows([("pgscope-demo-v1",)])


def test_rejects_non_loopback_address_after_dns_resolution():
    with pytest.raises(ValueError, match="loopback"):
        benchmark.validate_connected_endpoint("192.168.1.8", 55432)


def test_accepts_resolved_loopback_address():
    benchmark.validate_connected_endpoint("127.0.0.1", 55432)


def test_median_uses_all_five_measured_execution_times():
    trials = [plan(value) for value in [9.0, 2.0, 4.0, 20.0, 6.0]]
    summary = benchmark.summarize_trials(trials)
    assert summary["median_execution_ms"] == 6.0
    assert [trial["execution_time_ms"] for trial in summary["trials"]] == [
        9.0,
        2.0,
        4.0,
        20.0,
        6.0,
    ]
    assert summary["trials"][0]["raw_plan"] == trials[0]


def test_rejects_incomplete_measurement_set():
    with pytest.raises(ValueError, match="5"):
        benchmark.summarize_trials([plan(1.0)] * 4)


def test_records_root_buffers_and_nested_scan_types():
    document = [
        {
            "Plan": {
                "Node Type": "Aggregate",
                "Shared Hit Blocks": 17,
                "Shared Read Blocks": 3,
                "Temp Written Blocks": 2,
                "Plans": [
                    {
                        "Node Type": "Bitmap Heap Scan",
                        "Relation Name": "orders",
                        "Plans": [
                            {"Node Type": "Bitmap Index Scan", "Index Name": "idx_demo_orders_date"}
                        ],
                    }
                ],
            },
            "Execution Time": 3.5,
        }
    ]
    metrics = benchmark.plan_metrics(document)
    assert metrics["execution_time_ms"] == 3.5
    assert metrics["root_buffers"] == {"shared_hit": 17, "shared_read": 3, "temp_written": 2}
    assert metrics["scans"] == [
        {"node_type": "Bitmap Heap Scan", "relation": "orders", "index": None},
        {"node_type": "Bitmap Index Scan", "relation": None, "index": "idx_demo_orders_date"},
    ]


def test_rejects_a_single_differing_result_row():
    before = [(1, 42), (2, 42)]
    after = [(1, 42), (2, 43)]
    with pytest.raises(ValueError, match="结果不一致"):
        benchmark.assert_same_rows(before, after)


def test_identical_complete_results_are_accepted():
    benchmark.assert_same_rows([(1, 42), (2, 42)], [(1, 42), (2, 42)])


def test_rejects_changed_demo_row_counts():
    with pytest.raises(ValueError, match="数据行数"):
        benchmark.validate_row_counts({"customers": 5000, "orders": 99999, "order_items": 200000})


def test_report_renders_measured_values_without_claiming_guaranteed_gain():
    report = {
        "database": "pgscope_demo",
        "experiments": [
            {
                "title": "客户索引",
                "results_equal": True,
                "before": {
                    "label": "无索引",
                    "query": "SELECT id FROM public.orders WHERE customer_id = 42",
                    "summary": benchmark.summarize_trials(
                        [plan(v, hit=8) for v in [4, 5, 6, 7, 8]]
                    ),
                },
                "after": {
                    "label": "有索引",
                    "query": "SELECT id FROM public.orders WHERE customer_id = 42",
                    "summary": benchmark.summarize_trials(
                        [plan(v, node_type="Index Scan") for v in [1, 2, 3, 4, 5]]
                    ),
                },
            }
        ],
    }
    markdown = benchmark.render_markdown(report)
    assert "客户索引" in markdown
    assert "5.000" in markdown
    assert "3.000" in markdown
    assert "Seq Scan" in markdown
    assert "Index Scan" in markdown
    assert "Shared Hit" in markdown
    assert "不保证" in markdown

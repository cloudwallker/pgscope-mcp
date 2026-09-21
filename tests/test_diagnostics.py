"""Hand-written PostgreSQL JSON plans for evidence-based diagnoses."""

import sqlglot

from pgscope_mcp.diagnostics import diagnose_query, summarize_plan
from pgscope_mcp.models import TableRef, ValidatedSQL


def validated(sql: str, *tables: TableRef) -> ValidatedSQL:
    return ValidatedSQL(sql, sqlglot.parse_one(sql, read="postgres"), tables)


def table(schema="public", name="orders", estimated_rows=100_000, indexes=None):
    return {
        "schema": schema,
        "name": name,
        "estimated_rows": estimated_rows,
        "size_bytes": 8192,
        "comment": None,
        "columns": [{"name": "status", "type": "text", "nullable": True, "default": None}],
        "primary_key": [],
        "foreign_keys": [],
        "indexes": indexes or [],
    }


def finding(findings, rule_id):
    matches = [item for item in findings if item["rule_id"] == rule_id]
    assert len(matches) == 1, findings
    item = matches[0]
    assert item["severity"] in {"info", "warning"}
    assert item["message"] and item["suggestion"]
    assert "ast_sql" in item["evidence"] or "node_path" in item["evidence"]
    assert "candidate_index" in item
    return item


def test_summary_keeps_paths_and_per_node_metrics_without_summing_children():
    plan = [
        {
            "Plan": {
                "Node Type": "Sort",
                "Startup Cost": 22.0,
                "Total Cost": 33.0,
                "Plan Rows": 100,
                "Actual Rows": 9,
                "Actual Loops": 2,
                "Actual Total Time": 5.0,
                "Sort Method": "external merge",
                "Sort Space Type": "Disk",
                "Sort Space Used": 512,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Schema": "public",
                        "Relation Name": "orders",
                        "Startup Cost": 0.0,
                        "Total Cost": 20.0,
                        "Plan Rows": 100,
                        "Actual Rows": 9,
                        "Actual Loops": 2,
                        "Actual Total Time": 4.0,
                    }
                ],
            },
            "Planning Time": 1.2,
            "Execution Time": 6.4,
        }
    ]
    result = summarize_plan(plan)
    assert result["estimated_cost"] == 33.0
    assert result["planning_time_ms"] == 1.2
    assert result["execution_time_ms"] == 6.4
    assert result["analyzed"] is True
    assert len(result["nodes"]) == 2
    assert result["nodes"][0]["path"] == "$.Plan"
    assert result["nodes"][1]["path"] == "$.Plan.Plans[0]"
    assert result["nodes"][1]["actual_rows"] == 9
    assert result["nodes"][1]["actual_loops"] == 2
    assert result["nodes"][0]["sort_space_type"] == "Disk"


def test_estimate_only_plan_does_not_invent_time_or_actual_rows():
    result = summarize_plan([{"Plan": {"Node Type": "Seq Scan", "Total Cost": 15.0}}])
    assert result["analyzed"] is False
    assert result["execution_time_ms"] is None
    assert result["planning_time_ms"] is None
    assert result["nodes"][0]["actual_rows"] is None
    assert result["estimated_cost"] == 15.0


def test_select_star_detects_projection_but_not_count_star():
    ref = TableRef("public", "orders")
    projection = diagnose_query(validated("SELECT * FROM orders", ref), [], [])
    item = finding(projection, "SELECT_STAR")
    assert "SELECT" in item["evidence"]["ast_sql"].upper()
    assert diagnose_query(validated("SELECT count(*) FROM orders", ref), [], []) == []


def test_cartesian_join_requires_unconditional_join():
    refs = (TableRef("public", "orders", "o"), TableRef("public", "customers", "c"))
    cross = diagnose_query(
        validated("SELECT o.id FROM orders o CROSS JOIN customers c", *refs), [], []
    )
    finding(cross, "CARTESIAN_JOIN")
    proper = diagnose_query(
        validated("SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id", *refs),
        [],
        [],
    )
    assert proper == []


def test_where_join_predicate_makes_cross_join_conditional_but_or_does_not():
    refs = (TableRef("public", "orders", "o"), TableRef("public", "customers", "c"))
    joined = validated(
        "SELECT o.id FROM orders o CROSS JOIN customers c WHERE o.customer_id = c.id",
        *refs,
    )
    assert diagnose_query(joined, [], []) == []
    conditional_or = validated(
        "SELECT o.id FROM orders o CROSS JOIN customers c "
        "WHERE o.customer_id = c.id OR o.status = 'open'",
        *refs,
    )
    finding(diagnose_query(conditional_or, [], []), "CARTESIAN_JOIN")


def test_on_true_is_unconditional_join():
    refs = (TableRef("public", "orders", "o"), TableRef("public", "customers", "c"))
    sql = validated("SELECT o.id FROM orders o JOIN customers c ON TRUE", *refs)
    finding(diagnose_query(sql, [], []), "CARTESIAN_JOIN")


def test_selective_large_sequential_scan_requires_table_statistics():
    sql = validated("SELECT id FROM orders WHERE status = 'open'", TableRef("public", "orders"))
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 1000,
            }
        }
    ]
    item = finding(diagnose_query(sql, plan, [table()]), "SELECTIVE_SEQ_SCAN")
    assert item["evidence"]["node_path"] == "$.Plan"
    assert item["evidence"]["estimated_fraction"] == 0.01
    assert item["candidate_index"] == 'CREATE INDEX ON "public"."orders" ("status");'
    assert diagnose_query(sql, plan, [table(estimated_rows=9999)]) == []
    assert diagnose_query(sql, plan, []) == []


def test_candidate_index_respects_schema_and_existing_leading_column():
    sql = validated(
        "SELECT id FROM sales.orders WHERE status = 'open'", TableRef("sales", "orders")
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "sales",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 1000,
            }
        }
    ]
    existing = {
        "name": "ix",
        "definition": "CREATE INDEX ix ON sales.orders USING btree (status)",
        "columns": ["status"],
        "unique": False,
        "method": "btree",
        "partial": False,
        "expression": False,
    }
    other_schema = table("public", "orders", indexes=[existing])
    found = finding(
        diagnose_query(sql, plan, [other_schema, table("sales", "orders")]), "SELECTIVE_SEQ_SCAN"
    )
    assert found["candidate_index"] == 'CREATE INDEX ON "sales"."orders" ("status");'
    found = finding(
        diagnose_query(sql, plan, [table("sales", "orders", indexes=[existing])]),
        "SELECTIVE_SEQ_SCAN",
    )
    assert found["candidate_index"] is None


def test_ambiguous_unqualified_plan_relation_does_not_guess_schema():
    refs = (TableRef("public", "orders", "p"), TableRef("sales", "orders", "s"))
    sql = validated("SELECT p.id FROM public.orders p JOIN sales.orders s ON p.id = s.id", *refs)
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 100,
            }
        }
    ]
    assert diagnose_query(sql, plan, [table(), table("sales", "orders")]) == []


def test_candidate_index_requires_confirmed_column_and_quotes_identifiers():
    sql = validated(
        'SELECT id FROM "Retail"."Order" WHERE "st""atus" = \'open\'', TableRef("Retail", "Order")
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "Retail",
                "Relation Name": "Order",
                "Filter": "(st\"atus = 'open'::text)",
                "Plan Rows": 100,
            }
        }
    ]
    meta = table("Retail", "Order")
    item = finding(diagnose_query(sql, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is None
    meta["columns"] = [{"name": 'st"atus', "type": "text", "nullable": True, "default": None}]
    item = finding(diagnose_query(sql, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] == 'CREATE INDEX ON "Retail"."Order" ("st""atus");'


def test_partial_and_expression_indexes_do_not_block_full_index_candidate():
    sql = validated("SELECT id FROM orders WHERE status = 'open'", TableRef("public", "orders"))
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 1000,
            }
        }
    ]
    for partial, expression in [(True, False), (False, True)]:
        ix = {
            "name": "ix",
            "definition": "",
            "columns": ["status"],
            "unique": False,
            "method": "btree",
            "partial": partial,
            "expression": expression,
        }
        item = finding(diagnose_query(sql, plan, [table(indexes=[ix])]), "SELECTIVE_SEQ_SCAN")
        assert item["candidate_index"] is not None


def test_invalid_index_does_not_count_as_usable_leading_index():
    sql = validated("SELECT id FROM orders WHERE status = 'open'", TableRef("public", "orders"))
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 1000,
            }
        }
    ]
    ix = {
        "name": "ix",
        "definition": "",
        "columns": ["status"],
        "unique": False,
        "method": "btree",
        "partial": False,
        "expression": False,
        "valid": False,
    }
    item = finding(diagnose_query(sql, plan, [table(indexes=[ix])]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is not None


def test_complex_join_filter_does_not_produce_index_ddl():
    refs = (TableRef("public", "orders", "o"), TableRef("public", "customers", "c"))
    sql = validated(
        "SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id "
        "WHERE o.status = 'open'",
        *refs,
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(status = 'open'::text)",
                "Plan Rows": 1000,
            }
        }
    ]
    item = finding(diagnose_query(sql, plan, [table()]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is None


def test_cte_alias_filter_does_not_suggest_index_on_output_alias():
    sql = validated(
        "WITH q AS (SELECT customer_id AS total FROM orders) SELECT total FROM q WHERE total = 42",
        TableRef("public", "orders"),
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(customer_id = 42)",
                "Plan Rows": 1000,
            }
        }
    ]
    meta = table()
    meta["columns"] = [
        {"name": name, "type": "integer", "nullable": False, "default": None}
        for name in ("customer_id", "total")
    ]
    item = finding(diagnose_query(sql, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is None


def test_derived_table_filter_does_not_suggest_index_on_output_alias():
    sql = validated(
        "SELECT total FROM (SELECT customer_id AS total FROM orders) q WHERE total = 42",
        TableRef("public", "orders"),
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(customer_id = 42)",
                "Plan Rows": 1000,
            }
        }
    ]
    meta = table()
    meta["columns"] = [
        {"name": name, "type": "integer", "nullable": False, "default": None}
        for name in ("customer_id", "total")
    ]
    item = finding(diagnose_query(sql, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is None


def test_base_table_column_alias_list_does_not_suggest_index_on_output_alias():
    ref = TableRef("public", "orders", "o")
    renamed = validated(
        "SELECT total FROM orders AS o(id, total, created_at, status, amount) WHERE total = 42",
        ref,
    )
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Schema": "public",
                "Relation Name": "orders",
                "Filter": "(customer_id = 42)",
                "Plan Rows": 1000,
            }
        }
    ]
    meta = table()
    meta["columns"] = [
        {"name": name, "type": "integer", "nullable": False, "default": None}
        for name in ("id", "customer_id", "created_at", "status", "total")
    ]
    item = finding(diagnose_query(renamed, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] is None

    ordinary = validated("SELECT o.total FROM orders AS o WHERE o.total = 42", ref)
    item = finding(diagnose_query(ordinary, plan, [meta]), "SELECTIVE_SEQ_SCAN")
    assert item["candidate_index"] == 'CREATE INDEX ON "public"."orders" ("total");'


def test_function_or_cast_on_indexed_filter_column_is_reported_only_for_matching_index():
    ref = TableRef("public", "orders")
    ix = {
        "name": "ix_status",
        "definition": "",
        "columns": ["status"],
        "unique": False,
        "method": "btree",
        "partial": False,
        "expression": False,
    }
    meta = table(indexes=[ix])
    for sql in [
        "SELECT id FROM orders WHERE lower(status) = 'open'",
        "SELECT id FROM orders WHERE status::text = 'open'",
    ]:
        finding(diagnose_query(validated(sql, ref), [], [meta]), "INDEXED_COLUMN_WRAPPED")
    assert (
        diagnose_query(
            validated("SELECT id FROM orders WHERE lower(status) = 'open'", ref), [], [table()]
        )
        == []
    )
    assert (
        diagnose_query(
            validated("SELECT id FROM orders WHERE status = lower('OPEN')", ref), [], [meta]
        )
        == []
    )


def test_row_mismatch_compares_per_loop_values_and_handles_zero():
    sql = validated("SELECT id FROM orders", TableRef("public", "orders"))
    plan = [
        {"Plan": {"Node Type": "Seq Scan", "Plan Rows": 100, "Actual Rows": 9, "Actual Loops": 20}}
    ]
    item = finding(diagnose_query(sql, plan, []), "ROW_ESTIMATE_MISMATCH")
    assert item["evidence"]["actual_rows_per_loop"] == 9
    assert item["evidence"]["actual_loops"] == 20
    plan[0]["Plan"]["Actual Rows"] = 0
    finding(diagnose_query(sql, plan, []), "ROW_ESTIMATE_MISMATCH")
    plan[0]["Plan"]["Actual Loops"] = 0
    assert diagnose_query(sql, plan, []) == []
    plan[0]["Plan"]["Actual Rows"] = 11
    plan[0]["Plan"]["Actual Loops"] = 20
    assert diagnose_query(sql, plan, []) == []  # Fewer than 10x, despite 20 loops.
    plan[0]["Plan"]["Plan Rows"] = 1000
    finding(diagnose_query(sql, plan, []), "ROW_ESTIMATE_MISMATCH")


def test_row_mismatch_ignores_scan_stopped_early_by_limit():
    sql = validated("SELECT id FROM orders LIMIT 1", TableRef("public", "orders"))
    scan = {
        "Node Type": "Seq Scan",
        "Schema": "public",
        "Relation Name": "orders",
        "Plan Rows": 100_000,
        "Actual Rows": 1,
        "Actual Loops": 1,
    }
    limited = [
        {
            "Plan": {
                "Node Type": "Limit",
                "Plan Rows": 1,
                "Actual Rows": 1,
                "Actual Loops": 1,
                "Plans": [scan],
            }
        }
    ]
    assert not any(
        item["rule_id"] == "ROW_ESTIMATE_MISMATCH" for item in diagnose_query(sql, limited, [])
    )
    full = validated("SELECT id FROM orders", TableRef("public", "orders"))
    finding(diagnose_query(full, [{"Plan": scan}], []), "ROW_ESTIMATE_MISMATCH")


def test_row_mismatch_ignores_exists_and_semi_join_short_circuit():
    refs = (TableRef("public", "orders", "o"), TableRef("public", "customers", "c"))
    semi_sql = validated(
        "SELECT o.id FROM orders o WHERE o.customer_id IN (SELECT c.id FROM customers c)",
        *refs,
    )
    short_scan = {
        "Node Type": "Seq Scan",
        "Relation Name": "customers",
        "Schema": "public",
        "Plan Rows": 5000,
        "Actual Rows": 1,
        "Actual Loops": 1,
    }
    semi_plan = [
        {
            "Plan": {
                "Node Type": "Nested Loop",
                "Join Type": "Semi",
                "Plan Rows": 100,
                "Actual Rows": 100,
                "Actual Loops": 1,
                "Plans": [short_scan],
            }
        }
    ]
    assert not any(
        item["rule_id"] == "ROW_ESTIMATE_MISMATCH"
        for item in diagnose_query(semi_sql, semi_plan, [])
    )
    inner_plan = [{"Plan": {**semi_plan[0]["Plan"], "Join Type": "Inner"}}]
    finding(diagnose_query(semi_sql, inner_plan, []), "ROW_ESTIMATE_MISMATCH")
    init_plan = [
        {
            "Plan": {
                "Node Type": "Result",
                "Plan Rows": 1,
                "Actual Rows": 1,
                "Actual Loops": 1,
                "Plans": [{**short_scan, "Parent Relationship": "InitPlan"}],
            }
        }
    ]
    exists_sql = validated(
        "SELECT EXISTS(SELECT 1 FROM customers)", TableRef("public", "customers")
    )
    assert not any(
        item["rule_id"] == "ROW_ESTIMATE_MISMATCH"
        for item in diagnose_query(exists_sql, init_plan, [])
    )


def test_sort_spill_requires_measured_disk_sort():
    sql = validated("SELECT id FROM orders ORDER BY id", TableRef("public", "orders"))
    sort = {
        "Node Type": "Sort",
        "Sort Method": "external merge",
        "Sort Space Type": "Disk",
        "Sort Space Used": 128,
        "Actual Rows": 100,
        "Actual Loops": 1,
    }
    item = finding(diagnose_query(sql, [{"Plan": sort}], []), "SORT_SPILL")
    assert item["evidence"]["node_path"] == "$.Plan"
    assert item["evidence"]["sort_space_used_kb"] == 128
    assert (
        diagnose_query(
            sql, [{"Plan": {"Node Type": "Sort", "Plan Rows": 100, "Total Cost": 100000}}], []
        )
        == []
    )
    assert (
        diagnose_query(
            sql, [{"Plan": {**sort, "Sort Space Type": "Memory", "Sort Method": "quicksort"}}], []
        )
        == []
    )

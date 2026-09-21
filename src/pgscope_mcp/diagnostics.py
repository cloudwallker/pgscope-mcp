"""Evidence-based diagnoses of validated SQL and PostgreSQL JSON plans."""

from collections.abc import Iterator
from typing import Any

from sqlglot import exp

from pgscope_mcp.models import TableRef, ValidatedSQL


def _plan_nodes(plan: list[dict]) -> Iterator[tuple[str, dict]]:
    """Walk each JSON plan once, preserving the path to its source node."""

    def visit(node: dict, path: str) -> Iterator[tuple[str, dict]]:
        yield path, node
        for offset, child in enumerate(node.get("Plans") or []):
            if isinstance(child, dict):
                yield from visit(child, f"{path}.Plans[{offset}]")

    for offset, entry in enumerate(plan):
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("Plan"), dict):
            prefix = "$.Plan" if len(plan) == 1 else f"$[{offset}].Plan"
            yield from visit(entry["Plan"], prefix)
        elif "Node Type" in entry:
            yield from visit(entry, f"$[{offset}]")


def summarize_plan(plan: list[dict]) -> dict:
    """Return root metrics and per-node facts; costs and time are distinct units."""
    fields = {
        "Node Type": "node_type",
        "Relation Name": "relation",
        "Schema": "schema",
        "Startup Cost": "startup_cost",
        "Total Cost": "total_cost",
        "Plan Rows": "plan_rows",
        "Actual Rows": "actual_rows",
        "Actual Loops": "actual_loops",
        "Actual Startup Time": "actual_startup_time_ms",
        "Actual Total Time": "actual_total_time_ms",
        "Filter": "filter",
        "Index Cond": "index_cond",
        "Join Filter": "join_filter",
        "Sort Method": "sort_method",
        "Sort Space Type": "sort_space_type",
        "Sort Space Used": "sort_space_used_kb",
    }
    nodes = [
        {"path": path, **{target: node.get(source) for source, target in fields.items()}}
        for path, node in _plan_nodes(plan)
    ]
    root = plan[0] if plan and isinstance(plan[0], dict) else {}
    root_node = root.get("Plan", root)
    estimated_cost = root_node.get("Total Cost") if isinstance(root_node, dict) else None
    return {
        "nodes": nodes,
        "estimated_cost": estimated_cost,
        "planning_time_ms": root.get("Planning Time"),
        "execution_time_ms": root.get("Execution Time"),
        "analyzed": root.get("Execution Time") is not None
        or any(node["actual_rows"] is not None for node in nodes),
    }


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    evidence: dict,
    suggestion: str,
    candidate_index: str | None = None,
) -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
        "evidence": evidence,
        "suggestion": suggestion,
        "candidate_index": candidate_index,
    }


def _table_metadata(ref: TableRef, tables: list[dict]) -> dict | None:
    return next(
        (
            table
            for table in tables
            if table.get("schema") == ref.schema and table.get("name") == ref.name
        ),
        None,
    )


def _relation_ref(node: dict, validated: ValidatedSQL) -> TableRef | None:
    relation = node.get("Relation Name")
    schema = node.get("Schema")
    matches = [
        ref
        for ref in validated.tables
        if ref.name == relation and (schema is None or ref.schema == schema)
    ]
    return matches[0] if len(matches) == 1 else None


def _column_ref(column: exp.Column, validated: ValidatedSQL) -> TableRef | None:
    if column.table:
        matches = [
            ref
            for ref in validated.tables
            if column.table in {ref.alias, ref.name} and (not column.db or column.db == ref.schema)
        ]
    else:
        matches = list(validated.tables)
    return matches[0] if len(matches) == 1 else None


def _has_leading_index(meta: dict, column: str) -> bool:
    return any(
        index.get("columns")
        and index["columns"][0] == column
        and not index.get("partial")
        and not index.get("expression")
        and index.get("valid", True)
        and (index.get("method") or "btree").lower() == "btree"
        for index in meta.get("indexes") or []
    )


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _simple_filter_column(validated: ValidatedSQL) -> str | None:
    tree = validated.tree
    if not isinstance(tree, exp.Select) or len(validated.tables) != 1:
        return None
    if (
        tree.args.get("with_")
        or tree.args.get("joins")
        or any(select is not tree for select in tree.find_all(exp.Select))
    ):
        return None
    source = (tree.args.get("from_") or exp.From()).this
    ref = validated.tables[0]
    if (
        not isinstance(source, exp.Table)
        or source.name != ref.name
        or source.db
        and source.db != ref.schema
    ):
        return None
    alias = source.args.get("alias")
    if isinstance(alias, exp.TableAlias) and alias.args.get("columns"):
        return None
    where = tree.args.get("where")
    if where is None:
        return None
    predicate = where.this
    if not isinstance(predicate, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None
    left, right = predicate.this, predicate.expression
    for column, value in ((left, right), (right, left)):
        if isinstance(column, exp.Column) and isinstance(value, exp.Literal):
            if _column_ref(column, validated) == validated.tables[0]:
                return column.name
    return None


def _candidate_index(validated: ValidatedSQL, ref: TableRef, meta: dict) -> str | None:
    column = _simple_filter_column(validated)
    if (
        column is None
        or validated.tables[0] != ref
        or column not in {item.get("name") for item in meta.get("columns") or []}
        or _has_leading_index(meta, column)
    ):
        return None
    return f"CREATE INDEX ON {_quoted(ref.schema)}.{_quoted(ref.name)} ({_quoted(column)});"


def _indexed_columns(meta: dict) -> set[str]:
    return {
        index["columns"][0]
        for index in meta.get("indexes") or []
        if index.get("columns")
        and not index.get("partial")
        and not index.get("expression")
        and index.get("valid", True)
    }


def _wrapped_indexed_filter(validated: ValidatedSQL, tables: list[dict]) -> tuple[str, str] | None:
    for select in validated.tree.find_all(exp.Select):
        where = select.args.get("where")
        if where is None:
            continue
        for predicate in where.find_all((exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            for side in (predicate.this, predicate.expression):
                if isinstance(side, exp.Column):
                    continue
                if not isinstance(side, (exp.Func, exp.Cast)):
                    continue
                for column in side.find_all(exp.Column):
                    ref = _column_ref(column, validated)
                    meta = _table_metadata(ref, tables) if ref else None
                    if meta and column.name in _indexed_columns(meta):
                        return column.name, side.sql(dialect="postgres")
    return None


def _where_connects(where: exp.Where | None, prior: set[str], target: str) -> bool:
    """Recognize direct conjunctive equality between the new and earlier tables."""
    if where is None:
        return False

    def conjuncts(expression: exp.Expression) -> Iterator[exp.Expression]:
        if isinstance(expression, exp.And):
            yield from conjuncts(expression.this)
            yield from conjuncts(expression.expression)
        else:
            yield expression

    for predicate in conjuncts(where.this):
        if (
            isinstance(predicate, exp.EQ)
            and isinstance(predicate.this, exp.Column)
            and isinstance(predicate.expression, exp.Column)
        ):
            left, right = predicate.this.table, predicate.expression.table
            if left == target and right in prior or right == target and left in prior:
                return True
    return False


def _unconditional_join(join: exp.Join) -> bool:
    condition = join.args.get("on")
    return (
        (join.args.get("kind") or "").upper() == "CROSS"
        or condition is None
        and not join.args.get("using")
        and (join.args.get("method") or "").upper() != "NATURAL"
        or isinstance(condition, exp.Boolean)
        and condition.this is True
    )


def _row_counts_may_be_truncated(validated: ValidatedSQL, plan: list[dict]) -> set[str] | None:
    """Mark plan subtrees whose executor can stop before reading planned rows."""
    if validated.tree.find(exp.Exists):
        return None  # EXISTS may short-circuit a scan even without a Limit plan node.
    return {
        path
        for path, node in _plan_nodes(plan)
        if node.get("Node Type") == "Limit"
        or node.get("Join Type") in {"Semi", "Anti"}
        or node.get("Parent Relationship") in {"InitPlan", "SubPlan"}
    }


def diagnose_query(validated: ValidatedSQL, plan: list[dict], tables: list[dict]) -> list[dict]:
    """Report only patterns supported by SQL structure or supplied plan evidence."""
    findings: list[dict[str, Any]] = []
    ast_sql = validated.tree.sql(dialect="postgres")

    for select in validated.tree.find_all(exp.Select):
        if any(
            isinstance(item, exp.Star) or isinstance(item, exp.Column) and item.is_star
            for item in select.expressions
        ):
            findings.append(
                _finding(
                    "SELECT_STAR",
                    "info",
                    "SELECT * 会读取查询未必需要的列。",
                    {"ast_sql": select.sql(dialect="postgres")},
                    "明确列出实际使用的列。",
                )
            )
            break

    for select in validated.tree.find_all(exp.Select):
        source = (select.args.get("from_") or exp.From()).this
        prior = {source.alias_or_name} if isinstance(source, exp.Table) else set()
        for join in select.args.get("joins") or []:
            if not isinstance(join.this, exp.Table):
                continue
            target = join.this.alias_or_name
            if _unconditional_join(join) and not _where_connects(
                select.args.get("where"), prior, target
            ):
                findings.append(
                    _finding(
                        "CARTESIAN_JOIN",
                        "warning",
                        "连接没有匹配条件，可能产生笛卡尔积。",
                        {"ast_sql": join.sql(dialect="postgres")},
                        "确认是否确实需要所有行组合；否则添加连接条件。",
                    )
                )
                break
            prior.add(target)
        if any(item["rule_id"] == "CARTESIAN_JOIN" for item in findings):
            break

    wrapped = _wrapped_indexed_filter(validated, tables)
    if wrapped:
        column, expression = wrapped
        findings.append(
            _finding(
                "INDEXED_COLUMN_WRAPPED",
                "warning",
                "过滤条件在已有索引列上应用函数或类型转换。",
                {"ast_sql": ast_sql, "column": column, "expression": expression},
                "检查能否直接按原列类型过滤，或评估符合查询形式的表达式索引。",
            )
        )

    truncated_roots = _row_counts_may_be_truncated(validated, plan)
    for path, node in _plan_nodes(plan):
        if node.get("Node Type") == "Seq Scan" and node.get("Filter"):
            ref = _relation_ref(node, validated)
            meta = _table_metadata(ref, tables) if ref else None
            population = meta.get("estimated_rows") if meta else None
            output = node.get("Plan Rows")
            if (
                isinstance(population, (int, float))
                and population >= 10_000
                and isinstance(output, (int, float))
                and 0 <= output / population <= 0.1
            ):
                findings.append(
                    _finding(
                        "SELECTIVE_SEQ_SCAN",
                        "warning",
                        "大表上的顺序扫描预计只返回少量行。",
                        {
                            "node_path": path,
                            "relation": ref.name,
                            "schema": ref.schema,
                            "table_estimated_rows": population,
                            "plan_rows": output,
                            "estimated_fraction": output / population,
                        },
                        "核对统计信息与过滤条件，再评估适合的普通索引。",
                        _candidate_index(validated, ref, meta),
                    )
                )

        estimated, actual, loops = (
            node.get("Plan Rows"),
            node.get("Actual Rows"),
            node.get("Actual Loops"),
        )
        row_count_truncated = truncated_roots is None or any(
            path == root or path.startswith(root + ".Plans[") for root in truncated_roots
        )
        if (
            not row_count_truncated
            and isinstance(estimated, (int, float))
            and isinstance(actual, (int, float))
            and isinstance(loops, (int, float))
            and loops > 0
            and estimated >= 0
            and actual >= 0
            and (
                min(estimated, actual) == 0
                and max(estimated, actual) > 0
                or min(estimated, actual) > 0
                and max(estimated, actual) / min(estimated, actual) >= 10
            )
        ):
            findings.append(
                _finding(
                    "ROW_ESTIMATE_MISMATCH",
                    "warning",
                    "节点实测每循环行数与规划估算相差至少 10 倍。",
                    {
                        "node_path": path,
                        "plan_rows_per_loop": estimated,
                        "actual_rows_per_loop": actual,
                        "actual_loops": loops,
                    },
                    "检查统计信息、数据分布与过滤条件，必要时更新统计信息。",
                )
            )

        if (
            "Sort" in (node.get("Node Type") or "")
            and node.get("Actual Loops", 0) > 0
            and node.get("Sort Space Type") == "Disk"
            and node.get("Sort Method") is not None
        ):
            findings.append(
                _finding(
                    "SORT_SPILL",
                    "warning",
                    "排序实测使用了磁盘空间。",
                    {
                        "node_path": path,
                        "sort_method": node["Sort Method"],
                        "sort_space_used_kb": node.get("Sort Space Used"),
                        "actual_loops": node["Actual Loops"],
                    },
                    "检查排序列、返回行数及适用索引，并评估会话排序内存配置。",
                )
            )

    return findings

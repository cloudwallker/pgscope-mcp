"""A deliberately small PostgreSQL SELECT subset for trusted business databases.

This is one layer alongside a read-only role/transaction, statement timeouts,
ordinary-table checks and ``search_path = pg_catalog`` in the execution layer.
It is not a sandbox against hostile database owners, extensions or custom types.
"""

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import traverse_scope

from .models import PGScopeError, TableRef, ValidatedSQL

_MAX_SQL_BYTES = 64 * 1024
_QUERY_NODES = frozenset({"Select", "Union", "Intersect", "Except", "Subquery"})

# Match exact AST classes, never a broad superclass: new parser features must
# receive an explicit review before they become executable through this API.
_STRUCTURAL_NODES = frozenset(
    "Select Union Intersect Except Subquery With CTE "
    "From Table TableAlias Join Column Identifier Star Alias "
    "Where Group Having Qualify Order Ordered Limit Offset Distinct "
    "Literal Boolean Null Var Tuple Paren "
    "And Or Not EQ NEQ GT GTE LT LTE Is In Between Like ILike Escape "
    "Add Sub Mul Div Mod Neg DPipe "
    "Case If Exists Filter Window WindowSpec WithinGroup "
    "Interval DataType DataTypeParam AtTimeZone".split()
)

_FUNCTION_NODES = frozenset(
    "Count Sum Avg Min Max "
    "Abs Ceil Floor Round Sqrt Power "
    "Lower Upper Length Trim Substring Replace Concat ConcatWs "
    "Coalesce Nullif Greatest Least "
    "Date DateTrunc TimestampTrunc Extract CurrentDate CurrentTimestamp CurrentTime "
    "Cast "
    "RowNumber Rank DenseRank Lag Lead FirstValue LastValue NthValue "
    "Case If Exists".split()
)

_CAST_TYPES = frozenset(
    "BOOLEAN SMALLINT INT BIGINT DECIMAL FLOAT DOUBLE "
    "CHAR BPCHAR VARCHAR TEXT DATE TIME TIMETZ TIMESTAMP TIMESTAMPTZ "
    "INTERVAL UUID JSON JSONB".split()
)


def _reject(message: str = "仅允许受支持的只读 SELECT 查询。") -> None:
    raise PGScopeError("QUERY_REJECTED", message)


class _GuardPostgres(Postgres):
    class Parser(Postgres.Parser):
        def _warn_unsupported(self) -> None:
            # Both command fallback paths call this before creating Command.
            # Upstream logs the original SQL here even with error_level=RAISE.
            # Reject locally instead; never change shared logger configuration.
            _reject()


def _validate_tree(tree: exp.Expression) -> None:
    if type(tree).__name__ not in _QUERY_NODES:
        _reject()
    for node in tree.walk():
        kind = type(node).__name__
        if (
            isinstance(node, (exp.CTE, exp.Subquery))
            and type(node.this).__name__ not in _QUERY_NODES
        ):
            # Invalid CTE bodies can otherwise reach scope's SQL-bearing warning
            # and be misidentified as ordinary table references.
            _reject()
        if isinstance(node, exp.SetOperation) and (
            type(node.this).__name__ not in _QUERY_NODES
            or type(node.expression).__name__ not in _QUERY_NODES
        ):
            _reject()
        if isinstance(node, exp.Func):
            if kind not in _FUNCTION_NODES:
                _reject("查询包含未获允许的函数。")
        elif kind not in _STRUCTURAL_NODES:
            # This also excludes INTO, locks, DML in CTEs, qualified functions
            # (Dot), explicit OPERATOR(...), collation, and table functions.
            _reject()
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            _reject("不支持表函数或动态数据源。")
        if isinstance(node, exp.DataType):
            if node.this.value not in _CAST_TYPES or node.args.get("kind"):
                _reject("查询包含未获允许的类型转换。")


def _bind_tables(tree: exp.Expression, allowed_schemas: tuple[str, ...]) -> tuple[TableRef, ...]:
    references: list[TableRef] = []
    visited: set[int] = set()
    for scope in traverse_scope(tree):
        for node, source in scope.selected_sources.values():
            if isinstance(node, exp.Table):
                visited.add(id(node))
            if not isinstance(source, exp.Table):
                continue  # CTE/derived-table references resolve to a Scope.
            table = source
            schema = table.db or allowed_schemas[0]
            if (
                table.catalog
                or schema not in allowed_schemas
                or schema.lower().startswith("pg_")
                or schema.lower() == "information_schema"
            ):
                raise PGScopeError("SCHEMA_DENIED", "查询引用了未获授权的 schema。")
            # Quoting preserves case of configuration values and avoids any
            # search_path-dependent relation lookup in the executed statement.
            table.set("db", exp.to_identifier(schema, quoted=True))
            reference = TableRef(schema=schema, name=table.name, alias=table.alias or None)
            if reference not in references:
                references.append(reference)
    if any(id(table) not in visited for table in tree.find_all(exp.Table)):
        _reject("无法确定查询的数据源作用域。")
    return tuple(references)


def validate_sql(sql: str, allowed_schemas: tuple[str, ...]) -> ValidatedSQL:
    """Validate one SELECT and return the rewritten SQL that must be executed.

    Unquoted identifiers follow PostgreSQL lowercase rules. ``allowed_schemas``
    contains exact database identifiers, not SQL snippets or search-path entries.
    """
    if not allowed_schemas or any(
        not isinstance(schema, str)
        or not schema
        or "\x00" in schema
        or len(schema.encode("utf-8")) > 63
        for schema in allowed_schemas
    ):
        raise PGScopeError("SCHEMA_DENIED", "未配置有效的授权 schema。")
    if not isinstance(sql, str) or not sql.strip() or "\x00" in sql:
        raise PGScopeError("INVALID_SQL", "SQL 为空或格式不正确。")
    try:
        if len(sql.encode("utf-8")) > _MAX_SQL_BYTES:
            raise PGScopeError("INVALID_SQL", "SQL 超过 64 KiB 输入上限。")
        statements = [
            statement
            for statement in sqlglot.parse(sql, read=_GuardPostgres, error_level=ErrorLevel.RAISE)
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
        if not statements:
            raise PGScopeError("INVALID_SQL", "SQL 为空或格式不正确。")
        if len(statements) != 1:
            _reject("一次仅允许一条 SELECT 查询。")
        tree = normalize_identifiers(statements[0], dialect="postgres")
        _validate_tree(tree)
        tables = _bind_tables(tree, allowed_schemas)
        executable = tree.sql(
            dialect="postgres", comments=False, unsupported_level=ErrorLevel.RAISE
        )
        return ValidatedSQL(sql=executable, tree=tree, tables=tables)
    except (SqlglotError, ValueError, RecursionError, UnicodeError):
        # Parser messages may contain identifiers, literal values and SQL text.
        raise PGScopeError("INVALID_SQL", "SQL 无法解析为受支持的 PostgreSQL 查询。") from None

"""Bounded, read-only PostgreSQL access. No administrative connection is accepted here."""

import asyncio
from contextlib import asynccontextmanager, suppress
from time import perf_counter

import anyio
import psycopg
from psycopg.rows import dict_row

from pgscope_mcp.config import Settings
from pgscope_mcp.diagnostics import diagnose_query, summarize_plan
from pgscope_mcp.models import PGScopeError, ValidatedSQL
from pgscope_mcp.results import MAX_RESULT_BYTES, bounded_result, json_size, to_json_value
from pgscope_mcp.validation import validate_sql


def database_error(error: psycopg.Error) -> PGScopeError:
    state = error.sqlstate or ""
    if state in {"57014", "55P03"}:
        return PGScopeError(
            "QUERY_TIMEOUT", "查询执行或锁等待超时，请缩小查询范围。", retryable=True
        )
    if state in {"42501", "25006"}:
        return PGScopeError("PERMISSION_DENIED", "数据库拒绝此操作，请使用业务表只读账号。")
    if state.startswith("42") or state.startswith("22"):
        return PGScopeError("INVALID_SQL", "数据库无法执行此查询，请检查字段、类型和 SQL 语法。")
    if isinstance(error, psycopg.OperationalError) or state.startswith("08"):
        return PGScopeError(
            "CONNECTION_FAILED", "无法连接 PostgreSQL，请检查连接配置及服务状态。", retryable=True
        )
    return PGScopeError("DATABASE_ERROR", "数据库操作失败，事务已回滚。")


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._slots = asyncio.Semaphore(4)

    @asynccontextmanager
    async def connection(self):
        """One connection/transaction per operation; cancellation closes both."""
        async with self._slots:
            connection = None
            try:
                connection = await psycopg.AsyncConnection.connect(
                    self.settings.dsn.get_secret_value(),
                    connect_timeout=self.settings.connect_timeout_seconds,
                    application_name="pgscope_mcp",
                    options="-c default_transaction_read_only=on -c search_path=pg_catalog",
                )
                await connection.execute("SET TRANSACTION READ ONLY")
                await connection.execute(
                    "SELECT set_config('statement_timeout', %s, true), "
                    "set_config('lock_timeout', %s, true), "
                    "set_config('search_path', 'pg_catalog', true)",
                    (str(self.settings.statement_timeout_ms), str(self.settings.lock_timeout_ms)),
                )
                cursor = await connection.execute(
                    "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                    "FROM pg_roles WHERE rolname = current_user"
                )
                flags = await cursor.fetchone()
                if not flags or any(flags):
                    raise PGScopeError("UNSAFE_ROLE", "请使用无管理权限的独立只读账号。")
                yield connection
            except psycopg.Error as error:
                raise database_error(error) from None
            finally:
                if connection is not None:
                    # MCP uses level cancellation: every unshielded await may be
                    # cancelled again. Always close, even if rollback times out.
                    with anyio.CancelScope(shield=True):
                        try:
                            with anyio.move_on_after(2):
                                with suppress(psycopg.Error):
                                    await connection.rollback()
                        finally:
                            await connection.close()

    def _schema(self, schema: str) -> None:
        if schema not in self.settings.schemas:
            raise PGScopeError("SCHEMA_DENIED", "此 schema 不在服务端允许列表中。")

    @staticmethod
    def _limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise PGScopeError("INVALID_ARGUMENT", "返回行数必须在 1 到 1000 之间。")

    async def _rows(self, connection, sql: str, params=()) -> list[dict]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(sql, params)
            return await cursor.fetchall()

    async def list_tables(self, schema: str = "public", limit: int = 100, offset: int = 0):
        self._schema(schema)
        self._limit(limit)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1000000:
            raise PGScopeError("INVALID_ARGUMENT", "offset 必须在 0 到 1000000 之间。")
        async with self.connection() as connection:
            rows = await self._rows(
                connection,
                """
                SELECT n.nspname AS schema, c.relname AS name,
                       CASE WHEN c.reltuples < 0 THEN NULL
                            ELSE c.reltuples::bigint END AS estimated_rows,
                       pg_total_relation_size(c.oid) AS size_bytes,
                       obj_description(c.oid, 'pg_class') AS comment
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
                  AND has_table_privilege(c.oid, 'SELECT')
                ORDER BY c.relname LIMIT %s OFFSET %s
            """,
                (schema, limit + 1, offset),
            )
        return {
            "tables": rows[:limit],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    async def _describe(self, connection, schema: str, table: str) -> dict:
        self._schema(schema)
        rows = await self._rows(
            connection,
            """
            SELECT c.oid, n.nspname AS schema, c.relname AS name,
                   CASE WHEN c.reltuples < 0 THEN NULL
                        ELSE c.reltuples::bigint END AS estimated_rows,
                   pg_total_relation_size(c.oid) AS size_bytes,
                   obj_description(c.oid, 'pg_class') AS comment
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
              AND has_table_privilege(c.oid, 'SELECT')
        """,
            (schema, table),
        )
        if not rows:
            raise PGScopeError("TABLE_NOT_FOUND", "未找到可访问的业务表；仅支持普通表和分区表。")
        result = rows[0]
        oid = result.pop("oid")
        result["columns"] = await self._rows(
            connection,
            """
            SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                   NOT a.attnotnull AS nullable,
                   pg_get_expr(d.adbin, d.adrelid) AS default,
                   col_description(a.attrelid, a.attnum) AS comment
            FROM pg_attribute a
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """,
            (oid,),
        )
        primary = await self._rows(
            connection,
            """
            SELECT a.attname AS name FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, position)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indrelid = %s AND i.indisprimary AND k.position <= i.indnkeyatts
            ORDER BY k.position
        """,
            (oid,),
        )
        result["primary_key"] = [row["name"] for row in primary]
        result["foreign_keys"] = await self._rows(
            connection,
            """
            SELECT conname AS name, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE conrelid = %s AND contype = 'f' ORDER BY conname
        """,
            (oid,),
        )
        result["indexes"] = await self._rows(
            connection,
            """
            SELECT ci.relname AS name, pg_get_indexdef(i.indexrelid) AS definition,
                   ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(num, pos)
                         LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.num
                         WHERE k.pos <= i.indnkeyatts ORDER BY k.pos) AS columns,
                   i.indisunique AS unique, am.amname AS method,
                   i.indpred IS NOT NULL AS partial, i.indexprs IS NOT NULL AS expression,
                   i.indisvalid AS valid
            FROM pg_index i JOIN pg_class ci ON ci.oid = i.indexrelid
            JOIN pg_am am ON am.oid = ci.relam WHERE i.indrelid = %s ORDER BY ci.relname
        """,
            (oid,),
        )
        return result

    async def describe_table(self, schema: str, table: str):
        async with self.connection() as connection:
            return await self._describe(connection, schema, table)

    async def _check_tables(self, connection, validated: ValidatedSQL) -> list[dict]:
        metadata = []
        seen = set()
        for ref in validated.tables:
            key = (ref.schema, ref.name)
            if key not in seen:
                metadata.append(await self._describe(connection, *key))
                seen.add(key)
        return metadata

    async def query(self, sql: str, max_rows: int = 100):
        self._limit(max_rows)
        validated = validate_sql(sql, self.settings.schemas)
        start = perf_counter()
        async with self.connection() as connection:
            await self._check_tables(connection, validated)
            async with connection.cursor(name="pgscope_result") as cursor:
                cursor.itersize = 16
                await cursor.execute(validated.sql)
                columns = [
                    {"name": col.name, "type_oid": col.type_code}
                    for col in cursor.description or ()
                ]
                rows = []
                size = 0
                async for row in cursor:
                    rows.append(row)
                    size += json_size(to_json_value(row))
                    if len(rows) > max_rows or size > MAX_RESULT_BYTES:
                        break
                result = bounded_result(columns, rows, max_rows, (perf_counter() - start) * 1000)
        return result

    def _analyze(self, analyze: bool):
        if analyze and not self.settings.allow_analyze:
            raise PGScopeError("ANALYZE_DISABLED", "服务端未开启实测；请使用估算计划。")

    async def _plan(self, connection, validated: ValidatedSQL, analyze: bool) -> list[dict]:
        options = "FORMAT JSON, VERBOSE TRUE, SETTINGS TRUE"
        if analyze:
            options += ", ANALYZE TRUE, BUFFERS TRUE"
        cursor = await connection.execute(f"EXPLAIN ({options}) " + validated.sql)
        row = await cursor.fetchone()
        return row[0]

    async def explain(self, sql: str, analyze: bool = False):
        self._analyze(analyze)
        validated = validate_sql(sql, self.settings.schemas)
        async with self.connection() as connection:
            await self._check_tables(connection, validated)
            plan = await self._plan(connection, validated, analyze)
        return {"sql": validated.sql, "plan": plan, "summary": summarize_plan(plan)}

    async def diagnose(self, sql: str, analyze: bool = False):
        self._analyze(analyze)
        validated = validate_sql(sql, self.settings.schemas)
        async with self.connection() as connection:
            tables = await self._check_tables(connection, validated)
            plan = await self._plan(connection, validated, analyze)
        return {
            "sql": validated.sql,
            "plan": plan,
            "summary": summarize_plan(plan),
            "findings": diagnose_query(validated, plan, tables),
            "limitations": []
            if analyze
            else ["未实际执行：不能判断实际耗时、基数偏差或排序落盘；cost 是估算成本单位。"],
        }

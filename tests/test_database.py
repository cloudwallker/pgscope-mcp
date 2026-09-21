import asyncio
import os

import anyio
import pytest

from pgscope_mcp.config import Settings
from pgscope_mcp.database import Database
from pgscope_mcp.models import PGScopeError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("PGSCOPE_TEST_DSN"), reason="需要真实 PostgreSQL：设置 PGSCOPE_TEST_DSN"
    ),
]


@pytest.fixture
def database():
    return Database(Settings(dsn=os.environ["PGSCOPE_TEST_DSN"], allow_analyze=True))


async def test_real_metadata_filters_tables_without_select_permission(database):
    result = await database.list_tables("public", 100, 0)
    assert {table["name"] for table in result["tables"]} == {"customers", "orders", "order_items"}
    table = await database.describe_table("public", "orders")
    assert table["primary_key"] == ["id"]
    assert table["estimated_rows"] >= 90000
    assert any(column["name"] == "total" for column in table["columns"])
    assert table["foreign_keys"]
    assert any(index["name"] == "orders_pkey" for index in table["indexes"])


async def test_real_queries_serialize_and_cap_rows(database):
    result = await database.query(
        "SELECT id, id, total, created_at, NULL AS missing FROM orders ORDER BY id", 2
    )
    assert result["row_count"] == 2
    assert result["truncated"]
    assert result["rows"][0] == [1, 1, "1.01", "2024-01-01T00:00:00", None]
    assert [col["name"] for col in result["columns"]][:2] == ["id", "id"]
    assert not (await database.query("SELECT 1", 1))["truncated"]


async def test_guard_and_server_both_enforce_read_only(database):
    with pytest.raises(PGScopeError):
        await database.query("DELETE FROM orders", 100)
    with pytest.raises(PGScopeError) as error:
        async with database.connection() as connection:
            await connection.execute("DELETE FROM public.orders WHERE false")
    assert error.value.code == "PERMISSION_DENIED"
    assert (await database.query("SELECT COUNT(*) FROM orders", 100))["rows"] == [[100000]]


async def test_schema_and_metadata_identifiers_are_not_interpolated(database):
    with pytest.raises(PGScopeError) as error:
        await database.describe_table("public", "orders'; DROP TABLE orders; --")
    assert error.value.code == "TABLE_NOT_FOUND"
    with pytest.raises(PGScopeError) as error:
        await database.list_tables("pg_catalog", 100, 0)
    assert error.value.code == "SCHEMA_DENIED"


async def test_explain_defaults_to_estimated_and_analyze_is_server_gated(database):
    estimated = await database.explain("SELECT id FROM orders WHERE id = 42")
    assert not estimated["summary"]["analyzed"]
    actual = await database.explain("SELECT id FROM orders WHERE id = 42", True)
    assert actual["summary"]["analyzed"]
    assert actual["summary"]["execution_time_ms"] >= 0
    restricted = Database(Settings(dsn=os.environ["PGSCOPE_TEST_DSN"]))
    with pytest.raises(PGScopeError) as error:
        await restricted.explain("SELECT 1", True)
    assert error.value.code == "ANALYZE_DISABLED"


async def test_timeout_rolls_back_and_next_query_succeeds():
    database = Database(Settings(dsn=os.environ["PGSCOPE_TEST_DSN"], statement_timeout_ms=50))
    with pytest.raises(PGScopeError) as error:
        await database.query("SELECT COUNT(*) FROM orders a CROSS JOIN orders b", 100)
    assert error.value.code == "QUERY_TIMEOUT"
    assert (await database.query("SELECT 42", 100))["rows"] == [[42]]


async def test_cancelled_query_releases_connection(database):
    task = asyncio.create_task(
        database.query("SELECT COUNT(*) FROM orders a CROSS JOIN orders b CROSS JOIN orders c", 100)
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await database.query("SELECT 42", 100))["rows"] == [[42]]


async def test_anyio_level_cancellation_closes_connection(database):
    connection = None
    with anyio.CancelScope() as scope:
        async with database.connection() as connection:
            scope.cancel()
            await anyio.sleep_forever()
    assert scope.cancelled_caught
    assert connection is not None and connection.closed
    assert (await database.query("SELECT 42", 100))["rows"] == [[42]]


async def test_database_error_does_not_leak_query_or_credentials(database):
    with pytest.raises(PGScopeError) as error:
        await database.query("SELECT nonexistent_secret_column FROM orders", 100)
    assert error.value.code == "INVALID_SQL"
    assert "nonexistent_secret_column" not in str(error.value)
    assert "pgscope_demo_readonly" not in str(error.value)

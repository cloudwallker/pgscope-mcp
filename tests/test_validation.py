"""SQL checks exercise executable output and rejection at the public boundary."""

import logging

import pytest
from sqlglot import exp, parse_one

from pgscope_mcp.models import PGScopeError
from pgscope_mcp.validation import validate_sql


def test_unqualified_table_is_bound_to_first_allowed_schema():
    result = validate_sql("SELECT o.id FROM orders AS o", ("analytics", "public"))
    assert [(t.schema, t.name, t.alias) for t in result.tables] == [("analytics", "orders", "o")]
    executed_table = next(parse_one(result.sql, read="postgres").find_all(exp.Table))
    assert executed_table.db == "analytics"
    assert executed_table.name == "orders"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM orders",
        "SELECT date(created_at), sum(total), avg(total), min(id), max(id) FROM orders "
        "GROUP BY date(created_at)",
        "SELECT date_trunc('month', created_at), extract(year FROM created_at) FROM orders",
        "SELECT * FROM orders WHERE created_at >= current_date - interval '7 days'",
        "SELECT lower(status), coalesce(total, 0), round(total, 2) FROM orders",
        "SELECT row_number() OVER (PARTITION BY status ORDER BY id) FROM orders",
        "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id",
        "SELECT id FROM orders UNION ALL SELECT id FROM customers",
        "WITH recent AS (SELECT id FROM orders) SELECT * FROM recent",
        "SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM customers WHERE customers.id = 1)",
        "SELECT 'drop table orders; pg_sleep(2)' AS text FROM orders -- DELETE FROM orders;",
        "/* harmless ; */ SELECT * FROM orders; -- trailing comment",
        "SELECT CAST(total AS numeric(12, 2)), created_at::date FROM orders",
    ],
)
def test_accepts_common_read_only_analytics(sql):
    result = validate_sql(sql, ("public",))
    assert result.tables
    assert result.sql


def test_cte_shadowing_does_not_hide_schema_qualified_real_table():
    result = validate_sql(
        "WITH orders AS (SELECT id FROM customers) "
        "SELECT o.id FROM orders o JOIN public.orders p ON o.id = p.id",
        ("public",),
    )
    assert {(t.schema, t.name) for t in result.tables} == {
        ("public", "customers"),
        ("public", "orders"),
    }
    tables = list(result.tree.find_all(exp.Table))
    assert any(t.name == "orders" and not t.db for t in tables)


def test_nested_cte_does_not_hide_outer_real_table():
    result = validate_sql(
        "SELECT * FROM orders WHERE id IN "
        "(WITH orders AS (SELECT id FROM customers) SELECT id FROM orders)",
        ("public",),
    )
    assert {t.name for t in result.tables} == {"orders", "customers"}


def test_postgres_identifier_case_rules_preserve_quoted_names():
    result = validate_sql('SELECT * FROM "Sales"."Orders" AS "O"', ("Sales",))
    assert [(t.schema, t.name, t.alias) for t in result.tables] == [("Sales", "Orders", "O")]
    assert '"Sales"."Orders"' in result.sql
    folded = validate_sql("SELECT * FROM PUBLIC.Orders", ("public",))
    assert [(t.schema, t.name) for t in folded.tables] == [("public", "orders")]


def test_quoted_cte_is_distinct_from_unquoted_real_table():
    result = validate_sql(
        'WITH "Orders" AS (SELECT id FROM customers) SELECT * FROM orders', ("public",)
    )
    assert {t.name for t in result.tables} == {"customers", "orders"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1; DELETE FROM orders",
        "DELETE FROM orders",
        "UPDATE orders SET total = 0",
        "INSERT INTO orders(id) VALUES (1)",
        "CREATE TABLE attack (id integer)",
        "DROP TABLE orders",
        "COPY orders TO '/tmp/orders'",
        "EXPLAIN SELECT * FROM orders",
        "WITH wiped AS (DELETE FROM orders RETURNING *) SELECT * FROM wiped",
        "WITH changed AS (UPDATE orders SET total = 1 RETURNING *) SELECT * FROM changed",
        "WITH added AS (INSERT INTO orders(id) VALUES (1) RETURNING *) SELECT * FROM added",
        "SELECT * INTO copied FROM orders",
        "SELECT * INTO TEMP copied FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "SELECT * FROM orders FOR SHARE",
        "SELECT * FROM orders FOR NO KEY UPDATE",
        "SELECT * FROM orders FOR KEY SHARE",
        "SELECT * FROM (SELECT * FROM orders FOR UPDATE) nested",
        "SELECT pg_sleep(1)",
        "SELECT set_config('search_path', 'private', false)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT nextval('orders_id_seq')",
        "SELECT custom_function(id) FROM orders",
        "SELECT public.lower(status) FROM orders",
        "SELECT pg_catalog.pg_sleep(1)",
        'SELECT "pg_sleep"(1)',
        "SELECT * FROM pg_sleep(1)",
        "SELECT * FROM generate_series(1, 1000000)",
        "SELECT id FROM orders UNION SELECT pg_sleep(1)",
        "SELECT CAST('orders' AS regclass)",
        "SELECT CAST('payload' AS public.custom_type)",
        "SELECT 'payload'::custom_type",
        "SELECT 1 OPERATOR(public.+) 2",
        "SELECT status COLLATE public.custom_collation FROM orders",
    ],
)
def test_rejects_side_effects_and_unapproved_execution_paths(sql):
    with pytest.raises(PGScopeError) as error:
        validate_sql(sql, ("public",))
    assert error.value.code in {"INVALID_SQL", "QUERY_REJECTED"}
    assert not error.value.retryable


@pytest.mark.parametrize(
    "sql,schemas",
    [
        ("SELECT * FROM private.orders", ("public",)),
        ('SELECT * FROM "PUBLIC".orders', ("public",)),
        ("SELECT * FROM pg_catalog.pg_class", ("public", "pg_catalog")),
        ("SELECT * FROM information_schema.tables", ("information_schema",)),
        ("SELECT * FROM pg_temp.orders", ("pg_temp",)),
        ("SELECT * FROM pg_toast.pg_toast_1", ("pg_toast",)),
        ("SELECT * FROM another_db.public.orders", ("public",)),
        ("WITH orders AS (SELECT * FROM private.orders) SELECT * FROM orders", ("public",)),
        ("SELECT 1", ()),
    ],
)
def test_denies_schemas_even_when_hidden_inside_cte(sql, schemas):
    with pytest.raises(PGScopeError) as error:
        validate_sql(sql, schemas)
    assert error.value.code == "SCHEMA_DENIED"


@pytest.mark.parametrize("sql", ["", "   ", "-- comment only", "SELECT '", "SELECT (1", "\x00"])
def test_invalid_sql_errors_are_sanitized(sql):
    with pytest.raises(PGScopeError) as error:
        validate_sql(sql, ("public",))
    assert error.value.code == "INVALID_SQL"
    assert not error.value.retryable
    assert "sqlglot" not in error.value.message.lower()


def test_parse_error_does_not_echo_private_sql():
    with pytest.raises(PGScopeError) as error:
        validate_sql("SELECT 'SECRET_7391' FROM (", ("public",))
    assert "SECRET_7391" not in error.value.message


def test_input_size_is_bounded_in_utf8_bytes():
    with pytest.raises(PGScopeError) as error:
        validate_sql("SELECT '" + "中" * 22000 + "'", ("public",))
    assert error.value.code == "INVALID_SQL"


@pytest.mark.parametrize(
    "sql",
    [
        "CALL private_procedure('secret_literal_7391')",
        "VACUUM secret_literal_7391",
        "REINDEX TABLE secret_literal_7391",
        "ALTER SYSTEM SET application_name = 'secret_literal_7391'",
        "SELECT 1; CALL private_procedure('secret_literal_7391')",
        "SELECT 'secret_literal_7391' FROM (",
        "WITH x AS ('secret_literal_7391') SELECT * FROM x",
        "WITH x AS (TABLE secret_literal_7391) SELECT * FROM x",
        "SELECT * FROM ('secret_literal_7391') x",
        "SELECT 1 UNION 'secret_literal_7391'",
        "SELECT 1 INTERSECT 'secret_literal_7391'",
        "SELECT 1 EXCEPT 'secret_literal_7391'",
        "('secret_literal_7391') UNION SELECT 1",
        "('secret_literal_7391') INTERSECT SELECT 1",
        "('secret_literal_7391') EXCEPT SELECT 1",
    ],
)
def test_rejected_sql_never_leaks_through_parser_logs(sql, caplog):
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PGScopeError) as error:
            validate_sql(sql, ("public",))
        # The guard must not silence shared logging to conceal parser leaks.
        logging.getLogger("sqlglot").warning("unrelated-parser-warning")
        logging.getLogger("another_library").warning("unrelated-library-warning")
    assert "secret_literal_7391" not in error.value.message
    assert "secret_literal_7391" not in caplog.text
    assert "unrelated-parser-warning" in caplog.text
    assert "unrelated-library-warning" in caplog.text

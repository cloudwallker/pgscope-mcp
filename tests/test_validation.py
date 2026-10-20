import pytest

from pgscope_mcp.models import PGScopeError, TableRef
from pgscope_mcp.validation import validate_sql


@pytest.mark.parametrize(
    "predicate",
    [
        "id = 1 AND id = 2",
        "id = 1 OR id = 2",
        "(id = 1 OR id = 2) AND id > 0",
    ],
)
def test_boolean_filters_preserve_predicates_and_bind_tables(predicate):
    validated = validate_sql(f"SELECT id FROM orders WHERE {predicate}", ("public",))

    assert validated.sql == f'SELECT id FROM "public".orders WHERE {predicate}'
    assert validated.tables == (TableRef(schema="public", name="orders", alias=None),)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(1)",
        "SELECT pg_read_file('/example')",
        "SELECT set_config('search_path', 'public', false)",
        "SELECT unknown_function(id) FROM orders",
        "SELECT pg_catalog.lower('example')",
    ],
)
def test_unapproved_functions_remain_rejected(sql):
    with pytest.raises(PGScopeError) as error:
        validate_sql(sql, ("public",))

    assert error.value.code == "QUERY_REJECTED"

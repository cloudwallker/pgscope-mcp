import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pgscope_mcp.config import Settings
from pgscope_mcp.models import PGScopeError
from pgscope_mcp.results import bounded_result, to_json_value


def test_config_denies_empty_or_system_schemas():
    for schemas in ("", "public,pg_catalog", "information_schema", "pg_temp", "public,,demo"):
        with pytest.raises(ValueError):
            Settings(dsn="postgresql://localhost/demo", allowed_schemas=schemas)


def test_config_does_not_expose_credentials_in_repr():
    config = Settings(dsn="postgresql://reader:secret@localhost/demo")
    assert "secret" not in repr(config)
    assert config.schemas == ("public",)
    assert not config.allow_analyze


def test_config_rejects_unbounded_timeouts():
    with pytest.raises(ValueError):
        Settings(dsn="postgresql://localhost/demo", statement_timeout_ms=0)


def test_json_preserves_precision_and_timezone():
    assert to_json_value(Decimal("9007199254740993.123456")) == "9007199254740993.123456"
    assert to_json_value(date(2025, 1, 2)) == "2025-01-02"
    assert to_json_value(datetime(2025, 1, 2, tzinfo=UTC)).endswith("+00:00")
    assert to_json_value(None) is None
    assert to_json_value({"items": [Decimal("1.2")]}) == {"items": ["1.2"]}


def test_row_limit_preserves_duplicate_column_names():
    result = bounded_result([{"name": "id"}, {"name": "id"}], [(1, 2), (3, 4)], 1, 2.0)
    assert result["rows"] == [[1, 2]]
    assert [col["name"] for col in result["columns"]] == ["id", "id"]
    assert result["truncated"] is True
    assert result["row_count"] == 1


def test_byte_limit_is_measured_on_utf8_json_not_characters():
    result = bounded_result([{"name": "text"}], [("中文" * 200,), ("ok",)], 100, 0, 256)
    assert result["truncated"] is True
    assert result["rows"] == []
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 256


def test_oversized_column_metadata_reports_error():
    with pytest.raises(PGScopeError) as caught:
        bounded_result([{"name": "x" * 2000}], [], 100, 0, 256)
    assert caught.value.code == "RESULT_TOO_LARGE"

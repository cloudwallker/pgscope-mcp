"""JSON serialization and query response bounds."""

import base64
import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from pgscope_mcp.models import PGScopeError

MAX_RESULT_BYTES = 1024 * 1024


def to_json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (Decimal, UUID, timedelta)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    return str(value)


def json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def bounded_result(
    columns: list[dict],
    rows,
    max_rows: int,
    elapsed_ms: float,
    max_bytes: int = MAX_RESULT_BYTES,
) -> dict:
    result = {
        "columns": columns,
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "elapsed_ms": round(elapsed_ms, 3),
    }
    if json_size(result) > max_bytes:
        raise PGScopeError("RESULT_TOO_LARGE", "列定义超过响应大小限制，请减少所选列。")
    used = json_size(result)
    for row in rows:
        if result["row_count"] >= max_rows:
            result["truncated"] = True
            break
        serialized = to_json_value(row)
        added = json_size(serialized) + (2 if result["rows"] else 0)
        # Reserve bytes for the largest row_count representation.
        if used + added + len(str(max_rows)) > max_bytes:
            result["truncated"] = True
            break
        result["rows"].append(serialized)
        result["row_count"] += 1
        used += added
    return result

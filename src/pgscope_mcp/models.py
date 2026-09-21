"""Small shared contracts between the SQL guard, database and diagnostic engine."""

from dataclasses import dataclass

from sqlglot import exp


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str
    alias: str | None = None


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    tree: exp.Expression
    tables: tuple[TableRef, ...]


class PGScopeError(Exception):
    """A sanitized error that can cross the MCP boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}

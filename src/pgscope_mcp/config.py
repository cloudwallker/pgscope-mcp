"""Server-owned configuration; tools cannot widen these limits."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PGSCOPE_", extra="ignore")

    dsn: SecretStr
    allowed_schemas: str = "public"
    allow_analyze: bool = False
    statement_timeout_ms: int = Field(default=5000, ge=1, le=60000)
    lock_timeout_ms: int = Field(default=1000, ge=1, le=10000)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=30)

    @field_validator("allowed_schemas")
    @classmethod
    def validate_schemas(cls, value: str) -> str:
        schemas = tuple(part.strip() for part in value.split(","))
        if any(not s or s.startswith("pg_") or s == "information_schema" for s in schemas):
            raise ValueError("allowed_schemas 必须是非空业务 schema 列表，不允许系统 schema")
        if len(set(schemas)) != len(schemas):
            raise ValueError("allowed_schemas 不允许重复")
        return ",".join(schemas)

    @field_validator("dsn")
    @classmethod
    def validate_dsn(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("PGSCOPE_DSN 不能为空")
        return value

    @property
    def schemas(self) -> tuple[str, ...]:
        return tuple(self.allowed_schemas.split(","))

# PGScope MCP

**中文简介：** 面向 Agent 的 PostgreSQL 只读 MCP 服务，可查询表结构、数据和执行计划并提供诊断依据。

**English overview:** A read-only PostgreSQL MCP server for agents to inspect schemas, query data, and examine execution plans with supporting evidence.

## 使用 / Usage

Python 3.12+；服务通过 stdio 运行。`compose.yaml` 和 `demo/init.sql` 提供本地演示数据库。连接信息通过环境变量配置。

Python 3.12+; the service runs over stdio. `compose.yaml` and `demo/init.sql` provide a local demo database. Configure the connection through environment variables.

```text
python -m pip install -e .
pgscope-mcp
```

## 许可 / License

见 [LICENSE](LICENSE)。 / See [LICENSE](LICENSE).

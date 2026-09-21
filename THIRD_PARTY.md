# 开源依赖与设计参考

| 项目 | 许可证 | 在 PGScope 中的作用 |
| --- | --- | --- |
| [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | 直接依赖，工具注册、协议、stdio 与测试客户端 |
| [SQLGlot](https://github.com/tobymao/sqlglot) | MIT | 直接依赖，PostgreSQL SQL 解析、scope 与 AST 变换 |
| [Psycopg 3](https://github.com/psycopg/psycopg) | LGPL-3.0 | 直接依赖，PostgreSQL 驱动；按原包动态使用 |
| [Pydantic](https://github.com/pydantic/pydantic) / [pydantic-settings](https://github.com/pydantic/pydantic-settings) | MIT | 类型与环境配置校验 |
| [DBHub](https://github.com/bytebase/dbhub) | MIT | 数据库 MCP 工具设计参考，未复制其源码 |

SQL 校验策略、元数据访问、六类诊断规则、优化实验和报告为本项目实现。没有 fork 大型平台，也没有把 SQLGlot 的 AST 优化器当成数据库查询优化器。依赖版本记录在 `uv.lock`，各依赖仍受其自己的许可证约束。

规划时 GitHub 页面显示 MCP Python SDK 约 24.4k、SQLGlot 约 9.6k、DBHub 约 3.5k stars；这些是调研时页面约数，不代表实时统计。

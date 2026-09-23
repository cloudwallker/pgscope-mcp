# 开发与接口说明

本文面向修改 PGScope MCP 的维护者。部署、启动和工具用法见 [README](../README.md)，权限与诊断边界见 [架构说明](architecture.md)。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `src/pgscope_mcp/server.py` | 注册五个 MCP 工具，校验参数，封装结构化结果与脱敏错误 |
| `src/pgscope_mcp/config.py` | 从环境变量读取服务端配置，限制 schema、超时与实测权限 |
| `src/pgscope_mcp/validation.py` | 解析 PostgreSQL SQL，递归检查 AST，将真实表绑定到授权 schema |
| `src/pgscope_mcp/database.py` | 获取表元数据，管理只读事务，执行查询与 JSON 执行计划 |
| `src/pgscope_mcp/diagnostics.py` | 汇总计划节点，结合 AST、元数据与计划生成六类诊断 |
| `src/pgscope_mcp/results.py` | JSON 序列化与查询响应大小控制 |
| `src/pgscope_mcp/models.py` | 定义 SQL 校验结果、表引用和可公开错误 |
| `scripts/benchmark.py` | 在专用演示库执行两组优化实验，保存计划与测量数据 |
| `scripts/smoke_mcp.py` | 通过 stdio 初始化服务并调用五个工具 |

## 查询调用链

1. MCP 层校验输入类型、长度与范围，将调用交给 `Database`。
2. `validate_sql()` 检查单条只读查询，输出可执行 SQL、AST 和真实表引用。未限定 schema 的真实表绑定到 `allowed_schemas[0]`；CTE 名称不作为真实表。
3. 数据库层建立独立连接和只读事务，设置固定 `search_path` 与超时，检查角色和目标表权限。
4. `query` 通过服务端游标读取结果；`explain` 与 `diagnose` 获取 PostgreSQL JSON 计划。`diagnose` 额外复用本次读取的表元数据。
5. 结果转换为 JSON 兼容值，并在 MCP 层返回简短摘要与 `structuredContent`。调用结束、异常或取消时，连接上下文负责回滚和关闭。

`list_tables` 与 `describe_table` 使用固定的系统目录查询和绑定参数，不接受用户提供的 SQL。

## 共享接口

### SQL 与错误模型

```python
TableRef(schema: str, name: str, alias: str | None = None)
ValidatedSQL(sql: str, tree: sqlglot.exp.Expression, tables: tuple[TableRef, ...])
PGScopeError(code: str, message: str, *, retryable: bool = False)

validate_sql(sql: str, allowed_schemas: tuple[str, ...]) -> ValidatedSQL
```

`ValidatedSQL.sql` 是已完成 schema 绑定的 SQL；执行层必须使用该字段。`PGScopeError` 的消息会跨越 MCP 边界，应使用固定、可公开的提示，不附带原始数据库异常、连接串、SQL 或数据值。

### 表元数据

`describe_table` 返回以下字段，诊断规则复用相同结构：

| 字段 | 内容 |
| --- | --- |
| `schema`、`name`、`comment` | 表标识与注释 |
| `estimated_rows`、`size_bytes` | 估算行数与表大小 |
| `columns` | 字段的 `name`、`type`、`nullable`、`default`、`comment` |
| `primary_key` | 按键顺序排列的列名 |
| `foreign_keys` | 外键的 `name` 与 `definition` |
| `indexes` | 索引的 `name`、`definition`、`columns`、`unique`、`method`、`partial`、`expression`、`valid` |

索引列顺序、有效性、部分索引和表达式索引标记都会影响建议，不能仅按列名判断索引是否适用。

### 查询结果

查询响应包含 `columns`、`rows`、`row_count`、`truncated`、`elapsed_ms`。`columns` 中的元素为 `name` 和 PostgreSQL `type_oid`，每一行以数组返回，因此重复列名不会覆盖数据。日期与时间使用 ISO 格式，`Decimal` 使用字符串保留精度，二进制值包装为含 `base64` 字段的对象。

默认返回 100 行，最大 1,000 行；JSON 数据体上限为 1 MiB。达到行数或字节限制时设置 `truncated=true`；列定义本身过大时返回 `RESULT_TOO_LARGE`。

### 执行计划与诊断

```python
summarize_plan(plan: list[dict]) -> dict
diagnose_query(
    validated: ValidatedSQL,
    plan: list[dict],
    tables: list[dict],
) -> list[dict]
```

计划摘要包含 `nodes`、`estimated_cost`、`planning_time_ms`、`execution_time_ms`、`analyzed`。各节点保存原始计划中的 `path`，并分别保留估算成本、估算行数和可用的实测指标。不存在的指标使用 `None`，不能补成零或据此推断实际性能。

每条诊断包含 `rule_id`、`severity`（`info` 或 `warning`）、`message`、`evidence`、`suggestion` 和 `candidate_index`。证据通过 `ast_sql` 或 `node_path` 关联到具体 SQL 片段或计划节点。候选索引为待验证的 SQL 文本或 `None`，MCP 服务不执行索引变更。

## 修改时必须保留的约束

- 工具参数不能扩大服务端允许的 schema、超时或 `ANALYZE` 权限。实测需要服务端 `PGSCOPE_ALLOW_ANALYZE=true`，且调用方显式传入 `analyze=true`。
- 扩展 SQL 语法和函数允许清单时，要检查嵌套查询、CTE 与集合查询；数据库账号的最小权限和只读事务仍是必要防线。
- 标准输出专用于 MCP 通信。日志写到标准错误，只记录安全的错误类型或固定消息，不打印凭据、原始 SQL 和查询数据。
- 诊断必须区分估算与实测。小表顺序扫描不直接判定为问题，缺少实测指标时不判断行数偏差或排序落盘。
- 仅在能解析到真实表字段且核对已有索引后生成候选索引；复杂作用域应保守给出检查建议。
- 修改连接生命周期时同时验证成功、异常、超时和客户端取消，避免取消状态阻断清理。

## 演示与验证

演示库包含 `public.customers`、`public.orders`、`public.order_items` 三张表。服务使用独立只读账号；实验脚本单独读取 `PGSCOPE_DEMO_ADMIN_DSN`，管理连接不进入 MCP 客户端配置。

演示命令见 [README](../README.md) 与 [演示环境](../demo/README.md)。优化实验保留原始计划与五次测量值，以中位数汇总；不同环境应重新测量，不把已有结果作为性能承诺。

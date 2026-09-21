# PGScope MCP

[![CI](https://github.com/cloudwallker/pgscope-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudwallker/pgscope-mcp/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)](compose.yaml)
[![MCP · stdio](https://img.shields.io/badge/MCP-stdio-222222)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**中文简介** · 面向 Agent 的 PostgreSQL 只读查询与诊断服务。通过本地 stdio MCP 查看表结构、查询数据、分析执行计划，以数据库证据支撑优化建议，并用可重复实验验证效果。

**English** · A read-only PostgreSQL MCP server for AI agents. Explore schemas, query data, inspect execution plans, and diagnose SQL with database evidence. Includes reproducible optimization benchmarks. Runs locally over stdio; no model API key required.

[快速启动](#快速启动) · [工具接口](#工具接口) · [优化实验](#复现优化实验) · [架构设计](docs/architecture.md) · [验收记录](docs/validation.md) · [参与开发](CONTRIBUTING.md)

```mermaid
flowchart LR
    Agent[Agent / MCP 客户端] <-->|本地 stdio| MCP[PGScope MCP]
    MCP --> Guard[SQL AST 检查]
    Guard <-->|只读事务| PG[(PostgreSQL 17)]
    PG --> Evidence[表结构 + 执行计划]
    Evidence --> Diagnosis[带证据的诊断与建议]
    Diagnosis --> MCP
```

服务内部不调用大模型，由接入的 Agent 组织工具调用。演示环境包含三张电商表、10 万条订单、两个优化实验和独立 MCP 测试客户端。

## 你可以演示什么

- 通过五个 MCP 工具完成「表结构 → 查询 → 执行计划 → 诊断」。
- 用 SQL AST 检查、只读事务、最小权限账号共同限制查询。
- 解释扫描、索引、基数估算和排序落盘，区分估算成本与实际耗时。
- 在 10 万订单上实测缺失索引和日期过滤改写，保存每次原始计划并核对查询结果一致。

依赖 [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 和 [SQLGlot](https://github.com/tobymao/sqlglot)，参考 [DBHub](https://github.com/bytebase/dbhub) 的数据库工具设计。原创边界与许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 实测一览

| 实验 | 优化前中位数 | 优化后中位数 | 验证方式 |
| --- | ---: | ---: | --- |
| 高选择性 customer_id 查询：新增索引 | 4.788 ms | 0.069 ms | 完整结果一致，保留原始执行计划 |
| 日期过滤：函数改写为半开范围 | 5.751 ms | 0.307 ms | 完整结果一致，保留原始执行计划 |

每种查询预热 1 次、测量 5 次。以上为 PostgreSQL 17.11 的一次本地观测，性能取决于硬件、缓存与数据分布。查看 [完整实验报告](artifacts/benchmark/benchmark.md) 或 [自行复现](#复现优化实验)。

## 快速启动

需要 [uv](https://docs.astral.sh/uv/) 和已启动的 Docker Desktop / Docker Engine。命令均在项目根目录执行。演示容器只绑定本机 `127.0.0.1:55432`，固定密码仅用于本地演示。

### Windows PowerShell

```powershell
# 把运行时和缓存放在项目目录；不改变系统 Python。
$env:UV_CACHE_DIR = "$PWD/.cache/uv"
$env:UV_PYTHON_INSTALL_DIR = "$PWD/.tools/python"
uv sync --frozen
docker compose up -d --wait

$env:PGSCOPE_DSN = 'postgresql://pgscope_reader:pgscope_demo_readonly@127.0.0.1:55432/pgscope_demo'
uv run --frozen python -m scripts.smoke_mcp --output-json artifacts/mcp-smoke.json
```

### Linux / macOS

```bash
uv sync --frozen
docker compose up -d --wait
export PGSCOPE_DSN='postgresql://pgscope_reader:pgscope_demo_readonly@127.0.0.1:55432/pgscope_demo'
uv run --frozen python -m scripts.smoke_mcp --output-json artifacts/mcp-smoke.json
```

看到五行 `PASS` 即表示独立客户端已通过 **真实 stdio 子进程** 调用了全部工具；不是函数内直接调用，也不是模拟数据库。该演示依赖内置 `orders` 表。

初始化得到 `customers`（5,000 行）、`orders`（100,000 行）、`order_items`（200,000 行）。初始化脚本仅在新数据卷首次启动时执行；普通 `docker compose down` 保留数据。

## 接入 Agent 或 Inspector

复制 [客户端配置示例](examples/mcp-client.json)，把 `/ABSOLUTE/PATH/TO/pgscope-mcp` 改为本项目绝对路径，并按客户端支持的位置导入。Windows JSON 中可使用 `F:/...` 形式的路径。客户端需要能找到 `uv`；也可以把 command 改为 `.venv` 中 Python 的绝对路径，args 改为 `["-m", "pgscope_mcp"]`。

配置只给出只读连接，**不要放入演示管理员连接**。示例采用常见的 `mcpServers` JSON 形态，具体导入位置依客户端而定。

使用 MCP Inspector 手动查看五个工具：

```bash
npx @modelcontextprotocol/inspector uv run --frozen pgscope-mcp
```

Inspector 需要 Node.js，首次使用会下载 Inspector。确保启动它的终端已设置 `PGSCOPE_DSN`。连接成功后可以用 [Agent 演示提示词](examples/agent-prompts.md) 展示真实工作流。

直接执行 `uv run --frozen pgscope-mcp` 会等待 MCP 输入，不会打开聊天界面。日志走 stderr，stdout 专用于协议通信。

## 工具接口

| 工具 | 参数 | 主要返回 |
| --- | --- | --- |
| `list_tables` | `schema="public"`, `limit=100`, `offset=0` | 可访问表、注释、估算行数、大小、分页标记 |
| `describe_table` | `table`, `schema="public"` | 字段与类型、主外键、索引及索引属性 |
| `query` | `sql`, `max_rows=100` | 列定义、行数组、耗时、截断标记 |
| `explain` | `sql`, `analyze=false` | 原始 JSON 计划、节点路径、估算或实测摘要 |
| `diagnose` | `sql`, `analyze=false` | 计划、六类诊断、证据、建议及可选索引 SQL |

输出使用 MCP `structuredContent`，同时附简短文字摘要。失败使用 `isError=true` 和 `error.code/message/retryable`；常见错误包括 `QUERY_REJECTED`、`SCHEMA_DENIED`、`TABLE_NOT_FOUND`、`QUERY_TIMEOUT`、`ANALYZE_DISABLED` 和 `CONNECTION_FAILED`。

结果保留重复列名，因此 rows 是数组而不是按列名覆盖的字典。日期为 ISO 字符串，Decimal 为精确字符串，二进制为 `{ "base64": "..." }`。`row_count` 是已返回行数；看到 `truncated=true` 时不能把结果当完整数据集。无 ORDER BY 的行顺序不保证稳定。

### 六类诊断

| 规则 | 依据 |
| --- | --- |
| `SELECT_STAR` | 投影包含 `*`；不会将 `COUNT(*)` 误报 |
| `CARTESIAN_JOIN` | SQL 中没有有效匹配条件的连接 |
| `SELECTIVE_SEQ_SCAN` | 大表、选择性过滤与实际规划的顺序扫描相结合 |
| `INDEXED_COLUMN_WRAPPED` | 过滤条件对已有索引列使用函数或转换 |
| `ROW_ESTIMATE_MISMATCH` | 实测每循环行数与估计显著偏离；避免提前停止带来的误报 |
| `SORT_SPILL` | 实测排序节点明确使用磁盘 |

规则是待验证的诊断线索。小表顺序扫描可能是合理选择；cost 不是毫秒；估算计划无法证明实际耗时。候选索引只针对可以确定真实字段的简单单表过滤，复杂查询提供检查建议。

## 配置与访问边界

服务从环境变量读取配置，默认不自动加载 `.env`。如需配置文件，复制 `.env.example` 为 `.env`，用 `uv run --env-file .env --frozen pgscope-mcp` 显式加载。

| 环境变量 | 默认值 / 含义 |
| --- | --- |
| `PGSCOPE_DSN` | 必填，只读数据库连接串 |
| `PGSCOPE_ALLOWED_SCHEMAS` | `public`；逗号分隔、区分大小写的业务 schema |
| `PGSCOPE_ALLOW_ANALYZE` | `false`；是否允许客户端显式请求实测 |
| `PGSCOPE_STATEMENT_TIMEOUT_MS` | `5000`，范围 1–60000 |
| `PGSCOPE_LOCK_TIMEOUT_MS` | `1000`，范围 1–10000 |
| `PGSCOPE_CONNECT_TIMEOUT_SECONDS` | `5`，范围 1–30 |

未限定 schema 的表解析为允许列表中的第一个 schema；工具元数据参数默认仍为 `public`，其他 schema 应显式传入。查询限定为受支持的单条 SELECT 子集：JOIN、只读 CTE、集合查询、聚合和常见日期/数值/文本函数；函数允许清单位于 `validation.py`。

多语句、DML/DDL、写入 CTE、行锁、`SELECT INTO`、系统 schema、任意自定义函数/类型/操作符均被拒绝。首版只支持普通表和分区表，不支持视图及外部表。每次操作使用只读事务、独立连接和固定 `search_path=pg_catalog`，并在完成、错误或取消后关闭；最多同时处理四个数据库操作。查询默认 100 行，最大 1,000 行，JSON 响应上限 1 MiB，SQL 输入上限 64 KiB。

连接真实库时，请由 DBA 提供仅有目标业务表 SELECT 和对应 schema USAGE 的账号；服务拒绝超级用户及常见管理角色，但这不代替数据库授权。账号与允许 schema 都应最小化。连接具有恶意扩展、自定义类型或恶意 DBA 的数据库不属于本项目的隔离保证。

查询结果、计划及元数据会传给 MCP 客户端，其后是否发送模型由客户端控制。数据库文本和注释都是数据，不能当作 Agent 指令。服务不持久化真实查询结果，也不打印原始 SQL、连接凭据或数据到日志。

## 复现优化实验

实验脚本仅接受 loopback 上、名称为 `pgscope_demo` 且具有专用 marker 的演示库。它使用独立管理员连接管理固定实验索引；MCP 不拥有写工具。

```powershell
$env:PGSCOPE_DEMO_ADMIN_DSN = 'postgresql://postgres:pgscope_demo_admin@127.0.0.1:55432/pgscope_demo'
uv run --frozen python -m scripts.benchmark
Remove-Item Env:PGSCOPE_DEMO_ADMIN_DSN
```

Linux/macOS：

```bash
PGSCOPE_DEMO_ADMIN_DSN='postgresql://postgres:pgscope_demo_admin@127.0.0.1:55432/pgscope_demo' \
  uv run --frozen python -m scripts.benchmark
```

两个实验分别是 customer_id 索引前后对比，以及已有日期索引时 `date(created_at)` 与半开区间过滤的对比。每组预热 1 次、实测 5 次，比较完整结果、耗时中位数和根节点缓冲区指标。脚本可重复执行，会重建固定实验场景的索引，不删除业务数据；实验结束后两个优化索引保留在演示库。

交付时已在本机真实运行并保存 [中文实验报告](artifacts/benchmark/benchmark.md) 和 [所有原始计划](artifacts/benchmark/benchmark.json)。这些是录制的本机测量结果，不能当作当前数据库状态或固定性能保证。

## 测试与工程结构

```powershell
$env:PGSCOPE_TEST_DSN = $env:PGSCOPE_DSN
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts
```

Linux/macOS 使用 `export PGSCOPE_TEST_DSN="$PGSCOPE_DSN"`。不设置测试连接时，真实数据库测试会明确跳过；不能据此声称真实集成验收通过。离线逻辑测试可用 `uv run --frozen pytest -q -m "not integration"`，但其中 stdio 错误链路仍会尝试连接本机未开放端口。

CI 包含 Linux PostgreSQL + 五工具 stdio + 基准实验，以及 Windows 无数据库测试。配置位于 [ci.yml](.github/workflows/ci.yml)，运行状态见 [GitHub Actions](https://github.com/cloudwallker/pgscope-mcp/actions)。

```text
src/pgscope_mcp/  配置、SQL 检查、只读执行、诊断、MCP 协议边界
tests/           单元测试、真实 PostgreSQL 测试、stdio 端到端测试
demo/            确定性数据初始化及权限
scripts/         独立 MCP 客户端与基准实验
artifacts/       可阅读的真实演示结果
docs/            架构、实现约定和面试讲解
```

进一步阅读：[架构与数据流](docs/architecture.md)、[实现约定](docs/implementation.md)、[面试讲解提纲](docs/interview.md)、[演示环境细节](demo/README.md)。

## 常见问题

- **Docker 无法连接**：先启动 Docker Desktop，并确认 Linux 容器引擎就绪；`docker context ls` 可检查当前上下文。
- **55432 被占用**：修改 Compose 主机端口，并同步修改 DSN。
- **Windows 异步驱动报 Proactor 错误**：使用项目提供的 `pgscope-mcp` 入口；它已选择兼容 Psycopg 的 Selector 事件循环。
- **无法访问 uv 缓存**：使用 Windows 快速启动段中的项目内缓存设置。
- **实测被拒绝**：重启服务时设置 `PGSCOPE_ALLOW_ANALYZE=true`，并在工具参数中显式传入 `analyze=true`。
- **停止演示**：退出 Agent/Inspector，再执行 `docker compose down`，保留数据卷供下次使用。

项目采用 [MIT 许可证](LICENSE)。

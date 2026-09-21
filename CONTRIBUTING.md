# 贡献指南

欢迎通过 Issue 描述可复现的问题，或提交范围明确的 Pull Request。项目使用 Python 3.12、uv 和 PostgreSQL 17；首次运行请先阅读 [README](README.md)。

## 开发环境

```bash
uv python install 3.12
uv sync --frozen
```

常规检查无需数据库：

```bash
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
uv run --frozen pytest -q -m "not integration"
```

代码格式由 Ruff 统一管理。需要格式化时运行 `uv run --frozen ruff format src tests scripts`。变更依赖后运行 `uv lock`，同时提交 `pyproject.toml` 与 `uv.lock`，并检查锁文件中的下载源。

## 数据库与 MCP 验证

```bash
docker compose up -d --wait
```

按 README 设置 `PGSCOPE_TEST_DSN` 和 `PGSCOPE_DSN`，两者均指向演示库的只读账号，然后运行：

```bash
uv run --frozen pytest -q
uv run --frozen python -m scripts.smoke_mcp
```

没有 `PGSCOPE_TEST_DSN` 时，数据库集成测试会跳过；这不等同于完整验收通过。修改 SQL 检查、连接清理或 MCP 接口时，应运行真实数据库和 stdio 测试。

优化实验使用独立的 `PGSCOPE_DEMO_ADMIN_DSN`，操作专用演示库：

```bash
uv run --frozen python -m scripts.benchmark --output-dir artifacts/local/benchmark
```

实验会创建和删除演示索引，请先核对目标库。提交实验结论时应保留原始计划、实际测量值和环境说明，不预设加速倍数。

## 修改原则

- 保持服务端配置、只读账号、SQL AST 校验和只读事务之间的权限边界；工具调用不能扩大授权。
- 新增诊断应携带 AST 或计划节点证据，并为证据不足的情况保留明确限制。成本与毫秒、估算行数与实测行数必须分开。
- 候选索引只作为建议返回。管理操作与实验脚本不进入 MCP 服务的执行路径。
- 修复缺陷时添加覆盖实际失败场景的回归测试；诊断规则同时覆盖命中和不应命中的样例。
- 不提交真实凭据、API Key、个人信息、生产 SQL、表结构、查询数据或未经检查的日志与执行计划。使用合成数据与占位符，在发布附件前同样检查内容。

模块分工和数据结构见 [开发与接口说明](docs/implementation.md)，部署与数据边界见 [架构说明](docs/architecture.md)。

## Pull Request

说明触发问题的具体场景、修改后的行为以及运行过的检查。若依赖真实数据库或需要新增环境变量，注明复现方式；未运行的检查请直接说明。文档、示例与公开接口发生变化时同步更新。

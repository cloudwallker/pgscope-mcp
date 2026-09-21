# 本地验收记录

记录时间：2026-09-21。环境为 Windows、Python 3.12.12，以及 Docker 中的 PostgreSQL 17.11。

依赖：MCP SDK 2.2.0、SQLGlot 30.18.0、Psycopg 3.3.6。

- 完整测试：**146 passed，0 failed，0 skipped**，已设置真实 PostgreSQL 只读测试连接。
- Ruff 检查通过，19 个 Python 文件格式检查通过。
- 独立 stdio 客户端调用五个工具全部 PASS；测试同时覆盖 legacy 初始化和当前协议客户端。
- wheel 构建通过；依赖按 uv.lock 离线同步成功。
- 两个基准实验完整结果一致，每种查询保留 5 次原始计划，已多次重复运行。
- 已独立审查并回归修复：CTE/列别名索引来源、提前停止误报、持续取消连接清理、解析器日志脱敏。

本页记录本地实际执行结果；云端检查独立运行，最新状态见 [GitHub Actions](https://github.com/cloudwallker/pgscope-mcp/actions)。

## 实验记录

| 实验 | 优化前中位数 ms | 优化后中位数 ms | 完整结果一致 |
| --- | ---: | ---: | --- |
| customer_id 索引前后 | 4.788 | 0.069 | 是 |
| 日期函数与半开范围 | 5.751 | 0.307 | 是 |

这些是本机该次运行的观测值，受缓存、硬件与数据分布影响，不是固定性能承诺。

证据：[MCP 输出](../artifacts/mcp-smoke.json)、[基准报告](../artifacts/benchmark/benchmark.md)、[原始计划](../artifacts/benchmark/benchmark.json)。原始 JUnit 文件可能包含机器标识，仅保留在本地且由 Git 忽略；使用 README 中的测试命令可重新验证。

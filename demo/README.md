# 演示数据库与可重复基准

在项目根目录运行 `docker compose up -d`。PostgreSQL 17 只映射到本机
`127.0.0.1:55432`，首次创建数据卷时执行 `demo/init.sql`。它按确定的
`generate_series` 规则填充 5,000 个客户、100,000 个订单和 200,000 个订单项。
已存在的数据卷不会再次执行初始化 SQL；基准脚本可在同一数据卷上重复运行。

MCP 服务使用只读连接：

```text
postgresql://pgscope_reader:pgscope_demo_readonly@127.0.0.1:55432/pgscope_demo
```

该角色仅可访问 `public.customers`、`public.orders`、`public.order_items`，
没有数据库 `CREATE`/`TEMP` 或 schema `CREATE` 权限；专用 marker 表不可读。
管理员连接仅供下述基准脚本使用，不应传给 MCP 服务的 `PGSCOPE_DSN`。

Windows PowerShell：

```powershell
$env:PGSCOPE_DEMO_ADMIN_DSN = 'postgresql://postgres:pgscope_demo_admin@127.0.0.1:55432/pgscope_demo'
.venv/Scripts/python.exe -m scripts.benchmark
```

Linux/macOS shell：

```sh
export PGSCOPE_DEMO_ADMIN_DSN='postgresql://postgres:pgscope_demo_admin@127.0.0.1:55432/pgscope_demo'
python -m scripts.benchmark
```

可用 `--output-dir` 改变输出目录，默认生成 `artifacts/benchmark/benchmark.json`
和 `artifacts/benchmark/benchmark.md`。脚本先核验 DSN、本机实际连接地址、
数据库名、账号、marker 和固定行数，随后仅重建两种名称固定的演示索引，
不会删改业务数据。

实验一比较 `orders.customer_id = 42` 在无索引和有索引时的计划；
实验二在 `orders.created_at` 索引存在时比较 `date(created_at)` 条件和
等价的半开时间范围。每种查询预热一次、实测五次，使用
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，并比较前后查询的完整结果。
报告保存每次原始计划、执行时间、根节点 buffers、扫描类型与执行时间中位数。
耗时受缓存和机器状态影响，观测倍数不构成性能保证。

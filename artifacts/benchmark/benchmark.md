# PGScope 演示数据库基准报告

数据库：`pgscope_demo`。每种查询预热 1 次，随后运行 5 次 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`。

下列倍数仅描述本次实测；受缓存、机器和 PostgreSQL 执行计划影响，不保证固定提升倍数。

## 高选择性 customer_id：无索引与添加索引

完整结果一致：是。执行时间中位数：4.788 ms → 0.069 ms；本次观测比值：69.39×。

### customer_id 无索引

SQL：`SELECT id, customer_id, created_at, status, total FROM public.orders WHERE customer_id = 42 ORDER BY id`

| 次数 | 执行时间 (ms) | 根节点 Shared Hit | 根节点 Shared Read | 根节点其他 buffers | 扫描类型 |
| ---: | ---: | ---: | ---: | --- | --- |
| 1 | 6.059 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 2 | 5.926 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 3 | 4.440 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 4 | 4.788 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 5 | 4.015 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |

### customer_id 有索引

SQL：`SELECT id, customer_id, created_at, status, total FROM public.orders WHERE customer_id = 42 ORDER BY id`

| 次数 | 执行时间 (ms) | 根节点 Shared Hit | 根节点 Shared Read | 根节点其他 buffers | 扫描类型 |
| ---: | ---: | ---: | ---: | --- | --- |
| 1 | 0.117 | 22 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 2 | 0.069 | 22 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 3 | 0.068 | 22 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 4 | 0.099 | 22 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 5 | 0.063 | 22 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |

## created_at 日期函数与半开范围（日期索引已存在）

完整结果一致：是。执行时间中位数：5.751 ms → 0.307 ms；本次观测比值：18.73×。

### date(created_at) 函数条件

SQL：`SELECT id, customer_id, created_at, status, total FROM public.orders WHERE date(created_at) = date '2024-06-15' ORDER BY id`

| 次数 | 执行时间 (ms) | 根节点 Shared Hit | 根节点 Shared Read | 根节点其他 buffers | 扫描类型 |
| ---: | ---: | ---: | ---: | --- | --- |
| 1 | 4.886 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 2 | 5.977 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 3 | 5.751 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 4 | 6.310 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |
| 5 | 4.718 | 863 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Seq Scan |

### created_at 半开范围

SQL：`SELECT id, customer_id, created_at, status, total FROM public.orders WHERE created_at >= timestamp '2024-06-15 00:00:00' AND created_at < timestamp '2024-06-16 00:00:00' ORDER BY id`

| 次数 | 执行时间 (ms) | 根节点 Shared Hit | 根节点 Shared Read | 根节点其他 buffers | 扫描类型 |
| ---: | ---: | ---: | ---: | --- | --- |
| 1 | 0.319 | 277 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 2 | 0.270 | 277 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 3 | 0.255 | 277 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 4 | 0.307 | 277 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |
| 5 | 0.469 | 277 | 0 | shared_dirtied=0, shared_written=0, local_hit=0, local_read=0, local_dirtied=0, local_written=0, temp_read=0, temp_written=0 | Bitmap Heap Scan, Bitmap Index Scan |

每次完整原始 JSON 计划保存在同目录的 `benchmark.json`。

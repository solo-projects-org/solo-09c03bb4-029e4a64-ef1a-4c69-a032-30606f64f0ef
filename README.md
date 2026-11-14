# 隐私预算队列统计 API（DP Cohort Count API）

面向公共卫生队列数据协作的统计服务：数据员**不直接交出精确计数**，合作研究组按
「地区 + 0/1 结局」拿到加 **Laplace 噪声**的人数估计。服务用 FastAPI + SQLite
实现，带全局隐私预算账本、请求级幂等和并发安全扣减。

## 1. 隐私模型与语义保证

- 每个查询返回 `真实计数 + Laplace(scale = 1/ε)` 的独立噪声，噪声尺度严格随本次
  `epsilon` 取值（`0 < epsilon ≤ 1`）。
- 全局总预算固定为 **1**，每接受一个**新** request_id 就从账本扣减其 `epsilon`；
  剩余预算不足时请求被拒绝（HTTP 402），不产生结果、不扣费。
- **幂等重试**：同一 `request_id` + 完全相同参数的重试，返回首次保存的带噪结果，
  `replayed=true`，且**不再扣费**。
- **同号改参拒绝**：同一 `request_id` 绑定不同 region/outcome/epsilon 一律 409。
- **并发不超支**：所有写操作走 `BEGIN IMMEDIATE` 事务（WAL 模式），预算检查、噪声
  生成、结果落盘、账本扣减在同一事务内原子提交；并发请求在写锁上串行化。
- **失败不扣费**：无效请求（参数 422、未导入 409、超预算 402）或事务内任何异常都会
  回滚，不会留下部分结果或预算扣减。
- **重启可恢复**：结果与账本持久化在 SQLite（compose 命名卷）。结果已保存但回执丢失
  时，重启后用相同 request_id 重试仍返回原值（见下方测试与演示）。
- **数据只导入一次**：首次 `/import` 成功后，再次导入返回 409，不支持替换数据。

> 部署提示：SQLite 的单写者串行化以**单进程**为前提。请保持 compose 默认的单副本；
> 如需多副本，应换用 PostgreSQL 等支持行锁的后端（接口与事务语义不变）。

## 2. 目录结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由与全部业务语义
│   ├── database.py      # SQLite 连接、WAL、即时写事务、建表
│   ├── privacy.py       # 线程安全的 Laplace 噪声采样（逆 CDF）
│   ├── csv_import.py    # 队列 CSV 解析与全量校验
│   ├── schemas.py       # Pydantic 请求/响应模型
│   └── config.py        # 环境变量与常量（总预算=1）
├── data/sample_cohort.csv   # 示例 CSV（24 名受试者，3 个地区）
├── tests/               # 38 个 pytest 测试（含并发与重启场景）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── requirements-dev.txt
```

## 3. 快速启动（Docker Compose）

```bash
docker compose up --build
# 服务监听 http://localhost:8000
# Swagger UI:  http://localhost:8000/docs
# ReDoc:        http://localhost:8000/redoc
# OpenAPI JSON: http://localhost:8000/openapi.json
```

数据库位于容器内 `/data/dpapi.db`，由命名卷 `dpapi-data` 持久化；容器重启或
`docker compose up --force-recreate` 后数据、历史结果和预算账本都保留。

停止并清空全部数据（含已导入队列）：

```bash
docker compose down -v
```

## 4. API 一览

| 方法 | 路径      | 说明                                   |
| ---- | --------- | -------------------------------------- |
| POST | `/import` | 一次性上传 CSV（multipart 字段 `file`）|
| POST | `/query`  | 加噪计数查询，按 epsilon 扣预算        |
| GET  | `/budget` | 总预算 / 剩余 / 已花费                 |
| GET  | `/status` | 导入状态与预算概览                     |
| GET  | `/health` | 存活探针                               |
| GET  | `/docs`   | Swagger UI                             |

### 4.1 导入 CSV

表头必须恰好为 `subject_id,region,outcome`（顺序不限，UTF-8，支持 BOM/CRLF）；
`outcome` 只能是 `0` 或 `1`，`subject_id` 必须唯一。

```bash
curl -X POST http://localhost:8000/import \
  -F 'file=@data/sample_cohort.csv'
# 201
# {"file_name":"sample_cohort.csv","imported_rows":24,"distinct_regions":3}
```

### 4.2 加噪查询

```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"q-001","region":"北京","outcome":1,"epsilon":0.2}'
```

响应：

```json
{
  "request_id": "q-001",
  "region": "北京",
  "outcome": 1,
  "epsilon": 0.2,
  "noisy_result": -1.880884200832063,
  "remaining_budget": 0.8,
  "replayed": false
}
```

- `noisy_result` 为浮点数（真实计数不单独返回）。
- 相同请求体重试：`noisy_result` 不变、`replayed=true`、预算不变。
- 同号改任何参数：`409`；超预算的新请求：`402`；`epsilon` 越界/字段缺失：`422`。

### 4.3 查看预算

```bash
curl http://localhost:8000/budget
# {"total_budget":1.0,"remaining_budget":0.8,"spent_budget":0.2}
```

## 5. 本地开发与测试（无需 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

export DPAPI_DB_PATH=/tmp/dpapi-dev.db
uvicorn app.main:app --reload

# 运行测试（每个用例使用独立临时数据库）
pytest -q
```

测试覆盖：导入校验与一次性约束、噪声统计特性（均值/方差）、幂等重试、同号改参
拒绝、预算耗尽与浮点边界、无效请求零扣费、10/20 线程真实并发不超支、以及模拟进程
重启后的原值回放。

## 6. 关键设计说明

- **事务即协议**：`/query` 在单个 `BEGIN IMMEDIATE` 事务内完成
  「查重放 → 参数比对 → 预算校验 → 计数 → 加噪 → 写结果 → 扣账本」，任一步失败整体
  回滚，因此「保存失败不扣费、不返回新结果」由数据库原子性保证，而非应用层补丁。
- **请求号即幂等键**：`query_requests.request_id` 为主键，冲突时走回放分支；参数比对
  对 epsilon 使用 1e-9 容差，仅吸收浮点表示误差，不放宽 `0 < ε ≤ 1` 的入参校验。
- **独立噪声**：进程级 `random.Random` 加锁采样，仅对每个**新**请求抽一次并立即落盘；
  回放永远读取已保存的值，不重新抽样。
- **预算钳制**：账本更新使用 `MAX(0, remaining - ε)`，配合 `CHECK (remaining >= 0)`，
  避免重复浮点减法产生负零尾差。

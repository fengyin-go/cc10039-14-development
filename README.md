# 数据湖分区与存储治理中心

管理数据湖分区策略、存储优化、冷热分层和小文件合并的数据湖治理平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8014/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5194/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8014`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 分区策略 | `partition_policy` | 分区策略 | 策略名称、分区维度、分区粒度 |
| 湖表注册 | `lake_table` | 湖表 | 表名、所属业务域、存储格式 |
| 冷热分层 | `storage_tier` | 分层策略 | 策略名称、热层阈值、冷层阈值 |
| 小文件合并 | `file_compaction` | 合并任务 | 任务编号、目标湖表、文件阈值 |
| 数据目录 | `data_catalog` | 目录条目 | 条目名称、数据层级、所属湖表 |
| 湖内质量监控 | `quality_monitor` | 监控规则 | 规则名称、监控指标、告警阈值 |
| 保留策略 | `retention_mgr` | 保留策略 | 策略名称、适用湖表、保留天数 |
| 入湖管道 | `ingest_pipeline` | 入湖任务 | 任务名称、来源系统、目标湖表 |
| 快照版本 | `snapshot_version` | 快照版本 | 湖表名、快照时间戳、版本号 |
| 访问审计 | `access_audit` | 访问记录 | 访问者、目标湖表、操作类型 |
| Schema演进 | `schema_evolution` | Schema变更 | 湖表名、变更类型、变更字段 |
| 负载均衡配置 | `load_balance` | 分片配置 | 湖表名、当前分片数、分片策略 |
| 元数据同步 | `metadata_sync` | 同步任务 | 任务名称、源端类型、目标端类型 |
| 数据回填任务 | `backfill_job` | 回填任务 | 任务名称、目标湖表、回填时间范围 |
| 数据字典管理 | `data_dictionary` | 字典条目 | 字典名称、适用范围、值域列表 |
| 任务调度 | `task_scheduler` | 调度任务 | 任务名称、调度表达式、关联管道 |
| 存储告警规则 | `alert_rule` | 告警规则 | 规则名称、告警指标、告警级别 |
| 存储成本分析 | `cost_report` | 成本报告 | 报告周期、目标业务域、存储总量 |
| 湖表血缘 | `lineage_graph` | 血缘关系 | 上游源、中间转换、下游消费 |
| 治理报告 | `govern_report` | 治理报告 | 报告周期、治理指标、优化建议 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖

## 分域访问计量（访问审计派生）

「分域访问计量」页（`/metering`）把访问审计明细按湖表归属聚合成业务域用量，
数据链路在 `backend/app/services/domain_metering.py`：

- **湖表注册**：每张湖表带稳定的 `表标识`（如 `LT-0004`），注册时写入一条带
  生效区间的业务域归属记录（`lake_table_ownership`）。转域时关闭旧记录、追加新记录，
  历史归属可逐条复查。
- **访问审计**：每条访问有唯一 `访问编号`，用量按其实际 `返回记录数` 计量。
- **周期结算**：`settlement_period` 定义历史/当期周期，`domain_settlement` 是
  按业务域汇总的结算单，`settlement_ledger` 是每条访问唯一的归属对照
  （访问编号 + 归属记录编号 + 生效/失效时间）。
- **归属规则**：按访问时点生效的归属记录归属；转域后旧账不迁移、不回滚，
  每条访问在所有结算里最多出现一次。旧域看到「当前归属已变更」的冲突提示，
  新域按未结算的返回记录数复核当期用量。
- **业务域隔离**：所有查询必须带 `domain`，跨域读取结算明细返回 403 且不携带明细。

接口前缀 `/api/metering`：

| 接口 | 用途 |
| --- | --- |
| `GET /overview` | 业务域口径、周期、湖表归属总览（无明细） |
| `GET /ownership?table_id=` | 湖表归属记录（含表标识、生效区间） |
| `POST /ownership/change` | 变更湖表业务域归属 |
| `GET /datasets?domain=&period=` | 本域、本周期分域访问数据集（明细 + 按表聚合） |
| `GET /settlements?domain=` | 本域结算统计（不含明细） |
| `GET /settlements/{结算编号}?domain=` | 从本域结算追到相关访问明细 |
| `GET /accesses/{访问编号}?domain=` | 按访问编号追溯唯一归属对照 |
| `POST /settle` | 结算并冻结本域账期 |
| `POST /periods/{YYYY-MM}/refresh` | 补计周期内新增访问 |

示例数据固定演示「订单明细湖表」在 2026-09-01 由交易域转给风控域：
2026-08 两条访问（120、80 条）计入交易域历史结算，2026-09 两条访问（150、50 条）
计入风控域当期结算；历史结算在转域后才出账，因此交易域可以看到归属冲突提示。

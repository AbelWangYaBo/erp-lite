# erp-lite v1 实现计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan stage-by-stage.

**Goal:** 单租户轻量制造 ERP v1（进销存+BOM/MRP/工单+应收应付+全量审计），FastAPI+PostgreSQL 后端、Vue3+Element Plus 前端、docker compose 部署。

**Architecture / Tech Stack:** 见 docs/DESIGN.md（前后端契约以 DESIGN.md §4 为唯一依据，改动须先改 DESIGN.md）。

## 阶段划分与依赖

| 阶段 | 内容 | 依赖 | 可并行 |
|---|---|---|---|
| S0 | 仓库骨架：backend uv 工程 + web Vite 工程 + docker-compose + 三个 Dockerfile + nginx.conf + .gitignore + README | 无 | - |
| S1 | 数据模型+迁移+基础设施：全部 SQLAlchemy 模型、alembic 初始迁移、config/database/deps、auth（login+JWT）、审计写入与查询、编号服务 | S0 | - |
| S2 | 基础数据 API：materials/warehouses/partners CRUD + tests | S1 | - |
| S3 | 进销存：PO/SO 状态机+入库/出库+库存台账+移动加权成本 + tests | S2 | - |
| S4 | 生产：BOM 维护 + MRP 运算 + MO 领料/完工 + tests | S3 | - |
| S5 | 财务：AP/AR 自动生成 + 核销 + 对账单 + tests | S3 | 与 S4 并行（不同 router 文件，需合并时先 rebase） |
| S6 | 前端框架+全部页面：布局/登录/基础数据/进销存/生产/财务/审计查看 | S0（契约=DESIGN §4） | 与 S2–S5 并行 |
| S7 | 部署+验收：seed 数据、compose 全链路启动、API 冒烟脚本跑通核心闭环、README 部署说明 | S4,S5,S6 | - |

## 执行约定

- 每阶段派一个实现 subagent，完成后跑该阶段 tests，随后派一个合并的 spec+quality 审查 subagent；问题修复后再进入下一阶段。
- 每阶段结束 git commit（`feat(scope): ...`）。
- 前端不写自动化测试，验收走 S7 手工冒烟清单。
- TDD 适用于后端阶段（先写失败测试再实现）。
- 分阶段验收命令写进各任务；S7 是 DoD 总验收（docs/PRD.md §5）。

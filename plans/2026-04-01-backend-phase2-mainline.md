# 第二阶段后端主链路计划

## 1. 背景

当前 `closed_loop_v2` 已完成第一阶段最小骨架，具备以下能力：

- FastAPI 应用入口与基础路由
- PostgreSQL + SQLAlchemy 2.x 模型
- Alembic 初始迁移
- visit / inspection / proactive 三个 mock collector
- recognizer / planner 的最小实现
- `POST /api/sync/run` 可完成 snapshot -> normalized_records -> task_plans 的基础写库

但现阶段实现仍偏“演示骨架”，接口契约、详情查询 API、同步结果统计、以及集成测试覆盖还不够完整，距离“真实可落地的采集-识别-规划主链路”还有明显差距。

本次任务进入第二阶段后端主链路的计划阶段，目标是在不接入真实执行器的前提下，把主链路从“mock 打通”升级到“契约更完整、规则更清晰、API 更可用、测试更可信”的状态。

本次计划文件固定路径为：

- `plans/2026-04-01-backend-phase2-mainline.md`

## 2. 目标

本次实施完成后，应达到以下目标：

- collector 契约统一，支持 `collect()`、`validate()`、`healthcheck()`
- `CollectResult` 字段统一且作为 collector 标准输出
- 3 个 planner 的 eligibility / skip_reason / planned_payload 规则明确且稳定
- 3 个 recognizer 的识别输出结构统一
- 同步接口返回更完整的 snapshot / recognition / task plan 概览
- 新增 snapshot / record / task 详情查询 API
- 新增模块 latest API
- 补齐 planner 单测与 `/api/sync/run` 全链路写库测试

## 3. 范围

本次实施范围包含：

- collector 抽象接口重构
- visit / inspection / proactive 三个 mock collector 对齐新契约
- recognizer 输出结构补全与统一
- planner 规则增强与测试补齐
- sync service 输出增强
- 新增详情查询与模块 latest 路由
- repository 层补充按 ID 查询与 latest 查询能力
- 为 `/api/sync/run` 增加链路统计信息
- 新增或完善后端自动化测试
- 回写本计划中的实施记录、偏差、验证结果、待跟进事项

## 4. 非范围

本次明确不做：

- 真实钉钉文档采集接入
- Playwright 真实采集逻辑
- 真实执行器、闭环执行、任务下发
- 前端页面开发或联调
- 权限、鉴权、用户体系扩展
- 定时任务真正调度生产同步
- 更改第一阶段已创建的数据库核心表结构，除非在实施中发现契约无法支撑本次目标且需要单独升级迁移

## 5. 当前现状 / 已知问题

当前现状：

- `BaseCollector` 只有 `collect()` 协议，没有 `validate()` 和 `healthcheck()`
- `CollectResult` 已包含大部分必需字段，但未作为完整 collector 契约的一部分统一约束
- planner 规则已有基础实现，但单测断言粒度偏粗，未完全体现“只挑出符合条件的记录”
- recognizer 已输出 `normalized_records`、`field_mapping`、`field_confidence`、`field_evidence`、`field_samples`、`unresolved_fields`、`recognition_status`，但需要复核三者一致性与返回细节
- `/api/sync/run` 当前返回过于简单，仅包含 `snapshot_id/module_code/sync_status/data_source/row_count`
- 现有 API 只有列表接口，没有 snapshot / record / task 的详情接口
- 缺少 `/api/modules/{module_code}/latest`
- 当前测试只有 planner 基础单测，没有 sync 全链路写库测试

已知问题：

- `services/sync_service.py` 当前只返回简化的 `SyncRunResponse`，无法直接体现 recognition 和 task planning 的结果统计
- repository 现阶段偏列表导向，缺少详情读取能力
- 如果新增 richer response，需同步调整 schema、service、router 和测试，改动面横跨多个模块
- 若要做 `/api/sync/run` 集成测试，需要构建隔离数据库或测试 Session 方案

## 6. 技术方案

### 6.1 Collector 契约

统一 `BaseCollector` 为带三个方法的协议：

- `collect() -> CollectResult`
- `validate() -> None | ValidationResult`
- `healthcheck() -> dict`

第一阶段仍使用 mock collector，但每个 collector 必须实现上述三个方法：

- `validate()`：校验 collector 配置、模块定义和最小输入约束
- `healthcheck()`：返回当前 collector 的模块状态、数据源类型、fallback 状态、可用性信息
- `collect()`：产出标准 `CollectResult`

`PlaywrightFallbackCollector` 继续只做 stub，但会遵守新契约。

### 6.2 Recognizer 输出

保留当前 recognizer 设计，但明确统一输出语义：

- `normalized_records`：逐行标准化结果
- `field_mapping`：标准字段与原始列映射
- `field_confidence`：字段识别置信度
- `field_evidence`：识别依据
- `field_samples`：样本值
- `unresolved_fields`：当前未解析或缺失字段
- `recognition_status`：`full / partial / failed`

必要时会把 recognizer 的公共行为进一步下沉，避免三套 recognizer 输出口径漂移。

### 6.3 Planner 规则

三类 planner 继续基于标准化记录做判断：

- `visit_planner`
  - `visit_owner == "舒磊"`
  - `visit_status == "已回访"`
  - `visit_link` 为空
- `inspection_planner`
  - `inspection_done == true`
- `proactive_planner`
  - `liaison_status == "已建联"`
  - `visit_link` 为空

每条 `TaskPlanDTO` 都必须包含：

- `task_type`
- `eligibility`
- `skip_reason`
- `planned_payload`

同时约束：

- `eligibility=true` 时，`plan_status="planned"`
- `eligibility=false` 时，`plan_status="skipped"`
- `skip_reason` 需可读、可用于审计

### 6.4 Sync 主链路

增强 `SyncService.run_sync()`：

1. 先校验 module 定义与 collector 可用性
2. 执行 `validate()`
3. 执行 `collect()`
4. 写入 `source_snapshots`
5. 执行 `recognize()`
6. 写入 `normalized_records`
7. 执行 `plan()`
8. 写入 `task_plans`
9. 返回增强版同步结果

增强版同步返回建议包含：

- `snapshot`
  - `snapshot_id`
  - `module_code`
  - `sync_status`
  - `data_source`
  - `row_count`
- `recognition`
  - `record_count`
  - `full_count`
  - `partial_count`
  - `failed_count`
  - `unresolved_field_count`
- `task_plans`
  - `total_count`
  - `planned_count`
  - `skipped_count`

### 6.5 API 设计

本次新增并实现：

- `GET /api/snapshots/{snapshot_id}`
- `GET /api/records/{record_id}`
- `GET /api/tasks/{task_id}`
- `GET /api/modules/{module_code}/latest`

实现策略：

- 路由层仅做参数接收和错误转译
- 查询逻辑放在 repository / service
- 404 情况明确返回
- `latest` 接口按模块返回最近一次 snapshot 及聚合统计

### 6.6 测试策略

测试分两层：

- 单元测试
  - planner 规则断言精细化
- 集成测试
  - `/api/sync/run` 调接口
  - 验证 `source_snapshots`、`normalized_records`、`task_plans` 全链路写库
  - 验证返回的 snapshot / recognition / task plan 统计结构
  - 验证详情接口异常分支：
    - snapshot 不存在
    - record 不存在
    - task 不存在
    - 非法 `module_code`

测试数据库方案优先考虑：

- 使用测试专用数据库 URL
- 每条测试事务隔离或每次重建 schema

如果当前项目尚未有统一测试 fixture，则本次一并补最小可维护版本。

## 7. 分步骤实施计划

### 步骤 1：统一契约与 schema

- 重构 `BaseCollector`
- 调整 `CollectResult`、`SyncRunResponse` 及相关响应 schema
- 明确识别统计与任务统计的响应结构

### 步骤 2：改造 collectors

- 为 visit / inspection / proactive mock collector 增加 `validate()` 与 `healthcheck()`
- 更新 Playwright fallback stub 契约
- 统一 collector 元信息输出

### 步骤 3：校准 recognizers

- 复核 3 个 recognizer 的输出字段完整性
- 如有必要抽公共辅助逻辑
- 明确 `recognition_status` 规则

### 步骤 4：校准 planners

- 重新确认 3 个 planner 的 eligibility 规则
- 统一 `skip_reason` 和 `planned_payload` 结构
- 保证 `TaskPlanDTO` 字段齐备

### 步骤 5：增强 repository 与 service

- repository 增加按 ID 查询能力
- 模块 latest 查询能力落到 service 或 repo
- `run_sync()` 返回增强版聚合结果

### 步骤 6：扩展 API

- 新增 snapshot / record / task 详情 API
- 新增 `/api/modules/{module_code}/latest`
- 更新 `/api/sync/run` 返回结构

### 步骤 7：补测试

- 加强 planner 规则单测
- 新增 sync 全链路集成测试
- 如需要，补充测试 fixture、测试数据库初始化逻辑

### 步骤 8：验证与收尾

- 跑单测 / 集成测试
- 必要时本地执行格式或语法校验
- 回写 plan 实施记录、偏差、验证结果、待跟进事项

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase2-mainline.md`
- `schemas/sync.py`
- `schemas/common.py`
- `schemas/snapshot.py`
- `schemas/task.py`
- 可能新增详情响应 schema 文件
- `services/collectors/base.py`
- `services/collectors/visit_collector.py`
- `services/collectors/inspection_collector.py`
- `services/collectors/proactive_collector.py`
- `services/collectors/playwright_fallback.py`
- `services/recognizers/field_inference.py`
- `services/recognizers/visit_recognizer.py`
- `services/recognizers/inspection_recognizer.py`
- `services/recognizers/proactive_recognizer.py`
- `services/planners/visit_planner.py`
- `services/planners/inspection_planner.py`
- `services/planners/proactive_planner.py`
- `services/sync_service.py`
- `repositories/source_snapshot_repo.py`
- `repositories/normalized_record_repo.py`
- `repositories/task_plan_repo.py`
- 可能新增或调整 repository 公共方法
- `apps/api/routers/sync.py`
- `apps/api/routers/snapshots.py`
- `apps/api/routers/records.py`
- `apps/api/routers/tasks.py`
- `apps/api/routers/modules.py`
- `tests/`
- 若测试需要，也可能新增 `tests/conftest.py`

## 9. 风险与缓解

### 风险 1：同步返回结构改动导致现有调用方不兼容

缓解：

- 明确本项目尚处开发阶段，可接受接口演进
- 用 response schema 明确新结构
- 同步更新测试

### 风险 2：collector / recognizer / planner 契约改动面较大

缓解：

- 先从 schema 和协议入手，统一约束再逐模块替换
- 保持模块注册与注入方式不变，减少外部波及

### 风险 3：集成测试依赖 PostgreSQL，环境不稳定

缓解：

- 优先使用单独测试数据库
- 将测试 fixture 最小化并集中管理
- 若无法完全自动化初始化，至少提供明确执行前提

### 风险 4：过早引入真实采集复杂度

缓解：

- 本次只做“可落地主链路”，仍坚持 mock collector
- Playwright 继续保持 stub，不提前扩 scope

## 10. 验收标准

满足以下条件视为本次任务完成：

- 3 个 collector 都实现 `collect()`、`validate()`、`healthcheck()`
- `CollectResult` 字段完整且统一
- 3 个 recognizer 都输出完整七项识别结果
- 3 个 planner 都按指定规则生成 `task_type / eligibility / skip_reason / planned_payload`
- `POST /api/sync/run` 返回 snapshot 概览、recognition 统计、task plan 统计
- `GET /api/snapshots/{snapshot_id}` 可查询单个快照详情
- `GET /api/records/{record_id}` 可查询单条标准化记录详情
- `GET /api/tasks/{task_id}` 可查询单条任务详情
- `GET /api/modules/{module_code}/latest` 可查询模块最近一次同步摘要
- planner 规则测试覆盖指定条件
- `/api/sync/run` 集成测试可验证 snapshot -> records -> task_plans 全链路写库
- 实施完成后，必须贴出以下接口的真实返回样例：
  - `POST /api/sync/run`
  - `GET /api/modules/{module_code}/latest`
  - `GET /api/snapshots/{snapshot_id}`

## 11. 验证步骤

计划中的验证步骤如下：

1. 运行语法检查或最小编译检查
2. 运行 planner 单测，确认三类规则只挑出正确记录
3. 运行 `/api/sync/run` 集成测试，确认三层数据入库
4. 验证同步返回结构包含 snapshot / recognition / task plan 统计
5. 验证四个新增详情类 API 的成功与 404 分支
6. 验证非法 `module_code` 的错误分支
7. 记录并整理以下接口的真实返回样例：
   - `POST /api/sync/run`
   - `GET /api/modules/{module_code}/latest`
   - `GET /api/snapshots/{snapshot_id}`
8. 如本地数据库可用，补一次手工 smoke：
   - 触发 `POST /api/sync/run`
   - 查询 `/api/modules/{module_code}/latest`
   - 查询对应 snapshot / record / task 详情

## 12. 实施记录（先留空）

- 实际完成内容
  - 已统一 `BaseCollector` 契约，新增 `validate()`、`healthcheck()`、`collect()`
  - 已为 visit / inspection / proactive 三个 mock collector 对齐新契约
  - 已保留 `PlaywrightFallbackCollector` stub，并补齐契约方法
  - 已增强 `SyncRunResponse`，返回 `snapshot`、`recognition`、`task_plans` 三段聚合信息
  - 已为 snapshot / record / task 新增详情查询 API
  - 已新增 `GET /api/modules/{module_code}/latest`
  - 已补充 repository 按 ID 查询能力
  - 已实现 `SyncService` 详情查询与 latest 汇总能力
  - 已补充 planner 更精细的规则测试
  - 已补充 `/api/sync/run` 集成测试，验证 snapshot -> records -> task_plans 全链路写库
  - 已补充详情接口 404 分支和非法 `module_code` 400 分支测试
  - 已抓取以下真实返回样例：
    - `POST /api/sync/run`
    - `GET /api/modules/{module_code}/latest`
    - `GET /api/snapshots/{snapshot_id}`

- 与原计划偏差
  - 未修改数据库表结构与迁移文件，因为当前二阶段目标可在现有五张表上完成
  - 集成测试夹具增加了“自动拉起 Docker PostgreSQL 容器”的实现细节，这是计划中“测试数据库方案”下的具体落地
  - 为避免无 Docker 环境直接失败，测试夹具增加了在无 `TEST_DATABASE_URL` 且 Docker daemon 不可用时跳过集成测试的保护逻辑

- 验证结果
  - `python3 -m compileall .` 通过
  - `.venv/bin/pytest -q` 通过，结果为 `6 passed`
  - 已使用真实 PostgreSQL 容器 + FastAPI TestClient 验证：
    - `POST /api/sync/run`
    - `GET /api/modules/visit/latest`
    - `GET /api/snapshots/{snapshot_id}`
    - `GET /api/records/{record_id}`
    - `GET /api/tasks/{task_id}`
  - 已验证异常分支：
    - snapshot 不存在返回 404
    - record 不存在返回 404
    - task 不存在返回 404
    - 非法 `module_code` 返回 400

- 待跟进事项
  - 后续若进入真实采集阶段，需要把 collector 的 `validate()` 与 `healthcheck()` 接到真实配置与外部依赖检查
  - 后续可考虑将 recognition 统计改为按记录级别而非仅按聚合结果推导
  - 后续可补充模块无快照时 `/api/modules/{module_code}/latest` 的产品语义说明或前端处理约定

## 13. 遗留问题（先留空）

- 当前 recognizer 仍是规则映射型实现，尚未引入更复杂字段推断或置信度差异化策略
- `raw_meta` 中同时保留了 `healthcheck` 与 `collector_health`，语义接近，后续可再统一
- 集成测试依赖 Docker 或外部 `TEST_DATABASE_URL`，在纯离线环境下不会自动获得 PostgreSQL 能力

# 第九阶段平台计划：调度、重试、审计与运营

## 1. 背景

当前项目已经完成以下阶段：

- Phase 1：项目骨架
- Phase 2：后端主链路
- Phase 3：real collector 架构
- Phase 4：真实钉钉 transport
- Phase 5：字段识别与标准化增强
- Phase 6：执行器接入
- Phase 7：巡检报告匹配与文件处理
- Phase 8：前端工作台

当前系统已经具备较完整的人工可操作能力：

- 可以采集 3 个模块的数据
- 可以进行字段识别与标准化
- 可以生成 `task_plans`
- 可以通过执行器进行 `precheck / dry_run / simulated execute`
- 可以通过工作台查看模块、快照、任务、执行结果

但系统目前仍然更接近“手动可用的工具”，距离“可持续运行的平台”还有明显差距，主要体现在：

- 缺少稳定的定时调度
- 缺少统一的重试和重跑机制
- 缺少更完整的同步/执行审计聚合
- 缺少运行状态导向的运营视图
- 缺少幂等保护，无法有效防止重复 sync 和重复 execute

Phase 9 的目标，是把当前系统从“手动可用的工作台”升级为“可持续运行的平台”，补齐调度、重试、审计、运营视图和重跑能力。

## 2. 目标

本次实施完成后，应达到以下目标：

- 使用 APScheduler 建立 3 个模块的定时 sync 能力
- 支持模块级调度启停与 cron / interval 配置
- 建立 sync 失败重试、execute 失败重试和 manual rerun 能力
- 区分：
  - 可重试
  - 不可重试
  - `manual_required`
- 建立更完整的运行审计视图：
  - 同步历史
  - 执行历史
  - 最近成功/失败状态
  - 按模块查看最近运行情况
- 增强工作台，展示：
  - 最近运行状态
  - 失败任务
  - `manual_required`
  - 可重跑入口
- 建立幂等保护，防止：
  - 重复 sync
  - 重复 execute
  - 同一 task 多次并发执行
- 补齐相关自动化测试

## 3. 范围

本次实施范围包含：

- APScheduler 集成与生命周期管理
- 模块级 sync job 注册、启停和配置解析
- sync retry / execute retry / manual rerun 机制
- 运行审计聚合与状态查询
- 工作台运营视图增强
- 幂等控制与并发保护
- 调度、重试、重跑、幂等和运营视图相关测试
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 企业级告警中心
- 多角色权限系统
- 外部消息通知平台
- 大规模分布式调度
- 多实例调度协调器
- 复杂 SLA 管理
- 历史报表导出中心

## 5. 当前现状 / 已知问题

当前现状：

- 已有 `scheduler/` 目录和 APScheduler 依赖，但尚未形成真正的平台级调度链路
- `module_configs` 已具备 `enabled` 和 `sync_cron` 等基础字段，但调度配置使用方式还比较初级
- sync 目前以手动触发为主：`POST /api/sync/run`
- execute 目前以手动触发为主：`POST /api/tasks/{task_id}/precheck` / `execute`
- `source_snapshots`、`task_runs` 已积累了基础审计数据
- 前端工作台已上线，但更偏数据浏览和手动操作，还不是“运营视图”

已知问题：

- 没有统一的 scheduler 启停与 job 注册机制
- 没有明确的 retry policy 和 retry eligibility 判断
- 没有统一的 rerun 入口来重跑 sync 或 execute
- 没有防止同一模块重复 sync、同一 task 并发 execute 的保护
- 工作台当前缺少“失败任务、manual_required、最近运行状态”的集中视图
- 现有审计数据分散在表里，缺少聚合层

## 6. 技术方案

### 6.1 调度层

本阶段调度能力基于 APScheduler 落地，建议：

- 在应用启动时初始化 scheduler
- 从 `module_configs` 读取调度配置
- 为 3 个模块注册 sync job
- 支持：
  - 启用/停用
  - cron
  - interval

建议调度配置来源：

- `module_configs.enabled`
- `module_configs.sync_cron`
- `module_configs.extra_config`

如有必要，可在 `extra_config` 中补：

- `schedule_type`
- `schedule_interval_minutes`
- `retry_policy`

### 6.2 重试与重跑

本阶段将 retry 与 rerun 分开：

- retry
  - 系统自动触发
  - 用于可判定为临时失败的 sync / execute
- rerun
  - 用户手动触发
  - 用于明确再次执行某次 sync 或 task

需要明确以下状态：

- 可重试：例如请求失败、临时依赖异常、可恢复 transport 错误
- 不可重试：例如配置缺失、关键数据缺失、契约不匹配
- `manual_required`：例如业务语义需要人工处理，不适合自动重试

本阶段固定边界如下：

- 自动重试只针对临时性技术失败
- 手动重跑用于用户显式再次触发
- `manual_required` 默认不进入自动重试

建议为 sync 和 execute 都定义 retry policy：

- 最大重试次数
- 重试间隔
- 是否允许自动重试
- 是否允许人工 rerun

### 6.3 审计聚合

当前已有基础原始审计表：

- `source_snapshots`
- `task_runs`

Phase 9 需要补一层“运营聚合”能力，用于快速回答：

- 最近一次 sync 是否成功
- 最近一次 execute 是否成功
- 当前有哪些失败任务
- 当前有哪些 `manual_required`
- 某模块最近的运行情况如何

实现方式优先考虑：

- 基于现有表做 repository/service 聚合查询
- 尽量不新增过多物化表
- 仅在必要时新增轻量运行记录或锁记录表

### 6.4 幂等保护

本阶段要防止三类问题：

- 同一模块短时间内重复 sync
- 同一 task 被重复 execute
- 同一 task 被并发执行

第一版采用保守规则：

- 同一 `module_code` 处于运行中时，不允许再次 sync
- 同一 `task_plan_id` 处于运行中时，不允许再次 execute

建议策略：

- sync 幂等 key：`module_code + time window + active run status`
- execute 幂等 key：`task_plan_id + active run status`
- 对“运行中”状态增加锁定语义

可以通过以下方式实现：

- 数据库级查询 + 状态检查
- 必要时增加轻量锁字段或运行状态枚举
- 对重复请求直接返回“已有运行中/最近已执行”的结果

### 6.5 运营视图增强

Phase 8 工作台已经有基础页面，Phase 9 需要增强为“运营视图”：

- 模块总览页增加最近运行状态
- 展示失败 sync / 失败 execute
- 展示 `manual_required`
- 展示可重跑入口

第一版只做最小可用：

- 最近运行状态
- 失败任务
- `manual_required`
- rerun 入口

不在本阶段扩成复杂运营后台。

如果现有 API 不足，可以补：

- 最近运行摘要 API
- 失败任务列表 API
- `manual_required` 列表 API
- rerun API

### 6.6 API 设计

本阶段可能需要新增或增强以下 API：

- `POST /api/sync/run`
  - 支持幂等判断
- `POST /api/modules/{module_code}/sync/rerun`
- `POST /api/tasks/{task_id}/rerun`
- `GET /api/ops/overview`
- `GET /api/ops/failures`
- `GET /api/ops/manual-required`

若实施中发现已有接口可复用，允许减少新增接口数量，但运营视图和手动重跑能力必须可落地。

### 6.7 测试策略

至少覆盖：

- 定时任务触发测试
- 重试逻辑测试
- 重跑测试
- 幂等测试
- 运营视图/API 测试

测试优先使用：

- fake / fixture collector
- stub executor
- 可控时间窗口
- 可控失败注入

不把真实线上环境作为 CI 前提。

## 7. 分步骤实施计划

### 步骤 1：梳理调度与运行状态模型

- 明确 scheduler 生命周期接入点
- 明确 sync / execute 的运行状态与可重试语义
- 明确幂等判定口径

### 步骤 2：实现 APScheduler 主链路

- 初始化 scheduler
- 读取模块配置
- 注册 3 个模块 sync job
- 支持启停和 cron / interval

### 步骤 3：实现 retry / rerun 机制

- sync 失败重试
- execute 失败重试
- manual rerun 入口
- 可重试 / 不可重试 / `manual_required` 区分

### 步骤 4：实现审计聚合

- 聚合同步历史
- 聚合执行历史
- 计算最近成功/失败状态
- 支持按模块查看最近运行情况

### 步骤 5：实现幂等保护

- 防止重复 sync
- 防止重复 execute
- 防止同一 task 并发执行

### 步骤 6：增强运营视图

- 工作台展示最近运行状态
- 展示失败任务
- 展示 `manual_required`
- 提供可重跑入口

### 步骤 7：补测试

- 定时任务触发测试
- 重试逻辑测试
- 重跑测试
- 幂等测试
- 运营视图/API 测试

### 步骤 8：验证与收尾

- 跑语法与自动化测试
- 做最小手工 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-platform-phase9-ops.md`
- `scheduler/`
- `services/sync_service.py`
- `services/task_execution_service.py`
- `repositories/`
- `apps/api/routers/`
- `apps/web/router.py`
- `templates/console/`
- `static/console/app.js`
- `core/config.py`
- `models/`
- 如有必要，新增 Alembic 迁移
- `tests/`

## 9. 风险与缓解

### 风险 1：调度和手动触发冲突

缓解：

- 增加幂等保护
- 对运行中状态做显式检查
- scheduler 与手动入口复用同一服务层

### 风险 2：重试机制误重试不可恢复错误

缓解：

- 明确错误分类
- 引入 retry eligibility 判断
- 配置缺失、契约错误等默认不可自动重试

### 风险 3：运营视图依赖查询过重

缓解：

- 第一版优先做轻量聚合查询
- 必要时限制时间窗口和分页
- 不急于新增复杂物化层

### 风险 4：幂等规则过松或过严

缓解：

- 第一版先按模块 sync、按 task execute 做保守保护
- 用测试固定边界
- 对 manual rerun 留显式入口

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已使用 APScheduler 接通 3 个模块定时 sync
- 已支持调度启停和 cron / interval 配置
- 已支持：
  - sync 失败重试
  - execute 失败重试
  - manual rerun
- 已区分：
  - 可重试
  - 不可重试
  - `manual_required`
- 已建立运行审计聚合，支持：
  - 同步历史
  - 执行历史
  - 最近成功/失败状态
  - 按模块查看最近运行情况
- 已增强工作台，支持展示：
  - 最近运行状态
  - 失败任务
  - `manual_required`
  - 可重跑入口
- 已建立幂等保护，防止：
  - 重复 sync
  - 重复 execute
  - 同一 task 多次并发执行
- 自动重试只针对临时性技术失败
- 手动重跑用于用户显式再次触发
- `manual_required` 默认不进入自动重试
- 第一版幂等保护采用保守规则：
  - 同一 `module_code` 运行中时禁止再次 sync
  - 同一 `task_plan_id` 运行中时禁止再次 execute
- 第一版运营视图保持最小可用，不扩成复杂运营后台
- 测试覆盖：
  - 定时任务触发
  - 重试逻辑
  - 重跑
  - 幂等
  - 运营视图/API

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证 APScheduler 启动与 job 注册
2. 验证 3 个模块的定时 sync 触发
3. 验证 sync 和 execute 的 retry 行为
4. 验证 manual rerun 入口
5. 验证幂等保护与并发保护
6. 验证运营视图中的最近状态、失败任务和 `manual_required`
7. 运行自动化测试
8. 做最小手工 smoke

## 12. 实施记录（先留空）

- 已接入 APScheduler 调度主链路：
  - 启动时读取 `module_configs`
  - 支持 cron / interval 注册
  - 新增 `run_scheduled_sync_job()`，调度任务以 `trigger=scheduler` 进入现有 sync 主链路
- 已实现 sync 自动重试：
  - 仅对 `TimeoutError`、`ConnectionError`、`OSError` 这类临时性技术失败自动重试
  - 重试审计写入 `source_snapshots.raw_meta._ops`
  - 最终 `SyncRunResponse` 增加 `run_context`
- 已实现 execute 自动重试：
  - 仅当 executor 返回 `run_status=failed` 且 `retryable=true` 时自动重试
  - `manual_required` 和 `precheck_failed` 默认不进入自动重试
  - 重试审计写入 `task_runs.result_payload._ops`
- 已实现手动重跑入口：
  - `POST /api/modules/{module_code}/sync/rerun`
  - `POST /api/tasks/{task_id}/rerun`
- 已实现第一版保守幂等保护：
  - 同一 `module_code` 运行中禁止再次 sync
  - 同一 `task_plan_id` 运行中禁止再次 execute
  - 以进程内运行时锁实现，符合本阶段“非分布式”范围
- 已新增最小运营 API：
  - `GET /api/ops/overview`
  - `GET /api/ops/failures`
  - `GET /api/ops/manual-required`
- 已增强工作台最小运营视图：
  - dashboard 展示最近运行状态、失败任务、`manual_required`
  - dashboard 和 tasks 页增加 rerun 入口
- 已补自动化测试：
  - 调度 job 注册与 scheduler 触发
  - sync 自动重试
  - execute 自动重试
  - sync / execute 幂等冲突
  - task rerun
  - ops API 与 console 渲染

### 实际完成内容

- 新增运行时锁注册表 `core/runtime_state.py`
- 扩展配置：
  - `SCHEDULER_ENABLED`
  - `SYNC_RETRY_MAX_ATTEMPTS`
  - `EXECUTE_RETRY_MAX_ATTEMPTS`
- 重构 `SyncService`
  - 增加 trigger / retry / failure snapshot 审计元数据
  - 增加运行中冲突保护
- 重构 `TaskExecutionService`
  - 增加 execute 自动重试
  - 增加 rerun 入口
  - 增加任务运行锁
- 新增 `OpsService` 聚合运营视图
- 新增 ops API router
- 更新前端 dashboard / tasks 页面与原生 JS 交互

### 与原计划偏差

- 未新增独立“运行记录表”或数据库锁表
  - 本次优先复用 `source_snapshots` 和 `task_runs`
  - 幂等锁第一版使用进程内锁，符合本阶段“不做分布式调度”的范围
- 自动重试未实现异步延迟重试队列
  - 当前为同一请求/任务内的即时重试
  - 先保证链路正确、审计完整、测试稳定
- 运营视图保持在最小可用范围
  - 仅补最近运行状态、失败任务、`manual_required`、rerun 入口
  - 没有扩成复杂运营后台

### 验证结果

- `python3 -m compileall apps core models repositories schemas services scheduler tests` 通过
- `.venv/bin/python -m pytest -q` 通过
- 测试结果：`47 passed`
- 已验证：
  - scheduler job 注册与触发
  - sync 自动重试
  - execute 自动重试
  - sync / execute 并发冲突 409
  - manual rerun
  - ops API
  - console 运营视图渲染

### 待跟进事项

- 下一步如进入更长期运营阶段，可把进程内锁升级为数据库级或分布式锁
- 若后续需要更细粒度重试策略，可把 retry policy 下沉到模块级配置
- 如需要更完整运营后台，可在后续单独阶段扩展趋势图、分页、筛选和报表
- 若未来引入多实例部署，需要重新设计 scheduler 与锁协调机制

## 13. 遗留问题（先留空）

- 当前幂等锁为单进程实现，多实例部署下不够
- 自动重试当前为即时重试，不包含退避策略和异步任务队列
- 运营视图目前偏轻量，适合现阶段，不适合作为复杂值班后台

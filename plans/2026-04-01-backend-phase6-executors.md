# 第六阶段后端计划：执行器接入

## 1. 背景

当前项目已经完成以下阶段：

- Phase 1：项目骨架
- Phase 2：`source_snapshots -> normalized_records -> task_plans` 后端主链路
- Phase 3：real collector 架构与 source config 抽象
- Phase 4：真实钉钉 transport 接入
- Phase 5：字段识别与标准化增强

目前系统已经具备较完整的“采集 -> 识别 -> 规划”能力：

- `source_snapshots` 已能记录原始采集快照
- `normalized_records` 已能承载标准化记录与识别审计
- `task_plans` 已能稳定输出 `planned / skipped / skip_reason`

此外，数据库中已经存在 `task_runs` 表，用于承载执行审计；`services/executors/` 目录也已经预留了 stub。但执行器目前仍处于空壳状态：

- 没有统一 `BaseExecutor` 契约
- 没有 `precheck / dry_run / healthcheck`
- 没有真正的 task 执行主链路
- 没有将执行结果写入 `task_runs`
- API 中也没有执行相关接口

Phase 6 的目标，是在现有识别与规划成果之上，开始接入执行层，先把执行抽象、执行审计和 visit executor 第一版链路搭起来，让 `planned task` 逐步具备真实闭环执行能力。

本阶段重点不是“一次性把所有真实线上动作全部打通”，而是先把执行器架构、审计链路和 dry-run / precheck 能力做对。

## 2. 目标

本次实施完成后，应达到以下目标：

- 建立统一 `BaseExecutor` 契约，至少包含：
  - `precheck()`
  - `execute()`
  - `dry_run()`
  - `healthcheck()`
- 建立统一执行结果结构，至少包含：
  - `run_status`
  - `manual_required`
  - `result_payload`
  - `final_link`
  - `error_message`
- 把执行器真正接到：
  - `task_plans`
  - `task_runs`
- 支持 dry-run
- 执行前先做 precheck
- 让成功、失败、人工处理都能写入 `task_runs` 并可审计
- 完成 `visit executor` 第一版设计与联调
- 为 `inspection` / `proactive` executor 建立统一骨架与 stub
- 新增执行 API：
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
  - `GET /api/task-runs/{run_id}`

## 3. 范围

本次实施范围包含：

- 统一 executor 抽象设计
- 统一执行结果 schema 设计
- task execution service / orchestration 主链路
- `task_runs` 写库与详情查询
- visit executor 第一版：
  - 动作编排模型
  - precheck
  - dry-run
  - task_run 写库
- inspection / proactive executor 骨架与 stub
- 执行 API 路由
- 执行链路自动化测试
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 前端页面
- 巡检报告自动上传
- PTS 全量真实线上联调
- 三个执行器全部完全打通
- 调度与告警平台化
- 大规模浏览器自动化基础设施

## 5. 当前现状 / 已知问题

当前现状：

- `task_runs` 数据表已经存在，字段包含：
  - `run_status`
  - `manual_required`
  - `result_payload`
  - `final_link`
  - `error_message`
  - `executor_version`
- `services/executors/base.py` 目前只有一个极简 `execute()` 协议
- `visit_executor.py`、`inspection_executor.py`、`proactive_executor.py` 目前都是 stub
- `repositories/task_run_repo.py` 只有按 task_plan 列表查询能力
- tasks API 目前只有：
  - `GET /api/tasks`
  - `GET /api/tasks/{task_id}`

已知问题：

- 还没有“根据 task_plan 选择 executor -> precheck -> execute/dry_run -> 写 task_runs”的统一执行主链路
- 还没有区分：
  - precheck 失败
  - dry-run 成功
  - execute 成功
  - execute 失败
  - manual_required
- 还没有 task_run 详情查询 API
- visit executor 虽然业务动作目标清晰，但当前阶段不宜直接承诺把所有真实线上动作全部打通
- 如果执行器没有严格绑定 `task_type` / `module_code`，后续很容易出现错误执行风险

## 6. 技术方案

### 6.1 统一 Executor 契约

本阶段将统一 `BaseExecutor` 协议，至少包含：

- `precheck(task_plan, normalized_record) -> PrecheckResult`
- `dry_run(task_plan, normalized_record) -> ExecutionResult`
- `execute(task_plan, normalized_record) -> ExecutionResult`
- `healthcheck() -> dict`

设计原则：

- `precheck()` 不做真实写操作，只检查执行前条件是否满足
- `dry_run()` 只输出计划动作与预期结果，不做真实线上变更
- 第一版 `execute()` 优先支持 `precheck + dry_run + simulated execute`
- 不要求默认直接打通全部真实线上动作
- 即使是 `execute()`，默认也允许走受控 stub / simulated execute，并写入 `task_runs`
- 只有后续明确开启真实执行开关时，才允许碰真实外部系统
- `healthcheck()` 返回执行器可用性、依赖状态、版本信息

### 6.2 执行结果统一结构

需要建立统一执行结果结构，至少包含：

- `run_status`
- `manual_required`
- `result_payload`
- `final_link`
- `error_message`

建议同时补充：

- `executor_version`
- `dry_run`
- `precheck_passed`
- `action_trace`

其中 `run_status` 建议固定为：

- `precheck_failed`
- `dry_run_ready`
- `success`
- `failed`
- `manual_required`

### 6.3 task_runs 执行主链路

本阶段会引入执行服务层，例如 `TaskExecutionService`，统一负责：

1. 按 `task_id` 读取 `task_plan`
2. 关联读取 `normalized_record`
3. 根据 `module_code` / `task_type` 选择 executor
4. 执行 `precheck()`
5. 根据请求选择：
   - `dry_run()`
   - `execute()`
6. 将执行结果写入 `task_runs`
7. 返回标准化响应

要求：

- 任何执行动作都必须生成 `task_run` 审计记录
- precheck 失败也要可审计
- manual_required 也要可审计
- 失败信息要可追踪
- 默认执行前置条件硬规则如下，以下情况默认不允许执行，只能返回 `precheck_failed` 或 `manual_required`：
  - `plan_status != planned`
  - 关联 `normalized_record` 不存在
  - `recognition_status == failed`
  - 关键字段缺失
  - executor 与 `module_code / task_type` 不匹配

### 6.4 visit executor 第一版

visit executor 的完整目标动作包括：

- 打开 PTS 交付链接
- 创建回访工单
- 指派舒磊
- 根据回访类型选择工单类型
- 标记回访对象
- 处理工单
- 填满意度和反馈
- 完成回访
- 返回回访工单链接

但本阶段优先只做：

- 动作编排模型
- precheck
- dry-run
- task_run 写库

建议把 visit executor 拆成两层：

- `VisitExecutor`
  - 对外执行器接口
- `VisitExecutionPlanner` 或 `VisitActionBuilder`
  - 根据 `task_plan` + `normalized_record` 生成动作序列

这样后续接真实线上动作时，只需要把 action runner 补上，不必重写编排层。

### 6.5 inspection / proactive executor

本阶段为以下执行器建立统一骨架和 stub：

- `inspection_executor`
- `proactive_executor`

要求：

- 具备统一接口
- 能返回标准 dry-run / not-implemented 结果
- 能写入 `task_runs`
- 不要求本阶段接通真实线上执行

### 6.6 API 设计

本阶段新增以下 API：

- `POST /api/tasks/{task_id}/precheck`
- `POST /api/tasks/{task_id}/execute`
- `GET /api/task-runs/{run_id}`

建议执行接口支持请求体：

- `dry_run: bool = False`

也可以明确拆分：

- `POST /api/tasks/{task_id}/precheck`
- `POST /api/tasks/{task_id}/execute` with `dry_run=true/false`

返回结构建议包含：

- `task_run_id`
- `task_plan_id`
- `run_status`
- `manual_required`
- `result_payload`
- `final_link`
- `error_message`

### 6.7 测试策略

本阶段测试以 fake / stub 执行器为主，不把真实线上 PTS 或工单系统作为前提。

至少覆盖：

- executor dry-run
- precheck 失败
- execute 成功
- execute 失败
- execute 返回 manual_required
- task_runs 写库
- visit executor 第一版联调测试

必要时补充：

- 非 `planned` task 不允许执行
- `recognition_status=failed` 的关联记录不允许执行
- 任务与 executor 类型不匹配时报错

## 7. 分步骤实施计划

### 步骤 1：统一 schema 与契约

- 定义 executor 协议
- 定义 precheck / execute / dry-run 结果结构
- 明确 `run_status` 枚举

### 步骤 2：增强 repository 与 service

- `task_run_repo` 增加创建与按 ID 查询能力
- `task_plan_repo` / `normalized_record_repo` 补充执行链路所需读取能力
- 新增执行主链路 service

### 步骤 3：实现 visit executor 第一版

- 落动作编排模型
- 实现 precheck
- 实现 dry-run
- 实现最小 execute 行为或受控 stub
- 写入 `task_runs`

### 步骤 4：补 inspection / proactive executor 骨架

- 对齐统一接口
- 返回可审计 stub 结果

### 步骤 5：扩展 API

- 新增 `/api/tasks/{task_id}/precheck`
- 新增 `/api/tasks/{task_id}/execute`
- 新增 `/api/task-runs/{run_id}`

### 步骤 6：补测试

- dry-run 测试
- precheck 失败测试
- execute 成功 / 失败 / manual_required 测试
- task_runs 写库测试
- visit executor 第一版联调测试

### 步骤 7：验证与收尾

- 跑语法和自动化测试
- 如条件允许做最小手工 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase6-executors.md`
- `services/executors/base.py`
- `services/executors/visit_executor.py`
- `services/executors/inspection_executor.py`
- `services/executors/proactive_executor.py`
- 可能新增：
  - `services/executors/schemas.py`
  - `services/executors/visit_actions.py`
  - `services/task_execution_service.py`
- `repositories/task_run_repo.py`
- `repositories/task_plan_repo.py`
- `repositories/normalized_record_repo.py`
- `schemas/task.py`
- `schemas/common.py`
- 可能新增 `schemas/task_run.py`
- `apps/api/router.py`
- `apps/api/routers/tasks.py`
- 可能新增 `apps/api/routers/task_runs.py`
- `tests/`

## 9. 风险与缓解

### 风险 1：执行器过早耦合真实线上系统

缓解：

- 本阶段优先做 precheck / dry-run / 编排模型
- 真实线上动作只做受控最小化接入

### 风险 2：没有严格执行前检查，导致误执行

缓解：

- 强制先做 `precheck()`
- 非 `planned` task 默认不允许执行
- 关键字段缺失时直接进入 `precheck_failed` 或 `manual_required`

### 风险 3：task_runs 审计信息不足，后续难排障

缓解：

- 统一结果结构
- 保证每次执行都有 `task_run`
- 在 `result_payload` 中记录动作轨迹和关键信息

### 风险 4：三个 executor 的接口逐渐分叉

缓解：

- 先定义统一契约
- visit 先做深一点，inspection / proactive 先按同一协议做 stub

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已建立统一 `BaseExecutor` 契约，包含：
  - `precheck()`
  - `execute()`
  - `dry_run()`
  - `healthcheck()`
- 已建立统一执行结果结构，包含：
  - `run_status`
  - `manual_required`
  - `result_payload`
  - `final_link`
  - `error_message`
- 执行主链路已接通：
  - `task_plans`
  - `task_runs`
- 已支持 dry-run
- 执行前会先 precheck
- 执行结果可写入 `task_runs`
- 成功、失败、人工处理都可审计
- visit executor 第一版已完成：
  - 动作编排模型
  - precheck
  - dry-run
  - task_run 写库
- inspection / proactive executor 已建立统一骨架和 stub
- 已新增 API：
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
  - `GET /api/task-runs/{run_id}`
- 测试覆盖：
  - executor dry-run
  - precheck 失败
  - execute 成功 / 失败 / 人工处理
  - task_runs 写库
  - visit executor 第一版联调测试
- 实施完成后，必须贴出 4 类执行结果样例：
  - `precheck_failed`
  - `dry_run_ready`
  - `success` 或 `simulated_success`
  - `manual_required`
- 每条样例至少包含：
  - `task_run_id`
  - `task_plan_id`
  - `run_status`
  - `manual_required`
  - `result_payload`
  - `final_link`
  - `error_message`

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证 executor 契约与结果结构
2. 验证 `precheck()` 失败分支
3. 验证 `dry_run()` 返回结构和 task_run 写库
4. 验证 `execute()` 的成功 / 失败 / manual_required
5. 验证 `GET /api/task-runs/{run_id}` 详情查询
6. 验证 visit executor 第一版联调链路
7. 运行自动化测试
8. 如有必要补最小手工 smoke

## 12. 实施记录

### 实际完成内容

- 建立了统一执行器契约 [`services/executors/base.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/base.py)，包含：
  - `precheck()`
  - `execute()`
  - `dry_run()`
  - `healthcheck()`
- 建立了统一执行结果结构 [`services/executors/schemas.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/schemas.py)，覆盖：
  - `run_status`
  - `manual_required`
  - `result_payload`
  - `final_link`
  - `error_message`
  - `executor_version`
- 打通了 `task_plans -> task_runs` 执行主链路 [`services/task_execution_service.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/task_execution_service.py)：
  - 执行前统一做 generic precheck
  - 再做 executor-specific precheck
  - 支持 dry-run
  - 支持 simulated execute
  - 所有结果都写入 `task_runs`
- 落实了执行前硬规则，以下情况默认不允许执行并返回 `precheck_failed`：
  - `plan_status != planned`
  - 关联 `normalized_record` 不存在
  - `recognition_status == failed`
  - executor 与 `module_code / task_type` 不匹配
  - visit executor 关键字段缺失
- 完成了 visit executor 第一版：
  - [`services/executors/visit_actions.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/visit_actions.py) 中实现动作编排模型
  - [`services/executors/visit_executor.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/visit_executor.py) 中实现：
    - precheck
    - dry-run
    - simulated execute
    - simulated final_link 生成
- 为 inspection / proactive executor 建立了统一骨架与 stub：
  - [`services/executors/inspection_executor.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/inspection_executor.py)
  - [`services/executors/proactive_executor.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/proactive_executor.py)
- 扩展了 task_runs repository 与 schema：
  - [`repositories/task_run_repo.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/repositories/task_run_repo.py)
  - [`schemas/common.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/schemas/common.py)
  - [`schemas/task.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/schemas/task.py)
- 新增执行 API：
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
  - `GET /api/task-runs/{run_id}`
- 新增 Phase 6 测试：
  - dry-run
  - precheck_failed
  - simulated_success
  - failed
  - manual_required
  - `task_runs` 写库
  - task_run detail 查询

### 与原计划偏差

- 第一版 `execute()` 默认统一走 simulated/stub 路径，没有接真实线上外部系统；这与阶段开始前新增的安全边界约束一致，不构成负偏差
- `run_status` 在计划建议值基础上补充了 `precheck_passed` 与 `simulated_success`，用于更清晰地区分预检查成功和受控模拟成功；这是为了让执行审计更可读，属于正向补强
- inspection / proactive 本阶段保持 stub，但已经能返回可审计的 `failed` / `manual_required` 结果并写入 `task_runs`

### 验证结果

- 语法校验：
  - `python3 -m compileall apps services tests`
  - 结果：通过
- 自动化测试：
  - `.venv/bin/pytest -q`
  - 结果：`30 passed`
- 最小执行链路样例验证：
  - 使用临时 PostgreSQL + TestClient 跑通：
    - `precheck_failed`
    - `dry_run_ready`
    - `simulated_success`
    - `manual_required`
  - 结果：四类样例均成功写入 `task_runs`

### 待跟进事项

- 后续如果进入真实执行阶段，需要在显式开关保护下，把 visit executor 的 action runner 接到真实外部系统
- inspection / proactive executor 当前仍为 stub，后续需要各自补动作编排和真实前置条件
- 当前执行 API 还没有前端工作台承接，后续进入前端阶段再做联调

## 13. 遗留问题

- 真实外部系统执行仍未接入，当前默认仅支持 dry-run / simulated execute / stub
- task_runs 当前只保存结果审计，尚未补重试策略、并发保护和幂等控制
- 如果后续真实执行需要上传文件、处理浏览器状态或接复杂权限，还需要额外扩展执行上下文与审计字段

# Phase 9 收尾子计划：Proactive Real Execution Polish

计划文件路径：`plans/2026-04-01-phase9-proactive-real-execution-polish.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-01-phase9-proactive-real-execution-polish.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的调度、重试、审计与运营基础能力已经落地：

- APScheduler 已接入
- sync / execute 已具备自动重试与手动 rerun
- 工作台已具备最小运营视图
- `task_runs` 已承载执行审计

在执行层方面，目前已经有：

- visit executor 的真实联调能力
- inspection executor 的真实联调能力
- proactive executor 仍停留在 Phase 6 的 stub 状态

当前 proactive 模块已经具备：

- collector / recognizer / planner 主链路
- `proactive_visit_close` task 生成
- `task_runs` 执行审计落库

但 proactive executor 目前只支持：

- `precheck_passed`
- `dry_run_ready`
- `manual_required`

还没有：

- proactive 专属真实执行开关
- proactive 的最小真实执行路径
- proactive 真实执行审计
- proactive 真实联调测试

本次任务不再是新大阶段，而是 Phase 9 范围内的新收尾子任务，目标是把当前 proactive executor 从 stub / simulated 能力推进到真实联调可用状态，同时保持可控开关和安全边界。

## 2. 目标

本次实施完成后，应达到以下目标：

- 梳理 proactive 真实执行动作链
- 固化真实执行的安全边界和 precheck 硬条件
- 接入 proactive 专属真实执行开关
- 实现 proactive 的最小真实执行路径
- 保留 simulated / manual_required fallback
- 增强 proactive 真实执行审计
- 补自动化测试和最小 smoke 验证方案

## 3. 范围

本次实施范围包含：

- 重新梳理 proactive executor 动作链与执行阶段划分
- 设计并实现 proactive 真实执行开关
- 明确 proactive precheck 的硬条件
- 在现有 `ProactiveExecutor` 之上增加最小真实执行路径
- 保留并验证 simulated / manual_required fallback
- 增强 `task_runs` 中 proactive 执行审计信息
- 增加 proactive 真实联调相关测试或最小 smoke 验证方案
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- visit / inspection 真实执行增强
- 前端复杂执行编排界面
- 工单系统复杂审批分支
- 更重的权限审批流
- 外部告警与通知平台

## 5. 当前现状 / 已知问题

当前现状：

- `ProactiveExecutor` 仍然是 stub
- 当前 execute 默认直接返回 `manual_required`
- proactive planner 已能稳定产出 `proactive_visit_close`
- `task_runs` 已能记录 proactive 的执行审计

已知问题：

- proactive 真实执行动作还没有和真实 runner / 真实接口接起来
- 当前缺少 proactive 专属真实执行开关和 endpoint 配置
- precheck 对“真实执行前的外部依赖可用性”还没有体现
- 当前 execute 的审计结果没有 action-level 的 `runner_diagnostics`
- 自动化测试目前还没有 proactive 的真实 runner 路径

## 6. 技术方案

### 6.1 真实执行目标定义

本次“真实联调可用”不等于“一次性打通 proactive 全量线上动作细节”，而是指：

- executor 结构能切换到真实执行模式
- precheck 条件清晰
- 至少有一条最小真实执行路径可工作
- 真实执行失败时能清晰审计
- 仍可安全回退到 simulated / manual_required

换句话说，本次优先把 proactive 真实执行框架与最小链路做对，而不是一次性覆盖复杂业务分支。

### 6.2 开关与安全边界

建议采用双层开关：

- 全局开关：
  - `ENABLE_REAL_EXECUTION`
- proactive 专属开关：
  - `PROACTIVE_REAL_EXECUTION_ENABLED`

同时要求：

- 全局开关未开启时，proactive 一律只能 simulated
- proactive 专属开关未开启时，proactive 一律只能 simulated
- 只有两个开关都开启时，才允许进入真实执行路径

必要时可再加：

- `PROACTIVE_REAL_EXECUTION_DRY_GATE`
  - 限制仅对特定环境或特定数据生效

### 6.3 Precheck 硬条件

真实执行前，proactive 必须满足以下硬条件：

- `plan_status == planned`
- 关联 `normalized_record` 存在
- `recognition_status != failed`
- `customer_name` 存在
- `liaison_status == "已建联"`
- `visit_link` 为空
- `feedback_note` 或可用备注信息存在
- executor 与 `module_code / task_type` 匹配

如有必要，本次可再补：

- 真实执行环境配置完整
- 认证/会话可用
- 必需 endpoint / runner 配置完整

任何一项不满足，都不能进入真实执行，只能：

- `precheck_failed`
- 或 `manual_required`

### 6.4 最小真实执行路径

本次 proactive 最小真实执行路径聚焦：

1. 创建客户满意度调研类型工单
2. 指派舒磊
3. 写入反馈备注
4. 返回真实或准真实 `final_link`

建议将 proactive 真实执行拆成以下层次：

- `ProactiveExecutor`
  - 负责统一入口、precheck、模式切换
- `ProactiveRealRunner`
  - 负责真实动作落地

第一版最小真实执行路径的验收标准固定为：

- 能成功进入 `ProactiveRealRunner`
- 能成功执行“创建客户满意度调研类型工单 + 指派舒磊 + 写入反馈备注”
- 能返回真实或准真实 `final_link`
- 失败时有完整 `runner_diagnostics`

### 6.5 simulated / manual_required fallback

即使进入 Phase 9 收尾，本次仍必须保留 simulated / manual_required fallback：

- 开关未开启时自动走 simulated
- 业务条件不满足时维持 `manual_required`
- 真实 runner 不可用时可返回 `precheck_failed`

本次倾向：

- 配置未开启：直接 simulated
- 业务条件不满足：`manual_required`
- 配置开启但真实 runner 缺失或关键依赖不满足：`precheck_failed`
- 配置开启且依赖满足：进入真实执行路径

### 6.6 审计增强

真实执行结果的 `task_runs.result_payload` 建议新增：

- `execution_mode`
  - `simulated`
  - `real`
  - `real_attempted`
- `action_trace`
- `action_results`
- `precheck_summary`
- `runner_diagnostics`
- `real_execution_enabled`

同时要求：

- 成功时记录真实或准真实 `final_link`
- 失败时记录失败动作、错误阶段、可否重试
- 必要时记录真实 runner 版本

### 6.7 配置注入方式

proactive 真实执行所需配置应统一通过：

- 环境变量
- `extra_config`
- 统一 settings 注入

不允许在 executor 内硬编码真实 URL、cookie、token、账号信息。

本次可能新增：

- `PROACTIVE_REAL_EXECUTION_ENABLED`
- `PROACTIVE_REAL_BASE_URL`
- `PROACTIVE_REAL_TOKEN`
- `PROACTIVE_REAL_TOKEN_HEADER`
- `PROACTIVE_REAL_CREATE_ENDPOINT`
- `PROACTIVE_REAL_ASSIGN_ENDPOINT_TEMPLATE`
- `PROACTIVE_REAL_FEEDBACK_ENDPOINT_TEMPLATE`
- `PROACTIVE_REAL_FINAL_LINK_PATH`

### 6.8 测试与验证策略

本次测试优先分两层：

- 自动化测试
  - simulated fallback
  - 开关关闭时不进入真实执行
  - precheck 硬条件失败时 `precheck_failed`
  - 开关开启但配置缺失时返回 `precheck_failed`
  - fake/mock real runner 成功时写入真实模式审计
- 最小手工 smoke
  - 在可控环境开启真实开关
  - 跑一条 proactive task
  - 验证真实 final link 或真实 runner diagnostics

不把“真实线上环境一定可达”作为 CI 硬前提。

## 7. 分步骤实施计划

### 步骤 1：梳理 proactive 动作链和真实执行边界

- 明确最小真实执行路径
- 固定真实执行安全边界

### 步骤 2：补配置与开关

- 增加 proactive 专属真实执行开关
- 设计真实 runner 配置注入方式
- 明确缺配置时的行为

### 步骤 3：实现真实 runner 抽象

- 抽出 `ProactiveRealRunner`
- 约束输入输出结构
- 支持 fake/mock real runner 以便测试

### 步骤 4：接入 ProactiveExecutor

- precheck 增强
- execute 模式切换：
  - simulated
  - real
- 失败时审计结构补强

### 步骤 5：补测试与 smoke

- simulated fallback 测试
- 真实开关关闭测试
- 真实配置缺失测试
- fake real runner 成功测试
- 最小手工 smoke 方案整理

### 步骤 6：验证与收尾

- 跑语法与自动化测试
- 视环境做最小真实联调 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-proactive-real-execution-polish.md`
- `services/executors/proactive_executor.py`
- 可能新增：
  - `services/executors/proactive_real_runner.py`
- `services/executors/schemas.py`
- `services/task_execution_service.py`
- `core/config.py`
- `.env.example`
- `tests/test_executors_api.py`
- `tests/conftest.py`

## 9. 风险与缓解

### 风险 1：真实执行过早碰到外部工单系统复杂依赖

缓解：

- 先做最小真实执行路径
- 保持 simulated / manual_required fallback
- 用开关严格控制

### 风险 2：真实执行误触发线上动作

缓解：

- 双层开关
- 严格 precheck
- 默认关闭真实执行

### 风险 3：测试难以稳定

缓解：

- 用 fake/mock runner 做自动化测试
- 真实联调仅做最小 smoke

### 风险 4：审计不完整导致联调难排查

缓解：

- 强制记录 `action_trace`、`action_results`、`runner_diagnostics`
- 统一写入 `task_runs.result_payload`

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已梳理并固化 proactive 真实执行动作链
- 已接入 proactive 真实执行开关
- 已明确 precheck 硬条件并落实到代码
- 已实现 proactive 最小真实执行路径：
  - 创建客户满意度调研类型工单
  - 指派舒磊
  - 写入反馈备注
  - 返回真实或准真实 `final_link`
- proactive 第一版最小真实执行路径至少满足：
  - 能成功进入 `ProactiveRealRunner`
  - 能成功执行“创建客户满意度调研类型工单 + 指派舒磊 + 写入反馈备注”
  - 能返回真实或准真实 `final_link`
  - 失败时有完整 `runner_diagnostics`
- 已保留 simulated / manual_required fallback
- 已增强 proactive 真实执行审计结果
- 自动化测试已覆盖：
  - simulated fallback
  - 开关关闭
  - 配置缺失
  - fake/mock real runner 成功
- 已给出最小真实联调 smoke 验证步骤
- 实施完成后，至少贴出 3 类结果样例：
  - simulated
  - precheck_failed
  - real 或 real_attempted
  每条至少包含：
  - `task_run_id`
  - `run_status`
  - `result_payload`
  - `final_link`
  - `error_message`
  - `execution_mode`
  - `runner_diagnostics`

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证开关关闭时 proactive 仍走 simulated
2. 验证 precheck 硬条件不满足时不进入真实执行
3. 验证真实配置缺失时返回 `precheck_failed`
4. 验证 fake/mock real runner 成功时写入真实执行审计
5. 运行自动化测试
6. 如环境可用，执行一次最小真实联调 smoke

## 12. 实施记录

### 实际完成内容

- 新增 `ProactiveRealRunner`，接入 proactive 第一版真实执行链路：
  - 创建客户满意度调研类型工单
  - 指派舒磊
  - 写入反馈备注
  - 返回真实或准真实 `final_link`
- 为 proactive 增加双开关控制：
  - `ENABLE_REAL_EXECUTION`
  - `PROACTIVE_REAL_EXECUTION_ENABLED`
- 为 proactive 增加真实执行配置：
  - `proactive_real_base_url`
  - `proactive_real_token`
  - `proactive_real_token_header`
  - `proactive_real_create_endpoint`
  - `proactive_real_assign_endpoint_template`
  - `proactive_real_feedback_endpoint_template`
  - `proactive_real_final_link_path`
  - `proactive_real_timeout_seconds`
  - `proactive_real_verify_ssl`
- `ProactiveExecutor` 已从 phase6 的 stub 版本升级为 phase9 真实联调版本：
  - 保留 simulated fallback
  - 保留 manual_required fallback
  - 新增 real precheck 校验
  - 新增 `action_trace` / `action_results` / `runner_diagnostics`
- proactive precheck 硬条件已收紧：
  - `customer_name`
  - `liaison_status == 已建联`
  - `visit_link` 为空
  - `feedback_note` 存在
  - executor 与 `module_code / task_type` 匹配
- 新增 fake proactive real server 测试夹具
- 新增 proactive executor 联调测试：
  - simulated fallback
  - `manual_required`
  - `precheck_failed`
  - real success

### 与原计划偏差

- 本轮按原计划完成了 proactive 第一版最小真实执行路径，没有扩展到更复杂的审批或权限分支。
- `manual_required` fallback 本轮采用“缺少联系人信息”这一保守规则落地，作为人工兜底入口，避免把所有边界都压成 `precheck_failed`。

### 验证结果

- `python3 -m compileall /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/proactive_executor.py /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/executors/proactive_real_runner.py /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/conftest.py /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/test_executors_api.py /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/test_ops_platform.py /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/core/config.py`
- `python3 -m compileall /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/core /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/models /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/repositories /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/schemas /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/scheduler /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests`
- `.venv/bin/python -m pytest /Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/test_executors_api.py -q`
  - 结果：`21 passed`
- `.venv/bin/python -m pytest -q`
  - 结果：`62 passed in 17.59s`

### 待跟进事项

- proactive 真实执行目前已覆盖：
  - 创建客户满意度调研类型工单
  - 指派舒磊
  - 写入反馈备注
  - 返回 `final_link`
- 仍未覆盖的 proactive 真实动作：
  - 更复杂的负责人补充策略
  - 更细的权限失败分类
  - 如果外部系统需要额外状态确认的二次校验
- 如果下一轮继续收尾，最自然的方向是补 proactive 的失败分类细化和更丰富的业务校验。

## 13. 遗留问题

- proactive 第一版真实执行依赖外部工单接口约定，当前自动化测试通过 fake real server 验证，真实线上字段细节仍需最小 smoke 继续收口。

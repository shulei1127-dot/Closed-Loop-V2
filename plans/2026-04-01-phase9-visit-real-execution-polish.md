# Phase 9 收尾子计划：Visit Real Execution Polish

计划文件路径：

- `plans/2026-04-01-phase9-visit-real-execution-polish.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的调度、重试、审计与运营基础能力已经落地：

- APScheduler 已接入
- sync / execute 已具备自动重试与手动 rerun
- 工作台已具备最小运营视图
- `task_runs` 已承载执行审计

在执行层方面，当前 `visit executor` 已经具备：

- 完整的动作编排模型
- 严格的 precheck
- dry-run
- simulated execute
- `task_runs` 审计落库

但它距离“真实联调可用”还差最后一段关键链路：

- 真实外部动作尚未真正接通
- `ENABLE_REAL_EXECUTION` 只是总开关，还没有形成更细的 visit 真实执行控制
- 没有最小真实执行路径
- 没有对真实执行结果做更明确的审计结构

本次任务不再是新大阶段，而是 Phase 9 范围内的收尾冲刺子任务，目标是把当前 visit executor 从 simulated execute 推进到真实联调可用状态，同时保持可控开关和安全边界。

## 2. 目标

本次实施完成后，应达到以下目标：

- 梳理 visit 真实执行动作链
- 明确 visit 真实执行的安全边界
- 在现有 executor 结构上接入真实执行开关
- 明确 precheck 必须通过的条件
- 实现最小真实执行路径
- 保留 simulated execute 回退能力
- 对真实执行结果补强审计结构
- 补充真实联调测试或最小 smoke 验证步骤

## 3. 范围

本次实施范围包含：

- 重新梳理 visit executor 动作链与执行阶段划分
- 设计并实现 visit 真实执行开关
- 明确 visit precheck 的硬条件
- 在现有 `VisitExecutor` / `VisitActionBuilder` 之上增加最小真实执行路径
- 保留并验证 simulated execute fallback
- 增强 `task_runs` 中 visit 执行审计信息
- 增加真实联调相关测试或最小 smoke 验证方案
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- inspection / proactive 真实执行增强
- 前端复杂执行编排界面
- 大规模 PTS 全量自动化覆盖
- 复杂浏览器池/账号池
- 外部告警平台
- 更重的权限审批流

## 5. 当前现状 / 已知问题

当前现状：

- `VisitExecutor` 已支持：
  - `precheck()`
  - `dry_run()`
  - `execute()`
- 当前 `execute()` 默认返回 `simulated_success`
- `ENABLE_REAL_EXECUTION=false` 已作为全局安全总开关存在
- `VisitActionBuilder` 已能构建：
  - 打开 PTS 链接
  - 创建工单
  - 指派舒磊
  - 标记回访对象
  - 填反馈
  - 完成回访

已知问题：

- 真实执行动作还没有和真实 transport / 页面操作真正接起来
- 当前没有 visit 专属的执行器开关、endpoint 配置或真实 runner 注入方式
- precheck 已经较严格，但还需要再明确“真实执行前置条件”的边界
- 真实执行结果目前还没有比 simulated 模式更细的 action-level 审计
- 当前测试主要覆盖 simulated 行为，缺少真实联调路径的结构化验证

## 6. 技术方案

### 6.1 真实执行目标定义

本次“真实联调可用”不等于“一次性打通所有 visit 线上动作细节”，而是指：

- executor 结构能切换到真实执行模式
- precheck 条件清晰
- 至少有一条最小真实执行路径可工作
- 真实执行失败时能清晰审计
- 仍可安全回退到 simulated execute

换句话说，本次优先把真实执行框架与最小链路做对，而不是一次性覆盖所有复杂线上分支。

### 6.2 开关与安全边界

建议采用双层开关：

- 全局开关：
  - `ENABLE_REAL_EXECUTION`
- visit 专属开关：
  - 例如 `VISIT_REAL_EXECUTION_ENABLED`

同时要求：

- 全局开关未开启时，visit 一律只能 simulated
- visit 专属开关未开启时，visit 一律只能 simulated
- 只有两个开关都开启时，才允许进入真实执行路径

必要时可再加：

- `VISIT_REAL_EXECUTION_DRY_GATE`
  - 限制仅对特定测试数据、特定环境生效

### 6.3 Precheck 硬条件

真实执行前，visit 必须满足以下硬条件：

- `plan_status == planned`
- 关联 `normalized_record` 存在
- `recognition_status != failed`
- `customer_name` 存在
- `pts_link` 存在
- `delivery_id` 存在
- `visit_owner == "舒磊"`
- `visit_status == "已回访"`
- `visit_link` 为空
- executor 与 `module_code / task_type` 匹配

如有必要，本次可再补：

- 真实执行环境配置完整
- 认证/会话可用
- 必需 endpoint / runner 配置完整

任何一项不满足，都不能进入真实执行，只能：

- `precheck_failed`
- 或 `manual_required`

### 6.4 最小真实执行路径

本次建议把 visit 真实执行拆成以下层次：

- `VisitExecutor`
  - 负责统一入口、precheck、模式切换
- `VisitActionBuilder`
  - 负责产出标准动作链
- `VisitRealRunner`
  - 负责真实动作落地

本次最小真实执行路径建议聚焦：

1. 打开 PTS 交付链接
2. 创建回访工单
3. 返回真实或准真实的工单链接

后续更复杂步骤如：

- 指派舒磊
- 选择工单类型
- 标记回访对象
- 填满意度和反馈
- 完成回访

可以先保留在动作链中，并在真实模式下分阶段接通。

这样做的原因是：

- 先打通“最小可验证闭环”
- 降低一次性联调风险
- 保留现有动作编排结构

本次第一版“最小真实执行路径”的验收标准固定为：

- 能成功进入真实 runner
- 能成功执行“打开 PTS 链接 + 创建回访工单”
- 能返回真实或准真实 `final_link`
- 失败时有完整 `runner_diagnostics`

### 6.5 simulated fallback

即使进入 Phase 9 收尾，本次仍必须保留 simulated fallback：

- 开关未开启时自动 fallback
- 真实 runner 不可用时可返回 `manual_required` 或 fallback simulated
- 在本地与 CI 测试中，默认仍以 simulated / fake transport 为主

本次倾向：

- 配置未开启：直接 simulated
- 配置开启但真实 runner 缺失或关键依赖不满足：`manual_required`
- 配置开启且依赖满足：进入真实执行路径

### 6.6 审计增强

真实执行结果的 `task_runs.result_payload` 建议新增：

- `execution_mode`
  - `simulated`
  - `real`
- `action_trace`
- `action_results`
- `precheck_summary`
- `runner_diagnostics`
- `real_execution_enabled`

同时要求：

- 成功时记录真实 final link
- 失败时记录失败动作、错误阶段、可否重试
- 必要时记录真实 runner 版本

### 6.7 配置注入方式

visit 真实执行所需配置应统一通过：

- 环境变量
- `extra_config`
- 统一 settings 注入

不允许在 executor 内硬编码真实 URL、cookie、token、账号信息。

本次可能新增：

- `VISIT_REAL_EXECUTION_ENABLED`
- `VISIT_REAL_BASE_URL`
- `VISIT_REAL_TOKEN`
- 或等价配置项

### 6.8 测试与验证策略

本次测试优先分两层：

- 自动化测试
  - simulated fallback
  - 开关关闭时不进入真实执行
  - 开关开启但配置缺失时返回 `manual_required` 或 `precheck_failed`
  - 真实 runner fake/mock 成功时写入真实模式审计
- 最小手工 smoke
  - 在可控环境开启真实开关
  - 跑一条 visit task
  - 验证真实 final link 或真实 runner diagnostics

不把“真实线上环境一定可达”作为 CI 硬前提。

## 7. 分步骤实施计划

### 步骤 1：梳理 visit 动作链和真实执行边界

- 明确哪些动作保留 simulated
- 明确最小真实执行路径
- 固定真实执行安全边界

### 步骤 2：补配置与开关

- 增加 visit 专属真实执行开关
- 设计真实 runner 配置注入方式
- 明确缺配置时的行为

### 步骤 3：实现真实 runner 抽象

- 抽出 `VisitRealRunner`
- 约束输入输出结构
- 支持 fake/mock real runner 以便测试

### 步骤 4：接入 VisitExecutor

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

- `plans/2026-04-01-phase9-visit-real-execution-polish.md`
- `services/executors/visit_executor.py`
- `services/executors/visit_actions.py`
- 可能新增：
  - `services/executors/visit_real_runner.py`
  - `services/executors/visit_real_client.py`
- `services/executors/schemas.py`
- `services/task_execution_service.py`
- `core/config.py`
- `.env.example`
- `tests/test_executors_api.py`
- 可能新增 visit real execution 专项测试文件

## 9. 风险与缓解

### 风险 1：真实执行过早碰到复杂线上依赖

缓解：

- 先做最小真实执行路径
- 保持 simulated fallback
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

- 强制记录 action trace、action results、runner diagnostics
- 统一写入 `task_runs.result_payload`

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已梳理并固化 visit 真实执行动作链
- 已接入 visit 真实执行开关
- 已明确 precheck 硬条件并落实到代码
- 已实现最小真实执行路径
- 已保留 simulated execute fallback
- 已增强 visit 真实执行审计结果
- 第一版最小真实执行路径满足：
  - 能成功进入真实 runner
  - 能成功执行“打开 PTS 链接 + 创建回访工单”
  - 能返回真实或准真实 `final_link`
  - 失败时有完整 `runner_diagnostics`
- 自动化测试已覆盖：
  - simulated fallback
  - 开关关闭
  - 配置缺失
  - fake/mock real runner 成功
- 已给出最小真实联调 smoke 验证步骤
- 实施完成后，至少贴出 3 类真实执行结果样例：
  - simulated
  - manual_required
  - real 或 real_attempted
- 每条样例至少包含：
  - `task_run_id`
  - `run_status`
  - `result_payload`
  - `final_link`
  - `error_message`
  - `execution_mode`
  - `runner_diagnostics`

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证开关关闭时 visit 仍走 simulated
2. 验证 precheck 硬条件不满足时不进入真实执行
3. 验证真实配置缺失时返回 `precheck_failed` 或 `manual_required`
4. 验证 fake/mock real runner 成功时写入真实执行审计
5. 运行自动化测试
6. 如环境可用，执行一次最小真实联调 smoke

## 12. 实施记录（先留空）

- 已新增 visit 专属真实执行配置与开关：
  - `VISIT_REAL_EXECUTION_ENABLED`
  - `VISIT_REAL_BASE_URL`
  - `VISIT_REAL_TOKEN`
  - `VISIT_REAL_TOKEN_HEADER`
  - `VISIT_REAL_CREATE_ENDPOINT`
  - `VISIT_REAL_FINAL_LINK_PATH`
  - `VISIT_REAL_TIMEOUT_SECONDS`
  - `VISIT_REAL_VERIFY_SSL`
- 已新增 `VisitRealRunner`
  - 支持最小真实执行路径：
    - 打开 PTS 链接
    - 创建回访工单
  - 成功时返回真实或准真实 `final_link`
  - 失败时输出完整 `runner_diagnostics`
- 已重构 `VisitExecutor`
  - precheck 增强，明确真实执行硬条件
  - execute 支持：
    - simulated fallback
    - real success
    - real_attempted failed
  - 所有 visit 执行结果统一补充：
    - `execution_mode`
    - `action_trace`
    - `action_results`
    - `runner_diagnostics`
- 已补 visit 真实执行专项测试：
  - simulated fallback
  - 真实开关开启但配置缺失
  - unsupported `visit_type` 的 `manual_required`
  - fake real runner success
  - fake real runner failure

### 实际完成内容

- 新增文件：
  - `services/executors/visit_real_runner.py`
- 更新文件：
  - `services/executors/visit_executor.py`
  - `core/config.py`
  - `.env.example`
  - `tests/conftest.py`
  - `tests/test_executors_api.py`
- 已将 `VisitExecutor` 版本提升为 `phase9-visit-real-v1`
- 真实执行路径当前已能在 fake real server 上完成：
  - 进入真实 runner
  - GET 打开 PTS 链接
  - POST 创建回访工单
  - 回填 `final_link`
- 在本轮追加完成：
  - POST `assign_owner`
  - POST `complete_visit`
  - 将真实最小路径从 2 步扩展到 4 步
  - 将失败诊断细化到 `failed_action`
- 在本轮继续追加完成：
  - POST `mark_visit_target`
  - POST `fill_feedback`
  - 将真实最小路径从 4 步扩展到 6 步
  - success / failed 的 `action_results` 与 `runner_diagnostics` 继续补齐

### 与原计划偏差

- 第一版真实路径只接通了最小链路：
  - 打开 PTS 链接
  - 创建回访工单
- 尚未把以下动作变成真实线上动作：
  - 标记回访对象
  - 填满意度与反馈
- 这些动作仍保留在 `action_trace` 中，便于后续继续扩展
- 配置注入本次优先使用 settings / env，未额外引入更复杂的模块级 executor 配置模型
- 本轮只扩到你要求的两步：
  - `assign_owner`
  - `complete_visit`
- 没有继续扩到 `mark_visit_target` 和 `fill_feedback`
- 本轮继续只扩到你要求的两步：
  - `mark_visit_target`
  - `fill_feedback`
- 没有进一步扩 scope，也没有改动 inspection / proactive
- 当前 visit 真实执行链路已经覆盖 action builder 中的 6 个动作里的 6/6 审计链条，但真实线上语义仍属于“最小可联调版本”

### 验证结果

- `python3 -m compileall core services tests` 通过
- `python3 -m compileall services/executors tests` 通过
- `.venv/bin/python -m pytest tests/test_executors_api.py -q` 通过
- `.venv/bin/python -m pytest -q` 通过
- 测试结果：`52 passed`
- 已验证：
  - simulated fallback
  - real config missing -> `precheck_failed`
  - unsupported `visit_type` -> `manual_required`
  - fake real runner success -> `success`
  - fake real runner failure -> `failed` with `execution_mode=real_attempted`
  - fake real runner `assign_owner` failure -> `failed`
  - fake real runner `complete_visit` failure -> `failed`
- 本轮再次验证：
  - fake real runner `mark_visit_target` failure -> `failed`
  - fake real runner `fill_feedback` failure -> `failed`
  - real success 已包含 6 个动作中的真实执行结果
- 最新测试结果：`54 passed`

### 待跟进事项

- 若继续收尾，可把 visit 后续动作逐步接入真实 runner：
- 若需要更强安全控制，可增加 allowlist 或测试环境 gating
- 若未来要接真实线上会话，可补 cookie/session 注入与 runner healthcheck 深化
- 若要继续深挖 visit 真实执行，下一步更适合处理：
  - 更真实的页面状态校验
  - 更细的 real precheck
  - 真实工单字段回填校验

## 13. 遗留问题（先留空）

- 当前真实路径仍是“最小可联调”而非完整 visit 全流程
- 真实执行配置目前主要依赖环境变量
- 多环境、多账号、多会话隔离尚未在本次子任务中展开
- 真实路径已覆盖：
  - `open_pts_delivery_link`
  - `create_visit_work_order`
  - `assign_owner`
  - `mark_visit_target`
  - `fill_feedback`
  - `complete_visit`
- 仍待强化的不是动作覆盖，而是更真实的外部系统联调细节与健壮性

# Phase 9 收尾子计划：Inspection Real Execution Polish

计划文件路径：`plans/2026-04-01-phase9-inspection-real-execution-polish.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的调度、重试、审计与运营基础能力已经落地：

- APScheduler 已接入
- sync / execute 已具备自动重试与手动 rerun
- 工作台已具备最小运营视图
- `task_runs` 已承载执行审计

在执行层方面，inspection 模块目前已经具备：

- inspection planner 可基于标准化记录生成 `inspection_close`
- 巡检报告扫描、标准化和匹配能力已接入
- `InspectionExecutor` 已支持：
  - `precheck()`
  - `dry_run()`
  - `execute()` 的 simulated 路径
- 报告缺失、冲突等场景已能进入 `manual_required`

但 inspection executor 距离“真实联调可用”还差关键一步：

- 真实外部动作尚未接通
- 还没有 inspection 专属真实执行开关
- 还没有最小真实执行路径
- 真实执行结果还没有比 simulated 模式更细的 action-level 审计

本次任务不再是新大阶段，而是 Phase 9 范围内的新收尾子任务，目标是把当前 inspection executor 从 stub / simulated 能力推进到真实联调可用状态，同时保持可控开关和安全边界。

## 2. 目标

本次实施完成后，应达到以下目标：

- 梳理 inspection 真实执行动作链
- 固化真实执行的安全边界和 precheck 硬条件
- 接入 inspection 专属真实执行开关
- 实现 inspection 的最小真实执行路径
- 保留 simulated / manual_required fallback
- 增强 inspection 真实执行审计
- 补自动化测试和最小 smoke 验证方案

## 3. 范围

本次实施范围包含：

- 重新梳理 inspection executor 动作链与执行阶段划分
- 设计并实现 inspection 真实执行开关
- 明确 inspection precheck 的硬条件
- 在现有 `InspectionExecutor` 之上增加最小真实执行路径
- 保留并验证 simulated / manual_required fallback
- 增强 `task_runs` 中 inspection 执行审计信息
- 增加 inspection 真实联调相关测试或最小 smoke 验证方案
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- visit / proactive 真实执行增强
- 前端复杂执行编排界面
- 巡检报告内容解析
- 巡检工单复杂权限处理
- 自动添加成员、复杂负责人补充逻辑
- 更重的权限审批流

## 5. 当前现状 / 已知问题

当前现状：

- `InspectionExecutor` 已支持报告匹配：
  - 无报告、缺 Word/PDF、多候选冲突时直接 `manual_required`
- `InspectionExecutor` 已支持：
  - `precheck_passed`
  - `dry_run_ready`
  - `simulated_success`
- inspection 报告根目录和匹配逻辑已可配置
- `task_runs` 已能审计 inspection 的 simulated 执行结果

已知问题：

- inspection 真实执行动作还没有和真实 runner / 真实接口接起来
- 当前缺少 inspection 专属真实执行开关和 endpoint 配置
- precheck 目前对“真实执行前的外部依赖可用性”校验还不够严格
- 当前 execute 的审计结果没有 action-level 的 `runner_diagnostics`
- 自动化测试主要覆盖 simulated 与 report matching，还没有真实 runner 路径

## 6. 技术方案

### 6.1 真实执行目标定义

本次“真实联调可用”不等于“一次性打通 inspection 全量线上动作细节”，而是指：

- executor 结构能切换到真实执行模式
- precheck 条件清晰
- 至少有一条最小真实执行路径可工作
- 真实执行失败时能清晰审计
- 仍可安全回退到 simulated / manual_required

换句话说，本次优先把 inspection 真实执行框架与最小链路做对，而不是一次性覆盖复杂权限分支和负责人补充分支。

### 6.2 开关与安全边界

建议采用双层开关：

- 全局开关：
  - `ENABLE_REAL_EXECUTION`
- inspection 专属开关：
  - `INSPECTION_REAL_EXECUTION_ENABLED`

同时要求：

- 全局开关未开启时，inspection 一律只能 simulated
- inspection 专属开关未开启时，inspection 一律只能 simulated
- 只有两个开关都开启时，才允许进入真实执行路径

必要时可再加：

- `INSPECTION_REAL_EXECUTION_DRY_GATE`
  - 限制仅对特定环境或特定数据生效

### 6.3 Precheck 硬条件

真实执行前，inspection 必须满足以下硬条件：

- `plan_status == planned`
- 关联 `normalized_record` 存在
- `recognition_status != failed`
- `customer_name` 存在
- `inspection_done == true`
- `work_order_link` 或 `work_order_id` 至少存在一个
- 报告匹配结果为：
  - `matched == true`
  - `manual_required == false`
  - Word / PDF 均已齐备
- executor 与 `module_code / task_type` 匹配

如有必要，本次可再补：

- 真实执行环境配置完整
- 认证/会话可用
- 必需 endpoint / runner 配置完整

任何一项不满足，都不能进入真实执行，只能：

- `precheck_failed`
- 或 `manual_required`

### 6.4 最小真实执行路径

本次 inspection 最小真实执行路径建议聚焦：

1. 打开巡检工单链接
2. 上传已匹配的巡检报告文件
3. 完成工单处理

后续更复杂动作如：

- 指定负责人舒磊
- 若无舒磊则尝试添加成员
- 无权限分支处理

留到下一轮收尾优化。

建议将 inspection 真实执行拆成以下层次：

- `InspectionExecutor`
  - 负责统一入口、precheck、模式切换
- `InspectionRealRunner`
  - 负责真实动作落地

第一版“最小真实执行路径”的验收标准固定为：

- 能成功进入 `InspectionRealRunner`
- 能成功执行“打开巡检工单链接 + 上传已匹配报告文件 + 完成工单处理”
- 能返回真实或准真实 `final_link`
- 失败时有完整 `runner_diagnostics`

### 6.5 simulated / manual_required fallback

即使进入 Phase 9 收尾，本次仍必须保留 simulated / manual_required fallback：

- 开关未开启时自动走 simulated
- 报告匹配未通过时维持 `manual_required`
- 真实 runner 不可用时可返回 `manual_required` 或 `precheck_failed`

本次倾向：

- 配置未开启：直接 simulated
- 报告未就绪：`manual_required`
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
- `report_match`
- `runner_diagnostics`
- `real_execution_enabled`

同时要求：

- 成功时记录真实或准真实 `final_link`
- 失败时记录失败动作、错误阶段、可否重试
- 必要时记录真实 runner 版本

### 6.7 配置注入方式

inspection 真实执行所需配置应统一通过：

- 环境变量
- `extra_config`
- 统一 settings 注入

不允许在 executor 内硬编码真实 URL、cookie、token、账号信息。

本次可能新增：

- `INSPECTION_REAL_EXECUTION_ENABLED`
- `INSPECTION_REAL_BASE_URL`
- `INSPECTION_REAL_TOKEN`
- `INSPECTION_REAL_TOKEN_HEADER`
- `INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE`
- `INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE`
- 或等价配置项

### 6.8 测试与验证策略

本次测试优先分两层：

- 自动化测试
  - simulated fallback
  - 开关关闭时不进入真实执行
  - 报告匹配失败时仍然 `manual_required`
  - 开关开启但配置缺失时返回 `precheck_failed`
  - fake/mock real runner 成功时写入真实模式审计
- 最小手工 smoke
  - 在可控环境开启真实开关
  - 跑一条 inspection task
  - 验证真实 final link 或真实 runner diagnostics

不把“真实线上环境一定可达”作为 CI 硬前提。

## 7. 分步骤实施计划

### 步骤 1：梳理 inspection 动作链和真实执行边界

- 明确哪些动作保留 simulated
- 明确最小真实执行路径
- 固定真实执行安全边界

### 步骤 2：补配置与开关

- 增加 inspection 专属真实执行开关
- 设计真实 runner 配置注入方式
- 明确缺配置时的行为

### 步骤 3：实现真实 runner 抽象

- 抽出 `InspectionRealRunner`
- 约束输入输出结构
- 支持 fake/mock real runner 以便测试

### 步骤 4：接入 InspectionExecutor

- precheck 增强
- execute 模式切换：
  - simulated
  - real
- 失败时审计结构补强

### 步骤 5：补测试与 smoke

- simulated fallback 测试
- 真实开关关闭测试
- 报告未就绪测试
- 真实配置缺失测试
- fake real runner 成功测试
- 最小手工 smoke 方案整理

### 步骤 6：验证与收尾

- 跑语法与自动化测试
- 视环境做最小真实联调 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-inspection-real-execution-polish.md`
- `services/executors/inspection_executor.py`
- 可能新增：
  - `services/executors/inspection_real_runner.py`
- `services/executors/schemas.py`
- `services/task_execution_service.py`
- `services/report_matching/`
- `core/config.py`
- `.env.example`
- `tests/test_executors_api.py`
- `tests/test_report_matching.py`

## 9. 风险与缓解

### 风险 1：真实执行过早碰到复杂上传与权限依赖

缓解：

- 先做最小真实执行路径
- 保持 simulated / manual_required fallback
- 用开关严格控制

### 风险 2：报告匹配成功但真实上传接口细节复杂

缓解：

- 将上传动作封装在 real runner 中
- 用 fake/mock runner 做自动化测试
- 最小 smoke 再验证真实接口

### 风险 3：真实执行误触发线上动作

缓解：

- 双层开关
- 严格 precheck
- 默认关闭真实执行

### 风险 4：审计不完整导致联调难排查

缓解：

- 强制记录 `action_trace`、`action_results`、`runner_diagnostics`
- 统一写入 `task_runs.result_payload`

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已梳理并固化 inspection 真实执行动作链
- 已接入 inspection 真实执行开关
- 已明确 precheck 硬条件并落实到代码
- 已实现 inspection 最小真实执行路径：
  - 打开巡检工单链接
  - 上传已匹配的巡检报告文件
  - 完成工单处理
- inspection 第一版最小真实执行路径至少满足：
  - 能成功进入 `InspectionRealRunner`
  - 能成功执行“打开巡检工单链接 + 上传已匹配报告文件 + 完成工单处理”
  - 能返回真实或准真实 `final_link`
  - 失败时有完整 `runner_diagnostics`
- 已保留 simulated / manual_required fallback
- 已增强 inspection 真实执行审计结果
- 自动化测试已覆盖：
  - simulated fallback
  - 开关关闭
  - 报告未就绪
  - 配置缺失
  - fake/mock real runner 成功
- 已给出最小真实联调 smoke 验证步骤
- 实施完成后，至少贴出 4 类真实执行结果样例：
  - simulated
  - manual_required
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

1. 验证开关关闭时 inspection 仍走 simulated
2. 验证报告未就绪时 inspection 仍是 `manual_required`
3. 验证 precheck 硬条件不满足时不进入真实执行
4. 验证真实配置缺失时返回 `precheck_failed`
5. 验证 fake/mock real runner 成功时写入真实执行审计
6. 运行自动化测试
7. 如环境可用，执行一次最小真实联调 smoke

## 12. 实施记录

### 实际完成内容

- 新增 `InspectionRealRunner`，接入 inspection 第一版真实执行链路：
  - 打开巡检工单链接
  - 上传已匹配的巡检报告文件
  - 完成工单处理
- 在本轮收尾中，继续将 inspection 真实执行扩展为：
  - `assign_owner`
  - `add_member_if_missing`
  - `permission_denied -> manual_required`
- 为 inspection 增加双开关控制：
  - `ENABLE_REAL_EXECUTION`
  - `INSPECTION_REAL_EXECUTION_ENABLED`
- 为 inspection 增加真实执行配置：
  - `inspection_real_base_url`
  - `inspection_real_token`
  - `inspection_real_token_header`
  - `inspection_real_assign_endpoint_template`
  - `inspection_real_add_member_endpoint_template`
  - `inspection_real_upload_endpoint_template`
  - `inspection_real_complete_endpoint_template`
  - `inspection_real_final_link_path`
  - `inspection_real_timeout_seconds`
  - `inspection_real_verify_ssl`
- `InspectionExecutor` 已从 phase7 的 simulated/stub 版本升级为 phase9 真实联调版本：
  - 保留 `manual_required` fallback
  - 保留 simulated fallback
  - 新增 real precheck 校验
  - 新增 `action_trace` / `action_results` / `runner_diagnostics`
- inspection precheck 硬条件已收紧：
  - `customer_name`
  - `inspection_done == true`
  - `work_order_link` 或 `work_order_id`
  - 报告匹配成功且无 `manual_required`
  - executor 与 `module_code / task_type` 匹配
- 新增 fake inspection real server 测试夹具
- 新增 inspection executor 真实联调测试：
  - simulated fallback
  - `manual_required`
  - `precheck_failed`
  - real success
  - real upload failure diagnostics
  - `assign_owner -> add_member_if_missing -> success`
  - `permission_denied -> manual_required`

### 与原计划偏差

- 初始这份子计划第一轮只覆盖了“打开工单 + 上传报告 + 完成工单”；本轮是在同一份子计划下按收尾优化继续扩展到负责人和成员分支，属于范围内追加，不是偏 scope。
- 本轮仍然没有扩展到更复杂的权限体系或多负责人策略，只实现了“成员缺失自动补充”和“权限不足直接人工处理”的保守路径。

### 验证结果

- `python3 -m compileall services/executors/inspection_executor.py services/executors/inspection_real_runner.py tests/conftest.py tests/test_executors_api.py core/config.py`
- `python3 -m compileall apps core models repositories schemas services scheduler tests`
- `.venv/bin/python -m pytest tests/test_executors_api.py -q`
  - 结果：`18 passed`
- `.venv/bin/python -m pytest tests/test_report_matching.py -q`
  - 结果：`6 passed`
- `.venv/bin/python -m pytest -q`
  - 结果：`59 passed in 16.58s`

### 待跟进事项

- inspection 真实执行目前已覆盖：
  - 打开巡检工单链接
  - 指派负责人
  - 成员缺失时自动补成员
  - 上传已匹配报告
  - 完成工单处理
- 仍未覆盖的 inspection 真实动作：
  - 更复杂的负责人补充策略
  - 多成员选择与回滚
  - 无权限时的更细粒度错误分类与提示
- 如果下一轮继续收尾，最自然的方向是补 inspection 的复杂权限分支和更细的成员管理策略。

## 13. 遗留问题

- inspection 真实执行已进入可联调状态，但 `assign_owner` / `add_member_if_missing` 的真实线上返回契约仍需最小 smoke 继续收口。

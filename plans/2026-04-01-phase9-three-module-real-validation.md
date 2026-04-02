# Phase 9 收尾子计划：Three Module Real Validation

计划文件路径：`plans/2026-04-01-phase9-three-module-real-validation.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-01-phase9-three-module-real-validation.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的主要能力已经落地：

- 采集、识别、规划、执行主链路已贯通
- visit / inspection / proactive 三模块都已具备真实执行开关与最小真实路径
- 调度、重试、rerun、幂等和 ops 视图已接通
- 前端工作台已经支持统一状态文案、错误解释、失败项与人工处理项展示
- 执行层 contract 已统一，三模块真实联调验收清单也已经形成

到这个阶段，单模块功能和收尾优化基本已经完成，下一步最重要的不是继续扩功能，而是做一轮面向试运行的整体联调验收，验证系统是否已经达到“初步落地试运行”条件。

这轮验收不再是新大阶段，而是 Phase 9 范围内的新收尾子任务，重点不是新增功能，而是围绕现有能力做一次系统性的、跨模块的真实联调确认，并输出统一的验收结论与遗留问题清单。

## 2. 目标

本次实施完成后，应达到以下目标：

- 完成 visit 真实链路验收
- 完成 inspection 真实链路验收
- 完成 proactive 真实链路验收
- 完成 console / ops 视图验收
- 完成 scheduler / rerun / manual_required 验收
- 输出统一验收结果
- 输出遗留问题清单

## 3. 范围

本次实施范围包含：

- visit 真实执行链路验收
- inspection 真实执行链路验收
- proactive 真实执行链路验收
- console / ops 视图验收
- scheduler / rerun / manual_required 验收
- 整理统一验收结论
- 整理遗留问题清单
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- 新功能开发
- 新模块接入
- 新执行器扩展
- 大规模 UI 重构
- 新的调度能力开发
- 新的真实联调链路开发
- 企业级告警或权限平台

## 5. 当前现状 / 已知问题

当前现状：

- visit 已具备完整最小真实执行路径
- inspection 已具备最小真实执行路径和权限/成员补充分支
- proactive 已具备最小真实执行路径
- console / ops 视图已统一展示文案和业务解释
- scheduler、retry、rerun、manual_required 已具备平台级能力

已知问题：

- 三模块虽然都已经能走真实路径，但还缺一轮统一的试运行前验收
- 单模块测试已较充分，但缺一次“按试运行口径”的系统级核查
- 真实联调配置、final_link 返回、task_runs 审计、ops 展示，需要统一复核
- 某些边界分支虽然已有测试，但还需要整理成正式验收结论和遗留问题清单

## 6. 技术方案

### 6.1 验收目标定义

本轮验收的目标不是“证明系统绝对没有问题”，而是回答两个核心问题：

1. 当前系统是否已经达到“初步落地试运行”条件
2. 如果还没完全达到，阻塞项和遗留项分别是什么

因此，这轮重点是：

- 按统一 checklist 验证
- 产出结论
- 识别阻塞项
- 识别可延后遗留项

### 6.2 验收口径

本轮统一按以下口径判断：

- `通过`
  - 功能按预期可运行，结果可审计，页面可查看
- `部分通过`
  - 主链路可运行，但存在非阻塞缺口或需要人工兜底
- `不通过`
  - 存在阻塞试运行的问题

并区分：

- 阻塞问题
  - 不解决则不建议试运行
- 非阻塞遗留
  - 可以进入试运行，但需要后续跟进

### 6.2.1 最终验收结果输出模板

本轮最终验收结果统一按以下模板输出：

- 模块
- 验收项
- 结果（通过 / 部分通过 / 不通过）
- 证据
- 是否阻塞
- 备注

要求：

- 三模块验收结果统一按此模板汇总
- console / ops 验收结果统一按此模板汇总
- scheduler / rerun / manual_required 验收结果统一按此模板汇总

### 6.2.2 必要的小修正边界

本轮允许做“必要的小修正”，但边界固定为：

- 只允许修阻塞试运行的问题
- 不允许顺手扩 scope
- 非阻塞问题统一进入遗留清单

这意味着：

- 本轮默认以验证和结论输出为主
- 只有当问题明确阻塞“初步试运行”时，才允许做最小修正
- 所有非阻塞改进项，不在本轮顺手实现

### 6.3 visit 验收重点

visit 验收至少覆盖：

- precheck 硬条件
- simulated fallback
- real execution 开关
- 最小真实路径是否可完成
- final_link 是否返回
- task_runs 审计是否完整
- 失败时 `runner_diagnostics` 是否完整
- rerun 后结构是否一致

### 6.4 inspection 验收重点

inspection 验收至少覆盖：

- 报告匹配是否稳定
- precheck 条件是否正确阻断
- simulated / manual_required fallback
- 最小真实路径：
  - 打开工单
  - assign_owner
  - add_member_if_missing
  - 上传报告
  - 完成处理
- permission_denied -> manual_required 分支
- task_runs 审计完整性

### 6.5 proactive 验收重点

proactive 验收至少覆盖：

- precheck 条件
- simulated fallback
- real execution 开关
- 最小真实路径：
  - 创建工单
  - 指派舒磊
  - 写入反馈备注
- final_link 是否返回
- task_runs 审计完整性

### 6.6 console / ops 验收重点

console / ops 验收至少覆盖：

- dashboard 状态文案是否统一
- tasks 页失败/人工处理解释是否统一
- task-run 详情页错误解释是否统一
- 人工处理清单是否可用
- failures / manual-required / overview API 是否与页面一致

### 6.7 scheduler / rerun / manual_required 验收重点

本轮至少验证：

- scheduler job 注册与触发
- sync rerun
- task rerun
- retryable 失败项是否可识别
- manual_required 是否进入人工处理清单
- 幂等保护是否仍然有效

### 6.8 输出产物

本轮正式产出至少包括：

- 三模块验收结果汇总
- console / ops 验收结果
- scheduler / rerun / manual_required 验收结果
- 阻塞问题清单
- 非阻塞遗留问题清单
- 是否建议进入“初步试运行”的结论

最终必须给出二选一结论：

- 建议进入初步试运行
- 或 暂不建议进入初步试运行

并说明理由。

## 7. 分步骤实施计划

### 步骤 1：准备验收基线

- 复核当前配置与开关
- 复核三模块真实执行前置条件
- 复核 console / ops 当前页面和 API

### 步骤 2：执行 visit 验收

- 验证 simulated / real / failure 路径
- 验证 task_runs 审计
- 记录结论

### 步骤 3：执行 inspection 验收

- 验证报告匹配、real path、manual_required、权限分支
- 验证 task_runs 审计
- 记录结论

### 步骤 4：执行 proactive 验收

- 验证 simulated / real / precheck_failed
- 验证 task_runs 审计
- 记录结论

### 步骤 5：执行 console / ops 验收

- 验证 dashboard / tasks / task-runs / manual_required 清单
- 验证 ops API
- 记录结论

### 步骤 6：执行 scheduler / rerun / manual_required 验收

- 验证 scheduler
- 验证 rerun
- 验证 retry / manual_required 展示
- 记录结论

### 步骤 7：汇总结论

- 输出通过 / 部分通过 / 不通过
- 输出阻塞问题
- 输出非阻塞遗留项
- 给出是否建议试运行

### 步骤 8：验证与收尾

- 跑必要测试
- 回写同一份计划

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-three-module-real-validation.md`
- 可能补充少量测试文件
- 如发现必要缺口，可能小幅调整：
  - `services/`
  - `apps/web/`
  - `templates/console/`
  - `tests/`

当前阶段默认以验收和结论输出为主，不预设大规模代码改动。

## 9. 风险与缓解

### 风险 1：验收过程中发现跨模块不一致

缓解：

- 先记录为发现项
- 区分阻塞问题和非阻塞遗留
- 只在必要时做最小修正

### 风险 2：真实联调环境不稳定

缓解：

- 自动化测试继续作为基础兜底
- 手工 smoke 和真实联调结果单独记录
- 在结论中明确环境依赖

### 风险 3：验收范围失控变成新一轮功能开发

缓解：

- 本轮以“验证与结论”为主
- 只允许必要的小修正
- 超出范围的问题列入遗留

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已完成 visit 真实链路验收
- 已完成 inspection 真实链路验收
- 已完成 proactive 真实链路验收
- 已完成 console / ops 视图验收
- 已完成 scheduler / rerun / manual_required 验收
- 已输出统一验收结果
- 已输出遗留问题清单
- 已明确是否建议进入“初步落地试运行”
- 已按统一模板输出验收结果：
  - 模块
  - 验收项
  - 结果（通过 / 部分通过 / 不通过）
  - 证据
  - 是否阻塞
  - 备注
- 已遵守本轮最小修正边界：
  - 只修阻塞试运行的问题
  - 不顺手扩 scope
  - 非阻塞问题进入遗留清单
- 已给出最终二选一结论：
  - 建议进入初步试运行
  - 或 暂不建议进入初步试运行
  并说明理由

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证三模块 precheck / real / fallback 路径
2. 验证三模块 final_link 与 task_runs 审计
3. 验证 console / ops 统一展示
4. 验证 scheduler / rerun / manual_required
5. 运行自动化测试
6. 汇总结论并输出遗留问题

## 12. 实施记录

### 12.1 实际完成内容

- 已补充本轮验收输出模板、最小修正边界和最终二选一结论要求
- 已执行三模块执行层验收，覆盖：
  - visit simulated / precheck_failed / manual_required / real / failure diagnostics
  - inspection simulated / precheck_failed / manual_required / real / add_member_if_missing / permission_denied
  - proactive simulated / precheck_failed / manual_required / real
- 已执行执行层 contract 验收，确认三模块以下字段和语义一致：
  - `execution_mode`
  - `action_trace`
  - `action_results`
  - `runner_diagnostics`
  - `error_type`
  - `retryable`
  - `missing_fields`
  - `config_valid`
  - `attempted_actions`
  - `failed_action`
  - `last_error`
- 已执行 console / ops 验收，覆盖：
  - dashboard 统一状态文案
  - tasks 页统一失败/人工处理解释
  - task-run 详情统一错误解释
  - 人工处理清单展示
- 已执行 scheduler / rerun / manual_required 验收，覆盖：
  - scheduler job 注册与触发
  - sync 自动重试
  - task rerun
  - manual_required 聚合
  - 幂等冲突 409
- 本轮未发现需要代码修复的阻塞性缺陷，因此未做超出 plan 范围的功能改动

### 12.2 与原计划偏差

- 本轮没有引入新的功能开发，也没有新增真实联调链路，整体符合“以验收与结论输出为主”的计划预期
- 本轮主要证据来源是自动化测试与本地 mock/fake real runner 验证
- 本轮未执行真实外部线上环境的最终手工 smoke，因此“真实联调验收”结论更准确地说是：
  - 代码级与本地联调级验收已通过
  - 外部真实环境级验收仍待补

### 12.3 验证结果

#### 编译与测试

- `python3 -m compileall apps core models repositories schemas services scheduler tests`
  - 结果：通过
- `.venv/bin/python -m pytest tests/test_executors_api.py tests/test_execution_contracts.py tests/test_ops_platform.py tests/test_web_console.py -q`
  - 结果：`35 passed in 21.95s`
- `.venv/bin/python -m pytest -q`
  - 结果：`66 passed in 21.63s`

#### 三模块验收结果汇总

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| visit | simulated / precheck_failed / real / failure diagnostics | 通过 | `tests/test_executors_api.py::test_visit_execute_returns_simulated_success`、`test_visit_precheck_fails_when_real_execution_enabled_but_config_missing`、`test_visit_execute_runs_real_runner_successfully`、`test_visit_execute_complete_visit_failure_records_diagnostics` | 否 | 最小真实路径与失败审计均已覆盖 |
| visit | 执行层 contract 一致性 | 通过 | `tests/test_execution_contracts.py::test_execution_contract_simulated_payloads_are_uniform`、`test_execution_contract_real_success_payloads_are_uniform` | 否 | contract 字段统一 |
| inspection | simulated / manual_required / real / 权限与成员分支 | 通过 | `tests/test_executors_api.py::test_inspection_execute_returns_manual_required_without_reports`、`test_inspection_execute_runs_real_runner_successfully`、`test_inspection_execute_assign_owner_add_member_then_success`、`test_inspection_execute_permission_denied_returns_manual_required` | 否 | 报告匹配、成员补充、权限不足均已覆盖 |
| inspection | 执行层 contract 一致性 | 通过 | `tests/test_execution_contracts.py::test_execution_contract_real_success_payloads_are_uniform`、`test_execution_contract_retryable_http_failures_are_uniform` | 否 | retryable 与 diagnostics 结构一致 |
| proactive | simulated / precheck_failed / manual_required / real | 通过 | `tests/test_executors_api.py::test_proactive_execute_returns_simulated_success`、`test_proactive_precheck_fails_when_real_execution_enabled_but_config_missing`、`test_proactive_execute_returns_manual_required_without_contact`、`test_proactive_execute_runs_real_runner_successfully` | 否 | 最小真实路径与人工兜底均已覆盖 |
| proactive | 执行层 contract 一致性 | 通过 | `tests/test_execution_contracts.py::test_execution_contract_simulated_payloads_are_uniform`、`test_execution_contract_precheck_failed_config_payloads_are_uniform` | 否 | real/precheck payload 结构一致 |

#### console / ops 验收结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| console / ops | dashboard 统一状态文案与人工处理清单 | 通过 | `tests/test_web_console.py::test_console_dashboard_renders_module_overview`、`tests/test_ops_platform.py::test_ops_api_and_console_render_failure_and_manual_required` | 否 | 状态文案、失败任务、人工处理清单均已渲染 |
| console / ops | tasks 页统一失败/人工处理解释 | 通过 | `tests/test_web_console.py::test_console_tasks_page_renders_actions_and_run_detail` | 否 | 业务解释和执行入口可见 |
| console / ops | task-run 详情统一错误解释 | 通过 | `tests/test_web_console.py::test_console_tasks_page_renders_actions_and_run_detail` | 否 | detail 页展示业务解释、预检查结果 |
| console / ops | ops API 与页面聚合一致性 | 通过 | `tests/test_ops_platform.py::test_ops_api_and_console_render_failure_and_manual_required` | 否 | overview / failures / manual-required 与页面一致 |

#### scheduler / rerun / manual_required 验收结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| scheduler | job 注册与触发 | 通过 | `tests/test_ops_platform.py::test_scheduler_registers_interval_job_and_runs_sync` | 否 | scheduler 触发的 snapshot 审计已写入 `_ops.trigger=scheduler` |
| retry / rerun | sync 自动重试 | 通过 | `tests/test_ops_platform.py::test_sync_auto_retry_for_temporary_failure` | 否 | 首次失败、二次成功、retry 审计完整 |
| retry / rerun | execute 自动重试与 task rerun | 通过 | `tests/test_ops_platform.py::test_execute_auto_retry_and_task_rerun` | 否 | retry 与 rerun 触发标记正确 |
| manual_required | 人工处理项聚合展示 | 通过 | `tests/test_ops_platform.py::test_ops_api_and_console_render_failure_and_manual_required` | 否 | manual-required API 与前端展示一致 |
| 幂等保护 | sync / execute 冲突保护 | 通过 | `tests/test_ops_platform.py::test_sync_conflict_returns_409`、`test_execute_conflict_returns_409` | 否 | 运行中重复触发能正确返回 409 |

### 12.4 阻塞问题清单

- 暂无代码级阻塞问题
- 当前唯一阻塞“建议进入初步试运行”的问题是：
  - 尚未在真实外部线上环境完成一次三模块端到端手工 smoke
  - 当前证据主要来自自动化测试与本地 mock/fake real runner

### 12.5 非阻塞遗留问题清单

- 真实外部环境下的认证、权限、上传耗时、接口限流等行为仍需单独 smoke 复核
- console 的人工处理清单目前已可用，但仍属于第一版最小视图，后续可补筛选、分页、独立页面
- ops 聚合目前主要基于现有表即时查询，后续如数据量增大可再评估更强的聚合策略

### 12.6 最终结论

- 结论：**暂不建议进入初步试运行**
- 理由：
  - 从代码级、自动化测试级、本地 mock real runner 级别看，三模块主链路和平台能力已经达到“接近试运行”的状态
  - 但当前尚缺“真实外部线上环境”的最终手工 smoke 证据
  - 在没有完成至少一轮三模块真实环境冒烟验证前，不建议直接给出“可试运行”的结论
  - 换句话说：
    - 内部联调验收：通过
    - 真实线上试运行准入：仍需最后一轮外部环境验证

## 13. 遗留问题

- 待补一轮三模块真实外部环境端到端 smoke：
  - visit：真实 PTS 路径、真实 final_link、真实权限
  - inspection：真实报告上传、真实负责人/成员权限、真实完成处理
  - proactive：真实工单创建、指派舒磊、写入反馈备注
- 若上述 smoke 通过，可再基于本计划补一轮“试运行准入复核”记录

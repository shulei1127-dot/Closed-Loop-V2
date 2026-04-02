# Phase 9 收尾子计划：Execution Layer Unification

计划文件路径：`plans/2026-04-01-phase9-execution-layer-unification.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-01-phase9-execution-layer-unification.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的调度、重试、审计与运营基础能力已经落地：

- APScheduler 已接入
- sync / execute 已具备自动重试与手动 rerun
- 工作台已具备最小运营视图
- `task_runs` 已承载执行审计

在执行层方面，目前三个模块都已经完成了从 stub/simulated 向真实联调可用状态的推进：

- visit executor 已具备真实 runner
- inspection executor 已具备真实 runner
- proactive executor 已具备真实 runner

这意味着三条执行链已经都“能跑”，但当前还有一个明显问题：三个模块是在不同轮收尾中分别演进出来的，因此在执行层的若干关键点上仍然存在风格漂移：

- 错误分类口径不完全统一
- `retryable` 判定规则不完全统一
- 配置命名虽然大体一致，但缺失提示和校验结构仍不完全统一
- `action_trace` / `action_results` / `runner_diagnostics` 的字段组织仍有模块差异
- 测试以单模块为主，跨模块统一行为的回归验证还不够强

在继续做更深的真实联调之前，先做一轮执行层统一化整理是有价值的。这样既能降低后续维护成本，也能为三模块真实联调验收准备统一口径。

本次任务不再是新大阶段，而是 Phase 9 范围内的新收尾子任务，目标是对 visit / inspection / proactive 三个 real runner 的执行层做统一化整理，统一错误分类、配置结构、action 审计格式，并为三模块真实联调验收做准备。

## 2. 目标

本次实施完成后，应达到以下目标：

- 统一三个 real runner 的错误分类
- 统一三个 real runner 的 `retryable` 判定
- 统一配置命名和配置缺失提示结构
- 统一 `action_trace` / `action_results` / `runner_diagnostics` 结构
- 补充跨模块执行层测试
- 形成三模块真实联调验收清单

## 3. 范围

本次实施范围包含：

- visit / inspection / proactive real runner 的错误分类整理
- 三模块执行层 `retryable` 判定整理
- 三模块真实执行配置与缺失提示统一
- 三模块执行审计结构统一
- 补跨模块执行层自动化测试
- 形成三模块真实联调验收清单文档或计划内清单
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- 新模块开发
- 新增第四类 executor
- 前端复杂执行管理界面
- 执行器大规模重写
- 外部告警平台
- 新的调度中心能力

## 5. 当前现状 / 已知问题

当前现状：

- `VisitRealRunner`、`InspectionRealRunner`、`ProactiveRealRunner` 都已经可运行
- 三个 executor 都已经支持：
  - precheck
  - dry-run
  - simulated fallback
  - real execution 开关
- 三个 executor 都会向 `task_runs.result_payload` 写入执行审计

已知问题：

- 三个 runner 的错误分类更多是“就地演进”，还没有收敛成统一 taxonomy
- `retryable=True/False` 的判定依据在不同 runner 中仍偏分散
- 配置缺失时的 `missing_fields` 结构已类似，但字段命名和提示文本仍可能不一致
- 三个模块的 `action_trace` / `action_results` / `runner_diagnostics` 虽然风格相近，但还没有明确统一 schema
- 自动化测试偏单模块验证，缺少跨模块统一约束测试
- 目前还没有一份可直接用于“三模块真实联调验收”的统一清单

## 6. 技术方案

### 6.1 执行层统一化目标定义

本次统一化整理不追求“重写三个 executor”，而是以低风险收敛为主，目标是：

- 提炼公共口径
- 收敛关键字段
- 消除明显不一致
- 提高联调与验收的一致性

换句话说，本次重点是“统一结构和语义”，不是“重构一整套新框架”。

本轮统一化整理的边界固定为：

- 只做结构收敛，不做大规模重写
- 不改变 visit / inspection / proactive 已经跑通的最小真实路径
- 不引入新的复杂继承体系
- 不扩展到新的业务模块

### 6.2 错误分类统一

建议为三个 real runner 固定统一错误分类口径，至少覆盖：

- `config_missing`
- `http_error`
- `timeout`
- `response_invalid`
- `business_rejected`
- `permission_denied`
- `manual_required`
- `unknown_error`

落地方式建议：

- 在 `runner_diagnostics` 中统一放：
  - `error_type`
  - `failed_action`
  - `last_error`
- `action_results` 中失败项也统一包含：
  - `status`
  - `http_status`
  - `error_message`
  - `retryable`
  - 如可行再加 `error_type`

### 6.3 retryable 判定统一

三模块的 `retryable` 建议固定为规则驱动，而不是各自散落判断。

第一版统一口径建议：

- `timeout` -> `retryable = true`
- `5xx http_error` -> `retryable = true`
- `config_missing` -> `retryable = false`
- `permission_denied` -> `retryable = false`
- `manual_required` -> `retryable = false`
- `response_invalid` -> 默认 `retryable = false`
- 明确业务拒绝场景 -> 默认 `retryable = false`

必要时可以抽一个公共 helper，例如：

- `classify_execution_error(...)`
- `is_retryable_error(...)`

### 6.4 配置结构统一

当前三个模块的真实执行配置已经基本按模块前缀命名，但还可以进一步统一：

- `<module>_real_execution_enabled`
- `<module>_real_base_url`
- `<module>_real_token`
- `<module>_real_token_header`
- `<module>_real_*_endpoint(_template)`
- `<module>_real_final_link_path`
- `<module>_real_timeout_seconds`
- `<module>_real_verify_ssl`

同时统一配置缺失提示结构：

- `config_valid`
- `missing_fields`
- `module_code`
- `runner`

### 6.5 action 审计结构统一

建议对三模块固定统一的 payload 结构：

- `action_trace`
  - 输入动作序列
- `action_results`
  - 实际执行结果序列
- `runner_diagnostics`
  - 执行器与环境诊断

建议统一字段约束：

- `action_trace[*]`
  - `action`
  - 模块特有参数
- `action_results[*]`
  - `action`
  - `status`
  - `http_status`
  - `retryable`
  - `error_message`
  - 可选业务字段
- `runner_diagnostics`
  - `runner`
  - `mode`
  - `config_valid`
  - `missing_fields`
  - `http_statuses`
  - `attempted_actions`
  - `failed_action`
  - `last_error`
  - `error_type`

本轮统一 contract 的硬验收项固定为三模块至少以下字段/语义必须统一：

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

### 6.6 统一测试策略

本次要补的不是单个模块测试，而是跨模块统一约束测试，重点验证：

- 三模块 simulated payload 结构一致性
- 三模块 precheck_failed 的配置缺失结构一致性
- 三模块 real success 的 diagnostics 关键字段一致性
- 三模块 failed / manual_required 的 `retryable` 语义一致性

如果合适，可以新增一组“execution layer contract tests”。

### 6.7 真实联调验收清单

本次需要形成一份三模块真实联调验收清单，建议至少包含：

- 开关检查
- 配置检查
- precheck 通过条件
- 最小真实路径动作
- final_link 验证
- task_runs 审计字段检查
- 失败场景检查
- retry / rerun 行为检查

这份清单可以直接写在本计划的实施记录/待跟进中，或新增轻量文档，但优先保持在本计划内，避免额外扩 scope。

“三模块真实联调验收清单”是本轮正式产出之一。实施完成后，需要给出一份可直接执行的 checklist，用于试运行前验收。

## 7. 分步骤实施计划

### 步骤 1：梳理三个 runner 的当前差异

- 对比 visit / inspection / proactive 的错误分类
- 对比 `retryable` 判定
- 对比 diagnostics 结构
- 对比配置缺失提示

### 步骤 2：收敛公共口径

- 固定统一错误分类
- 固定统一 `retryable` 规则
- 固定统一 diagnostics 基础字段

### 步骤 3：按模块小幅调整

- visit real runner 调整
- inspection real runner 调整
- proactive real runner 调整

### 步骤 4：统一 executor payload 结构

- 收敛 `action_trace`
- 收敛 `action_results`
- 收敛 `runner_diagnostics`

### 步骤 5：补跨模块测试

- 模拟三模块 success / precheck_failed / failed / manual_required 统一性
- 固定 contract-level 回归测试

### 步骤 6：形成验收清单

- 输出三模块真实联调验收项
- 明确尚未覆盖的边界

### 步骤 7：验证与收尾

- 跑语法和自动化测试
- 回写同一份计划

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-execution-layer-unification.md`
- `services/executors/visit_real_runner.py`
- `services/executors/inspection_real_runner.py`
- `services/executors/proactive_real_runner.py`
- `services/executors/visit_executor.py`
- `services/executors/inspection_executor.py`
- `services/executors/proactive_executor.py`
- `core/config.py`
- `.env.example`
- `tests/test_executors_api.py`
- 可能新增统一 contract 测试文件

## 9. 风险与缓解

### 风险 1：统一化整理误伤已经跑通的真实链路

缓解：

- 只做结构收敛，不做大规模重写
- 每个模块保留原有最小真实路径
- 用回归测试锁定行为

### 风险 2：为了统一而过度抽象

缓解：

- 第一版优先统一语义和字段
- 不急于抽出复杂继承体系

### 风险 3：测试范围变大导致定位困难

缓解：

- 保留原单模块测试
- 另外增加少量跨模块 contract tests

## 10. 验收标准

满足以下条件视为本次任务完成：

- 本轮统一化整理保持以下边界：
  - 只做结构收敛，不做大规模重写
  - 不改变 visit / inspection / proactive 已经跑通的最小真实路径
  - 不引入新的复杂继承体系
  - 不扩展到新的业务模块
- 三个 real runner 的错误分类已统一
- 三个 real runner 的 `retryable` 判定已统一
- 三个模块的真实执行配置命名和缺失提示已统一
- 三个模块的：
  - `action_trace`
  - `action_results`
  - `runner_diagnostics`
  已达到统一结构
- 三模块至少以下字段/语义已统一：
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
- 已补跨模块执行层测试
- 已形成三模块真实联调验收清单

## 11. 验证步骤

计划中的验证步骤如下：

1. 对比三模块 simulated 返回结构
2. 对比三模块 precheck_failed 的配置缺失结构
3. 对比三模块 real success 的 diagnostics 关键字段
4. 对比三模块 failed / manual_required 的 `retryable` 语义
5. 运行自动化测试

## 12. 实施记录

### 12.1 实际完成内容

- 新增轻量公共 helper：`services/executors/runner_contract.py`
  - 统一 `error_type` 分类
  - 统一 `retryable` 规则
  - 统一 `config_valid / missing_fields`
  - 统一 `attempted_actions / failed_action / last_error`
  - 统一 simulated diagnostics 基础字段
- 调整 `VisitRealRunner`
  - 接入统一 diagnostics 初始化与失败收尾
  - 统一 action result 规范化
  - 补 `timeout / response_invalid` 的显式 `error_type`
- 调整 `InspectionRealRunner`
  - 接入统一 diagnostics 初始化与失败收尾
  - 统一 `member_missing / permission_denied / manual_required` 的诊断结构
  - 补 `timeout / unknown_error / response_invalid` 的显式 `error_type`
- 调整 `ProactiveRealRunner`
  - 接入统一 diagnostics 初始化与失败收尾
  - 统一 action result 规范化
  - 补 `timeout / response_invalid` 的显式 `error_type`
- 调整三个 executor 的 simulated diagnostics
  - `VisitExecutor`
  - `InspectionExecutor`
  - `ProactiveExecutor`
  均接入统一 simulated diagnostics 基础结构
- 新增跨模块 contract tests：`tests/test_execution_contracts.py`
  - 三模块 simulated payload contract
  - 三模块 precheck_failed config contract
  - 三模块 real success contract
  - 三模块 real failure retryable contract

### 12.2 与原计划偏差

- 未做大规模重构，也未引入新的继承体系；实际实现保持为“新增轻量 helper + 三个 runner 小幅调整”
- `core/config.py` 和 `.env.example` 本轮未发生实际字段改名
  - 原因：三个模块的真实执行配置命名已基本统一
  - 本轮重点放在 contract 语义统一和 diagnostics 统一
- 未新增独立验收文档文件
  - 将“三模块真实联调验收清单”直接固化在本计划中，控制范围不外扩

### 12.3 验证结果

- 语法检查：
  - `python3 -m compileall apps core models repositories schemas services scheduler tests` 通过
- 新增专项测试：
  - `.venv/bin/python -m pytest tests/test_execution_contracts.py -q`
  - 结果：`4 passed`
- 全量测试：
  - `.venv/bin/python -m pytest -q`
  - 结果：`66 passed in 21.46s`

### 12.4 三模块真实联调验收清单

#### A. 开关与配置检查

- [ ] `ENABLE_REAL_EXECUTION=true`
- [ ] visit / inspection / proactive 各自模块专属真实执行开关已开启
- [ ] 三模块 `*_real_base_url` 已配置
- [ ] 三模块 `*_real_token` 已配置
- [ ] 三模块 endpoint / endpoint template 已配置
- [ ] 三模块 `*_real_final_link_path` 已配置
- [ ] `config_valid=true`
- [ ] `missing_fields=[]`

#### B. Precheck 检查

- [ ] visit precheck 通过
- [ ] inspection precheck 通过
- [ ] proactive precheck 通过
- [ ] 不满足条件时能返回 `precheck_failed` 或 `manual_required`
- [ ] precheck 结果中 `runner_diagnostics` 包含：
  - `module_code`
  - `runner`
  - `config_valid`
  - `missing_fields`
  - `error_type`

#### C. 最小真实路径检查

- [ ] visit 能完成最小真实路径并返回 `final_link`
- [ ] inspection 能完成最小真实路径并返回 `final_link`
- [ ] proactive 能完成最小真实路径并返回 `final_link`
- [ ] 三模块 `execution_mode` 均符合预期：
  - `real`
  - 或失败场景下 `real_attempted`

#### D. 审计字段检查

- [ ] `task_runs.result_payload.execution_mode` 存在
- [ ] `task_runs.result_payload.action_trace` 存在
- [ ] `task_runs.result_payload.action_results` 存在
- [ ] `task_runs.result_payload.runner_diagnostics` 存在
- [ ] `runner_diagnostics` 至少包含：
  - `error_type`
  - `retryable`（失败 action result 内）
  - `missing_fields`
  - `config_valid`
  - `attempted_actions`
  - `failed_action`
  - `last_error`

#### E. 失败与重试语义检查

- [ ] `timeout` -> `retryable=true`
- [ ] `5xx http_error` -> `retryable=true`
- [ ] `config_missing` -> `retryable=false`
- [ ] `permission_denied` -> `retryable=false`
- [ ] `manual_required` -> `retryable=false`
- [ ] `response_invalid` -> `retryable=false`

#### F. rerun / ops 联动检查

- [ ] 失败 task run 可在平台层被识别为失败项
- [ ] `manual_required` task run 可在运营视图中识别
- [ ] rerun 后仍保留统一 contract 结构

## 13. 遗留问题

- 真实 runner 的配置命名已基本统一，但仍保留模块特有 endpoint 名称；后续如继续统一，可考虑只统一 contract，不强行抹平业务语义
- 当前统一化主要聚焦执行 contract；尚未把 healthcheck 输出也完全纳入同一 contract
- 若后续进入更深的线上联调，下一轮优先项应是统一三模块的 `manual_required / business_rejected / permission_denied` 细分展示文案

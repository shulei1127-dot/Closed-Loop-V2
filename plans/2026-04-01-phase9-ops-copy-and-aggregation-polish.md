# Phase 9 收尾子计划：Ops Copy And Aggregation Polish

计划文件路径：`plans/2026-04-01-phase9-ops-copy-and-aggregation-polish.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-01-phase9-ops-copy-and-aggregation-polish.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且平台侧的调度、重试、审计与运营基础能力已经落地：

- APScheduler 已接入
- sync / execute 已具备自动重试与手动 rerun
- 工作台已具备最小运营视图
- `task_runs` 已承载执行审计
- 三模块真实执行链路已基本跑通
- 执行层 contract 已完成统一化整理

当前系统在“能跑”和“能审计”上已经具备较好的基础，但从真实运营视角看，前端和 ops 视图仍有一类明显问题没有完全收口：

- 三模块状态文案还不够统一
- 错误分类到业务解释的映射还不够稳定
- `manual_required`、`retryable`、`rerun` 的展示口径在不同页面上还可能存在差异
- dashboard / tasks / task-runs 页面的聚合更偏工程视角，还没有完全转成运营友好的视角
- 当前还缺少一块更聚焦的“人工处理清单”或等价展示

在执行层 contract 已统一后，下一步很自然的收尾工作，就是把这些统一后的结构翻译成一致、清晰、适合运营使用的前端文案与 ops 聚合视图。

本次任务不再是新大阶段，而是 Phase 9 范围内的新收尾子任务，目标是统一三模块在前端与 ops 视图中的状态文案、错误解释、`manual_required` 展示和失败聚合口径，让系统更适合真实运营使用。

## 2. 目标

本次实施完成后，应达到以下目标：

- 统一三模块状态文案映射
- 统一错误分类到业务解释的映射
- 统一 `rerun / retryable / manual_required` 展示
- 增强 dashboard / tasks / task-runs 的 ops 聚合
- 增加人工处理清单或等价展示
- 补相关前端/API 测试

## 3. 范围

本次实施范围包含：

- 前端状态文案映射统一
- 错误分类到业务解释文案映射统一
- `rerun / retryable / manual_required` 的前端展示统一
- dashboard / tasks / task-runs 页面聚合增强
- 增加人工处理清单或等价展示
- 必要时补轻量 ops 聚合 API
- 相关前端/API 自动化测试
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- 新业务模块开发
- 新执行器接入
- 大规模前端重构
- 复杂权限系统
- 外部告警平台
- 新的调度能力扩展
- 新的真实联调链路开发

## 5. 当前现状 / 已知问题

当前现状：

- dashboard 已能展示模块基本运行状态
- tasks / task-runs 已能展示执行结果和操作入口
- ops API 已具备 overview / failures / manual-required 的基础能力
- 三模块 real runner 的 contract 结构已经统一

已知问题：

- 同样的 `run_status` 在不同页面上的中文文案可能不完全一致
- `error_type` 虽然已统一，但还没有稳定映射成运营能快速理解的业务解释
- `manual_required`、`retryable`、`rerun` 的展示还偏底层字段，不够业务化
- dashboard 对失败任务、人工处理项、可重跑项的聚合还可以更清晰
- task-runs 页面更像原始结果页，仍缺少更强的“运营解释层”
- 当前没有专门面向运营的“人工处理清单”视图或等价块

## 6. 技术方案

### 6.1 统一目标定义

本次统一化整理的重点不是重做 UI，而是把现有页面和 ops 聚合做成：

- 文案一致
- 解释清晰
- 聚合口径稳定
- 方便运营快速识别“失败 / 可重跑 / 需人工处理”

换句话说，本次优先提升“可运营性”和“可解释性”，不追求复杂视觉重构。

### 6.2 状态文案映射统一

建议统一至少以下状态到中文展示文案：

- sync 相关：
  - `success`
  - `partial`
  - `failed`
- plan 相关：
  - `planned`
  - `skipped`
- run 相关：
  - `precheck_failed`
  - `precheck_passed`
  - `dry_run_ready`
  - `simulated_success`
  - `success`
  - `failed`
  - `manual_required`

同时统一 badge / 标签语义：

- 成功类
- 警告类
- 人工处理类
- 失败类
- 可重跑类

### 6.3 错误分类到业务解释的映射

基于执行层已统一的 `error_type`，增加前端/ops 层映射，例如：

- `config_missing` -> “执行配置缺失，需要补齐配置”
- `http_error` -> “外部系统请求失败，可稍后重试”
- `timeout` -> “请求超时，通常可重试”
- `response_invalid` -> “外部返回异常，需排查接口”
- `business_rejected` -> “业务条件不满足，无法自动继续”
- `permission_denied` -> “权限不足，需要人工处理”
- `manual_required` -> “需要人工介入处理”
- `unknown_error` -> “未知异常，需要进一步排查”

要求：

- 同一 `error_type` 在 dashboard / tasks / task-runs / ops API 中解释一致
- 业务解释优先展示给用户，底层字段可作为次级信息展示

### 6.4 rerun / retryable / manual_required 展示统一

建议统一展示规则：

- `retryable = true`
  - 显示“可重试”
- `manual_required = true`
  - 显示“需人工处理”
- 存在 rerun 入口
  - 显示“可重跑”

并明确优先级：

- `manual_required` 高于 `retryable`
- `precheck_failed(config_missing)` 不展示“可重试”，可展示“需补配置”
- `permission_denied` 不展示“可重试”，展示“需人工处理”

### 6.5 ops 聚合增强

本次增强的 ops 聚合重点放在：

- dashboard：
  - 最近运行状态
  - 最近失败摘要
  - 人工处理数量
  - 可重跑数量
- tasks：
  - 当前任务状态解释
  - 最近执行结果解释
  - 是否可重跑 / 可重试 / 需人工处理
- task-runs：
  - 运行结果摘要
  - 失败解释
  - 错误分类
  - runner diagnostics 的可读摘要

必要时可增加轻量聚合字段，但优先复用现有 ops service / ops API。

### 6.6 人工处理清单

“人工处理清单”是第一版正式产出之一，不只是“等价展示”的可选项。

第一版需要提供一块明确的人工处理视图，形式可以是：

- dashboard 中的独立区块
- 单独页面
- 或 tasks 页中的独立分组

第一版最少展示：

- 模块
- 客户名称
- `task_id`
- 当前状态
- 业务解释
- 最近运行时间
- 查看详情入口
- rerun 入口

第一版不要求复杂筛选系统，但要可直接用于人工排查。

### 6.7 测试策略

本次测试至少覆盖：

- 状态文案映射测试
- 错误分类解释映射测试
- dashboard 聚合展示测试
- task run 业务解释展示测试
- `manual_required` 列表或等价展示测试
- API 成功与失败分支测试

如合适，可补：

- 前端模板渲染断言
- ops API contract 断言

## 7. 分步骤实施计划

### 步骤 1：梳理当前页面与 ops 聚合差异

- 对比 dashboard / tasks / task-runs 当前展示
- 对比已有 ops API 字段
- 识别状态文案和错误解释不一致处

### 步骤 2：统一文案与解释映射

- 固定状态文案映射
- 固定 `error_type -> business copy` 映射
- 固定 `retryable / manual_required / rerun` 展示规则

### 步骤 3：增强 ops 聚合

- dashboard 聚合增强
- tasks 聚合增强
- task-runs 聚合增强
- 必要时补轻量后端聚合字段

### 步骤 4：增加人工处理清单或等价展示

- 选择页面形态
- 接通现有 `manual_required` 数据
- 提供查看详情和 rerun 入口

### 步骤 5：补测试

- 前端模板/页面测试
- ops API 测试
- 聚合口径测试

### 步骤 6：验证与收尾

- 跑语法和自动化测试
- 本地手工联调
- 回写同一份计划

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-ops-copy-and-aggregation-polish.md`
- `apps/web/router.py`
- `templates/console/dashboard.html`
- `templates/console/tasks.html`
- `templates/console/task_run_detail.html`
- `static/console/app.js`
- `services/ops_service.py`
- `apps/api/routers/ops.py`
- `schemas/ops.py`
- `tests/test_web_console.py`
- `tests/test_ops_platform.py`
- 可能新增轻量前端文案/映射 helper

## 9. 风险与缓解

### 风险 1：前端文案统一影响现有页面断言

缓解：

- 先固化映射表，再更新模板
- 用页面测试锁定最终文案

### 风险 2：ops 聚合增强引入过重查询

缓解：

- 第一版优先做轻量聚合
- 复用现有 ops service
- 不急于新增复杂统计表

### 风险 3：为了统一展示而掩盖底层差异

缓解：

- 页面上优先展示业务解释
- 同时保留底层错误分类与 diagnostics 入口

### 风险 4：人工处理视图扩 scope

缓解：

- 第一版只做最小可用展示
- 不扩成复杂运营后台

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已统一三模块状态文案映射
- 已统一 `error_type -> 业务解释` 映射
- 已统一 `rerun / retryable / manual_required` 展示口径
- 已增强：
  - dashboard
  - tasks
  - task-runs
  的 ops 聚合展示
- 已增加人工处理清单
- 已补相关前端/API 测试
- 实施完成后，至少能展示：
  - 模块总览中的统一状态文案
  - task 列表中的统一失败/人工处理解释
  - task run 详情中的统一错误解释
  - 人工处理清单
- 实施完成后，至少贴出 4 类页面结果：
  - dashboard 中统一状态文案
  - tasks 页中统一失败/人工处理解释
  - task-run 详情页中的统一错误解释
  - 人工处理清单展示

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证 dashboard 状态文案是否统一
2. 验证 tasks 页的失败/人工处理解释是否统一
3. 验证 task-runs 页的错误分类解释是否统一
4. 验证 `retryable / manual_required / rerun` 展示是否一致
5. 验证人工处理清单展示
6. 运行自动化测试
7. 本地手工联调

## 12. 实施记录

### 12.1 实际完成内容

- 新增统一文案与解释 helper：`services/ops_copy.py`
  - 统一 `run_status -> 中文状态文案`
  - 统一 `error_type -> 业务解释`
  - 统一 `retryable / manual_required / rerun` 展示文本
  - 统一 task run 视图层的 `display_status / business_explanation / error_type`
- 扩展 `schemas/ops.py`
  - 为 overview / failures / manual-required 增加运营展示字段
  - 包括：
    - `latest_sync_status_label`
    - `latest_execute_status_label`
    - `latest_execute_explanation`
    - `retryable_task_count`
    - `display_status`
    - `status_tone`
    - `business_explanation`
    - `customer_name`
    - `detail_url`
    - `rerun_available`
- 增强 `services/ops_service.py`
  - overview 聚合新增统一状态文案和最近执行解释
  - failures 列表新增统一错误解释和 customer_name
  - manual-required 列表新增客户、详情链接、业务解释、rerun 能力
- 增强 `apps/web/router.py`
  - dashboard 接入统一化后的 overview / failures / manual-required
  - tasks 页增加 latest run 解释与人工处理清单
  - task-run detail 页增加统一错误解释视图
- 更新前端模板：
  - `templates/console/dashboard.html`
  - `templates/console/tasks.html`
  - `templates/console/task_run_detail.html`
  - 统一中文状态文案
  - 统一失败/人工处理解释
  - 增加人工处理清单
- 更新前端交互与样式：
  - `static/console/app.js`
  - `static/console/console.css`
  - 将部分英文按钮/反馈文案统一为中文
  - 增加 `status-warning / status-manual / status-unknown` 视觉语义
- 更新测试：
  - `tests/test_web_console.py`
  - `tests/test_ops_platform.py`

### 12.2 与原计划偏差

- 未新增独立“人工处理清单页面”
  - 当前第一版落地为 dashboard 独立区块 + tasks 页补充区块
  - 满足“正式产出”要求，但仍保持范围克制
- 未新增新的 ops API 路由
  - 通过增强现有 `overview / failures / manual-required` 输出完成聚合统一
- 未重做 records 页
  - 按优先级保留为低优先级页面，本轮重点集中在 dashboard / tasks / task-runs / manual_required

### 12.3 验证结果

- 语法检查：
  - `python3 -m compileall apps core models repositories schemas services scheduler tests` 通过
- 页面与 ops 专项测试：
  - `.venv/bin/python -m pytest tests/test_web_console.py tests/test_ops_platform.py -q`
  - 结果：`10 passed`
- 全量测试：
  - `.venv/bin/python -m pytest -q`
  - 结果：`66 passed in 23.42s`

### 12.4 页面结果样例

#### A. dashboard 中统一状态文案

- visit 最近同步：`成功`
- visit 最近执行：`模拟执行成功`
- inspection 最近执行：`需人工处理` / `成功`
- proactive 最近执行：`预检查失败` / `成功`
- dashboard 中失败项展示统一业务解释，如：
  - `外部系统请求失败，可稍后重试。`
- dashboard 中人工处理清单展示统一业务解释，如：
  - `需要人工介入处理。`

#### B. tasks 页中统一失败/人工处理解释

- 最近执行列显示统一后的中文状态，例如：
  - `预检查失败`
  - `需人工处理`
  - `模拟执行成功`
  - `成功`
- 业务解释列显示统一后的运营解释，例如：
  - `执行配置缺失，需要先补齐配置。`
  - `权限不足，需要人工处理。`
  - `请求超时，通常可稍后重试。`

#### C. task-run 详情页中的统一错误解释

- 当前状态：`预检查失败` / `需人工处理` / `成功`
- 执行模式：`real_precheck` / `real_attempted` / `real`
- 业务解释：统一映射后的中文解释
- 错误分类：显示 `config_missing / permission_denied / timeout / http_error` 等统一值
- 失败动作与底层错误：保留给排障使用

#### D. 人工处理清单展示

第一版人工处理清单已在 dashboard 中正式提供，最少展示：

- 模块
- 客户名称
- `task_id`
- 当前状态
- 业务解释
- 最近运行时间
- 查看详情入口
- rerun 入口

## 13. 遗留问题

- 当前人工处理清单仍以内嵌区块形式展示，后续如运营频率提升，可演进为独立页面并加筛选
- 当前 business explanation 以统一映射为主，仍保留底层错误信息在详情页展示；后续可继续补更细的模块特有业务文案
- `records` 页未纳入本轮统一展示收口，后续若继续收尾，可补识别层错误解释与运营文案

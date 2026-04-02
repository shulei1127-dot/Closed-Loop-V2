# 第八阶段前端计划：工作台

## 1. 背景

当前项目已经完成以下阶段：

- Phase 1：项目骨架
- Phase 2：后端主链路
- Phase 3：real collector 架构
- Phase 4：真实钉钉 transport
- Phase 5：字段识别与标准化增强
- Phase 6：执行器接入
- Phase 7：巡检报告匹配与文件处理

目前后端已经具备较完整的业务能力：

- 采集：`source_snapshots`
- 识别：`normalized_records`
- 规划：`task_plans`
- 执行：`task_runs`
- 手动接口：
  - `POST /api/sync/run`
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`

但现在系统仍主要通过 API 和测试验证，缺少一个可运营、可排查、可人工触发动作的前端工作台。Phase 8 的目标是基于现有后端能力，建设一个可操作的轻前端工作台，让用户可以从模块、快照、记录、任务、执行结果等角度查看系统状态，并手动触发 precheck / dry-run / execute。

本阶段重点是先把“能用、可联调、可操作、可排查”的工作台做起来，而不是一次性追求复杂设计系统或权限平台。

## 2. 目标

本次实施完成后，应达到以下目标：

- 提供模块总览页，覆盖 3 个模块
- 提供 snapshots / records / tasks / task runs 查看能力
- 提供手动操作入口：
  - `POST /api/sync/run`
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
- 支持 dry-run / simulated execute 结果展示
- 页面能展示关键识别、规划、执行审计信息
- 补齐前端联调与基础测试

## 3. 范围

本次实施范围包含：

- 模块总览页
- snapshots 列表与详情查看
- normalized records 列表与详情查看
- task_plans 列表与详情查看
- task_runs 详情查看
- 手动触发 sync / precheck / execute 的前端交互
- 前端对成功 / 失败分支的反馈展示
- 页面基础渲染与 API 联调测试
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 复杂权限体系
- 高级 UI 设计系统
- 调度中心
- 告警系统
- 全量真实执行器深挖
- 多角色工作流
- 移动端专门适配到复杂运营后台级别

## 5. 当前现状 / 已知问题

当前现状：

- 项目当前是以后端为主，没有成型的前端工作台
- FastAPI 路由和 API 已较完整
- 数据查看主要依赖 API 返回 JSON
- 执行动作虽然已经可通过 API 触发，但没有统一页面入口

已知问题：

- 用户无法在一个页面里快速查看 3 个模块的整体状态
- 快照、记录、任务、执行结果分散在不同 API 中，不便运营排查
- 手动触发 sync / precheck / execute 目前需要手工调接口
- 前端技术栈用户尚未明确指定，需要在计划中先固定一个实现方向

## 6. 技术方案

### 6.1 前端实现方向

本阶段建议采用“轻前端工作台”方案，优先复用现有 FastAPI 工程，不额外引入复杂前后端分离体系。当前倾向：

- 由 FastAPI 承载页面路由
- 使用服务端模板渲染为主
- 配合少量原生 JavaScript 完成手动操作和局部刷新

选择这个方向的原因：

- 当前目标是快速把后端能力变成可操作工作台
- 可以减少 Node 构建链和前后端联调复杂度
- 更适合 Phase 8 的“轻前端工作台”定位

如实施时发现现有 repo 已更适合单独前端工程，也可以调整，但第一版优先以最小可落地方案为主。

第一版前端技术路线在本阶段固定为：

- FastAPI + Jinja2 模板 + 原生 JavaScript
- 不新建独立 React / Vue / Next 工程
- 目标是先做一个轻量、可操作、可排查的工作台

### 6.2 页面结构

建议至少包含以下页面：

- 模块总览页
  - 3 个模块卡片或表格
- 快照页
  - snapshots 列表
  - snapshot 详情抽屉或详情页
- 标准化记录页
  - records 列表
  - record 详情
- 任务页
  - task_plans 列表
  - task_runs 结果查看
  - 手动 precheck / execute 操作

第一版页面优先级固定为：

- 第一优先：
  - 模块总览页
  - snapshots 页
  - tasks 页
  - task-runs / 执行结果查看页
- `records` 页可以做，但优先级低于上面 4 个页面

### 6.3 模块总览页

展示字段至少包括：

- 模块名称
- 最近同步时间
- 最近快照状态
- 记录数
- planned 数量
- skipped 数量
- manual_required 数量

说明：

- 当前后端已有模块 summary 能力，但 `manual_required` 统计可能还需结合 `task_runs` 或任务结果做补充
- 若后端 summary 字段不够，Phase 8 实施中可追加轻量 API 扩展

### 6.4 快照与记录查看

需要支持：

- snapshots 列表与详情
- normalized records 列表与详情
- 字段识别信息展示：
  - `field_mapping`
  - `field_confidence`
  - `unresolved_fields`
  - `recognition_status`

建议：

- 列表页展示关键摘要
- 点击进入详情页或侧边栏查看完整 JSON/字段信息

### 6.5 任务与执行查看

需要支持：

- `task_plans`
- `task_runs`
- `skip_reason`
- `planned_payload`
- `result_payload`
- `final_link`
- `error_message`

建议：

- task 列表中直接展示 plan 状态与最近执行结果摘要
- 详情里展示完整 payload 和错误信息

### 6.6 手动操作入口

前端需要支持：

- 手动触发 `POST /api/sync/run`
- 手动触发 `POST /api/tasks/{task_id}/precheck`
- 手动触发 `POST /api/tasks/{task_id}/execute`

交互建议：

- sync：模块卡片或列表页按钮触发
- precheck：task 行级按钮触发
- execute：task 行级按钮触发，并允许选择 dry-run

反馈展示：

- loading 状态
- 成功提示
- 错误提示
- 执行结果详情展示

### 6.7 API 协作方式

本阶段前端主要依赖已有 API：

- `GET /api/modules/summary`
- `GET /api/modules/{module_code}/latest`
- `GET /api/snapshots`
- `GET /api/snapshots/{snapshot_id}`
- `GET /api/records`
- `GET /api/records/{record_id}`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/task-runs/{run_id}`
- `POST /api/sync/run`
- `POST /api/tasks/{task_id}/precheck`
- `POST /api/tasks/{task_id}/execute`

如实施中发现前端对某些聚合数据访问不便，可补少量后端接口增强，但应保持范围可控。

### 6.8 测试策略

至少覆盖：

- 页面基础渲染
- API 调用成功与失败分支
- 模块总览展示
- task run 结果展示
- precheck / execute 操作反馈

测试层次建议：

- 轻量页面路由测试
- 前端交互逻辑测试
- 必要时做最小端到端 smoke

## 7. 分步骤实施计划

### 步骤 1：确定前端目录与路由结构

- 明确模板目录、静态资源目录
- 新增页面入口路由
- 选定页面布局骨架

### 步骤 2：实现模块总览页

- 接通模块 summary API
- 展示 3 个模块状态
- 提供手动 sync 入口

### 步骤 3：实现快照与记录页

- 实现 snapshots 列表 / 详情
- 实现 records 列表 / 详情
- 展示 recognition 相关字段

### 步骤 4：实现任务与执行页

- 实现 tasks 列表 / 详情
- 实现 task run 查看
- 展示 skip_reason / payload / final_link / error_message

### 步骤 5：实现手动操作交互

- precheck 按钮
- execute 按钮
- dry-run 开关
- 成功 / 失败 / manual_required 反馈

### 步骤 6：补测试与联调

- 页面渲染测试
- API 交互测试
- 关键页面 smoke

### 步骤 7：验证与收尾

- 跑测试
- 本地手工联调
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-frontend-phase8-console.md`
- 可能新增：
  - `apps/web/` 或 `apps/frontend/`
  - `templates/`
  - `static/`
  - 前端页面路由文件
  - 前端资源文件
- `apps/api/main.py`
- `apps/api/router.py`
- 可能少量扩展现有 API schema / route
- `tests/`

## 9. 风险与缓解

### 风险 1：前端技术方案过重，拖慢交付

缓解：

- 第一版优先服务端模板 + 少量 JS
- 避免引入复杂前后端分离体系

### 风险 2：现有 API 不够适合前端展示

缓解：

- 优先复用已有 API
- 仅做少量聚合增强，不扩大后端范围

### 风险 3：页面信息量大，容易变成纯 JSON 堆砌

缓解：

- 按模块、快照、记录、任务、执行结果分区展示
- 列表展示摘要，详情展示完整内容

### 风险 4：操作按钮缺少反馈，用户不确定结果

缓解：

- 明确 loading / success / error / manual_required 状态
- 展示最近 task run 结果

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已提供模块总览页，展示：
  - 最近同步时间
  - 最近快照状态
  - 记录数
  - planned / skipped / manual_required 数量
- 已支持查看：
  - snapshots 列表与详情
  - normalized records 列表与详情
  - `field_mapping`
  - `field_confidence`
  - `unresolved_fields`
  - `recognition_status`
- 已支持查看：
  - `task_plans`
  - `task_runs`
  - `skip_reason`
  - `planned_payload`
  - `result_payload`
  - `final_link`
  - `error_message`
- 已支持手动触发：
  - `POST /api/sync/run`
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
- 已支持展示 dry-run / simulated execute 结果
- 测试覆盖：
  - 页面基础渲染
  - API 调用成功与失败分支
  - 模块总览展示
  - task run 结果展示
  - precheck / execute 操作反馈
- 实施完成后，至少要给出以下页面结果展示：
  - 模块总览页页面结果
  - task 列表页页面结果
  - 一次 precheck 操作结果展示
  - 一次 execute 的 dry-run / simulated execute 结果展示

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证模块总览页展示
2. 验证 snapshots / records / tasks / task runs 页面渲染
3. 验证手动 sync 操作
4. 验证 precheck / dry-run / execute 操作反馈
5. 验证错误分支和 manual_required 展示
6. 运行自动化测试
7. 做本地手工联调

## 12. 实施记录

### 实际完成内容

- 按计划固定采用 **FastAPI + Jinja2 模板 + 原生 JavaScript** 技术路线，没有新建独立 React / Vue / Next 工程
- 新增前端页面路由：
  - [`/console`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/web/router.py)
  - [`/console/snapshots`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/web/router.py)
  - [`/console/tasks`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/web/router.py)
  - [`/console/task-runs/{run_id}`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/web/router.py)
  - [`/console/records`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/web/router.py)
- 新增模板结构：
  - [`templates/console/base.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/base.html)
  - [`templates/console/dashboard.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/dashboard.html)
  - [`templates/console/snapshots.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/snapshots.html)
  - [`templates/console/tasks.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/tasks.html)
  - [`templates/console/task_run_detail.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/task_run_detail.html)
  - [`templates/console/records.html`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/templates/console/records.html)
- 新增静态资源：
  - [`static/console/console.css`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/static/console/console.css)
  - [`static/console/app.js`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/static/console/app.js)
- 模块总览页已完成并支持：
  - 最近同步时间
  - 最近快照状态
  - 记录数
  - planned / skipped / manual_required 数量
  - 手动触发 sync
- 已完成 snapshots 页和 tasks 页
- 已完成 task-runs / 执行结果查看页
- `records` 页也已补基础版本，但优先级保持低于上述 4 个核心页面
- 已支持手动触发：
  - `POST /api/sync/run`
  - `POST /api/tasks/{task_id}/precheck`
  - `POST /api/tasks/{task_id}/execute`
- 已支持前端展示：
  - precheck 结果
  - dry-run 结果
  - simulated execute 结果
  - final_link / error_message / result_payload
- 少量后端配合改动已完成：
  - `FastAPI` 主应用挂载 web router 与 static
  - `pyproject.toml` 增加 `jinja2`
  - 页面路由中补了 manual_required 统计和任务最近执行结果聚合
- 已新增前端页面测试：
  - 模块总览页渲染
  - snapshots 页渲染
  - tasks 页渲染
  - task-runs 详情页渲染
  - records 页基础渲染
- 已导出并验证：
  - 模块总览页页面结果
  - task 列表页页面结果
  - 一次 precheck 操作结果展示
  - 一次 execute 的 dry-run / simulated execute 结果展示

### 与原计划偏差

- `records` 页虽然在优先级上低于 dashboard / snapshots / tasks / task-runs，但本阶段仍补了基础可查看版本；这属于正向补充，不构成范围偏差
- 页面第一版采用服务端模板 + 原生 JS，没有额外扩展更复杂的前端构建体系，与计划固定技术路线一致
- 没有额外新增复杂后端 API，而是优先复用现有 API 和仓储层能力；这与计划中的“少量后端增强、范围可控”一致

### 验证结果

- 语法校验：
  - `python3 -m compileall apps core schemas services tests`
  - 结果：通过
- 自动化测试：
  - `.venv/bin/python -m pytest -q`
  - 结果：`41 passed`
- 页面联调验证：
  - 已通过临时 PostgreSQL + TestClient 生成数据，并实际访问：
    - `/console`
    - `/console/snapshots`
    - `/console/tasks`
    - `/console/task-runs/{run_id}`
  - 已验证页面可展示模块、任务和执行结果
- 操作联调验证：
  - 已验证前端依赖的后端接口可正确返回：
    - precheck
    - dry-run
    - simulated execute

### 待跟进事项

- 后续如进入更完整的运营阶段，可以继续增强：
  - task-runs 列表页
  - records 页交互体验
  - 页面筛选与分页
  - 更细的状态聚合
- 当前工作台以“轻量、可操作、可排查”为目标，后续如果用户量和功能规模上升，再考虑更完整的前端状态管理和设计系统

## 13. 遗留问题

- 当前页面仍以快速联调为主，复杂交互和高阶表格能力尚未引入
- 自动化测试主要覆盖服务端渲染和接口交互，尚未引入浏览器级前端 E2E 测试
- `records` 页目前是基础版，后续如需要更深排查能力，还可继续增强

# Phase 9 收尾子计划：Real Environment Smoke

计划文件路径：`plans/2026-04-01-phase9-real-environment-smoke.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-01-phase9-real-environment-smoke.md`

## 1. 背景

当前路线图主阶段已经推进到 Phase 9，并且系统已经完成：

- 三模块 collector / recognizer / planner / executor 主链路
- visit / inspection / proactive 三模块最小真实执行路径
- scheduler / retry / rerun / manual_required / ops 视图
- 执行层 contract 统一化
- 前端展示文案与 ops 聚合统一

上一轮“三模块真实联调验收”已经完成，结论是：

- 代码级、自动化测试级、本地 mock/fake real runner 级别：通过
- 是否建议进入初步试运行：暂不建议

当时未建议直接进入初步试运行的唯一核心原因是：

- 尚未完成三模块真实外部环境 hand smoke

因此，这一轮的目标不再是新增功能开发，而是补上最后一环：在真实外部环境下，对 visit / inspection / proactive 三个模块做一轮手工 smoke，并结合 console / ops 联动结果，最终给出是否满足“初步试运行准入”的明确结论。

## 2. 目标

本次实施完成后，应达到以下目标：

- 完成 visit 真实外部环境 smoke
- 完成 inspection 真实外部环境 smoke
- 完成 proactive 真实外部环境 smoke
- 完成 console / ops 联动验证
- 输出试运行准入结论

## 3. 范围

本次实施范围包含：

- visit 真实 smoke
- inspection 真实 smoke
- proactive 真实 smoke
- console / ops 联动验证
- 输出试运行准入结论
- 实施完成后回写同一份子计划

## 4. 非范围

本次明确不做：

- 新功能开发
- 新模块开发
- 执行器扩展
- 前端大改
- 新的调度能力开发
- 新的业务流程扩展

## 5. 当前现状 / 已知问题

当前现状：

- visit / inspection / proactive 三模块都已具备真实执行开关与最小真实路径
- console / ops 已可展示统一状态、失败项和人工处理项
- scheduler / rerun / manual_required 已具备平台级能力
- 自动化测试和本地联调级验证已通过

已知问题：

- 真实外部环境的认证、权限、接口稳定性、文件上传、最终链接返回等，还未做最终手工 smoke
- 真实环境下可能出现 mock 测试无法覆盖的边界问题
- 最终是否建议进入初步试运行，仍依赖这轮 smoke 结果

## 6. 技术方案

### 6.1 Smoke 目标定义

本轮 smoke 的目标不是做大规模回归测试，而是验证：

- 三模块最小真实路径是否能在真实外部环境下跑通
- 失败时审计与页面联动是否仍然正确
- manual_required / rerun / ops 聚合是否在真实结果下仍然成立
- 系统是否达到“初步试运行准入”条件

### 6.2 Smoke 验收口径

本轮统一按以下口径判断：

- `通过`
  - 真实外部环境下按预期完成，结果可审计，页面可查看
- `部分通过`
  - 主链路可运行，但存在人工兜底或非阻塞缺口
- `不通过`
  - 存在阻塞试运行的问题

并区分：

- 阻塞问题
  - 不解决则不建议进入初步试运行
- 非阻塞遗留
  - 可以进入试运行，但需要后续跟进

### 6.3 最终验收结果输出模板

本轮最终结果统一按以下模板输出：

- 模块
- 验收项
- 结果（通过 / 部分通过 / 不通过）
- 证据
- 是否阻塞
- 备注

### 6.3.1 Smoke 证据记录模板

每个模块至少固定记录以下 smoke 证据：

- precheck 结果
- execute 结果
- final_link
- task_run_id
- `task_runs.result_payload` 摘要
- console / ops 页面联动结果

要求：

- visit / inspection / proactive 三模块都按同一模板记录
- 如果 smoke 未能真正进入真实外部环境，也必须按同一模板记录失败证据
- 证据优先记录真实返回结果与页面联动现象，不只写结论

### 6.3.2 问题分类模板

本轮每个发现项都统一按以下模板分类：

- 问题类型：`system_defect` / `environment_issue`
- 是否阻塞
- 临时绕过方式
- 后续处理建议

要求：

- 阻塞问题和非阻塞遗留问题都按此模板整理
- 需要明确区分“系统实现缺陷”和“真实外部环境问题”

### 6.4 必要的小修正边界

本轮允许做“必要的小修正”，但边界固定为：

- 只允许修阻塞试运行的问题
- 不允许顺手扩 scope
- 非阻塞问题统一进入遗留清单

### 6.5 visit smoke 验收重点

visit 至少验证：

- 真实 precheck 通过
- 真实执行开关生效
- 最小真实路径可完成：
  - 打开 PTS 链接
  - 创建回访工单
  - assign_owner
  - mark_visit_target
  - fill_feedback
  - complete_visit
- 返回真实 final_link
- `task_runs.result_payload` 审计完整
- console / ops 可看到真实执行结果

### 6.6 inspection smoke 验收重点

inspection 至少验证：

- 报告匹配成功
- 真实 precheck 通过
- 最小真实路径可完成：
  - 打开巡检工单
  - assign_owner
  - 如有需要 add_member_if_missing
  - 上传报告
  - complete_inspection
- 如遇权限问题，能正确进入 `manual_required`
- 返回真实或准真实 final_link
- `task_runs.result_payload` 审计完整
- console / ops 可看到真实执行结果

### 6.7 proactive smoke 验收重点

proactive 至少验证：

- 真实 precheck 通过
- 最小真实路径可完成：
  - 创建客户满意度调研工单
  - assign_owner
  - fill_feedback
- 返回真实或准真实 final_link
- `task_runs.result_payload` 审计完整
- console / ops 可看到真实执行结果

### 6.8 console / ops 联动重点

至少验证：

- dashboard 能反映三模块真实最新执行状态
- tasks 页能展示真实 smoke 结果
- task-run detail 能展示真实错误解释或成功结果
- manual_required 项能进入人工处理清单
- rerun 入口在真实结果下仍可用

### 6.9 输出产物

本轮正式产出至少包括：

- 三模块真实 smoke 结果汇总
- console / ops 联动验收结果
- 阻塞问题清单
- 非阻塞遗留问题清单
- 最终二选一结论：
  - 建议进入初步试运行
  - 或 暂不建议进入初步试运行

## 7. 分步骤实施计划

### 步骤 1：准备 smoke 基线

- 复核三模块真实执行开关和环境配置
- 复核 console / ops 当前可视状态
- 复核 smoke 所需测试数据与外部依赖

### 步骤 2：执行 visit 真实 smoke

- 跑 visit precheck / execute
- 验证真实 final_link
- 验证 task_runs 审计
- 记录结论

### 步骤 3：执行 inspection 真实 smoke

- 跑 inspection precheck / execute
- 验证报告匹配、上传、完成处理
- 记录权限/成员分支结果
- 验证 task_runs 审计
- 记录结论

### 步骤 4：执行 proactive 真实 smoke

- 跑 proactive precheck / execute
- 验证真实 final_link
- 验证 task_runs 审计
- 记录结论

### 步骤 5：执行 console / ops 联动验证

- 验证 dashboard / tasks / task-runs / manual_required 清单
- 验证 rerun 入口
- 记录结论

### 步骤 6：汇总结论

- 输出通过 / 部分通过 / 不通过
- 输出阻塞问题
- 输出非阻塞遗留项
- 给出是否建议进入初步试运行

### 步骤 7：回写计划与收尾

- 回写同一份计划
- 补充验证结果、遗留问题与最终结论

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-phase9-real-environment-smoke.md`
- 如发现阻塞性缺陷，可能最小调整：
  - `services/`
  - `apps/web/`
  - `templates/console/`
  - `tests/`

默认以 smoke 验证和结论输出为主，不预设功能开发。

## 9. 风险与缓解

### 风险 1：真实环境不稳定

缓解：

- 明确记录环境问题和时间点
- 区分系统缺陷与外部环境波动
- 在结论中单独说明

### 风险 2：验收中发现问题后 scope 膨胀

缓解：

- 只修阻塞试运行的问题
- 非阻塞项统一进入遗留清单

### 风险 3：手工 smoke 证据不完整

缓解：

- 每个模块都记录：
  - precheck 结果
  - execute 结果
  - final_link
  - task_runs 审计
  - console / ops 页面联动

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已完成 visit 真实 smoke
- 已完成 inspection 真实 smoke
- 已完成 proactive 真实 smoke
- 已完成 console / ops 联动验证
- 已输出统一验收结果
- 已输出阻塞问题清单
- 已输出非阻塞遗留问题清单
- 已给出最终二选一结论：
  - 建议进入初步试运行
  - 或 暂不建议进入初步试运行
  并说明理由

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证三模块真实 precheck / execute 路径
2. 验证三模块 final_link 与 task_runs 审计
3. 验证 console / ops 联动展示
4. 验证 manual_required / rerun 在真实结果下的行为
5. 汇总结论并输出遗留问题

## 12. 实施记录

### 12.1 实际完成内容

- 已补充 smoke 证据记录模板与问题分类模板
- 已检查真实环境基线配置：
  - 项目目录不存在 `.env`
  - 当前 shell 环境中三模块真实执行相关环境变量均缺失
  - 默认 `DATABASE_URL` 对应的 PostgreSQL 不可达
  - `INSPECTION_REPORT_ROOT` 默认目录存在，且报告文件目录可见
- 已进行真实环境级应用启动与页面/API smoke：
  - 启动本地应用进程
  - 验证 scheduler 初始化阶段无法加载数据库配置
  - 验证 `/console` 返回 `500`
  - 验证 `/api/ops/overview` 返回 `500`
- 已按真实环境 smoke 口径整理 visit / inspection / proactive 三模块结果
- 已按问题分类模板整理阻塞项与遗留项
- 本轮未进行代码修改，因为发现的问题均属于环境基线缺失，不属于允许范围内的系统阻塞修复

### 12.2 与原计划偏差

- 原计划预期是对三模块逐一执行真实 precheck / execute hand smoke
- 实际执行中，因真实环境基线未准备完成，smoke 在“环境准备”阶段即被阻断
- 因此本轮没有拿到：
  - 真实 `precheck` 返回
  - 真实 `execute` 返回
  - 真实 `task_run_id`
  - 真实 `final_link`
- 这不是执行器逻辑本身的直接失败证据，而是明确的真实环境前置条件缺失

### 12.3 验证结果

#### 真实环境基线检查

- `.env`
  - 结果：缺失
  - 证据：`test -f .../.env && echo present || echo missing` -> `missing`
- 真实执行环境变量
  - 结果：全部缺失
  - 证据：
    - `ENABLE_REAL_EXECUTION=missing`
    - `VISIT_REAL_EXECUTION_ENABLED=missing`
    - `VISIT_REAL_BASE_URL=missing`
    - `VISIT_REAL_TOKEN=missing`
    - `INSPECTION_REAL_EXECUTION_ENABLED=missing`
    - `INSPECTION_REAL_BASE_URL=missing`
    - `INSPECTION_REAL_TOKEN=missing`
    - `INSPECTION_REPORT_ROOT=missing`
    - `PROACTIVE_REAL_EXECUTION_ENABLED=missing`
    - `PROACTIVE_REAL_BASE_URL=missing`
    - `PROACTIVE_REAL_TOKEN=missing`
    - `DATABASE_URL=missing`
- inspection 报告目录
  - 结果：存在
  - 证据：`/Users/shulei/Downloads/巡检报告集合-已审核` 可列出 `.docx/.pdf`
- 默认数据库
  - 结果：不可达
  - 证据：
    - `.venv/bin/python` 连接 `postgresql+psycopg://postgres:postgres@localhost:5432/closed_loop_v2`
    - 返回 `OperationalError`
    - 关键信息：`connection refused`

#### 应用与页面/API smoke

- 应用启动
  - 结果：进程可启动，但 scheduler 初始化时数据库访问失败
  - 证据：`failed to load module configs for scheduler`
- `/console`
  - 结果：`500 Internal Server Error`
  - 证据：访问 `http://127.0.0.1:8010/console` 返回 `500`
- `/api/ops/overview`
  - 结果：`500 Internal Server Error`
  - 证据：访问 `http://127.0.0.1:8010/api/ops/overview` 返回 `500`

#### visit smoke 结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| visit | 真实环境 smoke 基线检查 | 不通过 | `.env` 缺失；`ENABLE_REAL_EXECUTION`、`VISIT_REAL_EXECUTION_ENABLED`、`VISIT_REAL_BASE_URL`、`VISIT_REAL_TOKEN` 全部缺失 | 是 | 真实执行根本无法开启 |
| visit | precheck 结果 | 不通过 | 未执行；数据库不可达，无法创建/读取真实 task | 是 | `task_run_id = N/A` |
| visit | execute 结果 | 不通过 | 未执行；真实环境未完成配置 | 是 | `final_link = N/A` |
| visit | task_runs.result_payload 摘要 | 不通过 | 无真实 `task_run` 生成 | 是 | `result_payload = N/A` |
| visit | console / ops 页面联动结果 | 不通过 | `/console` 与 `/api/ops/overview` 在真实环境下均返回 `500` | 是 | 页面联动受数据库阻断 |

#### inspection smoke 结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| inspection | 真实环境 smoke 基线检查 | 不通过 | `INSPECTION_REAL_EXECUTION_ENABLED`、`INSPECTION_REAL_BASE_URL`、`INSPECTION_REAL_TOKEN` 缺失；数据库不可达 | 是 | 报告目录存在，但不足以完成 smoke |
| inspection | precheck 结果 | 不通过 | 未执行；数据库不可达，无法创建/读取真实 task | 是 | `task_run_id = N/A` |
| inspection | execute 结果 | 不通过 | 未执行；真实环境未完成配置 | 是 | `final_link = N/A` |
| inspection | task_runs.result_payload 摘要 | 不通过 | 无真实 `task_run` 生成 | 是 | `result_payload = N/A` |
| inspection | console / ops 页面联动结果 | 不通过 | `/console` 与 `/api/ops/overview` 在真实环境下均返回 `500` | 是 | 报告目录可见，但页面链路被数据库阻断 |

#### proactive smoke 结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| proactive | 真实环境 smoke 基线检查 | 不通过 | `PROACTIVE_REAL_EXECUTION_ENABLED`、`PROACTIVE_REAL_BASE_URL`、`PROACTIVE_REAL_TOKEN` 缺失；数据库不可达 | 是 | 真实执行根本无法开启 |
| proactive | precheck 结果 | 不通过 | 未执行；数据库不可达，无法创建/读取真实 task | 是 | `task_run_id = N/A` |
| proactive | execute 结果 | 不通过 | 未执行；真实环境未完成配置 | 是 | `final_link = N/A` |
| proactive | task_runs.result_payload 摘要 | 不通过 | 无真实 `task_run` 生成 | 是 | `result_payload = N/A` |
| proactive | console / ops 页面联动结果 | 不通过 | `/console` 与 `/api/ops/overview` 在真实环境下均返回 `500` | 是 | 页面联动受数据库阻断 |

#### console / ops 联动结果

| 模块 | 验收项 | 结果 | 证据 | 是否阻塞 | 备注 |
| --- | --- | --- | --- | --- | --- |
| console / ops | dashboard 联动 | 不通过 | `/console` 返回 `500 Internal Server Error` | 是 | 无法查看统一状态、人工处理清单 |
| console / ops | ops overview API | 不通过 | `/api/ops/overview` 返回 `500 Internal Server Error` | 是 | 无法完成运营视图验证 |
| console / ops | 人工处理清单联动 | 不通过 | 数据库不可达，无法读取任务与 task_runs | 是 | 不是文案问题，是环境基线问题 |

### 12.4 问题清单

#### 阻塞问题清单

1. `.env` 缺失，真实执行基线配置未落地
   - 问题类型：`environment_issue`
   - 是否阻塞：是
   - 临时绕过方式：补齐 `.env`，至少配置 `ENABLE_REAL_EXECUTION`、三模块 `*_REAL_EXECUTION_ENABLED`、`*_REAL_BASE_URL`、`*_REAL_TOKEN`
   - 后续处理建议：按三模块真实 smoke 清单逐项配置并复跑

2. 默认数据库不可达
   - 问题类型：`environment_issue`
   - 是否阻塞：是
   - 临时绕过方式：启动本地/目标 PostgreSQL，或在 `.env` 中配置可用 `DATABASE_URL`
   - 后续处理建议：先恢复数据库连接，再执行真实 smoke

3. console / ops 在真实环境下不可访问
   - 问题类型：`environment_issue`
   - 是否阻塞：是
   - 临时绕过方式：先恢复数据库，再重新启动应用
   - 后续处理建议：数据库恢复后重新验证 `/console`、`/api/ops/overview`、人工处理清单

#### 非阻塞遗留问题清单

- inspection 报告目录已存在，但当前尚未验证真实 smoke 数据是否能与目录中的实际客户名称稳定匹配
  - 问题类型：`environment_issue`
  - 是否阻塞：否
  - 临时绕过方式：待数据库与真实执行配置补齐后再做针对性 smoke
  - 后续处理建议：在真实 inspection task 生成后，补一轮报告匹配实测

### 12.5 最终结论

- 结论：**暂不建议进入初步试运行**
- 理由：
  - 本轮 smoke 阻断点已经明确，不是执行器代码路径本身缺失，而是真实环境基线未准备完成
  - 目前真实执行配置未落地，数据库也不可达，导致三模块真实 smoke 无法开始
  - console / ops 在真实环境下同样因数据库不可达而无法完成验证
  - 在这些阻塞性 `environment_issue` 未解决前，不满足“初步试运行准入”条件

## 13. 遗留问题

- 待补齐 `.env` 与三模块真实执行配置后，重新执行：
  - visit 真实 smoke
  - inspection 真实 smoke
  - proactive 真实 smoke
- 待恢复数据库连接后，重新验证：
  - `/console`
  - `/api/ops/overview`
  - tasks / task-run detail / manual_required 清单
- inspection 需在真实 task 数据生成后，补一次真实报告匹配 smoke

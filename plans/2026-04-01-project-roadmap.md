# 闭环平台 V2 项目总路线图

## 1. 项目背景

本项目目标是从 0 开始重构一个稳定的闭环平台，替代旧的 Flask + 浏览器页面识别方案。

业务范围覆盖 3 个模块：

1. 交付转售后回访闭环
2. 巡检工单闭环
3. 超半年主动回访闭环

当前旧方案的问题是：

* 强依赖浏览器页面 DOM 识别
* 对钉钉页面结构和视图状态敏感
* 采集、识别、规划、执行耦合过重
* 难以审计、难以重试、难以定位问题

新平台的核心目标是：

**稳定采集 → 自动识别 → 任务规划 → 自动执行 → 审计可追踪**

---

## 2. 总体目标

构建一套可长期运行的业务平台，具备以下能力：

* 从 3 个钉钉文档持续采集数据
* 自动识别字段并标准化
* 生成待执行任务
* 自动执行闭环动作
* 支持失败回退、人工接管、审计和重试
* 有前端工作台用于查看、确认、执行、排查

---

## 3. 总体架构

系统按 5 层拆分：

### 3.1 采集层 Collectors

职责：

* 从钉钉文档拉取原始数据
* 优先结构化数据源
* 页面 state/store 次之
* Playwright fallback 最后兜底
* 原始快照落库

### 3.2 识别层 Recognizers

职责：

* 自动识别字段语义
* 输出标准字段映射、置信度、依据、样本值
* 形成标准化记录

### 3.3 规划层 Planners

职责：

* 基于标准化记录生成待执行任务
* 区分 planned / skipped / manual_required

### 3.4 执行层 Executors

职责：

* 真实调用 PTS / 工单系统
* 创建工单、处理工单、上传附件、闭环
* 输出最终结果或人工处理项

### 3.5 审计层 Audit

职责：

* 记录每次采集、识别、规划、执行
* 支持失败排查、重试、人工回放

---

## 4. 技术栈

### 后端

* FastAPI
* PostgreSQL
* SQLAlchemy 2.x
* Alembic
* APScheduler

### 自动化与采集

* 结构化请求 / payload fetcher
* state/store extractor
* Playwright fallback

### 前端

* 轻前端工作台
* 后期可扩展为更完整的管理界面

---

## 5. 已完成阶段

### Phase 1：项目骨架

已完成内容：

* 新项目初始化
* FastAPI + PostgreSQL + Alembic
* 基础模型、基础路由、基础表结构

### Phase 2：后端主链路

已完成内容：

* `source_snapshots -> normalized_records -> task_plans` 主链路
* collector / recognizer / planner 基础契约
* snapshot / record / task / latest API
* 单元测试和集成测试

### Phase 3：real collector 架构

已完成内容：

* `module_configs` 扩展为真实 collector 配置模型
* real collector 基类
* diagnostics / source_config / fetchers 抽象
* fixture/fake transport 驱动
* collector 优先级固定为：

  1. 结构化数据
  2. 页面 state/store
  3. Playwright fallback

---

## 6. 后续阶段规划

## Phase 4：真实钉钉 transport 接入

### 目标

把当前 `fetchers.py` 中的扩展位升级成真实可工作的钉钉 transport 层。

### 核心任务

* 实现真实 `DingtalkPayloadFetcher`
* 支持 cookies / headers / 认证注入
* 支持真实 payload 解析
* 让 3 个 collector 可接真实 transport
* 保留 fake transport / fixture transport

### 验收结果

* 真实 payload 可进入 `source_snapshots`
* 真实 collector 模式下 `/api/sync/run` 可写库
* diagnostics 可区分：

  * 配置缺失
  * 认证失败
  * 请求失败
  * 响应为空
  * payload 解析失败
  * fallback 命中

---

## Phase 5：字段识别与标准化增强

### 目标

让真实钉钉数据能稳定识别为标准字段。

### 核心任务

* visit 字段识别增强
* inspection 字段识别增强
* proactive 字段识别增强
* 统一别名、枚举、链接模式、空值规则
* 优化 recognition_status：

  * `full`
  * `partial`
  * `failed`

### 验收结果

* 三个模块的真实 rows 都能生成稳定的 `normalized_records`
* 字段映射、置信度、依据、样本值完整可追踪
* unresolved_fields 可解释

---

## Phase 6：执行器接入

### 目标

让可执行任务真正变成自动闭环动作。

### 6.1 Visit Executor

执行：

* 打开 PTS 交付链接
* 创建回访工单
* 指派舒磊
* 根据回访类型选择工单类型
* 标记回访对象
* 处理工单
* 填满意度和反馈
* 完成回访
* 返回闭环工单链接

### 6.2 Inspection Executor

执行：

* 打开巡检工单链接
* 指定负责人舒磊
* 若无舒磊则尝试添加成员
* 若无权限则进入 manual_required
* 上传巡检报告 Word/PDF
* 完成工单处理

### 6.3 Proactive Executor

执行：

* 创建客户满意度调研类型工单
* 回访人固定舒磊
* 备注写入客户意见反馈
* 完成闭环或人工处理

### 验收结果

* 三个 executor 都可独立运行
* 成功、失败、人工处理有明确状态落库
* `task_runs` 有完整执行审计

---

## Phase 7：巡检报告匹配与文件处理

### 目标

稳定支持巡检闭环中的报告匹配和文件上传。

### 核心任务

* 扫描报告目录
* 公司名匹配
* Word/PDF 双文件校验
* 缺失文件、异常命名、重复文件识别
* 与 inspection planner / executor 打通

### 验收结果

* 巡检任务能自动匹配报告
* 无报告或报告异常时转人工处理
* 文件处理结果可审计

---

## Phase 8：前端工作台

### 目标

提供可运营的工作台，而不只是 API。

### 页面建议

1. 模块总览页
2. 快照列表页
3. 标准化记录页
4. 待执行任务页
5. 执行结果页

### 页面能力

* 查看同步状态
* 查看字段识别结果
* 查看 planned / skipped / manual_required
* 查看执行结果和失败原因
* 手工重试 / 重新同步 / 重新规划

### 验收结果

* 支持日常使用
* 支持排查和人工确认
* 支持从模块视角和任务视角查看数据

---

## Phase 9：调度、重试、审计与运营

### 目标

把系统从“开发工具”升级为“可持续运行的平台”。

### 核心任务

* APScheduler 定时同步
* 失败重试
* 幂等控制
* 告警与异常日志
* 手工重跑
* 每日任务报表
* 执行历史审计

### 验收结果

* 系统可每天自动跑
* 失败不会悄悄丢失
* 每次同步和执行都可追踪
* 支持人工介入与恢复

---

## 7. 阶段顺序原则

必须按以下顺序推进：

1. Phase 4：真实钉钉 transport
2. Phase 5：字段识别增强
3. Phase 6：执行器接入
4. Phase 7：巡检报告匹配
5. Phase 8：前端工作台
6. Phase 9：调度与审计

不建议跳阶段开发，原因是：

* 没有真实 transport，前端和执行器都建立在假数据上
* 没有稳定字段识别，执行器会频繁误执行
* 没有执行器，前端只能看不能用
* 没有调度和审计，系统难以长期运行

---

## 8. 各阶段 plan 规则

后续每个阶段开始前，必须：

1. 新建该阶段 plan 文件
2. 先写计划，再开始编码
3. 实施完成后回写同一份 plan

建议 plan 文件命名如下：

* `plans/2026-04-01-backend-phase4-dingtalk-transport.md`
* `plans/2026-04-01-backend-phase5-recognition-enhancement.md`
* `plans/2026-04-01-backend-phase6-executors.md`
* `plans/2026-04-01-backend-phase7-report-matching.md`
* `plans/2026-04-01-frontend-phase8-console.md`
* `plans/2026-04-01-platform-phase9-ops.md`

---

## 9. 当前建议的下一步

当前最优先推进的是：

### Phase 4：真实钉钉 transport 接入

因为这是从 fixture/fake 数据进入真实业务数据的关键门槛。

这一阶段完成后，后续所有模块都将建立在真实输入上，后面的识别、规划、执行才有意义。

---

## 10. 总结

本项目最终目标不是单纯做一个“钉钉页面抓取脚本”，而是建设一套：

**钉钉文档实时采集 → 字段自动识别 → 任务自动规划 → 闭环自动执行 → 审计与人工兜底**

的业务平台。

路线图上，已完成阶段为：

* Phase 1
* Phase 2
* Phase 3

接下来按 Phase 4 到 Phase 9 顺序推进。

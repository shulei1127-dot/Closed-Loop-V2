# PTS 巡检工单 Deadline Reminder 旁路子能力计划

计划文件路径：`plans/2026-04-15_pts_inspection_deadline_reminder_plan.md`
完整路径：`/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-15_pts_inspection_deadline_reminder_plan.md`

## 1. 任务摘要

本次需求不是修改 inspection 现有 `sync / recognize / execute` 主链路，而是在其旁边新增一条独立的 deadline reminder 子能力，用于基于 PTS 巡检工单数据做截止日期提醒。

本次能力边界固定如下：

- 不改 `SyncService` 主链路
- 不改 `InspectionRecognizer / InspectionPlanner / InspectionExecutor` 的现有业务判断
- 不依赖钉钉文档字段，不依赖 `normalized_records` 中的 inspection 字段识别结果
- 只依赖 PTS 工单数据
- 独立 collector、独立 service、独立 scheduler job、独立持久化表
- sender 采用可插拔设计，优先钉钉，缺失时先用 `log + db record` fallback 跑通
- 提醒类型只做三类：
  - `due_in_3d`
  - `due_in_1d`
  - `overdue`
- 同一工单同一 `remind_type` 只允许提醒一次，必须通过“业务查重 + 数据库唯一约束”双重保证幂等

## 2. 目标

本次实施完成后，应达到以下目标：

- 新增独立的 PTS 巡检工单 deadline collector
- 新增独立 reminder service，完成采集、规则判定、查重、发送、落库
- 新增独立 `deadline_reminders` 表
- 新增独立 scheduler job，按 Asia/Shanghai 定时运行
- 新增 sender 抽象与最小 sender 实现
- 缺少钉钉发送配置时，仍可通过 `log + db record` 跑通整条链路
- 提供最小 API 管理入口，支持查看提醒记录与手动触发
- 保持对 inspection 主链路的最小侵入

## 3. 范围

本次实施范围包含：

- deadline collector 设计与实现
- reminder 判定规则实现
- 幂等去重与数据库唯一约束
- sender 可插拔抽象与最小实现
- scheduler 注册与配置接入
- 最小 API 管理入口
- 自动化测试与最小验收方案

## 4. 非范围

本次明确不做：

- 修改 inspection 现有 sync / recognize / execute 主链路
- 将 reminder 结果回写到 `task_plans / task_runs / normalized_records`
- 新增前端 console 页面
- 新增复杂消息模板编辑器
- 新增重型消息中间件、任务队列或事件总线
- 新增复杂告警编排、升级、抄送规则
- 同一天重复提醒、小时级提醒、更多 remind_type

## 5. 现状与设计锚点

基于当前代码结构，本次设计优先复用以下现有风格与能力：

- `core/config.py`
  - 用于新增 reminder 相关开关与 cron 配置
- `scheduler/jobs.py`
  - 当前已负责 sync job 注册，适合追加 reminder job 注册
- `core/db.py` / SQLAlchemy 2.x / Alembic
  - 继续沿用现有模型、仓储、迁移风格
- `repositories/*`
  - 继续用 repository 封装读写
- `services/*`
  - reminder 逻辑以 service 为中心，不引入新框架
- `services/executors/inspection_real_runner.py`
  - 当前已存在 PTS 浏览器态查询模式，可作为 reminder collector 查询 PTS 的风格参考
- `apps/api/routers/ops.py`
  - 适合放置最小 reminder 管理接口

本次不复用：

- `source_snapshots / normalized_records / task_plans / task_runs`
  - reminder 是 inspection 的旁路子能力，不应强耦合到主链路审计表
- `module_configs.sync_cron`
  - reminder job 独立于 sync job，不应混入现有模块同步配置语义

## 6. 总体方案

### 6.1 旁路架构

新增一条完全独立的旁路链路：

1. `PtsInspectionDeadlineCollector`
   - 从 PTS 拉取巡检工单 deadline 相关最小字段
   - 输出内存态 DTO 列表
2. `InspectionDeadlineReminderService`
   - 过滤符合规则的工单
   - 生成 `remind_type`
   - 做业务查重
   - 插入 reminder 记录
   - 调用 sender
   - 回写发送结果
3. `scheduler.jobs`
   - 注册独立 job，例如 `reminder:inspection-deadline`
4. `deadline_reminders`
   - 持久化提醒记录与发送结果
5. `ReminderSender`
   - 通过配置选择钉钉或 fallback sender

这条链路不进入 `SyncService.run_sync()`，也不复用 inspection 现有 task execute 入口。

### 6.2 PTS Deadline Collector

建议新增独立 collector，固定落点为：

- `services/collectors/inspection_deadline_collector.py`

collector 职责固定为：

- 连接 PTS
- 查询巡检工单 deadline 最小字段
- 输出统一结构的 deadline item 列表

建议 collector 的统一输出字段为：

- `pts_work_order_id: str`
- `pts_work_order_link: str | None`
- `customer_name: str | None`
- `service_type: str | None`
- `status_text: str | None`
- `plan_finish_time_raw: str | None`
- `plan_finish_date: date | None`
- `raw_payload: dict`

说明：

- `status_text` 是 reminder 层真正消费的标准化状态字段
- 无论 PTS 原始返回的是 `status`、`status_name`、`current_stage.name`、`is_finished` 还是组合字段，collector 都应先归一成 `status_text`
- 这样 reminder 规则不依赖 PTS GraphQL 细节

### 6.3 PTS 采集字段最小集合

本次 reminder 只需要最小字段集合，不做 inspection execute 那种复杂详情查询。

PTS 最小采集字段目标为：

- 工单标识：
  - `pts_work_order_id`
- 工单链接：
  - `pts_work_order_link`
- 客户信息：
  - `customer_name`
- 服务类型：
  - `service_type`
- 截止时间：
  - `plan_finish_time`
- 状态相关：
  - `status` / `status_name` / `current_stage.name` / `is_finished`

collector 归一后的最小业务字段必须至少包含：

- `pts_work_order_id`
- `customer_name`
- `service_type`
- `status_text`
- `plan_finish_date`

如果 PTS 查询结果无法稳定得到 `pts_work_order_link`，允许 collector 根据 `PTS_BASE_URL + pts_work_order_id` 规则拼出链接。

### 6.4 PTS 字段口径确认步骤

正式写代码前，先做一轮字段口径确认，不默认假设当前字段名正确。

确认步骤固定如下：

1. 先检查当前仓库里是否已有可复用的 PTS 查询能力
   - 重点查看：
     - `services/executors/inspection_real_runner.py`
     - 现有 `_PtsBrowserSession`
     - 已有 GraphQL/runtime query helper
2. 确认当前可复用能力是否足以支撑“只读 deadline 查询”
   - 若足够，则优先复用查询会话/认证/运行时探测能力
   - 若不足，再新增最小 collector 查询实现
3. 用真实或测试可控环境确认以下字段口径
   - 工单主键真实字段名
   - 工单链接字段或拼接规则
   - `service_type` 真实字段名与实际值枚举
   - 状态字段真实字段名与实际值枚举
   - `plan_finish_time` 的真实格式
     - ISO 字符串
     - 时间戳
     - 本地时间字符串
     - 其他格式
4. 只有字段口径确认后，才固化 collector 的字段映射与状态过滤规则

本次 plan 先锁定业务输出字段与提醒规则，但实现时必须先完成该确认步骤。

### 6.5 提醒判定规则

提醒判定固定规则如下：

- 全部时间先转换到 `Asia/Shanghai`
- 转换后只取日期部分比较
- 只按自然日判定
- 完全不看时分秒

判定步骤：

1. 取当前时间的 `Asia/Shanghai` 本地日期 `today`
2. 将 `plan_finish_time` 归一成 `Asia/Shanghai` 下的 `deadline_date`
3. 计算：
   - `delta_days = (deadline_date - today).days`

只处理以下工单：

- `service_type == "巡检工单"`
- `plan_finish_time` 非空
- `status_text` 不在：
  - `已完成`
  - `已关闭`
  - `已取消`

提醒类型规则固定为：

- `delta_days == 3`
  - `remind_type = "due_in_3d"`
- `delta_days == 1`
  - `remind_type = "due_in_1d"`
- `delta_days < 0`
  - `remind_type = "overdue"`

本次明确不处理：

- `delta_days == 0`
- `delta_days == 2`
- `delta_days > 3`

这样可以把业务规则保持最小、清晰、可测。

### 6.6 去重与幂等方案

本次幂等必须采用双重保证：

#### A. 业务查重

在 service 内先基于候选键做业务查重：

- 候选业务键：
  - `(pts_work_order_id, remind_type)`

流程：

1. collector 产出候选工单
2. reminder service 生成候选 `remind_type`
3. repository 批量查询已存在 reminder keys
4. 已存在则直接跳过

这样可以减少无意义 insert 尝试与异常日志。

#### B. 数据库唯一约束

在 `deadline_reminders` 表上增加唯一约束：

- `UNIQUE (pts_work_order_id, remind_type)`

建议唯一约束名：

- `uq_deadline_reminders_pts_work_order_remind_type`

这样即使多实例并发、重复手动触发、scheduler 重入，也能在数据库层硬性兜底。

#### C. 落库顺序

为避免“先发消息，后落库”导致重复发送，建议顺序固定为：

1. 业务查重过滤
2. 先插入一条 reminder 记录，初始状态为 `pending`
3. 若插入触发唯一约束冲突，则视为重复，直接跳过
4. 插入成功后再调用 sender
5. 根据 sender 结果更新为：
   - `sent`
   - `failed`

说明：

- 该策略优先保证“同一工单同一 remind_type 不会重复发”
- 如果 sender 真正失败，记录会保留在 `failed`，默认不自动重试
- 若未来需要重试，应通过显式手动 API 另行设计，不在本次范围内

### 6.7 `deadline_reminders` 表结构

建议新增模型：

- `models/deadline_reminder.py`

建议表结构如下：

- `id: UUID PK`
- `module_code: String(32) not null`
  - 固定写 `inspection`
- `pts_work_order_id: String(128) not null`
- `pts_work_order_link: Text null`
- `customer_name: String(255) null`
- `service_type: String(64) null`
- `status_text: String(64) null`
- `plan_finish_time_raw: String(128) null`
- `plan_finish_date: Date not null`
- `remind_type: String(32) not null`
- `send_status: String(32) not null`
  - 建议值：
    - `pending`
    - `sent`
    - `failed`
- `message_channel: String(32) null`
  - 例如：
    - `dingtalk`
    - `log_fallback`
- `message_title: String(255) null`
- `message_body: Text null`
- `sender_payload: JSONB not null default {}`
  - 记录实际请求体或 fallback 记录
- `sender_result: JSONB not null default {}`
  - 记录发送结果、错误码、回包摘要
- `error_message: Text null`
- `sent_at: DateTime(timezone=True) null`
- `created_at`
- `updated_at`

唯一约束：

- `UNIQUE (pts_work_order_id, remind_type)`

索引建议：

- `(module_code, created_at desc)`
- `(send_status, created_at desc)`
- `(plan_finish_date, remind_type)`

### 6.8 Sender 设计

新增 sender 抽象，建议结构：

- `services/reminders/senders/base.py`
- `services/reminders/senders/dingtalk_sender.py`
- `services/reminders/senders/log_sender.py`

建议定义统一协议：

- `ReminderSender`
  - `sender_type`
  - `is_available() -> bool`
  - `send(message) -> SendResult`

#### 发送优先级

sender 解析策略固定为：

1. 若钉钉 sender 配置完整，则优先使用钉钉
2. 若钉钉 sender 配置缺失，则使用 `log + db record` fallback

本次“fallback”语义固定为：

- sender 配置缺失时的正常降级路径
- 不是“钉钉发送失败后再偷偷补发一次 log”

即：

- 钉钉未配置：
  - `sender_type = "log_fallback"`
  - `message_channel = "log_fallback"`
  - `send_status = "sent"`
- 钉钉已配置但发送失败：
  - `sender_type = "dingtalk"`
  - `message_channel = "dingtalk"`
  - `send_status = "failed"`

这样可以避免真实通道故障被 fallback 掩盖。

#### 钉钉 sender 配置

建议新增最小配置：

- `INSPECTION_DEADLINE_REMINDER_DINGTALK_WEBHOOK`
- `INSPECTION_DEADLINE_REMINDER_DINGTALK_SECRET`
  - 可选

钉钉消息内容建议包含：

- 提醒类型中文标签
- 客户名称
- 工单 ID
- 截止日期
- 剩余/逾期天数
- 工单链接

### 6.9 Scheduler Job 注册方式

本次采用独立 job，不复用 `sync:{module_code}` 的注册方式。

建议在 `scheduler/jobs.py` 中新增：

- `register_inspection_deadline_reminder_job(...)`
- `run_inspection_deadline_reminder_job(...)`

注册策略：

- 在现有 `register_jobs()` 中保留 sync job 注册逻辑
- 在末尾追加 reminder job 注册逻辑
- 独立 job id：
  - `reminder:inspection-deadline`

建议新增配置：

- `INSPECTION_DEADLINE_REMINDER_ENABLED=false`
- `INSPECTION_DEADLINE_REMINDER_CRON="0 9 * * *"`

说明：

- scheduler 时区继续复用现有 `scheduler_timezone=Asia/Shanghai`
- reminder job 独立开关控制
- reminder job 不依赖 `module_configs.sync_cron`

### 6.10 是否补最小 API 管理入口

本次建议补最小 API，但只做“第一版运维/调试入口”，不做前端页面。

接口先放在：

- `apps/api/routers/ops.py`

说明：

- 这是第一版最小管理入口，方便运维与调试
- 不代表长期最终归属
- 后续若 reminder 子能力继续扩展，再考虑拆出独立 router / domain API

建议接口：

- `GET /api/ops/inspection-deadline-reminders`
  - 查看最近提醒记录
  - 支持 `limit / send_status / remind_type`
- `POST /api/ops/inspection-deadline-reminders/run`
  - 手动触发一次 reminder job
  - 建议支持：
    - `dry_run: bool = false`
    - `today: str | None = None`

说明：

- `dry_run=true` 时：
  - 只返回候选与判定结果
  - 不写库、不发消息
- `today` 仅用于测试/验收覆盖，正式 scheduler 不传

本次不做：

- console 页面
- 手动重发单条 reminder

### 6.11 配置接入方案

建议在 `core/config.py` 与 `.env.example` 中新增以下配置：

- `inspection_deadline_reminder_enabled: bool = False`
- `inspection_deadline_reminder_cron: str = "0 9 * * *"`
- `inspection_deadline_reminder_sender: str = "auto"`
- `inspection_deadline_reminder_dingtalk_webhook: str = ""`
- `inspection_deadline_reminder_dingtalk_secret: str = ""`
- `inspection_deadline_reminder_query_limit: int = 500`

说明：

- 继续复用：
  - `PTS_BASE_URL`
  - `PTS_COOKIE_HEADER`
  - `PTS_VERIFY_SSL`
- reminder collector 不新增独立 PTS 会话体系

## 7. 预计新增 / 修改文件

以下为当前建议的预计文件清单。

### 7.1 预计新增文件

- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/plans/2026-04-15_pts_inspection_deadline_reminder_plan.md`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/migrations/versions/20260415_0004_deadline_reminders.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/models/deadline_reminder.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/repositories/deadline_reminder_repo.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/schemas/deadline_reminder.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/__init__.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/schemas.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/rules.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/collectors/inspection_deadline_collector.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/inspection_deadline_service.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/senders/base.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/senders/dingtalk_sender.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/reminders/senders/log_sender.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/test_inspection_deadline_reminders.py`

### 7.2 预计修改文件

- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/core/config.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/.env.example`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/models/__init__.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/scheduler/jobs.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/apps/api/routers/ops.py`
- `/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/tests/conftest.py`

说明：

- 若实现中希望避免新增独立 schema 文件，也可把少量 response/request schema 合并到 `schemas/ops.py`
- 若实现中需要共享少量 PTS 运行时 helper，可能还会新增一个轻量 helper 文件，但本次不计划 refactor 现有 inspection execute 主链路

## 8. 分步骤实施计划

### 步骤 1：落库模型与迁移

- 新增 `deadline_reminders` 模型
- 新增 Alembic 迁移
- 建立唯一约束与必要索引

### 步骤 2：实现 repository

- 支持：
  - 批量查重键查询
  - 创建 pending reminder
  - 更新发送结果
  - 最近记录列表查询

### 步骤 3：先完成 PTS 字段口径确认

- 确认是否已有可复用 PTS 查询能力
- 确认真实字段名、状态值枚举、`plan_finish_time` 实际格式
- 若现有能力不足，再新增最小 collector

### 步骤 4：实现 reminder rule 与 DTO

- 固化 `due_in_3d / due_in_1d / overdue`
- 固化 Asia/Shanghai 日期归一逻辑
- 固化状态过滤与服务类型过滤

### 步骤 5：实现 PTS deadline collector

- 新增独立 collector
- 仅采集 reminder 所需最小字段
- 输出统一 DTO
- 不接入 `SyncService`

### 步骤 6：实现 sender 抽象与最小 sender

- 抽象 `ReminderSender`
- 新增 `DingtalkReminderSender`
- 新增 `LogReminderSender`
- 实现 sender resolver

### 步骤 7：实现 reminder service

- 串起：
  - collect
  - rule evaluate
  - business dedupe
  - insert pending
- send
- update result
- 增加 `dry_run` 支持

### 步骤 8：接入 scheduler

- 在 `scheduler/jobs.py` 注册独立 reminder job
- 接入独立 config 开关与 cron

### 步骤 9：补最小 API 管理入口

- 查看 reminder 记录
- 手动触发 job
- 明确 dry-run 输出结构

### 步骤 10：补测试与验收

- 单测
- service 集成测试
- scheduler 注册测试
- API 测试

## 9. 测试与验收方案

### 9.1 自动化测试

建议至少覆盖以下场景：

#### 规则判定

- `plan_finish_date = today + 3`
  - 生成 `due_in_3d`
- `plan_finish_date = today + 1`
  - 生成 `due_in_1d`
- `plan_finish_date < today`
  - 生成 `overdue`
- `plan_finish_date = today`
  - 不生成提醒

#### 过滤逻辑

- `service_type != 巡检工单`
  - 跳过
- `plan_finish_time` 为空
  - 跳过
- `status_text in 已完成/已关闭/已取消`
  - 跳过

#### 幂等

- 同一工单同一 `remind_type` 已存在记录
  - 业务查重跳过
- 并发/重复插入触发唯一约束
  - service 正常吞掉冲突并记为 duplicate skip

#### sender

- 钉钉配置缺失
  - 自动走 `log_fallback`
  - `send_status = sent`
- 钉钉配置存在且发送成功
  - `send_status = sent`
- 钉钉配置存在但发送失败
  - `send_status = failed`

#### service 集成

- 一次 run 成功插入 reminder 记录并更新发送结果
- 第二次 run 不重复发同一 `pts_work_order_id + remind_type`

#### scheduler

- 开启 reminder config 后注册独立 job
- 关闭开关时不注册

#### API

- `GET /api/ops/inspection-deadline-reminders`
  - 能返回记录列表
- `POST /api/ops/inspection-deadline-reminders/run`
  - 支持 dry_run
  - 支持真实 run

### 9.2 最小验收标准

本次功能完成后，应满足以下验收标准：

- inspection 现有 sync / recognize / execute 回归不受影响
- reminder job 可独立运行
- reminder 仅依赖 PTS 数据
- `due_in_3d / due_in_1d / overdue` 三类规则生效
- 同一工单同一 remind_type 只产生一条记录
- 钉钉未配置时可通过 `log + db` 跑通
- 钉钉配置后可优先走钉钉 sender
- API 能查看 reminder 记录并手动触发

## 10. 风险

### 10.1 PTS 查询字段不稳定

风险：

- reminder collector 依赖 PTS 返回 `service_type / plan_finish_time / status`
- 若实际 GraphQL 字段名与预期不一致，collector 需要适配

缓解：

- collector 先归一最小字段 DTO，再让 reminder service 消费
- 首轮实现保持 query 最小化，避免拉过多字段

### 10.2 严格幂等与失败不可自动重试的冲突

风险：

- 若 sender 真失败，唯一约束会阻止自动补发

缓解：

- 本次优先保证“不重复提醒”
- 失败保留 `failed` 状态与完整 sender_result
- 后续若要重试，单独设计显式手动重发能力

### 10.3 Asia/Shanghai 与原始时间格式差异

风险：

- PTS 原始 `plan_finish_time` 可能是：
  - ISO 字符串
  - 时间戳
  - 本地时间字符串

缓解：

- 在 rule 层集中做时间归一
- 测试覆盖多种时间格式

### 10.4 Scheduler 与手动 API 同时触发

风险：

- 两条入口同时跑可能导致重复发送

缓解：

- 业务查重 + DB 唯一约束双保险
- 如有需要，可再加单 job runtime lock，但不是本次必需条件

## 11. 回滚方式

本次方案可低风险回滚，步骤如下：

1. 配置层回滚
   - 关闭 `INSPECTION_DEADLINE_REMINDER_ENABLED`
   - scheduler 不再注册 reminder job
2. API 层回滚
   - 保留代码但不暴露入口，或直接回退相关 router 改动
3. 数据层回滚
   - Alembic downgrade 删除 `deadline_reminders` 表
4. 运行层回滚
   - sender 配置清空后，系统不会再走钉钉发送

由于本次不改 inspection 主链路，因此即使完整回滚 reminder 子能力，也不会影响现有 inspection 的 sync / recognize / execute 行为。

## 12. 实施建议

实施时建议坚持以下原则：

- reminder 子能力全程旁路，不侵入 inspection 主链路
- 不把 reminder 结果塞进现有 `task_runs`
- 先用最小 API + log fallback 跑通，再考虑 UI
- 避免为了“复用”去重构 `InspectionRealRunner`
- 只复用现有 config / scheduler / repo / model 风格，不做重型抽象升级

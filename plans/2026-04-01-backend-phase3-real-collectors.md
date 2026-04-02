# 第三阶段后端计划：真实采集接入

## 1. 背景

当前项目已完成第二阶段“采集 -> 识别 -> 规划”主链路增强，具备以下基础能力：

- `POST /api/sync/run` 可完成 `source_snapshots -> normalized_records -> task_plans` 的完整写库
- collector 契约已统一为 `validate()`、`healthcheck()`、`collect()`
- recognizer 与 planner 已有稳定输出结构
- snapshot / record / task 详情接口与模块 latest 接口已可用
- 第二阶段仍然基于 mock collectors，未接入真实采集

这意味着当前主链路已经具备“后处理能力”，但“采集输入”仍是伪造示例数据。第三阶段要解决的是：在保持现有识别与规划链路可用的前提下，把 mock collector 升级为真实可落地的采集器，且采集策略必须以结构化数据优先、页面 state 次之、Playwright fallback 最后。

本阶段仍然不接真实执行器，重点是把“真实采集 -> 审计入库 -> 识别 -> 规划”做扎实。

本阶段的“真实 collector”实现目标进一步固定为：

- 支持真实 source 配置
- 支持 fake transport / fixture payload 驱动
- 支持后续接真实钉钉
- 当前阶段测试优先使用 fixture / fake transport
- 不要求一开始就强依赖真实线上钉钉环境

也就是说，本阶段优先把 collector 架构做对，而不是在同一轮里强行解决所有真实线上环境问题。

## 2. 目标

本次实施完成后，应达到以下目标：

- 为 `visit` / `inspection` / `proactive` 接入真实 collector
- collector 基于真实 source 配置工作，而不是硬编码 mock 源
- collector 内部按固定优先级执行：
  1. 请求/缓存结构化数据
  2. 页面 state/store 数据
  3. Playwright fallback
- 真实 collector 输出统一 `CollectResult`
- 真实采集结果可写入 `source_snapshots`
- 真实采集后的 `raw_rows` 可继续驱动 recognizer，生成 `normalized_records`
- 真实采集后的标准化结果可继续驱动 planner，生成 `task_plans`
- 审计信息中显式包含 `data_source`、`sync_status`、`sync_error`、`raw_columns`、`raw_rows`、`raw_meta`、`collector diagnostics`
- 补齐真实 collector 的配置校验、失败场景、空数据场景与真实模式写库测试

## 3. 范围

本次实施范围包含：

- 真实 collector 基础架构设计与实现
- source 配置结构设计、加载方式与默认配置更新
- `module_configs` 现有使用方式升级为真实 source 配置驱动
- `visit_collector` / `inspection_collector` / `proactive_collector` 从 mock 版本升级为真实模式优先
- 结构化数据抓取、页面 state/store 抓取、Playwright fallback 的优先级编排
- collector diagnostics 设计与入库
- `CollectResult` / sync service / snapshot 审计链路补强
- 真实 rows 输入下 recognizer 的适配与验证
- 测试补充：
  - 配置校验
  - 配置缺失/非法失败
  - 空 rows 的 partial/failed
  - `/api/sync/run` 真实 collector 模式写库
  - recognizer 处理真实 rows 的完整结构输出
- 回写本 plan 的实施记录、偏差、验证结果和待跟进事项

## 4. 非范围

本次明确不做：

- 真实执行器
- 前端页面
- 工单创建/闭环
- 巡检报告上传
- PTS 联调
- 最终生产级钉钉认证与密钥管理平台接入
- 复杂登录态托管或账号池
- 全量浏览器 DOM 扫描作为主路径

## 5. 当前现状 / 已知问题

当前现状：

- `services/module_registry.py` 中的 `MODULE_DEFINITIONS` 仍是 mock 风格元数据
- `models/module_config.py` 只有 `module_code/module_name/source_url/enabled/sync_cron/extra_config`，尚未显式承载 `source_doc_key`、`source_view_key`、`collector_type`
- 三个 collector 目前仍然返回硬编码 rows
- `PlaywrightFallbackCollector` 只是 stub，没有与真实 collector 链路编排在一起
- sync service 当前默认直接从 `COLLECTOR_REGISTRY[module_code]()` 实例化 collector，没有配置注入与 source 配置装配层
- recognizer 已能处理“类似真实结构”的 rows，但还没有针对真实采集结果做专门测试

已知问题：

- 真实钉钉文档的数据入口不稳定，可能有多种来源：
  - 已缓存/请求返回的结构化 JSON
  - 页面内嵌的 state/store
  - 最后才是 Playwright 驱动浏览器
- 第三阶段若要接“真实 collector”，需要先定义明确的 source 配置和 transport 层，否则 collector 实现会和环境耦合过深
- `module_configs` 当前表结构可能不足以显式表达 collector 类型与 source 键值，可能需要新增迁移
- 如果直接把真实 HTTP 请求写死在 collector 里，会导致可测试性差，因此需要抽 transport/client 层
- 真实采集可能出现：
  - 配置缺失
  - 页面可达但数据为空
  - 结构化接口返回异常
  - state/store 解析失败
  - fallback 不可用

## 6. 技术方案

### 6.1 Collector 架构分层

第三阶段 collector 建议拆为三层：

- `collector orchestration layer`
  - 决定采集优先级
  - 汇总 diagnostics
  - 统一产出 `CollectResult`
- `source client / fetcher layer`
  - 负责结构化请求、页面 state/store 提取、fallback 调用
- `normalization prep layer`
  - 把真实返回整理成统一 `raw_columns/raw_rows/raw_meta`

collector 对外仍然暴露统一接口：

- `validate()`
- `healthcheck()`
- `collect()`

### 6.2 固定采集优先级

每个真实 collector 内部必须固定按以下顺序尝试：

1. 请求/缓存结构化数据
2. 页面 state/store 数据
3. Playwright fallback

约束：

- DOM/浏览器扫描不能再作为主路径
- 只有在前两种方式不可用或失败时，才允许走 Playwright fallback
- 每一步都要记录 diagnostics：
  - 是否尝试
  - 是否成功
  - 失败原因
  - 命中数据量
  - 最终选中的 `data_source`

### 6.3 真实 source 配置

source 配置需要支持至少以下字段：

- `module_code`
- `source_url`
- `source_doc_key`
- `source_view_key`
- `enabled`
- `collector_type`
- `extra_config`

建议实现方式：

- 数据库存储：`module_configs`
- 代码默认值：`services/module_registry.py`
- 加载流程：
  1. 启动或同步时加载数据库配置
  2. 数据库缺失则使用代码默认值初始化
  3. collector 从已解析的 `ModuleSourceConfig` 领域对象中读取配置，而不是直接访问裸字典

`extra_config` 建议承载：

- 请求 headers/cookies 占位
- state/store 解析 hint
- fallback 开关
- endpoint 模板
- 采集超时、重试、分页等参数

### 6.4 数据模型与迁移策略

现有 `module_configs` 可能需要补充以下列：

- `source_doc_key`
- `source_view_key`
- `collector_type`

如果决定保持表结构不动，也至少要把这些字段收敛到 `extra_config`，但从可读性与可查询性看，更推荐新增迁移，把这几个核心字段显式化。

本阶段字段策略固定采用方案 A：

- 新增迁移，扩展 `module_configs`
- 将以下字段显式入表：
  - `source_doc_key`
  - `source_view_key`
  - `collector_type`
- 其余 collector 细节继续放入 `extra_config`

### 6.5 真实采集结果与审计

`CollectResult` 与 `source_snapshots` 审计目标保持一致，要求真实采集返回中必须带：

- `data_source`
- `sync_status`
- `sync_error`
- `raw_columns`
- `raw_rows`
- `raw_meta`
- `collector diagnostics`

建议落地方式：

- `raw_meta` 中新增：
  - `collector`
  - `collector_type`
  - `diagnostics`
  - `attempt_chain`
  - `selected_source`
- `data_source` 值标准化为：
  - `structured_api`
  - `cached_payload`
  - `page_state`
  - `playwright_fallback`
- `sync_status` 标准化为：
  - `success`
  - `partial`
  - `failed`

### 6.6 Real Collector 实现策略

三类 collector 的总体结构一致：

- `visit_collector`
- `inspection_collector`
- `proactive_collector`

每个 collector 中应实现：

1. 配置校验
2. 尝试结构化数据抓取
3. 若失败，尝试页面 state/store
4. 若仍失败，尝试 Playwright fallback
5. 将命中结果转为统一 `CollectResult`

建议新增公共基类，例如：

- `ConfiguredCollectorBase`
- `StructuredFetchMixin`
- `StatePayloadMixin`

这样可以减少三类 collector 的重复逻辑。

### 6.7 Recognizer 验证策略

本阶段不重写 recognizer 核心规则，但要验证 recognizer 对真实 rows 生效。

至少验证：

- visit 真实数据能生成 `normalized_records`
- inspection 真实数据能生成 `normalized_records`
- proactive 真实数据能生成 `normalized_records`

必要时对 recognizer 做以下适配：

- 允许真实 rows 的字段命名与 mock 不完全一致
- 引入更稳的字段别名映射
- 在字段缺失时输出 `partial` 与 `unresolved_fields`

### 6.8 测试策略

本阶段测试至少分为四类：

- 配置测试
  - collector 配置校验通过
  - 配置缺失/非法时报错
- collector 行为测试
  - 结构化数据命中
  - state/store 命中
  - fallback 命中
  - 空 rows 时 `partial/failed`
- sync 集成测试
  - `/api/sync/run` 在真实 collector 模式下写入 `source_snapshots`、`normalized_records`、`task_plans`
- recognizer 兼容性测试
  - 对真实 rows 输入输出完整结构

如果真实外部依赖不可用，测试应通过 fake transport / fixture payload 驱动，而不是直接依赖真实线上系统。

本阶段的测试优先策略固定为：

- 默认优先使用 fixture / fake transport
- collector 架构要为未来真实钉钉接入保留扩展点
- 真实线上环境联调不作为本阶段完成前置条件

## 7. 分步骤实施计划

### 步骤 1：明确 source 配置模型

- 确认 `module_configs` 是否需要新增列
- 设计 `ModuleSourceConfig` 领域对象
- 明确默认配置与数据库加载流程

### 步骤 2：抽 transport / fetcher 层

- 抽象结构化请求 client
- 抽象页面 state/store 提取器
- 保留 Playwright fallback stub/adapter
- 让 collector 依赖这些抽象层，而不是硬编码请求

### 步骤 3：重构 collector 基类

- 从 `MockCollectorBase` 演进到可配置真实 collector 基类
- 统一 attempt chain、diagnostics、错误处理
- 固化采集优先级

### 步骤 4：实现 3 个真实 collector

- `visit_collector`
- `inspection_collector`
- `proactive_collector`

要求每个 collector 都能：

- 读真实 source 配置
- 按优先级尝试采集
- 输出统一 `CollectResult`

### 步骤 5：接通 sync service

- 在 `SyncService` 中接入真实 collector 选择与配置注入
- 保证真实采集结果仍能进入：
  - `source_snapshots`
  - `normalized_records`
  - `task_plans`

### 步骤 6：补强 recognizer 兼容性

- 根据真实 rows 样式调整字段映射
- 确认 recognizer 输出完整结构
- 必要时补别名映射策略

### 步骤 7：补测试

- 配置校验测试
- source 配置缺失/非法失败测试
- 空 rows partial/failed 测试
- 真实 collector 模式 `/api/sync/run` 写库测试
- recognizer 处理真实 rows 测试

### 步骤 8：验证与收尾

- 跑单测 / 集成测试
- 记录真实/仿真采集结果样例
- 回写 plan 的实施记录、偏差、验证结果、待跟进事项

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase3-real-collectors.md`
- `models/module_config.py`
- 可能新增 Alembic 迁移文件
- `repositories/module_config_repo.py`
- `services/module_registry.py`
- `services/collectors/base.py`
- `services/collectors/visit_collector.py`
- `services/collectors/inspection_collector.py`
- `services/collectors/proactive_collector.py`
- `services/collectors/playwright_fallback.py`
- 可能新增：
  - `services/collectors/http_client.py`
  - `services/collectors/state_extractor.py`
  - `services/collectors/source_config.py`
  - `services/collectors/diagnostics.py`
- `schemas/sync.py`
- `services/sync_service.py`
- `services/recognizers/visit_recognizer.py`
- `services/recognizers/inspection_recognizer.py`
- `services/recognizers/proactive_recognizer.py`
- `tests/`
- 可能新增 `tests/fixtures/` 下的真实 payload 样例

## 9. 风险与缓解

### 风险 1：真实钉钉文档结构不稳定

缓解：

- 采用多级采集优先级而不是单一路径
- 记录 diagnostics，便于判断失败点
- 通过配置与解析 hint 降低硬编码

### 风险 2：外部依赖导致测试不稳定

缓解：

- 测试中优先使用 fake transport / fixture payload
- 把“真实接入能力”和“真实在线环境依赖”解耦

### 风险 3：配置模型不清晰导致 collector 与环境强耦合

缓解：

- 先定义统一 source 配置对象，再落 collector 实现
- 避免在 collector 内直接读取零散环境变量

### 风险 4：空 rows 场景语义不清

缓解：

- 事先定义：
  - 可达但无数据：`partial` 或 `success(row_count=0)` 的判定规则
  - 配置错误或采集失败：`failed`
- 在测试中固定这些语义

### 风险 5：直接修改现有 collector 造成第二阶段稳定性回退

缓解：

- 以可配置方式演进，保留 mock/fixture 驱动能力
- 在单测与集成测试中同时覆盖真实模式与回退模式

## 10. 验收标准

满足以下条件视为本次任务完成：

- 三个模块都已接入真实 collector：
  - `visit_collector`
  - `inspection_collector`
  - `proactive_collector`
- collector 基于真实 source 配置运行
- collector 严格按“结构化数据 -> 页面 state/store -> Playwright fallback”顺序采集
- 真实采集结果统一输出 `CollectResult`
- 真实采集结果可写入 `source_snapshots`
- 真实采集审计中包含：
  - `data_source`
  - `sync_status`
  - `sync_error`
  - `raw_columns`
  - `raw_rows`
  - `raw_meta`
  - `collector diagnostics`
- 真实采集后仍可生成：
  - `normalized_records`
  - `task_plans`
- 至少验证三类模块真实 rows 都能驱动 recognizer 输出完整结构
- 测试覆盖：
  - collector 配置校验
  - source 配置缺失/非法失败
  - 空 rows 的 partial/failed
  - `/api/sync/run` 真实 collector 模式写库
  - recognizer 在真实 rows 下输出完整结构

## 11. 验证步骤

计划中的验证步骤如下：

1. 校验 source 配置加载逻辑
2. 分别验证三个 collector 的 `validate()` 与 `healthcheck()`
3. 验证优先级链路：
   - 结构化数据命中
   - state/store 命中
   - fallback 命中
4. 验证空 rows / 失败时的 `sync_status`
5. 运行 `/api/sync/run` 集成测试，确认真实 collector 模式下写入：
   - `source_snapshots`
   - `normalized_records`
   - `task_plans`
6. 验证 recognizer 在真实 rows 下输出：
   - `normalized_records`
   - `field_mapping`
   - `field_confidence`
   - `field_evidence`
   - `field_samples`
   - `unresolved_fields`
   - `recognition_status`
7. 视环境情况补一次手工 smoke

## 12. 实施记录（先留空）

- 实际完成内容
  - 已将 `module_configs` 模型扩展为显式包含：
    - `source_doc_key`
    - `source_view_key`
    - `collector_type`
  - 已新增 Alembic 迁移：
    - `20260401_0002_expand_module_configs_for_real_collectors.py`
  - 已新增 `ModuleSourceConfig` 领域对象，统一承载 collector 的真实 source 配置
  - 已将 `services/module_registry.py` 的默认配置升级为真实 source 配置风格，并默认指向 fixture payload
  - 已实现 fixture/fake transport 优先的 collector fetcher 抽象：
    - `FixturePayloadFetcher`
    - `DingtalkPayloadFetcher` 预留位
    - `build_fetcher()` 选择逻辑
  - 已实现 collector diagnostics 结构与 attempt chain 记录
  - 已将 collector 基类从 mock 风格重构为配置驱动的 `ConfiguredCollectorBase`
  - 已实现 3 个真实 collector：
    - `VisitCollector`
    - `InspectionCollector`
    - `ProactiveCollector`
  - 已固定 collector 内部优先级：
    1. 结构化数据
    2. 页面 state/store
    3. Playwright fallback
  - 已增强 Playwright fallback，使其支持 fixture payload 或 stub 返回
  - 已更新 `SyncService`，使其通过 `module_configs` 加载 source 配置并驱动真实 collector
  - 已提供默认 fixture payload，确保当前阶段不依赖真实线上钉钉环境
  - 已补充测试：
    - collector 配置校验通过
    - 配置缺失/非法失败
    - 空 rows -> `partial`
    - `/api/sync/run` 在真实 collector 模式下写库
    - recognizer 在真实 rows 输入下输出完整结构
  - 已抓取一次 fixture 驱动的 `/api/sync/run` 真实返回样例

- 与原计划偏差
  - 本轮未实现真实线上钉钉 transport，只保留 `DingtalkPayloadFetcher` 扩展位；这是与已确认设计决策一致的主动收敛，不属于遗漏
  - recognizer 核心规则本轮未重写，仅通过真实 rows/fixture rows 做兼容性验证；当前输入结构已足以支撑第三阶段目标
  - `/api/sync/run` 的验证样例本轮以 fixture 驱动 real collector 完成，而不是依赖真实线上环境

- 验证结果
  - `python3 -m compileall apps core models repositories schemas services tests migrations` 通过
  - `.venv/bin/pytest -q` 通过，结果为 `13 passed`
  - 已使用临时 PostgreSQL 容器验证 Alembic：
    - `alembic upgrade head` 成功执行到 `20260401_0002`
  - 已使用 fixture 驱动 + 临时 PostgreSQL 容器 + FastAPI TestClient 验证：
    - `POST /api/sync/run` 写入 `source_snapshots`
    - recognizer 生成 `normalized_records`
    - planner 生成 `task_plans`
  - 已验证 collector diagnostics 会进入 `raw_meta`

- 待跟进事项
  - 后续第四阶段可落真实钉钉 transport 实现，并挂接到当前 `DingtalkPayloadFetcher` 抽象下
  - 后续可为 state/store 提取增加更明确的页面解析 hint 与选择器策略
  - 后续可将 fixture payload 进一步扩充为更多边界场景样本，如缺字段、字段别名漂移、分页数据
  - 后续可根据真实线上数据表现，调整空 rows 对应 `partial/failed` 的最终产品语义

## 13. 遗留问题（先留空）

- 当前真实 collector 的“真实”主要体现在配置驱动与真实采集优先级架构，默认运行仍基于 fixture/fake transport，而非真实线上钉钉
- `DingtalkPayloadFetcher` 目前仅是扩展位，尚未接真实请求鉴权与返回解析
- `state/store` 解析仍使用 fixture 驱动，真实页面 state 结构适配需要在后续联调阶段补齐

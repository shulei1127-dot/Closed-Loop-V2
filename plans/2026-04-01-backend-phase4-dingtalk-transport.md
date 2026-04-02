# 第四阶段后端计划：真实钉钉 Transport 接入

## 1. 背景

当前项目已完成第三阶段“真实 collector 架构接入”，主要成果包括：

- `module_configs` 已显式支持：
  - `source_doc_key`
  - `source_view_key`
  - `collector_type`
- collector 已从 mock 风格演进为配置驱动架构
- 当前 collector 采集优先级已固定为：
  1. 结构化数据
  2. 页面 state/store
  3. Playwright fallback
- `FixturePayloadFetcher` 已可作为 fake transport / fixture transport 驱动主链路
- `DingtalkPayloadFetcher` 已预留扩展位，但尚未实现真实请求
- 三个模块的 collector 已可在 fixture 驱动下进入：
  - `source_snapshots`
  - `normalized_records`
  - `task_plans`

这说明目前架构已经为真实 transport 接入做好了接口准备，但“真实钉钉请求、真实认证、真实响应解析”仍未打通。第四阶段的目标，就是把 `fetchers.py` 中的 `DingtalkPayloadFetcher` 从占位扩展位升级为真实可工作的 transport 层，并让三类 collector 能基于真实 source 配置接入钉钉 payload，继续进入现有主链路。

本阶段仍然不涉及执行器、闭环动作和前端。

本阶段“真实 transport”的优先目标进一步固定为：

- transport 抽象可工作
- 配置注入清晰
- fake / fixture / real 三种模式可切换
- diagnostics 完整
- 不以“一次性打通真实线上钉钉全部细节”作为本阶段唯一成功标准

## 2. 目标

本次实施完成后，应达到以下目标：

- `DingtalkPayloadFetcher` 实现真实 transport 能力
- 支持真实请求、真实响应解析
- 支持 cookies / headers / 认证注入
- 保留 fake transport / fixture transport
- 三个 collector 可切换到真实 transport 模式：
  - `visit_collector`
  - `inspection_collector`
  - `proactive_collector`
- 真实 transport 所需配置通过统一方式注入：
  - `module_configs`
  - `extra_config`
  - 环境变量
- transport 失败分类清晰，并写入 diagnostics / `raw_meta` / `sync_error`
- `/api/sync/run` 在真实 transport 模式下仍能继续写入：
  - `source_snapshots`
  - `normalized_records`
  - `task_plans`
- 测试覆盖 fake / fixture / real 三种 transport 模式切换与异常分支

## 3. 范围

本次实施范围包含：

- 在 `services/collectors/fetchers.py` 中实现真实 `DingtalkPayloadFetcher`
- 设计 transport client、认证注入、请求封装与响应解析策略
- 将三类 collector 接通真实 transport 模式
- 配置加载和依赖注入整理：
  - `module_configs`
  - `extra_config`
  - 环境变量
- transport 失败分类与 diagnostics 增强
- 更新 sync 主链路，确保真实 transport 输出仍能进入现有识别/规划链路
- 补充测试：
  - fake transport 仍然可用
  - 配置缺失失败
  - 响应异常失败
  - 真实 transport 成功时写库
  - fixture / fake / real 切换正确
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 执行器
- 前端
- 工单闭环
- 巡检报告上传
- PTS 联调
- 长周期登录态托管平台
- 复杂账号池/浏览器池
- 钉钉外的其他数据源接入

## 5. 当前现状 / 已知问题

当前现状：

- `FixturePayloadFetcher` 已可用
- `DingtalkPayloadFetcher` 仍是 `NotImplementedError`
- collector 已通过 `collector_type` 做 transport 分流，但默认仍偏向 fixture
- source 配置目前已具备真实 transport 的基础字段，但认证相关配置仍主要依赖 `extra_config` 与未来环境变量约定
- 当前 diagnostics 能记录 attempt chain，但对 transport 失败原因的分类还不够细

已知问题：

- 真实钉钉请求通常依赖 cookies、headers、页面上下文或其他认证信息
- 如果把认证或请求细节散落在 collector 中，会破坏可维护性
- 钉钉不同页面的结构化 payload / state/store 位置可能不同，需要 transport 层具备可配置性
- 真实 transport 的失败语义必须清晰，否则后续排障困难
- 测试不能强依赖真实线上钉钉环境，需要保留可注入 fake transport 的方式

## 6. 技术方案

### 6.1 Transport 分层

建议把 fetcher/transport 分成三层：

- `transport config layer`
  - 从 `module_configs`、`extra_config`、环境变量拼出运行期配置
- `http transport layer`
  - 负责真实请求发送、headers/cookies 注入、超时、重试、状态码处理
- `payload extraction layer`
  - 负责从真实响应中提取：
    - 结构化数据 payload
    - 页面 state/store payload

`DingtalkPayloadFetcher` 应成为这三层的整合入口，而不是仅仅写几个硬编码请求。

### 6.2 配置注入策略

真实 transport 所需配置统一从三处注入：

- `module_configs`
  - 核心 source 标识
  - collector 类型
- `extra_config`
  - endpoint 模板
  - 请求方法
  - headers/cookies key 名称
  - payload 路径提示
  - state/store 提取 hint
  - fallback 开关
- 环境变量
  - 认证 cookies
  - 动态 token
  - 公共 headers
  - 可能的 base URL 覆盖项

约束：

- 不允许在 collector 中硬编码真实 cookie/header/token
- collector 只读取已整理好的 source config / transport config

### 6.3 `DingtalkPayloadFetcher` 设计

`DingtalkPayloadFetcher` 需要支持：

- `fetch_structured(config)`
  - 发起真实结构化请求
  - 解析 JSON 或类 JSON 响应
  - 返回统一 payload 对象
- `fetch_state(config)`
  - 从页面请求或页面源码/state 入口提取 payload
  - 输出统一 payload 对象

建议新增或抽象以下能力：

- `build_request_context(config)`
- `build_auth_headers(...)`
- `build_auth_cookies(...)`
- `parse_structured_response(...)`
- `parse_state_payload(...)`

### 6.4 失败分类

真实 transport 的失败必须显式区分：

- 认证失败
- 配置缺失
- 请求失败
- 响应为空
- payload 解析失败
- fallback 命中

建议落地方式：

- `sync_error`：给出最终用户可读摘要
- `raw_meta.collector_diagnostics`：记录分步骤失败细节
- 每个 attempt 记录：
  - `step`
  - `attempted`
  - `success`
  - `error_type`
  - `error`
  - `http_status`
  - `data_source`
  - `row_count`

### 6.5 Collector 接通方式

三类 collector 不应各自单独实现 transport 逻辑，而应继续复用统一的 `ConfiguredCollectorBase`，只在配置和字段语义上区分模块。

这意味着第四阶段的关键改动点不应主要在 collector 文件本身，而是在：

- `fetchers.py`
- source config 解析
- diagnostics
- sync service / config loading

collector 文件的职责更多是声明模块身份与复用基类。

### 6.6 测试策略

本阶段测试至少分为以下几类：

- 模式保留测试
  - fake transport 仍然可用
  - fixture transport 仍然可用
- 配置失败测试
  - real transport 模式下缺失认证配置时报错
  - endpoint 缺失时报错
- transport 异常测试
  - 请求失败
  - 响应异常
  - payload 解析失败
- 成功链路测试
  - real transport 模式下 `/api/sync/run` 继续写库
- 模式切换测试
  - fixture / fake / real 三种模式切换正确

测试优先使用：

- fake transport / mocked HTTP client
- fixture payload

不把真实线上钉钉可用性作为本阶段测试前提。

本阶段验证策略明确为：

- 自动化测试继续以 fake / fixture 为主
- real transport 只做最小手工 smoke
- 不把真实线上钉钉环境可用性作为 CI / 本地测试的硬前提

## 7. 分步骤实施计划

### 步骤 1：梳理 transport 配置模型

- 明确哪些配置来自 `module_configs`
- 明确哪些配置来自 `extra_config`
- 明确哪些配置来自环境变量
- 形成统一 transport config 对象

### 步骤 2：实现真实 HTTP transport 层

- 在 `fetchers.py` 中实现真实 `DingtalkPayloadFetcher`
- 封装真实请求逻辑
- 接入 headers / cookies / token 注入

### 步骤 3：实现真实响应解析

- 解析结构化接口响应
- 解析页面 state/store 响应
- 统一转为 collector 可消费的 payload 结构

### 步骤 4：增强 diagnostics 与失败分类

- 增加 `error_type`
- 增加 HTTP 相关诊断信息
- 区分认证失败、配置缺失、请求失败、响应为空、解析失败、fallback 命中

### 步骤 5：接通三类 collector

- 让 `visit` / `inspection` / `proactive` collector 能使用真实 transport
- 确认 collector 仍按固定优先级工作

### 步骤 6：接通 sync 主链路

- 验证 `/api/sync/run` 在真实 transport 模式下继续写库
- 确保 `source_snapshots` 审计信息完整
- 确保后续 recognizer / planner 不受影响

### 步骤 7：补测试

- fake transport 可用
- real transport 配置缺失失败
- real transport 响应异常失败
- real transport 成功写库
- fixture / fake / real 模式切换正确

### 步骤 8：验证与收尾

- 跑测试
- 如环境允许，做最小手工 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase4-dingtalk-transport.md`
- `services/collectors/fetchers.py`
- `services/collectors/base.py`
- `services/collectors/source_config.py`
- `services/collectors/diagnostics.py`
- `services/collectors/playwright_fallback.py`
- `services/collectors/visit_collector.py`
- `services/collectors/inspection_collector.py`
- `services/collectors/proactive_collector.py`
- `services/module_registry.py`
- `services/sync_service.py`
- `repositories/module_config_repo.py`
- `core/config.py`
- 可能新增 transport 相关辅助文件
- `tests/`

## 9. 风险与缓解

### 风险 1：真实钉钉认证方式复杂且不稳定

缓解：

- 通过环境变量和 `extra_config` 解耦认证细节
- 测试中优先使用 fake transport

### 风险 2：collector 与 transport 强耦合

缓解：

- 把真实请求实现放在 fetcher/transport 层
- collector 继续只做编排与统一输出

### 风险 3：响应解析分支过多

缓解：

- 统一 payload 提取函数
- 通过 diagnostics 记录命中路径和失败原因

### 风险 4：引入真实 transport 后破坏 fixture/fake 模式

缓解：

- 显式测试 fixture / fake / real 三种模式
- 保持 `collector_type` 驱动切换，不互相覆盖

## 10. 验收标准

满足以下条件视为本次任务完成：

- `DingtalkPayloadFetcher` 已实现真实 transport 能力
- 支持真实请求、真实响应解析
- 支持 cookies / headers / 认证注入
- fake transport / fixture transport 仍然可用
- 三个 collector 可切换到真实 transport 模式
- transport 配置统一通过：
  - `module_configs`
  - `extra_config`
  - 环境变量
  注入
- transport 失败可区分：
  - 认证失败
  - 配置缺失
  - 请求失败
  - 响应为空
  - payload 解析失败
  - fallback 命中
- `/api/sync/run` 在真实 transport 模式下仍能写入：
  - `source_snapshots`
  - `normalized_records`
  - `task_plans`
- 测试覆盖：
  - fake transport 仍然可用
  - 真实 transport 配置缺失时报错
  - 真实 transport 响应异常时报错
  - 真实 transport 成功时 `/api/sync/run` 可继续写库
  - fixture / fake / real 三种 transport 模式切换正确

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证 transport 配置装配逻辑
2. 验证 fake / fixture / real 模式切换
3. 验证真实 transport 缺配置时报错
4. 验证真实 transport 响应异常时报错
5. 验证真实 transport 成功时 `/api/sync/run` 写库
6. 验证 diagnostics / `raw_meta` / `sync_error` 中的失败分类
7. 如环境允许，补最小手工 smoke

## 12. 实施记录

### 实际完成内容

- 在 `services/collectors/fetchers.py` 中将 `DingtalkPayloadFetcher` 从扩展位升级为可工作的真实 transport，实现了：
  - 真实 HTTP 请求发送
  - JSON 响应解析
  - `structured` / `state` 两类 payload 提取
  - `headers` / `cookies` / token 注入
  - `fake` / `fixture` / `real` 三种 transport 模式切换
- 在 `core/config.py` 与 `.env.example` 中补充了真实 transport 所需的公共配置项：
  - `DINGTALK_DEFAULT_HEADERS_JSON`
  - `DINGTALK_DEFAULT_COOKIES_JSON`
  - `DINGTALK_AUTH_TOKEN`
  - `DINGTALK_REQUEST_TIMEOUT_SECONDS`
  - `DINGTALK_VERIFY_SSL`
- 在 `services/collectors/source_config.py` 中补充了 source config 的环境变量读取与 `extra_config` 访问能力，支持把配置统一从：
  - `module_configs`
  - `extra_config`
  - 环境变量
  注入到 transport 层
- 在 `services/collectors/base.py` 中增强 collector 编排逻辑：
  - real/dingtalk 模式校验
  - attempt chain 诊断信息
  - `sync_error` 归并
  - `raw_meta` 中的 `collector_diagnostics`、`transport_mode`、`selected_source`
- 在 `services/collectors/diagnostics.py` 中扩展单次尝试诊断结构，支持：
  - `error_type`
  - `http_status`
  - `transport_mode`
- 保持 `visit` / `inspection` / `proactive` 三个 collector 继续复用统一基类，让 transport 能力通过配置切换接入，而不是在模块 collector 内硬编码
- 补充了 Phase 4 的自动化测试，覆盖：
  - fake transport 可用
  - fixture transport 可用
  - real transport 缺配置失败
  - real transport 认证失败
  - real transport 响应解析失败
  - real transport 模式下 `/api/sync/run` 继续写入 `source_snapshots`、`normalized_records`、`task_plans`
- 额外完成一次最小 real transport 手工 smoke：通过本地 HTTP server 模拟真实钉钉 transport，成功跑通 `/api/sync/run`

### 与原计划偏差

- 本次实现优先完成了 transport 抽象、配置注入、失败分类和模式切换，没有把“真实线上钉钉所有登录态/上下文细节”作为一次性目标；这与阶段修订后的优先目标一致，不视为负偏差
- `visit` / `inspection` / `proactive` 三个 collector 文件本身改动较少，主要变化集中在 fetcher、source config、collector base 和测试层；这与原计划中“不要让 transport 逻辑散落在 collector 中”的方案一致
- real transport 自动化验证主要通过本地 fake HTTP server 完成，只补了一次最小手工 smoke，没有把真实线上钉钉环境接入作为本阶段硬前提；这与本阶段修订后的验证策略一致

### 验证结果

- 语法校验：
  - `python3 -m compileall apps core models repositories schemas services tests migrations`
  - 结果：通过
- 自动化测试：
  - `.venv/bin/pytest -q`
  - 结果：`19 passed`
- 最小 real transport 手工 smoke：
  - 使用本地 HTTP server 模拟真实 transport endpoint
  - 使用临时 PostgreSQL 数据库执行迁移并插入 `collector_type='dingtalk'` 的 `module_config`
  - 设置 `TEST_DINGTALK_TOKEN=transport-token`
  - 调用 `POST /api/sync/run`
  - 结果：成功写入 `source_snapshots`、`normalized_records`、`task_plans`

### 待跟进事项

- 下一阶段可在不破坏当前 transport 抽象的前提下，把 `DingtalkPayloadFetcher` 对接到真实线上钉钉 endpoint 与认证信息
- 如果真实钉钉页面返回格式比当前假设更复杂，后续需要继续增强：
  - 响应路径配置
  - state/store 提取策略
  - token/cookie 更新策略
- 真实 transport 已经可工作，但当前仍以 fake/fixture 测试为主；进入下一阶段前，建议先收集一批真实 payload 样本，用于 Phase 5 的字段识别增强

## 13. 遗留问题

- 尚未接入真实线上钉钉登录态托管或长期认证续期机制
- 当前 real transport smoke 依赖本地模拟 server，不代表已经覆盖所有真实线上返回分支
- 如果后续真实 payload 中存在嵌套表格、分页或增量加载，还需要在 transport 层继续扩展

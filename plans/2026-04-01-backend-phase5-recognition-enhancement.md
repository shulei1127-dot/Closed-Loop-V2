# 第五阶段后端计划：字段识别与标准化增强

## 1. 背景

当前项目已经完成以下阶段：

- Phase 1：项目骨架
- Phase 2：`source_snapshots -> normalized_records -> task_plans` 后端主链路
- Phase 3：real collector 架构与 source config 抽象
- Phase 4：真实钉钉 transport 接入，支持 `fake / fixture / real` 三种 transport 模式

当前系统已经可以从真实或 fixture payload 进入采集链路，并稳定写入：

- `source_snapshots`
- `normalized_records`
- `task_plans`

但 recognizer 目前仍偏向“最小可用版本”，虽然已经具备统一输出结构，但对真实 rows 的字段命名差异、字段别名、枚举值、链接模式、空值规则等处理还不够强，距离“稳定识别真实业务字段”还有明显差距。

Phase 5 的核心目标，是在真实 collector 和真实/fixture payload 已接通的前提下，增强三个模块的字段识别与标准化能力，让真实 rows 能稳定映射到标准业务字段，并输出可解释、可审计、可供 planner 稳定消费的识别结果。

## 2. 目标

本次实施完成后，应达到以下目标：

- 增强 `visit_recognizer`、`inspection_recognizer`、`proactive_recognizer`
- 支持真实 rows 的字段命名差异
- 支持字段别名、枚举、链接模式、空值规则
- 明确并固定 `recognition_status` 判定规则：
  - `full`
  - `partial`
  - `failed`
- 保持 3 个 recognizer 的统一输出结构：
  - `normalized_records`
  - `field_mapping`
  - `field_confidence`
  - `field_evidence`
  - `field_samples`
  - `unresolved_fields`
  - `recognition_status`
- 增强后继续保证 planner 在真实 rows 下稳定输出：
  - `planned`
  - `skipped`
  - `skip_reason`
- 补齐字段识别与 planner 联动测试

## 3. 范围

本次实施范围包含：

- 三个 recognizer 的字段识别能力增强
- 统一字段识别判定与输出结构
- 定义并实现固定的 `recognition_status` 规则
- 强化字段识别策略：
  - 别名匹配
  - 字段归一化
  - 枚举值识别
  - 链接/ID 模式识别
  - 空值与缺失字段识别
  - 必要时的位置先验与联合字段约束
- 验证 planner 在真实 rows 输入下不回退
- 补充 recognizer 与 planner 联动测试
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 执行器
- 前端
- 巡检报告上传
- PTS 联调
- 工单闭环动作
- 新的 transport 类型
- 调度、告警、重试平台化能力

## 5. 当前现状 / 已知问题

当前现状：

- 三个 recognizer 已有基础版字段推断能力
- 输出结构已经统一到 `RecognitionResult`
- planner 已依赖标准字段做 eligibility 判断
- 当前 fixture / fake / real transport 已经能把数据送入 recognizer

已知问题：

- 真实 rows 的列名可能与 mock/fixture 设计时不完全一致
- 同一业务字段在不同来源中可能存在多个别名
- 枚举值可能存在“是/否”“已完成/完成/完成了”“空字符串/null/未填写”等差异
- 链接字段和 ID 字段可能混在自由文本里，需要模式识别
- 某些字段可能需要联合判断，不能只靠单列名称
- `recognition_status` 目前语义还不够刚性，需要固定判定标准
- recognizer 增强后，如果标准化结果发生偏差，planner 可能出现 planned/skipped 回退风险

## 6. 技术方案

### 6.1 识别策略总原则

本阶段的 recognizer 增强目标不是引入重型 AI 推断，而是先把规则化识别做扎实，优先依赖：

- 列名别名匹配
- 值模式识别
- 业务枚举归一化
- 空值规则
- 位置与上下文先验
- 必要的联合字段约束

整体思路是：

1. 先识别原始列与标准字段的映射
2. 再对值做标准化
3. 最后根据必填字段、关键字段和 unresolved 情况计算 `recognition_status`

### 6.2 recognizer 输出统一

三个 recognizer 必须统一输出：

- `normalized_records`
- `field_mapping`
- `field_confidence`
- `field_evidence`
- `field_samples`
- `unresolved_fields`
- `recognition_status`

并且要求：

- `field_mapping`：标准字段 -> 原始列名
- `field_confidence`：标准字段 -> 置信度
- `field_evidence`：标准字段 -> 识别依据
- `field_samples`：标准字段 -> 样本值
- `unresolved_fields`：未识别、缺失或无法可靠识别的标准字段
- `recognition_status`：针对记录级和整体结果按固定规则判定

### 6.3 recognition_status 判定规则

本阶段要把 `recognition_status` 固定为可解释规则，初步采用以下标准：

- `full`
  - 所有关键字段都已识别
  - 关键字段值通过标准化
  - 无关键 unresolved 字段
- `partial`
  - 至少识别出部分关键字段
  - 可形成可用的标准化记录
  - 但存在非致命 unresolved 字段，或部分关键字段可信度不足
- `failed`
  - 关键字段缺失到无法形成稳定标准化记录
  - 或列映射/值识别严重失败
  - 或记录无法安全供 planner 使用

本阶段将关键字段固定如下，并以此作为 `recognition_status = full / partial / failed` 的判定基础：

- visit：
  - `customer_name`
  - `visit_owner`
  - `visit_status`
  - `visit_link`
- inspection：
  - `customer_name`
  - `inspection_done`
  - `work_order_link` 或 `work_order_id`
- proactive：
  - `customer_name`
  - `liaison_status`
  - `visit_link`

具体判定将按“关键字段是否成功识别、是否可供 planner 安全消费”落到代码：

- `full`
  - 关键字段全部已识别，并且关键值已完成标准化
- `partial`
  - 部分关键字段已识别，记录仍可形成可用标准化结果，但存在关键缺口或非关键 unresolved
- `failed`
  - 关键字段缺失严重，无法形成稳定标准化记录，或无法安全供 planner 使用

### 6.4 字段识别策略

本阶段优先实现以下策略：

- 别名匹配
  - 例如一个标准字段对应多个中文列名别名
- 字段归一化
  - 去空格、全半角差异、统一大小写、统一布尔语义
- 枚举值识别
  - 如“是/已完成/完成/true”统一成布尔或标准枚举
- 链接/ID 模式识别
  - URL、工单号、PTS 链接、交付单号等用正则或模式规则识别
- 空值与缺失字段识别
  - 明确空字符串、`None`、`null`、`-`、`--`、`未填写` 等都归为缺失
- 位置先验和联合字段约束
  - 在列名模糊时，结合相邻字段、典型值模式或同表常见结构做二次判断

### 6.5 模块级 recognizer 设计

三个 recognizer 需要各自强化：

- `visit_recognizer`
  - 重点增强回访人、回访状态、回访链接、回访类型、满意度、PTS 链接等识别
- `inspection_recognizer`
  - 重点增强巡检是否完成、巡检工单/链接、报告状态、负责人/客户名等识别
- `proactive_recognizer`
  - 重点增强建联状态、回访链接、客户反馈、联系人、客户名等识别

同时尽量把通用逻辑下沉到公共辅助层，例如：

- 字段别名表
- 空值判定函数
- 枚举归一化函数
- URL / ID 识别函数
- 记录级 status 判定辅助函数

### 6.6 planner 稳定性保护

recognizer 增强后，必须确保 planner 不回退。

实施时要重点验证：

- visit planner 仍然只对“舒磊 + 已回访 + 回访链接为空”产出 `planned`
- inspection planner 仍然只对“巡检完成=是”产出 `planned`
- proactive planner 仍然只对“已建联 + 回访链接为空”产出 `planned`

如果 recognizer 输出值被新的归一化规则改变，需要同步检查 planner 是否需要接受更稳定的标准枚举，而不是继续吃原始脏值。

### 6.7 测试策略

测试以 fixture / fake 驱动的真实 rows 为主，不把真实线上钉钉作为前提。

至少覆盖：

- visit 真实 rows 字段识别测试
- inspection 真实 rows 字段识别测试
- proactive 真实 rows 字段识别测试
- `recognition_status` 的 `full / partial / failed` 判定测试
- recognizer 输出结构完整性测试
- planner 在真实 rows 输入下的联动测试

必要时增加：

- 别名命中测试
- 空值规则测试
- 枚举归一化测试
- 链接模式识别测试

## 7. 分步骤实施计划

### 步骤 1：梳理标准字段与关键字段

- 明确三个模块的标准字段集合
- 明确每个模块的关键字段
- 明确哪些字段缺失会导致 `partial` 或 `failed`

### 步骤 2：增强公共识别辅助能力

- 扩展字段别名表
- 扩展空值判定
- 扩展枚举归一化
- 扩展链接/ID 模式识别
- 如有必要，下沉公共 helper

### 步骤 3：增强三个 recognizer

- 增强 `visit_recognizer`
- 增强 `inspection_recognizer`
- 增强 `proactive_recognizer`
- 对齐输出结构与 evidence/confidence 规则

### 步骤 4：固定 recognition_status 判定

- 把 `full / partial / failed` 规则落成代码
- 对记录级与整体结果判定保持一致

### 步骤 5：验证 planner 不回退

- 用真实 rows 驱动 recognizer + planner
- 校验 `planned / skipped / skip_reason` 结果不回退

### 步骤 6：补测试

- 三模块真实 rows 识别测试
- `recognition_status` 判定测试
- recognizer 输出结构测试
- planner 联动测试

### 步骤 7：验证与收尾

- 跑语法和测试校验
- 如有需要补最小手工 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase5-recognition-enhancement.md`
- `services/recognizers/field_inference.py`
- `services/recognizers/visit_recognizer.py`
- `services/recognizers/inspection_recognizer.py`
- `services/recognizers/proactive_recognizer.py`
- `services/recognizers/base.py`
- 可能新增 recognizer helper 文件
- `services/planners/visit_planner.py`
- `services/planners/inspection_planner.py`
- `services/planners/proactive_planner.py`
- `schemas/sync.py`
- `tests/test_real_collectors.py`
- `tests/test_planners.py`
- `tests/test_sync_api.py`
- 可能新增 recognizer 专项测试文件

## 9. 风险与缓解

### 风险 1：识别规则增强后误伤现有 fixture 数据

缓解：

- 保持 fixture 与真实 rows 双向覆盖
- 对关键字段识别补回归测试

### 风险 2：recognition_status 规则过严导致大量 `failed`

缓解：

- 明确关键字段与非关键字段边界
- 先以“可稳定供 planner 使用”为核心标准

### 风险 3：归一化规则变化导致 planner 结果回退

缓解：

- recognizer 改动后同步做 planner 联动测试
- 保证 planner 吃的是更稳定的标准值，而不是更脆弱的原始值

### 风险 4：各模块 recognizer 策略逐渐分叉

缓解：

- 把通用规则尽量收敛到公共 helper
- 仅保留模块特有规则在各自 recognizer 中

## 10. 验收标准

满足以下条件视为本次任务完成：

- 三个 recognizer 都已增强并支持真实 rows 字段命名差异
- 支持字段别名、枚举、链接模式、空值规则
- 三个 recognizer 都统一输出：
  - `normalized_records`
  - `field_mapping`
  - `field_confidence`
  - `field_evidence`
  - `field_samples`
  - `unresolved_fields`
  - `recognition_status`
- `recognition_status` 的 `full / partial / failed` 判定规则已固定且可解释
- 三个模块的真实 rows 都能生成稳定的 `normalized_records`
- planner 在真实 rows 输入下仍稳定输出：
  - `planned`
  - `skipped`
  - `skip_reason`
- 测试覆盖：
  - visit 真实 rows 字段识别
  - inspection 真实 rows 字段识别
  - proactive 真实 rows 字段识别
  - `recognition_status` 的 `full / partial / failed`
  - recognizer 输出结构完整性
  - planner 联动测试
- 实施完成后，必须贴出 3 份真实识别结果样例：
  - visit 一份
  - inspection 一份
  - proactive 一份
- 每份样例至少包含：
  - `normalized_records`
  - `field_mapping`
  - `field_confidence`
  - `unresolved_fields`
  - `recognition_status`

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证三个 recognizer 对真实 rows 的字段映射结果
2. 验证别名、枚举、链接模式、空值规则是否生效
3. 验证 `recognition_status` 在 full / partial / failed 三种场景下判定正确
4. 验证 recognizer 输出结构完整
5. 验证 planner 在真实 rows 输入下没有回退
6. 运行自动化测试
7. 如有必要补最小手工 smoke

## 12. 实施记录

### 实际完成内容

- 重构 `services/recognizers/field_inference.py`，补齐公共识别能力：
  - 字段别名匹配
  - 列名归一化
  - 空值识别
  - URL / ID / 手机号模式识别
  - 枚举归一化
  - 布尔值归一化
  - 记录级 `recognition_status` 判定
  - 整体 `recognition_status` 汇总
- 增强 `visit_recognizer`、`inspection_recognizer`、`proactive_recognizer`：
  - 支持真实 rows 的字段命名差异
  - 支持字段别名、枚举、链接模式、空值规则
  - 统一输出：
    - `normalized_records`
    - `field_mapping`
    - `field_confidence`
    - `field_evidence`
    - `field_samples`
    - `unresolved_fields`
    - `recognition_status`
- 固定了 `recognition_status` 判定规则，并按模块关键字段落地：
  - visit：
    - `customer_name`
    - `visit_owner`
    - `visit_status`
    - `visit_link`
  - inspection：
    - `customer_name`
    - `inspection_done`
    - `work_order_link` 或 `work_order_id`
  - proactive：
    - `customer_name`
    - `liaison_status`
    - `visit_link`
- 增强 planner 联动保护：
  - `visit_planner` 现在要求 `customer_name` 存在且 `recognition_status != failed`
  - `inspection_planner` 现在要求：
    - `customer_name` 存在
    - `inspection_done = true`
    - 至少存在 `work_order_link` 或 `work_order_id`
    - `recognition_status != failed`
  - `proactive_planner` 现在要求 `customer_name` 存在且 `recognition_status != failed`
- 新增 Phase 5 专项测试：
  - 三模块真实 rows / 别名识别测试
  - `recognition_status = full / partial / failed` 测试
  - recognizer 输出结构完整性测试
  - planner 联动测试
- 已生成 3 份真实识别结果样例，作为本阶段验收输出的一部分：
  - visit
  - inspection
  - proactive

### 与原计划偏差

- 本阶段没有引入新的独立 recognizer 基类文件，而是把公共识别能力集中落在 `field_inference.py`，用工具函数形式复用；这能更快收敛 Phase 5 的规则化识别目标，不构成负偏差
- planner 做了轻量级联动收紧，比原计划多补了一层“关键字段不可缺失时不计划执行”的保护；这是为了避免 recognizer 增强后出现“识别部分失败但 planner 仍 planned”的风险，属于正向补强

### 验证结果

- 语法校验：
  - `python3 -m compileall services tests`
  - 结果：通过
- 自动化测试：
  - `.venv/bin/pytest -q`
  - 结果：`25 passed`
- 验证覆盖包括：
  - visit / inspection / proactive 真实 rows 字段识别
  - 字段别名、枚举、链接模式识别
  - `recognition_status` 的 `full / partial / failed`
  - recognizer 输出结构完整性
  - planner 在真实 rows 输入下的联动稳定性

### 待跟进事项

- 下一阶段如果真实线上钉钉 payload 列名进一步分叉，可以继续扩字段别名表，而不需要大改 recognizer 主结构
- 如果后续接入更多真实样本，建议补充：
  - 更复杂的列位置先验
  - 多字段联合推断
  - 更细的置信度分层
- 当前 `unresolved_fields` 仍以字段级为主；如果后续要增强排障能力，可以继续补记录级 unresolved 明细

## 13. 遗留问题

- 目前字段识别仍以规则化为主，尚未引入更复杂的统计学习或模型辅助推断
- 真实 payload 的别名覆盖范围仍取决于当前样本，后续接入更多线上样本后可能还需补充别名字典

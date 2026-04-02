# 第七阶段后端计划：巡检报告匹配与文件处理

## 1. 背景

当前项目已经完成以下阶段：

- Phase 1：项目骨架
- Phase 2：`source_snapshots -> normalized_records -> task_plans` 主链路
- Phase 3：real collector 架构
- Phase 4：真实钉钉 transport 接入
- Phase 5：字段识别与标准化增强
- Phase 6：执行器接入

目前 inspection 模块已经具备：

- 真实或 fixture 数据采集
- 字段识别与标准化
- planner 输出 inspection task
- executor stub / precheck / dry-run / 审计链路

但 inspection 闭环里有一个关键前置条件仍未接入：巡检报告文件是否存在、是否匹配客户、是否同时具备 Word/PDF、是否存在多候选冲突。这意味着当前 inspection task 虽然能进入 planning 和 execution 链路，但在实际执行前还不知道“报告资源是否就绪”。

Phase 7 的目标，就是为巡检模块建立“巡检报告匹配与文件处理”能力，让 inspection task 在 planning / precheck / execution 阶段都能感知报告状态，并将匹配结果纳入审计链路。

## 2. 目标

本次实施完成后，应达到以下目标：

- 建立报告扫描器，扫描：
  - `/Users/shulei/Downloads/巡检报告集合-已审核`
- 支持：
  - Word / PDF 文件识别
  - 文件名标准化
  - 公司名提取
  - 目录遍历
  - 异常文件过滤
- 建立基于 `customer_name` 的报告匹配逻辑，至少支持：
  - 精确匹配
  - 归一化匹配
  - 同义/噪声字符处理
  - 多候选识别
  - 缺失文件识别
- 定义统一匹配结果结构，至少包含：
  - `matched`
  - `manual_required`
  - `customer_name`
  - `matched_files`
  - `missing_file_types`
  - `match_strategy`
  - `confidence`
  - `error_message`
- 让 inspection planner 或 precheck 能感知报告是否已就绪
- 让 inspection executor 能消费匹配结果
- 当报告缺失或冲突时，明确进入 `manual_required`

## 3. 范围

本次实施范围包含：

- 巡检报告目录扫描器
- 文件名标准化与公司名提取
- 报告匹配服务
- 匹配结果 schema
- inspection planner / precheck / executor 联动
- 报告匹配相关自动化测试
- 实施完成后回写同一份 plan

## 4. 非范围

本次明确不做：

- 前端页面
- 真实上传动作
- 巡检工单真实线上闭环
- visit / proactive 执行器增强
- 调度与告警平台化
- 报告内容解析
- OCR 或复杂文档内容识别

## 5. 当前现状 / 已知问题

当前现状：

- inspection planner 目前只根据 `inspection_done` 和工单信息决定是否 `planned`
- inspection executor 当前只是 Phase 6 stub，不知道报告文件状态
- 系统中还没有针对本地报告目录的扫描能力
- 也没有专门的报告匹配结果模型

已知问题：

- 巡检报告目录可能包含 Word、PDF、临时文件、异常命名文件、子目录
- 同一客户可能存在多个文件名变体
- 同一客户可能出现多个候选文件，无法直接自动选定
- 有的客户可能只有 Word 或只有 PDF，需要明确识别为缺失
- 公司名可能存在噪声字符、括号、空格、横杠、版本后缀、日期后缀等干扰
- planner 阶段是否立即引入报告匹配，需要控制好范围，避免让 planning 过度依赖本地文件系统

## 6. 技术方案

### 6.1 报告扫描器

建立独立扫描器，例如：

- `services/report_matching/scanner.py`

职责：

- 遍历目录 `/Users/shulei/Downloads/巡检报告集合-已审核`
- 递归扫描子目录
- 识别扩展名：
  - `.doc`
  - `.docx`
  - `.pdf`
- 过滤异常文件：
  - 临时文件
  - 隐藏文件
  - 无效扩展名
- 输出统一文件索引结构

每个文件索引项建议包含：

- `path`
- `filename`
- `extension`
- `file_type`（`word` / `pdf`）
- `normalized_name`
- `customer_name_candidate`

### 6.2 文件名标准化与公司名提取

建立文件名标准化 helper，例如：

- 去扩展名
- 去多余空格
- 去日期、版本、括号补充说明
- 统一全半角
- 去常见噪声词：
  - `巡检报告`
  - `已审核`
  - `最终版`
  - `word`
  - `pdf`

再从标准化后的文件名中提取公司名候选。

目标不是一次性解决所有命名问题，而是先把规则化匹配做扎实，并保留 `manual_required` 兜底。

第一版文件名标准化相关规则先支持可配置化，至少包括：

- 噪声词表
- 常见后缀清洗规则
- 扩展名白名单
- 临时文件过滤规则

第一版可以先放常量或配置文件，不要求一开始就做复杂配置中心。

### 6.3 报告匹配逻辑

建立匹配服务，例如：

- `services/report_matching/matcher.py`

输入：

- `customer_name`
- 扫描后的文件索引

输出统一匹配结果，至少包含：

- `matched`
- `manual_required`
- `customer_name`
- `matched_files`
- `missing_file_types`
- `match_strategy`
- `confidence`
- `error_message`

匹配策略至少包含：

- 精确匹配
- 归一化匹配
- 同义/噪声字符处理后的匹配
- 多候选冲突识别
- 缺失文件识别

建议 `match_strategy` 值可包括：

- `exact`
- `normalized`
- `fuzzy_like`
- `multiple_candidates`
- `missing_files`
- `no_match`

### 6.4 匹配结果语义

建议固定语义：

- `matched = true`
  - 至少命中一个可接受结果
- `manual_required = true`
  - 多候选冲突
  - 关键文件缺失
  - 文件名无法可靠归属
  - 扫描器异常

文件类型要求建议先按 inspection 业务规则定义为：

- 理想状态：同时存在 Word + PDF
- 缺失任一类型时：
  - `matched` 可视具体情况为 `false` 或 `partial`
  - 但最终执行链路进入 `manual_required`

本阶段建议保持简单明确：

- 两种类型都齐全且无冲突：可自动通过
- 缺任何一种或存在冲突：`manual_required`

### 6.5 接入 inspection planner / executor

接入策略建议分两层：

- planner 层
  - 可选择只补充 `planned_payload` 或 `skip_reason` 中的报告状态提示
  - 避免让 planner 直接依赖文件系统做过重判断
- precheck / executor 层
  - 正式接入报告匹配结果
  - 若缺失或冲突，返回 `manual_required`

当前倾向：

- planner 只保留轻量提示，不因报告缺失直接改变 `planned` 规则
- inspection executor precheck 做正式判断

这样可以避免把“业务是否需要闭环”和“本地报告资源是否就绪”混为一层。

### 6.6 审计链路

报告匹配结果需要进入审计链路，建议写入：

- `task_plans.planned_payload`
  - 可补轻量匹配摘要
- `task_runs.result_payload`
  - 写完整匹配结果
- precheck / execute 返回体
  - 明确展示匹配状态

### 6.7 测试策略

本阶段测试优先使用临时测试目录和假文件，不直接依赖真实报告目录中的全部文件。

至少覆盖：

- 报告目录扫描测试
- 公司名匹配测试
- Word / PDF 缺失测试
- 多候选冲突测试
- inspection planner / executor 联动测试

必要时补充：

- 噪声字符清洗测试
- 子目录扫描测试
- 异常文件过滤测试

## 7. 分步骤实施计划

### 步骤 1：建立扫描器与文件索引结构

- 实现目录遍历
- 实现文件类型识别
- 实现异常文件过滤
- 定义扫描结果模型

### 步骤 2：实现文件名标准化与公司名提取

- 设计标准化规则
- 实现公司名候选提取
- 用测试固定规则边界

### 步骤 3：实现报告匹配服务

- 基于 `customer_name` 做精确和归一化匹配
- 处理多候选冲突
- 处理缺失文件类型
- 输出统一匹配结果

### 步骤 4：接入 inspection planner / executor

- planner 增加轻量报告状态提示
- inspection executor precheck 接入正式匹配结果
- 冲突或缺失时进入 `manual_required`

### 步骤 5：补测试

- 报告扫描测试
- 匹配测试
- 缺失测试
- 多候选测试
- planner / executor 联动测试

### 步骤 6：验证与收尾

- 跑语法与自动化测试
- 如有必要对真实目录做最小手工 smoke
- 回写同一份 plan

## 8. 触及文件

预期会触及以下文件或目录：

- `plans/2026-04-01-backend-phase7-report-matching.md`
- 可能新增：
  - `services/report_matching/scanner.py`
  - `services/report_matching/matcher.py`
  - `services/report_matching/schemas.py`
  - `services/report_matching/normalizer.py`
- `services/planners/inspection_planner.py`
- `services/executors/inspection_executor.py`
- `services/task_execution_service.py`
- `schemas/task.py`
- `schemas/common.py`
- `tests/`

## 9. 风险与缓解

### 风险 1：文件名规则复杂，误匹配率高

缓解：

- 先做规则化标准化与保守匹配
- 冲突时直接 `manual_required`

### 风险 2：planner 过度依赖本地文件系统

缓解：

- planner 只放轻量提示
- 正式阻断逻辑放到 precheck / executor

### 风险 3：真实目录噪声文件过多

缓解：

- 扫描器先做扩展名过滤和临时文件过滤
- 测试中覆盖典型异常文件

### 风险 4：缺失文件语义不清

缓解：

- 先固定规则：
  - Word + PDF 都齐全才算自动通过
  - 缺任一类型则进入 `manual_required`

## 10. 验收标准

满足以下条件视为本次任务完成：

- 已建立报告扫描器，支持：
  - Word / PDF 文件识别
  - 文件名标准化
  - 公司名提取
  - 目录遍历
  - 异常文件过滤
- 已建立基于 `customer_name` 的报告匹配逻辑，支持：
  - 精确匹配
  - 归一化匹配
  - 同义 / 噪声字符处理
  - 多候选识别
  - 缺失文件识别
- 已定义统一匹配结果结构，至少包含：
  - `matched`
  - `manual_required`
  - `customer_name`
  - `matched_files`
  - `missing_file_types`
  - `match_strategy`
  - `confidence`
  - `error_message`
- inspection planner 或 precheck 已能感知报告是否就绪
- inspection executor 已能消费匹配结果
- 报告缺失或冲突时会进入 `manual_required`
- 测试覆盖：
  - 报告目录扫描
  - 公司名匹配
  - Word / PDF 缺失
  - 多候选冲突
  - inspection planner / executor 联动
- 实施完成后，必须贴出 4 类匹配结果样例：
  - 完全匹配成功
  - 缺 Word
  - 缺 PDF
  - 多候选冲突
- 每条样例至少包含：
  - `matched`
  - `manual_required`
  - `customer_name`
  - `matched_files`
  - `missing_file_types`
  - `match_strategy`
  - `confidence`
  - `error_message`

## 11. 验证步骤

计划中的验证步骤如下：

1. 验证目录扫描与异常文件过滤
2. 验证文件名标准化和公司名提取
3. 验证精确匹配与归一化匹配
4. 验证缺失文件与多候选冲突
5. 验证 inspection planner / executor 联动
6. 运行自动化测试
7. 如有必要对真实目录做最小手工 smoke

## 12. 实施记录

### 实际完成内容

- 新增报告匹配模块：
  - [`services/report_matching/normalizer.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/report_matching/normalizer.py)
  - [`services/report_matching/scanner.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/report_matching/scanner.py)
  - [`services/report_matching/matcher.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/report_matching/matcher.py)
  - [`services/report_matching/schemas.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/services/report_matching/schemas.py)
- 文件名标准化规则已做成轻量可配置形式，第一版以常量配置承载：
  - 噪声词表
  - 常见后缀清洗规则
  - 扩展名白名单
  - 临时文件过滤规则
- 扫描器已支持：
  - 目录递归遍历
  - Word / PDF 识别
  - 异常文件过滤
  - 文件名标准化
  - 公司名候选提取
- 匹配器已支持：
  - 精确匹配
  - 归一化匹配
  - 噪声字符处理
  - 多候选冲突识别
  - Word / PDF 缺失识别
- inspection planner 已补轻量报告上下文：
  - `report_match_name`
  - `report_lookup_customer`
  - `report_status_hint`
- inspection executor 已接入报告匹配结果：
  - precheck 返回报告匹配结果
  - dry-run 返回报告匹配结果
  - execute 返回报告匹配结果
  - 缺失或冲突时进入 `manual_required`
  - 报告齐全时允许 `simulated_success`
- 已把报告根目录配置加入：
  - [`core/config.py`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/core/config.py)
  - [` .env.example`](/Users/shulei/Downloads/AI/codex/fastapi-pg/closed_loop_v2/.env.example)
- 新增 Phase 7 测试：
  - 扫描测试
  - 匹配测试
  - 缺失 Word / PDF 测试
  - 多候选冲突测试
  - inspection planner / executor 联动测试
  - inspection executor API 级 manual_required / simulated_success 测试
- 已生成 4 类真实匹配结果样例：
  - 完全匹配成功
  - 缺 Word
  - 缺 PDF
  - 多候选冲突

### 与原计划偏差

- planner 侧保持了“轻量提示”而没有把报告缺失直接改成 `skipped`，正式阻断逻辑放在 inspection executor precheck / execute；这与计划中的倾向设计一致，不构成负偏差
- inspection executor 相比 Phase 6 的纯 stub 更进一步，报告齐全时会返回 `simulated_success`，而不是单纯失败；这是为了让报告匹配能力真正进入执行链路，属于正向补强

### 验证结果

- 语法校验：
  - `python3 -m compileall apps core schemas services tests`
  - 结果：通过
- 自动化测试：
  - `.venv/bin/pytest -q`
  - 结果：`37 passed`
- 匹配样例验证：
  - 已分别导出：
    - 完全匹配成功
    - 缺 Word
    - 缺 PDF
    - 多候选冲突
  - 结果均符合预期语义

### 待跟进事项

- 后续如果真实目录中出现更多命名变体，可以继续扩展噪声词表和后缀清洗规则
- 如果进入真实上传阶段，inspection executor 还需要把匹配结果接到真实文件上传动作
- 如果后续需要更复杂的报告归属判断，可以继续补：
  - 更强的公司名别名表
  - 文档内容校验
  - 文件时间优先级规则

## 13. 遗留问题

- 当前匹配仍以文件名规则为主，尚未做文档内容级校验
- 多候选冲突目前统一走 `manual_required`，尚未引入更细的自动决策规则
- 真实目录结构可能持续演化，后续仍需结合线上样本扩规则

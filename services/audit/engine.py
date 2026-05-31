"""审核引擎：编排 9 条规则，输出结论"""

from __future__ import annotations

from datetime import datetime, timezone

from services.audit.schemas import AuditConclusion, AuditInput, AuditResult, RuleResult
from services.audit.rules.rule1_stage import rule1_stage
from services.audit.rules.rule2_leader import rule2_leader
from services.audit.rules.rule3_delivery_items import rule3_delivery_items
from services.audit.rules.rule4_config_keywords import rule4_config_keywords
from services.audit.rules.rule5_contacts import rule5_contacts
from services.audit.rules.rule6_product_count import rule6_product_count
from services.audit.rules.rule7_product_detail import rule7_product_detail
from services.audit.rules.rule8_service_period import rule8_service_period
from services.audit.rules.rule9_escalation import rule9_escalation
from services.audit.service_package import parse_service_packages
from services.audit.product_type import extract_short_product_name


def run_audit(input: AuditInput) -> AuditResult:
    rules: list[RuleResult] = []

    service_content = _build_service_content(input.delivery_items, input.product_details)
    period_summary = _build_after_sales_service_period_summary(input.product_details)

    # Rule 1 — gate rule
    r1 = rule1_stage(input.delivery_stage, input.stage_status)
    rules.append(r1)
    if r1.result == "不通过":
        return _build_result(input, rules, "不通过", None, service_content, period_summary)

    # Rules 2-8
    rules.append(rule2_leader(input.after_sales_leader))
    rules.append(rule3_delivery_items(input.delivery_items))
    rules.append(rule4_config_keywords(input.delivery_items, input.product_details))
    rules.append(rule5_contacts(input.contacts))
    rules.append(rule6_product_count(input.delivery_items, input.product_details))
    rules.append(rule7_product_detail(input.product_details))
    rules.append(rule8_service_period(input.delivery_items, input.product_details, input.approval_time))

    # Rule 9 — escalation
    rules.append(rule9_escalation(rules))

    conclusion = _calculate_conclusion(rules)
    reminder = _get_value_added_reminder(input) if conclusion == "通过" else None

    return _build_result(input, rules, conclusion, reminder, service_content, period_summary)


def _calculate_conclusion(rules: list[RuleResult]) -> AuditConclusion:
    rules_2_8 = [r for r in rules if 2 <= r.rule_id <= 8]
    if any(r.result == "不通过" for r in rules_2_8):
        return "不通过"
    if any(r.result == "无法判定" for r in rules_2_8):
        return "转人工审核"
    return "通过"


def _get_value_added_reminder(input: AuditInput) -> str | None:
    services: list[str] = []
    for item in input.delivery_items:
        for ci in item.config_items:
            parsed = parse_service_packages(ci.text)
            if parsed.inspection_count > 0:
                services.append(f"产品巡检服务*{parsed.inspection_count}")
            if parsed.inspection_set_count > 0:
                services.append(f"产品巡检服务（套）*{parsed.inspection_set_count}")
            if parsed.advanced_renewal1_years > 0:
                services.append(f"高级服务包（续保一）*{parsed.advanced_renewal1_years}")
            if parsed.advanced_renewal2_years > 0:
                services.append(f"高级服务包（续保二）*{parsed.advanced_renewal2_years}")
            if parsed.log_analysis_count > 0:
                services.append(f"日志分析服务*{parsed.log_analysis_count}")

    if not services:
        return None
    return f"配置项包含{'、'.join(services)}，请及时创建对应增值服务工单"


def _build_result(
    input: AuditInput,
    rules: list[RuleResult],
    conclusion: AuditConclusion,
    reminder: str | None,
    service_content: str | None,
    period_summary: str | None,
) -> AuditResult:
    return AuditResult(
        project_id=input.project_id,
        project_name=input.project_name,
        customer_name=input.customer_name,
        rules=rules,
        conclusion=conclusion,
        value_added_service_reminder=reminder,
        audited_at=datetime.now(timezone.utc).isoformat(),
        assigner_username=input.assigner_username,
        assigner_name=input.assigner_name,
        person_in_charge_username=input.person_in_charge_username,
        person_in_charge_name=input.person_in_charge_name,
        service_content=service_content,
        after_sales_service_period_summary=period_summary,
    )


def _build_service_content(delivery_items: list, product_details: list) -> str | None:
    import re
    if not delivery_items:
        return None

    parts: list[str] = []
    for item in delivery_items:
        category = item.product_category or ""
        product_name = extract_short_product_name(category)
        if not product_name:
            continue

        is_renewal = any(
            extract_short_product_name(p.product_category or "") == product_name and p.is_renewal_record
            for p in product_details
        )
        prefix = "(续保)" if is_renewal else ""

        if item.config_items:
            for ci in item.config_items:
                clean = re.sub(r"\*\d+", "", ci.text).replace(" + ", "+").replace(" +", "+").replace("+ ", "+")
                qty_str = f"*1*{ci.quantity or 1}套"
                parts.append(f"{product_name}：{prefix}{clean}{qty_str}")
        else:
            dash_idx = category.find("-")
            service_pkg = category[dash_idx + 1:] if dash_idx > 0 else ""
            if service_pkg:
                parts.append(f"{product_name}：{prefix}{service_pkg}*1*1套")

    return "，".join(parts) if parts else None


def _build_after_sales_service_period_summary(product_details: list) -> str | None:
    if not product_details:
        return None

    seen: set[str] = set()
    parts: list[str] = []
    for p in product_details:
        name = extract_short_product_name(p.product_category or "")
        if not name or not p.after_sales_service_period:
            continue
        key = f"{name}{p.after_sales_service_period}"
        if key in seen:
            continue
        seen.add(key)
        parts.append(key)

    return "，".join(parts) if parts else None
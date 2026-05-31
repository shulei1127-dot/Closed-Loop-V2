"""规则 1：交付阶段必须是转售后审核"""

from services.audit.schemas import RuleResult


def rule1_stage(delivery_stage: str, stage_status: str) -> RuleResult:
    is_transfer = "转售后审核" in delivery_stage
    is_waiting = "等待审核是否转售后" in stage_status

    if is_transfer and is_waiting:
        return RuleResult(
            rule_id=1, rule_name="交付阶段=转售后审核", result="通过",
            message=f"交付阶段：{delivery_stage}，状态：{stage_status}",
        )

    if not is_transfer:
        return RuleResult(
            rule_id=1, rule_name="交付阶段=转售后审核", result="不通过",
            message=f'交付阶段为“{delivery_stage}”，不是“转售后审核”',
        )

    return RuleResult(
        rule_id=1, rule_name="交付阶段=转售后审核", result="不通过",
        message=f'状态为“{stage_status}”，不是“等待审核是否转售后”',
    )
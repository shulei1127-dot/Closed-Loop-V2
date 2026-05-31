"""规则 2：售后负责人必须是冯伟"""

from services.audit.schemas import RuleResult


def rule2_leader(after_sales_leader: str) -> RuleResult:
    if after_sales_leader == "冯伟":
        return RuleResult(
            rule_id=2, rule_name="售后负责人=冯伟", result="通过",
            message="售后负责人：冯伟",
        )

    return RuleResult(
        rule_id=2, rule_name="售后负责人=冯伟", result="不通过",
        message=(
            f'售后负责人为"{after_sales_leader}"，不是"冯伟"'
            if after_sales_leader
            else "售后负责人为空"
        ),
    )
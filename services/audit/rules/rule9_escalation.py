"""规则 9：无法准确判定时转人工审核"""

from services.audit.schemas import RuleResult


def rule9_escalation(rule_results: list[RuleResult]) -> RuleResult:
    undetermined = [r for r in rule_results if r.result == "无法判定"]

    if not undetermined:
        return RuleResult(rule_id=9, rule_name="转人工审核", result="通过", message="所有规则均可自动判定")

    reasons = "; ".join(f"规则{r.rule_id}({r.rule_name}): {r.message}" for r in undetermined)
    return RuleResult(rule_id=9, rule_name="转人工审核", result="无法判定", message=f"以下规则无法自动判定: {reasons}")
"""规则 5：企业联系人不能为空，电话格式正确"""

import re

from services.audit.schemas import Contact, RuleResult

_PHONE_REGEX = re.compile(r"^1[3-9]\d{9}$")
_UNICODE_INVISIBLE = re.compile(r"[^​-‏ -‮⁠-⁯﻿]")


def _normalize_phone(phone: str) -> str:
    invisible = re.compile(r"[​-‏ -‮⁠-⁯﻿]")
    return invisible.sub("", phone).strip()


def rule5_contacts(contacts: list[Contact]) -> RuleResult:
    if not contacts:
        return RuleResult(
            rule_id=5, rule_name="企业联系人+电话格式", result="不通过",
            message="无企业联系人记录",
        )

    no_phones = [c for c in contacts if not c.phone]
    if no_phones:
        return RuleResult(
            rule_id=5, rule_name="企业联系人+电话格式", result="不通过",
            message=f"{len(no_phones)} 个联系人电话为空",
        )

    invalid = [c for c in contacts if c.phone and not _PHONE_REGEX.match(_normalize_phone(c.phone))]
    if invalid:
        details = ", ".join(f"{c.name}电话格式错误" for c in invalid)
        return RuleResult(
            rule_id=5, rule_name="企业联系人+电话格式", result="不通过",
            message=details,
        )

    names = ", ".join(c.name for c in contacts)
    return RuleResult(
        rule_id=5, rule_name="企业联系人+电话格式", result="通过",
        message=f"{len(contacts)} 个联系人: {names}",
    )
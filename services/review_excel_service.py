"""交付转售后审核 — Excel 批量上传审核服务"""

from __future__ import annotations

import logging
from typing import Any

from services.audit.schemas import AuditInput, AuditResult, ProductType

logger = logging.getLogger(__name__)


class ExcelRow:
    __slots__ = ("company_name", "team", "crm_id", "pts_id", "project_name", "leader", "delivery_type", "project_type")

    def __init__(self, **kwargs: str):
        self.company_name = kwargs.get("企业名称", "")
        self.team = kwargs.get("企业所属战队", "")
        self.crm_id = kwargs.get("crm_id", "")
        self.pts_id = kwargs.get("pts_id", "")
        self.project_name = kwargs.get("项目名称", "")
        self.leader = kwargs.get("售后负责人", "")
        self.delivery_type = kwargs.get("交付资源类型", "")
        self.project_type = kwargs.get("项目类型", "")


def parse_excel_upload(file_bytes: bytes) -> list[ExcelRow]:
    """Parse an XLSX file and return list of ExcelRow."""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required for Excel upload. Install with: pip install openpyxl")

    import io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter, None)
    if not headers:
        return []

    # Map column headers to Chinese field names
    col_map: dict[int, str] = {}
    expected = ["企业名称", "企业所属战队", "crm_id", "pts_id", "项目名称", "售后负责人", "交付资源类型", "项目类型"]
    for i, h in enumerate(headers):
        if h and str(h).strip() in expected:
            col_map[i] = str(h).strip()

    results: list[ExcelRow] = []
    for row in rows_iter:
        data: dict[str, str] = {}
        for i, val in enumerate(row):
            if i in col_map:
                data[col_map[i]] = str(val or "").strip()
        if data.get("pts_id"):
            results.append(ExcelRow(**data))

    wb.close()
    return results


async def audit_from_excel(
    rows: list[ExcelRow],
    client,  # PtsGraphQLClient
    *,
    write_dingtalk: bool = True,
    dws_cli_path: str = "dws",
) -> dict[str, Any]:
    """Audit all projects from Excel rows, optionally write to DingTalk."""
    from services.extractors.review_main_page import extract_project_data
    from services.extractors.review_product_detail import extract_product_detail
    from services.extractors.review_approval_time import extract_approval_time
    from services.audit.engine import run_audit
    from services.audit.product_type import identify_product_type
    from services.integration.review_dingtalk import (
        write_audit_to_dingtalk, compute_region, compute_delivery_type, compute_project_type,
    )

    total = len(rows)
    passed = 0
    failed = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        project_id = row.pts_id
        try:
            # Extract data
            project_data = await extract_project_data(client, project_id)
            if not project_data:
                details.append({"projectId": project_id, "customerName": row.company_name, "conclusion": "提取失败", "error": "无法获取项目数据"})
                failed += 1
                continue

            # Get product details
            product_details = []
            for product in project_data.products:
                detail = await extract_product_detail(client, product.product_id)
                if detail:
                    product_details.append(detail)

            # Get approval time
            approval_time = await extract_approval_time(client, project_id)

            # Build audit input
            audit_input = AuditInput(
                project_id=project_data.project_id,
                project_name=project_data.project_name,
                customer_name=project_data.customer_name,
                delivery_stage=project_data.delivery_stage,
                stage_status=project_data.stage_status,
                after_sales_leader=project_data.after_sales_leader,
                assigner_username=project_data.assigner_username,
                assigner_name=project_data.assigner_name,
                person_in_charge_username=project_data.person_in_charge_username,
                person_in_charge_name=project_data.person_in_charge_name,
                delivery_items=project_data.delivery_items,
                contacts=project_data.contacts,
                products=project_data.products,
                product_details=product_details,
                approval_time=approval_time,
                partner_delivery_type=project_data.partner_delivery_type,
            )

            result = run_audit(audit_input)

            # Compute region/delivery/project type
            result.region = compute_region(result.assigner_name)
            result.delivery_type = compute_delivery_type(
                audit_input.delivery_items, product_details, audit_input.partner_delivery_type,
            )
            result.project_type = compute_project_type(audit_input.delivery_items)

            if result.conclusion == "通过":
                passed += 1
            else:
                failed += 1

            # DingTalk writeback
            if write_dingtalk:
                result.dingtalk = await write_audit_to_dingtalk(result, dws_cli_path=dws_cli_path)

            # Simplified output
            failed_rules = [r for r in result.rules if r.result in ("不通过", "无法判定") and r.rule_id != 9]
            details.append({
                "projectId": result.project_id,
                "projectName": result.project_name,
                "customerName": result.customer_name,
                "conclusion": result.conclusion,
                "reasons": [r.message for r in failed_rules],
                "dingtalk": result.dingtalk,
            })

        except Exception as e:
            logger.error("Excel audit failed for %s: %s", project_id, e)
            details.append({"projectId": project_id, "customerName": row.company_name, "conclusion": "异常", "error": str(e)})
            failed += 1

    return {"total": total, "passed": passed, "failed": failed, "details": details}

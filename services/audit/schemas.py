"""审核引擎数据模型"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── 产品类型 ──────────────────────────────────────────────

class ProductType(str, Enum):
    hardware = "hardware"
    software = "software"
    saas = "saas"


# ── 配置项 / 交付项 / 联系人 ──────────────────────────────

class ConfigItem(BaseModel):
    text: str = ""
    quantity: int = 1


class DeliveryItem(BaseModel):
    row_id: str = ""
    product_category: str = ""
    is_overdue: bool = False
    config_items: list[ConfigItem] = Field(default_factory=list)


class Contact(BaseModel):
    name: str = ""
    phone: str = ""
    email: str | None = None


class ProductListItem(BaseModel):
    product_id: str = ""
    product_type: str = ""
    customer: str = ""


# ── 产品详情 ──────────────────────────────────────────────

class ProductInfo(BaseModel):
    product_id: str = ""
    product_category: str = ""
    summary: str = ""
    number: int = 0
    is_renewal_record: bool = False

    info_type: str | None = None
    model: str | None = None
    serial_number: str | None = None
    machine_code: str | None = None
    product_version: str | None = None
    engine_version: str | None = None
    deploy_mode: str | None = None
    is_ha: str | None = None
    license_type: str | None = None
    license_expiry: str | None = None
    license_id: str | None = None
    after_sales_service_period: str | None = None
    is_overdue: str | None = None

    type: ProductType = ProductType.hardware


# ── 审核输入 ──────────────────────────────────────────────

class AuditInput(BaseModel):
    project_id: str
    project_name: str | None = None
    customer_name: str | None = None
    delivery_stage: str = ""
    stage_status: str = ""
    after_sales_leader: str = ""
    assigner_username: str | None = None
    assigner_name: str | None = None
    person_in_charge_username: str | None = None
    person_in_charge_name: str | None = None
    delivery_items: list[DeliveryItem] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    products: list[ProductListItem] = Field(default_factory=list)
    product_details: list[ProductInfo] = Field(default_factory=list)
    approval_time: str | None = None
    partner_delivery_type: str | None = None


# ── 审核结果 ──────────────────────────────────────────────

class RuleResult(BaseModel):
    rule_id: int
    rule_name: str
    result: str  # '通过' | '不通过' | '无法判定'
    message: str = ""


AuditConclusion = str  # '通过' | '不通过' | '转人工审核'


class AuditResult(BaseModel):
    project_id: str
    project_name: str | None = None
    customer_name: str | None = None
    rules: list[RuleResult] = Field(default_factory=list)
    conclusion: AuditConclusion = ""
    value_added_service_reminder: str | None = None
    audited_at: str = ""
    error: str | None = None
    assigner_username: str | None = None
    assigner_name: str | None = None
    person_in_charge_username: str | None = None
    person_in_charge_name: str | None = None
    service_content: str | None = None
    after_sales_service_period_summary: str | None = None
    region: str | None = None
    delivery_type: str | None = None
    project_type: str | None = None
    dingtalk: dict[str, Any] | None = None


# ── 服务包解析 ────────────────────────────────────────────

class ServicePackageResult(BaseModel):
    years: int = 0
    months: int = 0
    inspection_count: int = 0
    inspection_set_count: int = 0
    log_analysis_count: int = 0
    advanced_renewal1_years: int = 0
    advanced_renewal2_years: int = 0
    has_value_added_service: bool = False
    unrecognized: list[str] = Field(default_factory=list)
    is_operation_service: bool = False


# ── 待审核项目 ────────────────────────────────────────────

class PendingProject(BaseModel):
    project_id: str = ""
    project_name: str = ""
    customer_name: str = ""
    delivery_stage: str = ""
    stage_status: str = ""
    after_sales_leader: str = ""
    assigner_name: str | None = None
    person_in_charge_name: str | None = None


# ── 项目主页数据 ──────────────────────────────────────────

class ProjectData(BaseModel):
    project_id: str
    project_name: str = ""
    customer_name: str = ""
    delivery_stage: str = ""
    stage_status: str = ""
    after_sales_leader: str = ""
    assigner_username: str = ""
    assigner_name: str = ""
    person_in_charge_username: str = ""
    person_in_charge_name: str = ""
    delivery_items: list[DeliveryItem] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    products: list[ProductListItem] = Field(default_factory=list)
    partner_delivery_type: str | None = None

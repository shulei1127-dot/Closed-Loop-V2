from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db import get_db
from schemas.common import (
    EnvironmentCheckResponse,
    HealthzResponse,
    ModuleHealthDetailResponse,
    ModuleHealthListResponse,
)
from services.environment_check import EnvironmentCheckService


router = APIRouter()


@router.get("/healthz", response_model=HealthzResponse)
def healthz(db: Session = Depends(get_db)) -> HealthzResponse:
    db.execute(text("SELECT 1"))
    settings = get_settings()
    return HealthzResponse(service=settings.app_name, db="ok")


@router.get("/health/readiness", response_model=EnvironmentCheckResponse)
def readiness() -> EnvironmentCheckResponse:
    service = EnvironmentCheckService()
    return EnvironmentCheckResponse(**service.build_report())


@router.get("/health/modules", response_model=ModuleHealthListResponse)
def module_health() -> ModuleHealthListResponse:
    service = EnvironmentCheckService()
    return ModuleHealthListResponse(
        items=service.build_module_health_items(),
        served_at=datetime.now(timezone.utc),
    )


@router.get("/health/modules/{module_code}", response_model=ModuleHealthDetailResponse)
def module_health_detail(module_code: str) -> ModuleHealthDetailResponse:
    service = EnvironmentCheckService()
    return ModuleHealthDetailResponse(
        item=service.build_module_health_item(module_code),
        served_at=datetime.now(timezone.utc),
    )

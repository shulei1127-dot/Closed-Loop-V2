from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db import get_db
from schemas.common import EnvironmentCheckResponse, HealthzResponse
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

from fastapi import APIRouter, Depends, HTTPException

from apps.api.deps import get_ops_service
from schemas.ops import (
    OpsEventListResponse,
    OpsOverviewResponse,
    PtsSessionStatusResponse,
    PtsSessionUpdateRequest,
)
from services.ops_service import OpsService
from services.pts_session_service import PtsSessionService


router = APIRouter()


@router.get("/ops/overview", response_model=OpsOverviewResponse)
def ops_overview(service: OpsService = Depends(get_ops_service)) -> OpsOverviewResponse:
    return OpsOverviewResponse(items=service.build_overview())


@router.get("/ops/failures", response_model=OpsEventListResponse)
def ops_failures(service: OpsService = Depends(get_ops_service)) -> OpsEventListResponse:
    return OpsEventListResponse(items=service.list_failures())


@router.get("/ops/manual-required", response_model=OpsEventListResponse)
def ops_manual_required(service: OpsService = Depends(get_ops_service)) -> OpsEventListResponse:
    return OpsEventListResponse(items=service.list_manual_required())


@router.get("/ops/pts-session", response_model=PtsSessionStatusResponse)
def ops_pts_session_status() -> PtsSessionStatusResponse:
    return PtsSessionStatusResponse(**PtsSessionService().get_status())


@router.post("/ops/pts-session", response_model=PtsSessionStatusResponse)
def ops_update_pts_session(request: PtsSessionUpdateRequest) -> PtsSessionStatusResponse:
    try:
        return PtsSessionStatusResponse(**PtsSessionService().update_cookie(request.cookie_header))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

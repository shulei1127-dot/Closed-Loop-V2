from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query

from apps.api.deps import get_ops_service
from schemas.ops import (
    OpsDataMeta,
    OpsEventListResponse,
    OpsModuleSummaryResponse,
    OpsOverviewResponse,
    PendingTaskListResponse,
    PtsSessionStatusResponse,
    PtsSessionUpdateRequest,
    RecentVisitLinkListResponse,
    StringListResponse,
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


@router.get("/ops/dashboard/summary", response_model=OpsOverviewResponse)
def ops_dashboard_summary(service: OpsService = Depends(get_ops_service)) -> OpsOverviewResponse:
    items, cached, served_at = service.build_overview_cached_meta()
    return OpsOverviewResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/dashboard/failures", response_model=OpsEventListResponse)
def ops_dashboard_failures(
    limit: int = Query(default=10, ge=1, le=100),
    service: OpsService = Depends(get_ops_service),
) -> OpsEventListResponse:
    items, cached, served_at = service.list_failures_cached_meta(limit=limit)
    return OpsEventListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/dashboard/manual-required", response_model=OpsEventListResponse)
def ops_dashboard_manual_required(
    limit: int = Query(default=10, ge=1, le=100),
    service: OpsService = Depends(get_ops_service),
) -> OpsEventListResponse:
    items, cached, served_at = service.list_manual_required_cached_meta(limit=limit)
    return OpsEventListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/{module_code}/summary", response_model=OpsModuleSummaryResponse)
def ops_module_summary(
    module_code: str,
    service: OpsService = Depends(get_ops_service),
) -> OpsModuleSummaryResponse:
    item, cached, served_at = service.get_module_summary_cached_meta(module_code)
    return OpsModuleSummaryResponse(
        item=item,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/{module_code}/pending", response_model=PendingTaskListResponse)
def ops_module_pending(
    module_code: str,
    month: str | None = Query(default=None),
    visit_owner: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    service: OpsService = Depends(get_ops_service),
) -> PendingTaskListResponse:
    owner = visit_owner if module_code in {"visit", "proactive"} else None
    items, cached, served_at = service.list_pending_tasks_cached_meta(
        module_code=module_code,
        limit=limit,
        month=month,
        visit_owner=owner,
    )
    return PendingTaskListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/{module_code}/recent/visit", response_model=RecentVisitLinkListResponse)
def ops_module_recent_visit(
    module_code: str,
    limit: int = Query(default=20, ge=1, le=200),
    service: OpsService = Depends(get_ops_service),
) -> RecentVisitLinkListResponse:
    if module_code not in {"visit", "proactive"}:
        raise HTTPException(status_code=400, detail="recent visit 仅支持 visit / proactive 模块")
    items, cached, served_at = service.list_recent_visit_links_cached_meta(module_code=module_code, limit=limit)
    return RecentVisitLinkListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/visit/owners", response_model=StringListResponse)
def ops_visit_owners(service: OpsService = Depends(get_ops_service)) -> StringListResponse:
    items, cached, served_at = service.list_visit_owners_cached_meta()
    return StringListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/proactive/owners", response_model=StringListResponse)
def ops_proactive_owners(service: OpsService = Depends(get_ops_service)) -> StringListResponse:
    items, cached, served_at = service.list_proactive_owners_cached_meta()
    return StringListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/pts-session", response_model=PtsSessionStatusResponse)
def ops_pts_session_status() -> PtsSessionStatusResponse:
    return PtsSessionStatusResponse(**PtsSessionService().get_status())


@router.post("/ops/pts-session", response_model=PtsSessionStatusResponse)
def ops_update_pts_session(request: PtsSessionUpdateRequest) -> PtsSessionStatusResponse:
    try:
        service = PtsSessionService()
        if request.api_token:
            return PtsSessionStatusResponse(**service.update_api_token(request.api_token))
        if request.cookie_header:
            return PtsSessionStatusResponse(**service.update_cookie(request.cookie_header))
        raise HTTPException(status_code=400, detail="api_token 或 cookie_header 至少需要提供一个")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
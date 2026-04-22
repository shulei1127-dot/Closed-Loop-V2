from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query

from apps.api.deps import get_inspection_deadline_reminder_service, get_ops_service
from schemas.deadline_reminder import (
    DeadlineReminderItem,
    DeadlineReminderListResponse,
    DeadlineReminderRunResponse,
    DeadlineReminderRunSummary,
)
from schemas.ops import (
    OpsDataMeta,
    OpsEventListResponse,
    OpsModuleSummaryResponse,
    OpsOverviewResponse,
    PendingTaskListResponse,
    PtsSessionStatusResponse,
    PtsSessionUpdateRequest,
    RecentInspectionClosureListResponse,
    RecentVisitLinkListResponse,
    StringListResponse,
)
from services.ops_service import OpsService
from services.pts_session_service import PtsSessionService
from services.reminders.inspection_deadline_service import InspectionDeadlineReminderService


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


@router.get("/ops/modules/{module_code}/recent/inspection", response_model=RecentInspectionClosureListResponse)
def ops_module_recent_inspection(
    module_code: str,
    month: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=200),
    service: OpsService = Depends(get_ops_service),
) -> RecentInspectionClosureListResponse:
    if module_code != "inspection":
        raise HTTPException(status_code=400, detail="recent inspection 仅支持 inspection 模块")
    items, cached, served_at = service.list_recent_inspection_closures_cached_meta(month=month, limit=limit)
    return RecentInspectionClosureListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/{module_code}/reviewed/inspection", response_model=PendingTaskListResponse)
def ops_module_reviewed_inspection(
    module_code: str,
    month: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    service: OpsService = Depends(get_ops_service),
) -> PendingTaskListResponse:
    if module_code != "inspection":
        raise HTTPException(status_code=400, detail="reviewed inspection 仅支持 inspection 模块")
    items, cached, served_at = service.list_inspection_reviewed_tasks_cached_meta(month=month, limit=limit)
    return PendingTaskListResponse(
        items=items,
        meta=OpsDataMeta(cached=cached, served_at=served_at),
    )


@router.get("/ops/modules/{module_code}/no-action/inspection", response_model=PendingTaskListResponse)
def ops_module_no_action_inspection(
    module_code: str,
    month: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    service: OpsService = Depends(get_ops_service),
) -> PendingTaskListResponse:
    if module_code != "inspection":
        raise HTTPException(status_code=400, detail="no-action inspection 仅支持 inspection 模块")
    items, cached, served_at = service.list_inspection_no_action_tasks_cached_meta(month=month, limit=limit)
    return PendingTaskListResponse(
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
        return PtsSessionStatusResponse(**PtsSessionService().update_cookie(request.cookie_header))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _build_deadline_reminder_item(item) -> DeadlineReminderItem:
    return DeadlineReminderItem(
        reminder_id=str(item.id),
        module_code=item.module_code,
        pts_work_order_id=item.pts_work_order_id,
        pts_work_order_link=item.pts_work_order_link,
        customer_name=item.customer_name,
        service_type=item.service_type,
        status_text=item.status_text,
        remind_type=item.remind_type,
        deadline_date=item.deadline_date,
        plan_finish_time_raw=item.plan_finish_time_raw,
        send_status=item.send_status,
        message_channel=item.message_channel,
        sender_type=item.sender_type,
        error_message=item.error_message,
        raw_payload=item.raw_payload or {},
        send_payload=item.send_payload or {},
        sent_at=item.sent_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/ops/inspection-deadline-reminders", response_model=DeadlineReminderListResponse)
def ops_list_inspection_deadline_reminders(
    limit: int = Query(default=50, ge=1, le=500),
    send_status: str | None = Query(default=None),
    remind_type: str | None = Query(default=None),
    service: InspectionDeadlineReminderService = Depends(get_inspection_deadline_reminder_service),
) -> DeadlineReminderListResponse:
    items = service.list_reminders(limit=limit, send_status=send_status, remind_type=remind_type)
    return DeadlineReminderListResponse(items=[_build_deadline_reminder_item(item) for item in items])


@router.post("/ops/inspection-deadline-reminders/run", response_model=DeadlineReminderRunResponse)
async def ops_run_inspection_deadline_reminders(
    service: InspectionDeadlineReminderService = Depends(get_inspection_deadline_reminder_service),
) -> DeadlineReminderRunResponse:
    summary = await service.run_cycle(trigger="ops_api")
    return DeadlineReminderRunResponse(
        summary=DeadlineReminderRunSummary(
            trigger=summary.trigger,
            scanned_count=summary.scanned_count,
            eligible_count=summary.eligible_count,
            sent_count=summary.sent_count,
            failed_count=summary.failed_count,
            duplicate_count=summary.duplicate_count,
            skipped_count=summary.skipped_count,
        )
    )

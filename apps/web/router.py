from __future__ import annotations

import json
from pathlib import Path
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.db import get_db
from repositories.normalized_record_repo import NormalizedRecordRepository
from repositories.source_snapshot_repo import SourceSnapshotRepository
from repositories.task_plan_repo import TaskPlanRepository
from repositories.task_run_repo import TaskRunRepository
from services.ops_copy import build_run_view
from services.ops_service import OpsService
from services.pts_session_service import PtsSessionService
from services.sync_service import SyncService
from services.task_execution_service import TaskExecutionService


BASE_DIR = Path(__file__).resolve().parents[2]
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["to_pretty_json"] = lambda value: json.dumps(value or {}, ensure_ascii=False, indent=2)
_app_js_mtime = (BASE_DIR / "static" / "console" / "app.js").stat().st_mtime
_app_css_mtime = (BASE_DIR / "static" / "console" / "console.css").stat().st_mtime
templates.env.globals["static_rev"] = str(int(max(_app_js_mtime, _app_css_mtime)))


def _format_local_datetime(value) -> str:
    if not value:
        return "暂无"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["fmt_dt"] = _format_local_datetime

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse(url="/console", status_code=302)


@router.get("/console", response_class=HTMLResponse)
def console_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    pts_session_status = PtsSessionService().get_status()
    inspection_month = request.query_params.get("inspection_month") or datetime.now(LOCAL_TZ).strftime("%Y-%m")
    available_inspection_months = ops_service.list_pending_inspection_months()
    if inspection_month not in available_inspection_months and available_inspection_months:
        inspection_month = available_inspection_months[0]
    return templates.TemplateResponse(
        name="console/dashboard.html",
        request=request,
        context={
            "page_title": "模块总览",
            "module_summaries": [item.model_dump() for item in ops_service.build_overview()],
            "failure_items": [item.model_dump() for item in ops_service.list_failures(limit=10)],
            "manual_required_items": [item.model_dump() for item in ops_service.list_manual_required(limit=10)],
            "pending_visit_items": [
                item.model_dump()
                for item in ops_service.list_pending_tasks(module_code="visit", limit=20, visit_owner="舒磊")
            ],
            "pending_inspection_items": [
                item.model_dump()
                for item in ops_service.list_pending_tasks(module_code="inspection", limit=50, month=inspection_month)
            ],
            "inspection_month": inspection_month,
            "available_inspection_months": available_inspection_months,
            "recent_inspection_closures": [
                item.model_dump()
                for item in ops_service.list_recent_inspection_closures(month=inspection_month, limit=10)
            ],
            "recent_visit_links": [item.model_dump() for item in ops_service.list_recent_visit_links(limit=10)],
            "pts_session_status": pts_session_status,
            "active_nav": "dashboard",
        },
    )


@router.get("/console/modules/visit", response_class=HTMLResponse)
def console_visit_module(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    module_summary_map = {item.module_code: item.model_dump() for item in ops_service.build_overview()}
    selected_visit_owner = (request.query_params.get("visit_owner") or "").strip()
    visit_owner_filter = None if selected_visit_owner in {"", "all", "全部"} else selected_visit_owner
    pending_visit_items = [
        item.model_dump()
        for item in ops_service.list_pending_tasks(
            module_code="visit",
            limit=200,
            visit_owner=visit_owner_filter,
        )
    ]
    pending_visit_executable_count = sum(1 for item in pending_visit_items if item.get("can_execute"))
    pending_visit_total_count = len(ops_service.list_pending_tasks(module_code="visit", limit=5000, visit_owner=None))
    available_visit_owners = ops_service.list_visit_owners()
    if selected_visit_owner and selected_visit_owner not in {"all", "全部", *available_visit_owners}:
        available_visit_owners = sorted({*available_visit_owners, selected_visit_owner})
    return templates.TemplateResponse(
        name="console/module_visit.html",
        request=request,
        context={
            "page_title": "交付转售后回访",
            "module_summary": module_summary_map.get("visit"),
            "pending_visit_items": pending_visit_items,
            "pending_visit_executable_count": pending_visit_executable_count,
            "pending_visit_filtered_count": len(pending_visit_items),
            "pending_visit_total_count": pending_visit_total_count,
            "selected_visit_owner": selected_visit_owner,
            "available_visit_owners": available_visit_owners,
            "recent_visit_links": [item.model_dump() for item in ops_service.list_recent_visit_links(limit=20)],
            "active_nav": "module_visit",
        },
    )


@router.get("/console/modules/inspection", response_class=HTMLResponse)
def console_inspection_module(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    module_summary_map = {item.module_code: item.model_dump() for item in ops_service.build_overview()}
    inspection_month = request.query_params.get("inspection_month") or datetime.now(LOCAL_TZ).strftime("%Y-%m")
    available_inspection_months = ops_service.list_pending_inspection_months()
    available_sync_months = ops_service.list_known_inspection_months()
    if inspection_month not in available_sync_months:
        available_sync_months = sorted({*available_sync_months, inspection_month}, reverse=True)
    if inspection_month not in available_inspection_months and available_inspection_months:
        inspection_month = available_inspection_months[0]
    return templates.TemplateResponse(
        name="console/module_inspection.html",
        request=request,
        context={
            "page_title": "巡检工单闭环",
            "module_summary": module_summary_map.get("inspection"),
            "pending_inspection_items": [
                item.model_dump()
                for item in ops_service.list_pending_tasks(module_code="inspection", limit=100, month=inspection_month)
            ],
            "inspection_month": inspection_month,
            "available_inspection_months": available_inspection_months,
            "available_sync_months": available_sync_months,
            "recent_inspection_closures": [
                item.model_dump()
                for item in ops_service.list_recent_inspection_closures(month=inspection_month, limit=10)
            ],
            "active_nav": "module_inspection",
        },
    )


@router.get("/console/modules/proactive", response_class=HTMLResponse)
def console_proactive_module(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    module_summary_map = {item.module_code: item.model_dump() for item in ops_service.build_overview()}
    return templates.TemplateResponse(
        name="console/module_proactive.html",
        request=request,
        context={
            "page_title": "超半年主动回访",
            "module_summary": module_summary_map.get("proactive"),
            "pending_proactive_items": [
                item.model_dump() for item in ops_service.list_pending_tasks(module_code="proactive", limit=100)
            ],
            "active_nav": "module_proactive",
        },
    )


@router.get("/console/visit-links", response_class=HTMLResponse)
def console_visit_links(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    return templates.TemplateResponse(
        name="console/visit_links.html",
        request=request,
        context={
            "page_title": "闭环回访链接",
            "visit_link_items": [item.model_dump() for item in ops_service.list_recent_visit_links(limit=None)],
            "active_nav": "dashboard",
        },
    )


@router.get("/console/inspection-links", response_class=HTMLResponse)
def console_inspection_links(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ops_service = OpsService(db)
    inspection_month = request.query_params.get("inspection_month") or datetime.now(LOCAL_TZ).strftime("%Y-%m")
    return templates.TemplateResponse(
        name="console/inspection_links.html",
        request=request,
        context={
            "page_title": "全部巡检闭环记录",
            "inspection_month": inspection_month,
            "inspection_link_items": [
                item.model_dump()
                for item in ops_service.list_recent_inspection_closures(month=inspection_month, limit=None)
            ],
            "active_nav": "module_inspection",
        },
    )


@router.get("/console/snapshots", response_class=HTMLResponse)
def console_snapshots(
    request: Request,
    module_code: str | None = Query(default=None),
    snapshot_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    repo = SourceSnapshotRepository(db)
    service = SyncService(db)
    snapshots = repo.list_recent(module_code=module_code, limit=50)
    selected_snapshot = service.get_snapshot_detail(snapshot_id) if snapshot_id else None
    return templates.TemplateResponse(
        name="console/snapshots.html",
        request=request,
        context={
            "page_title": "快照",
            "snapshots": snapshots,
            "selected_snapshot": selected_snapshot,
            "selected_snapshot_id": str(snapshot_id) if snapshot_id else None,
            "module_code": module_code,
            "active_nav": "snapshots",
        },
    )


@router.get("/console/tasks", response_class=HTMLResponse)
def console_tasks(
    request: Request,
    module_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    month: str | None = Query(default=None),
    task_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    task_repo = TaskPlanRepository(db)
    task_run_repo = TaskRunRepository(db)
    record_repo = NormalizedRecordRepository(db)
    sync_service = SyncService(db)
    ops_service = OpsService(db)
    effective_status = status or "pending"
    if effective_status == "pending":
        pending_groups = ops_service._collect_pending_task_groups(module_code=module_code, month=month)
        tasks = [group["task"] for group in pending_groups]
        tasks.sort(key=lambda item: item.created_at, reverse=True)
    else:
        repo_status = None if effective_status == "all" else effective_status
        tasks = task_repo.list_latest_by_business_key(module_code=module_code, status=repo_status)

    task_rows = []
    for task in tasks:
        latest_runs = task_run_repo.list_by_task_plan(str(task.id))
        latest_run = latest_runs[0] if latest_runs else None
        record = record_repo.get_by_id(task.normalized_record_id)
        customer_name = (
            getattr(record, "customer_name", None)
            or (getattr(record, "normalized_data", {}) or {}).get("customer_name")
            or task.planned_payload.get("customer_name")
        )
        latest_run_view = (
            build_run_view(
                run_status=latest_run.run_status,
                result_payload=latest_run.result_payload,
                manual_required=latest_run.manual_required,
                retryable=ops_service._resolve_retryable(latest_run),
                error_message=latest_run.error_message,
                customer_name=customer_name,
                task_plan_id=str(task.id),
                task_run_id=str(latest_run.id),
            )
            if latest_run
            else None
        )
        task_rows.append(
            {
                "task": task,
                "customer_name": customer_name,
                "latest_run": latest_run,
                "latest_run_view": latest_run_view,
            }
        )

    selected_task = sync_service.get_task_detail(task_id) if task_id else None
    selected_runs = task_run_repo.list_by_task_plan(str(task_id)) if task_id else []
    failure_items = [item.model_dump() for item in ops_service.list_failures(limit=10)]
    manual_required_items = [item.model_dump() for item in ops_service.list_manual_required(limit=10)]
    selected_run_views = []
    if task_id:
        record = record_repo.get_by_id(selected_task.normalized_record_id) if selected_task else None
        customer_name = (
            getattr(record, "customer_name", None)
            or (getattr(record, "normalized_data", {}) or {}).get("customer_name")
            or (selected_task.planned_payload.get("customer_name") if selected_task else None)
        )
        for run in selected_runs:
            selected_run_views.append(
                {
                    "run": run,
                    "view": build_run_view(
                        run_status=run.run_status,
                        result_payload=run.result_payload,
                        manual_required=run.manual_required,
                        retryable=ops_service._resolve_retryable(run),
                        error_message=run.error_message,
                        customer_name=customer_name,
                        task_plan_id=str(task_id),
                        task_run_id=str(run.id),
                    ),
                }
            )
    return templates.TemplateResponse(
        name="console/tasks.html",
        request=request,
        context={
            "page_title": "任务",
            "task_rows": task_rows,
            "selected_task": selected_task,
            "selected_runs": selected_runs,
            "selected_run_views": selected_run_views,
            "selected_task_id": str(task_id) if task_id else None,
            "module_code": module_code,
            "status": effective_status,
            "month": month,
            "failure_items": failure_items,
            "manual_required_items": manual_required_items,
            "active_nav": "tasks",
        },
    )


@router.get("/console/task-runs/{run_id}", response_class=HTMLResponse)
def console_task_run_detail(
    run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    service = TaskExecutionService(db)
    task_run = service.get_task_run_detail(run_id)
    task_repo = TaskPlanRepository(db)
    record_repo = NormalizedRecordRepository(db)
    ops_service = OpsService(db)
    task_plan = task_repo.get_by_id(uuid.UUID(task_run.task_plan_id))
    customer_name = None
    if task_plan is not None:
        record = record_repo.get_by_id(task_plan.normalized_record_id)
        customer_name = (
            getattr(record, "customer_name", None)
            or (getattr(record, "normalized_data", {}) or {}).get("customer_name")
        )
    task_run_view = build_run_view(
        run_status=task_run.run_status,
        result_payload=task_run.result_payload,
        manual_required=task_run.manual_required,
        retryable=ops_service._resolve_retryable(task_run),
        error_message=task_run.error_message,
        customer_name=customer_name,
        task_plan_id=task_run.task_plan_id,
        task_run_id=task_run.task_run_id,
    )
    return templates.TemplateResponse(
        name="console/task_run_detail.html",
        request=request,
        context={
            "page_title": "执行结果",
            "task_run": task_run,
            "task_run_view": task_run_view,
            "active_nav": "tasks",
        },
    )


@router.get("/console/records", response_class=HTMLResponse)
def console_records(
    request: Request,
    module_code: str | None = Query(default=None),
    snapshot_id: str | None = Query(default=None),
    record_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    repo = NormalizedRecordRepository(db)
    service = SyncService(db)
    records = repo.list_by_filters(module_code=module_code, snapshot_id=snapshot_id)
    selected_record = service.get_record_detail(record_id) if record_id else None
    return templates.TemplateResponse(
        name="console/records.html",
        request=request,
        context={
            "page_title": "标准化记录",
            "records": records,
            "selected_record": selected_record,
            "selected_record_id": str(record_id) if record_id else None,
            "module_code": module_code,
            "snapshot_id": snapshot_id,
            "active_nav": "records",
        },
    )

import asyncio
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from core.config import Settings, get_settings
from core.db import get_db
from models.deadline_reminder import DeadlineReminder
from scheduler.jobs import register_jobs
from services.collectors.inspection_deadline_collector import InspectionDeadlineCollector
from services.reminders.inspection_deadline_service import InspectionDeadlineReminderService
from services.reminders.rules import LOCAL_TZ, parse_local_date
from services.reminders.schemas import ReminderRunSummary


def _build_settings(**overrides) -> Settings:
    return Settings(
        pts_base_url="https://pts.example.com",
        inspection_deadline_reminder_query_limit=50,
        **overrides,
    )


def _build_row(
    *,
    pts_work_order_id: str,
    deadline_offset_days: int,
    service_type: str = "巡检工单",
    status_text: str = "处理中",
) -> dict:
    deadline = datetime.now(LOCAL_TZ).date() + timedelta(days=deadline_offset_days)
    return {
        "id": pts_work_order_id,
        "customer_name": f"客户-{pts_work_order_id}",
        "service_type": service_type,
        "status_name": status_text,
        "plan_finish_time": deadline.isoformat(),
        "link": f"https://pts.example.com/project/{pts_work_order_id}",
    }


def test_parse_local_date_uses_asia_shanghai_natural_day() -> None:
    assert parse_local_date("2026-04-15T18:30:00Z") == date(2026, 4, 16)
    assert parse_local_date(1776210600000) == date(2026, 4, 15)


def test_deadline_reminder_service_uses_log_fallback_and_marks_sent(db_session) -> None:
    settings = _build_settings()

    async def raw_fetcher(limit: int) -> list[dict]:
        assert limit == 50
        return [_build_row(pts_work_order_id="PTS-001", deadline_offset_days=1)]

    collector = InspectionDeadlineCollector(settings, raw_fetcher=raw_fetcher)
    service = InspectionDeadlineReminderService(db_session, settings, collector=collector)

    summary = asyncio.run(service.run_cycle(trigger="test"))

    assert summary.sent_count == 1
    reminder = db_session.scalars(select(DeadlineReminder)).one()
    assert reminder.pts_work_order_id == "PTS-001"
    assert reminder.remind_type == "due_in_1d"
    assert reminder.send_status == "sent"
    assert reminder.message_channel == "log_fallback"
    assert reminder.sender_type == "log_fallback"


def test_deadline_reminder_service_filters_rows_and_is_business_idempotent(db_session) -> None:
    settings = _build_settings()

    async def raw_fetcher(limit: int) -> list[dict]:
        return [
            _build_row(pts_work_order_id="PTS-101", deadline_offset_days=3),
            _build_row(pts_work_order_id="PTS-102", deadline_offset_days=1, service_type="非巡检"),
            _build_row(pts_work_order_id="PTS-103", deadline_offset_days=-1, status_text="已关闭"),
            {"id": "PTS-104", "customer_name": "客户-PTS-104", "service_type": "巡检工单", "status_name": "处理中"},
        ]

    collector = InspectionDeadlineCollector(settings, raw_fetcher=raw_fetcher)
    service = InspectionDeadlineReminderService(db_session, settings, collector=collector)

    first_summary = asyncio.run(service.run_cycle(trigger="test"))
    second_summary = asyncio.run(service.run_cycle(trigger="test"))

    assert first_summary.sent_count == 1
    assert first_summary.skipped_count == 3
    assert second_summary.sent_count == 0
    assert second_summary.duplicate_count == 1


def test_deadline_reminder_service_handles_unique_constraint_conflict(db_session, monkeypatch) -> None:
    settings = _build_settings()
    deadline_date = datetime.now(LOCAL_TZ).date() + timedelta(days=3)
    db_session.add(
        DeadlineReminder(
            module_code="inspection",
            pts_work_order_id="PTS-201",
            pts_work_order_link="https://pts.example.com/project/PTS-201",
            customer_name="客户-PTS-201",
            service_type="巡检工单",
            status_text="处理中",
            remind_type="due_in_3d",
            deadline_date=deadline_date,
            plan_finish_time_raw=deadline_date.isoformat(),
            send_status="sent",
            message_channel="log_fallback",
            sender_type="log_fallback",
            raw_payload={},
            send_payload={},
        )
    )
    db_session.commit()

    async def raw_fetcher(limit: int) -> list[dict]:
        return [_build_row(pts_work_order_id="PTS-201", deadline_offset_days=3)]

    collector = InspectionDeadlineCollector(settings, raw_fetcher=raw_fetcher)
    service = InspectionDeadlineReminderService(db_session, settings, collector=collector)
    monkeypatch.setattr(service.repo, "get_by_business_key", lambda **kwargs: None)

    summary = asyncio.run(service.run_cycle(trigger="test"))

    assert summary.duplicate_count == 1
    reminders = list(db_session.scalars(select(DeadlineReminder)).all())
    assert len(reminders) == 1


def test_ops_deadline_reminder_api_list_and_run(db_session, monkeypatch) -> None:
    reminder = DeadlineReminder(
        module_code="inspection",
        pts_work_order_id="PTS-301",
        pts_work_order_link="https://pts.example.com/project/PTS-301",
        customer_name="客户-PTS-301",
        service_type="巡检工单",
        status_text="处理中",
        remind_type="due_in_1d",
        deadline_date=datetime.now(LOCAL_TZ).date() + timedelta(days=1),
        plan_finish_time_raw="2026-04-16T00:00:00+08:00",
        send_status="sent",
        message_channel="log_fallback",
        sender_type="log_fallback",
        raw_payload={"id": "PTS-301"},
        send_payload={"message_text": "fallback"},
    )
    db_session.add(reminder)
    db_session.commit()

    async def fake_run_cycle(self, *, trigger: str = "manual") -> ReminderRunSummary:
        return ReminderRunSummary(
            trigger=trigger,
            scanned_count=3,
            eligible_count=2,
            sent_count=2,
            failed_count=0,
            duplicate_count=1,
            skipped_count=0,
        )

    monkeypatch.setattr(InspectionDeadlineReminderService, "run_cycle", fake_run_cycle)

    class _FakeDispatcher:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    monkeypatch.setattr("apps.api.main.get_task_dispatcher", lambda: _FakeDispatcher())
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        list_response = test_client.get("/api/ops/inspection-deadline-reminders?limit=20")
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["pts_work_order_id"] == "PTS-301"
        assert list_response.json()["items"][0]["message_channel"] == "log_fallback"

        run_response = test_client.post("/api/ops/inspection-deadline-reminders/run")
        assert run_response.status_code == 200
        assert run_response.json()["summary"]["trigger"] == "ops_api"
        assert run_response.json()["summary"]["sent_count"] == 2
    app.dependency_overrides.clear()


def test_scheduler_registers_inspection_deadline_reminder_job(db_session, monkeypatch) -> None:
    monkeypatch.setenv("INSPECTION_DEADLINE_REMINDER_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_DEADLINE_REMINDER_CRON", "15 8 * * *")
    get_settings.cache_clear()

    testing_session_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    scheduler = BackgroundScheduler()

    job_ids = register_jobs(scheduler, session_factory=testing_session_factory)

    assert "reminder:inspection-deadline" in job_ids
    get_settings.cache_clear()

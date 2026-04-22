from __future__ import annotations

from datetime import datetime
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from models.deadline_reminder import DeadlineReminder
from repositories.deadline_reminder_repo import DeadlineReminderRepository
from services.collectors.inspection_deadline_collector import InspectionDeadlineCollector
from services.reminders.rules import LOCAL_TZ, is_eligible_candidate, resolve_remind_type
from services.reminders.schemas import InspectionDeadlineCandidate, ReminderRunSummary
from services.reminders.senders.dingtalk_sender import DingtalkInspectionDeadlineSender
from services.reminders.senders.log_sender import LogInspectionDeadlineSender


logger = logging.getLogger(__name__)


class InspectionDeadlineReminderService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        *,
        collector: InspectionDeadlineCollector | None = None,
        primary_sender=None,
        fallback_sender=None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.repo = DeadlineReminderRepository(db)
        self.collector = collector or InspectionDeadlineCollector(self.settings)
        self._primary_sender = primary_sender
        self._fallback_sender = fallback_sender or LogInspectionDeadlineSender()

    async def run_cycle(self, *, trigger: str = "manual") -> ReminderRunSummary:
        summary = ReminderRunSummary(trigger=trigger)
        items = await self.collector.collect(limit=self.settings.inspection_deadline_reminder_query_limit)
        summary.scanned_count = len(items)
        today = datetime.now(LOCAL_TZ).date()

        for item in items:
            if not is_eligible_candidate(item):
                summary.skipped_count += 1
                continue
            remind_type = resolve_remind_type(deadline_date=item.plan_finish_date, today=today)
            if not remind_type:
                summary.skipped_count += 1
                continue
            summary.eligible_count += 1
            if self.repo.get_by_business_key(pts_work_order_id=item.pts_work_order_id, remind_type=remind_type):
                summary.duplicate_count += 1
                continue

            reminder = self._create_pending_record(item=item, remind_type=remind_type)
            if reminder is None:
                summary.duplicate_count += 1
                continue

            sender = self._resolve_sender()
            try:
                send_result = await sender.send(
                    item=item,
                    remind_type=remind_type,
                    message_text=self._build_message(item=item, remind_type=remind_type),
                )
            except Exception as exc:
                logger.exception(
                    "inspection deadline reminder sender raised unexpected error: pts_work_order_id=%s remind_type=%s",
                    item.pts_work_order_id,
                    remind_type,
                )
                self.repo.mark_failed(
                    reminder,
                    message_channel=getattr(sender, "message_channel", None),
                    sender_type=getattr(sender, "sender_type", None),
                    send_payload={"exception": str(exc)},
                    error_message=str(exc),
                )
                self.db.commit()
                summary.failed_count += 1
                continue
            if send_result.success:
                self.repo.mark_sent(
                    reminder,
                    message_channel=send_result.message_channel,
                    sender_type=send_result.sender_type,
                    send_payload=send_result.payload,
                )
                self.db.commit()
                summary.sent_count += 1
            else:
                self.repo.mark_failed(
                    reminder,
                    message_channel=send_result.message_channel,
                    sender_type=send_result.sender_type,
                    send_payload=send_result.payload,
                    error_message=send_result.error_message,
                )
                self.db.commit()
                summary.failed_count += 1
        return summary

    def list_reminders(
        self,
        *,
        limit: int = 50,
        send_status: str | None = None,
        remind_type: str | None = None,
    ) -> list[DeadlineReminder]:
        return self.repo.list_recent(limit=limit, send_status=send_status, remind_type=remind_type)

    def _create_pending_record(
        self,
        *,
        item: InspectionDeadlineCandidate,
        remind_type: str,
    ) -> DeadlineReminder | None:
        try:
            reminder = self.repo.create_pending(reminder_item=item, remind_type=remind_type)
            self.db.commit()
            self.db.refresh(reminder)
            return reminder
        except IntegrityError:
            self.db.rollback()
            logger.info(
                "inspection deadline reminder duplicate skipped by unique constraint: pts_work_order_id=%s remind_type=%s",
                item.pts_work_order_id,
                remind_type,
            )
            return None

    def _resolve_sender(self):
        if self._primary_sender is not None:
            return self._primary_sender
        dingtalk_sender = DingtalkInspectionDeadlineSender(self.settings)
        if dingtalk_sender.is_configured():
            return dingtalk_sender
        return self._fallback_sender

    @staticmethod
    def _build_message(*, item: InspectionDeadlineCandidate, remind_type: str) -> str:
        remind_label_map = {
            "due_in_3d": "3天后到期",
            "due_in_1d": "1天后到期",
            "overdue": "已逾期",
        }
        lines = [
            f"[巡检工单截止提醒] {remind_label_map.get(remind_type, remind_type)}",
            f"工单ID: {item.pts_work_order_id}",
            f"客户: {item.customer_name or '-'}",
            f"服务类型: {item.service_type or '-'}",
            f"状态: {item.status_text or '-'}",
            f"截止日期: {item.plan_finish_date.isoformat() if item.plan_finish_date else '-'}",
        ]
        if item.pts_work_order_link:
            lines.append(f"链接: {item.pts_work_order_link}")
        return "\n".join(lines)

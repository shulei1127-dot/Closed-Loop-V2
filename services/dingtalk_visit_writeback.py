from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

from core.config import Settings, get_settings
from services.executors.schemas import ExecutorContext

# AI 表格中"回访链接"字段的 field-id，按文档 docKey 区分
_VISIT_LINK_FIELD_ID = "35voqsv7xy8pk5pknr47m"  # 交付转售后回访
_PROACTIVE_LINK_FIELD_ID = "vxfqjxrfpcm57vc1rlbu4"  # 超半年主动回访

# aitable base-id 映射（docKey -> base-id）
_AITABLE_BASE_MAP = {
    "4j6OJ5jPAGa8eq3p": "o14dA3GK8g5LavPaT7dDQqoxV9ekBD76",  # 交付转售后
    "J9LnW6jQKp6yelvD": "KGZLxjv9VG37XNDXS45epDXYV6EDybno",  # 超半年主动回访
}

# aitable table-id 映射（docKey -> table-id）
_AITABLE_TABLE_MAP = {
    "4j6OJ5jPAGa8eq3p": "Igz9TVd",
    "J9LnW6jQKp6yelvD": "Z991EZV",
}


class DingtalkVisitWritebackError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.retryable = retryable


def _resolve_dws_cli_path(settings: Settings) -> str | None:
    """返回 DWS CLI 可执行文件路径，不存在则返回 None。"""
    explicit = settings.visit_writeback_dws_cli_path
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None
    return shutil.which("dws")


class DingtalkVisitWritebackService:
    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()

    async def write_visit_link(self, *, context: ExecutorContext, final_link: str) -> dict[str, Any]:
        # 非 dingtalk/dws_cli 来源直接跳过
        if context.source_collector_type not in {"dingtalk", "real", "dws_cli"}:
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "skipped",
                "http_status": None,
                "retryable": False,
                "error_message": None,
                "error_type": None,
                "writeback_mode": "disabled",
            }

        # DWS CLI 可用时，只需 source_row_id 和 source_doc_key 即可写入
        dws_cli = _resolve_dws_cli_path(self.settings)
        if dws_cli and context.source_row_id and context.source_doc_key:
            return await self._write_via_dws_cli(
                dws_cli=dws_cli,
                target_row_id=context.source_row_id,
                final_link=final_link,
                source_doc_key=context.source_doc_key,
            )

        return {
            "action": "writeback_visit_link_to_dingtalk",
            "status": "skipped",
            "http_status": None,
            "retryable": False,
            "error_message": None,
            "error_type": None,
            "writeback_mode": "disabled",
        }

    async def _write_via_dws_cli(
        self,
        *,
        dws_cli: str,
        target_row_id: str,
        final_link: str,
        source_doc_key: str | None = None,
    ) -> dict[str, Any]:
        """使用 DWS CLI aitable record update 写入回访链接。"""
        # 根据 source_doc_key 确定 aitable 配置
        doc_key = source_doc_key or ""
        base_id = _AITABLE_BASE_MAP.get(doc_key, self.settings.visit_writeback_aitable_base_id)
        table_id = _AITABLE_TABLE_MAP.get(doc_key, "Igz9TVd")
        link_field_id = _PROACTIVE_LINK_FIELD_ID if doc_key == "J9LnW6jQKp6yelvD" else _VISIT_LINK_FIELD_ID

        # 标签标记回写使用纯文本；URL 回写使用 link+text 格式
        if final_link.startswith("已打标签"):
            cell_value: str | dict[str, str] = "已打标签"
        else:
            cell_value = {"link": final_link, "text": final_link}

        records = json.dumps(
            [
                {
                    "recordId": target_row_id,
                    "cells": {
                        link_field_id: cell_value,
                    },
                }
            ],
            ensure_ascii=False,
        )

        cmd = [
            dws_cli,
            "aitable",
            "record",
            "update",
            "--base-id", base_id,
            "--table-id", table_id,
            "--records", records,
            "-y",
            "--timeout", "60",
        ]

        try:
            result = await asyncio.to_thread(
                _run_subprocess,
                cmd,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                return {
                    "action": "writeback_visit_link_to_dingtalk",
                    "status": "failed",
                    "http_status": None,
                    "retryable": True,
                    "error_message": f"DWS CLI 执行失败: {stderr}",
                    "error_type": "dws_cli_error",
                    "source_row_id": target_row_id,
                    "field_name": "回访链接",
                    "visit_link": final_link,
                    "writeback_mode": "dws_cli",
                }

            # 解析 DWS CLI 输出
            stdout = (result.stdout or "").strip()
            try:
                output = json.loads(stdout)
            except json.JSONDecodeError:
                output = {}
            status = output.get("status", "unknown")
            updated_ids = output.get("data", {}).get("recordIds", [])

            if status == "success" and target_row_id in updated_ids:
                return {
                    "action": "writeback_visit_link_to_dingtalk",
                    "status": "success",
                    "http_status": 200,
                    "retryable": False,
                    "error_message": None,
                    "error_type": None,
                    "source_row_id": target_row_id,
                    "field_name": "回访链接",
                    "field_id": link_field_id,
                    "visit_link": final_link,
                    "writeback_mode": "dws_cli",
                }
            error_msg = output.get("error", {}).get("message", "") or stdout[:200]
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "failed",
                "http_status": None,
                "retryable": True,
                "error_message": f"DWS CLI 返回失败: {error_msg}",
                "error_type": "dws_cli_error",
                "source_row_id": target_row_id,
                "field_name": "回访链接",
                "field_id": link_field_id,
                "visit_link": final_link,
                "writeback_mode": "dws_cli",
            }
        except FileNotFoundError:
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "failed",
                "http_status": None,
                "retryable": False,
                "error_message": f"DWS CLI 未找到: {dws_cli}",
                "error_type": "dws_cli_not_found",
                "source_row_id": target_row_id,
                "field_name": "回访链接",
                "field_id": link_field_id,
                "visit_link": final_link,
                "writeback_mode": "dws_cli",
            }
        except Exception as exc:
            return {
                "action": "writeback_visit_link_to_dingtalk",
                "status": "failed",
                "http_status": None,
                "retryable": True,
                "error_message": f"DWS CLI 执行异常: {exc}",
                "error_type": "dws_cli_error",
                "source_row_id": target_row_id,
                "field_name": "回访链接",
                "field_id": link_field_id,
                "visit_link": final_link,
                "writeback_mode": "dws_cli",
            }


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """同步运行子进程，供 asyncio.to_thread 调用。"""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

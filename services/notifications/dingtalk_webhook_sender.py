from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from core.config import Settings, get_settings


class DingtalkTextWebhookSender:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory=None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client_factory = client_factory

    def is_configured(self) -> bool:
        return bool(str(self.settings.scheduler_summary_dingtalk_webhook or "").strip())

    async def send_text(self, message_text: str) -> dict:
        webhook = self._build_signed_webhook(str(self.settings.scheduler_summary_dingtalk_webhook or "").strip())
        payload = {
            "msgtype": "text",
            "text": {"content": message_text},
        }
        client = self._client_factory
        if client is None:
            async with httpx.AsyncClient(
                timeout=self.settings.scheduler_summary_dingtalk_timeout_seconds,
                verify=self.settings.dingtalk_verify_ssl,
            ) as default_client:
                response = await default_client.post(webhook, json=payload)
        else:
            response = await client.post(webhook, json=payload)
        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "response_text": response.text,
                "request_payload": payload,
                "error_message": f"DingTalk webhook request failed: {response.status_code}",
            }
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        if isinstance(body, dict) and body.get("errcode") not in (None, 0):
            return {
                "success": False,
                "response": body,
                "request_payload": payload,
                "error_message": str(body.get("errmsg") or "DingTalk webhook rejected"),
            }
        return {
            "success": True,
            "response": body,
            "request_payload": payload,
        }

    def _build_signed_webhook(self, webhook: str) -> str:
        secret = str(self.settings.scheduler_summary_dingtalk_secret or "").strip()
        if not secret:
            return webhook
        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
        sign = base64.b64encode(hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()).decode("utf-8")
        parsed = urlsplit(webhook)
        query = parsed.query
        encoded = urlencode({"timestamp": timestamp, "sign": sign})
        merged_query = f"{query}&{encoded}" if query else encoded
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, merged_query, parsed.fragment))

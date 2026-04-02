from typing import Any

from schemas.sync import CollectResult
from services.collectors.source_config import ModuleSourceConfig


class PlaywrightFallbackCollector:
    def __init__(self, config: ModuleSourceConfig) -> None:
        self.config = config

    def validate(self) -> None:
        return None

    def healthcheck(self) -> dict[str, Any]:
        return {
            "ok": False,
            "module_code": self.config.module_code,
            "collector": "PlaywrightFallbackCollector",
            "data_source": "playwright_fallback",
            "fallback_enabled": self.config.supports_fallback(),
        }

    async def collect(self) -> CollectResult:
        fallback_payload = self.config.extra_config.get("fallback_payload")
        if not isinstance(fallback_payload, dict):
            fallback_path = self.config.resolve_path("fallback_payload_path")
            if fallback_path and fallback_path.exists():
                import json

                with fallback_path.open("r", encoding="utf-8") as file:
                    fallback_payload = json.load(file)

        if isinstance(fallback_payload, dict):
            raw_columns = fallback_payload.get("raw_columns", fallback_payload.get("columns", []))
            raw_rows = fallback_payload.get("raw_rows", fallback_payload.get("rows", []))
            raw_meta = fallback_payload.get("raw_meta", fallback_payload.get("meta", {}))
            return CollectResult(
                module_code=self.config.module_code,
                source_url=self.config.source_url,
                source_doc_key=self.config.source_doc_key,
                source_view_key=self.config.source_view_key,
                data_source="playwright_fallback",
                sync_status="success" if raw_rows else "partial",
                raw_columns=raw_columns,
                raw_rows=raw_rows,
                raw_meta={"note": "playwright fallback fixture payload", **raw_meta},
                sync_error=None if raw_rows else "fallback payload returned no rows",
            )

        return CollectResult(
            module_code=self.config.module_code,
            source_url=self.config.source_url,
            source_doc_key=self.config.source_doc_key,
            source_view_key=self.config.source_view_key,
            data_source="playwright_fallback",
            sync_status="partial",
            raw_columns=[],
            raw_rows=[],
            raw_meta={"note": "playwright fallback stub only", "healthcheck": self.healthcheck()},
            sync_error="playwright fallback is not implemented in phase one",
        )

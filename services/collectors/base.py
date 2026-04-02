from __future__ import annotations

from typing import Any, Protocol

from schemas.sync import CollectResult
from services.collectors.diagnostics import build_attempt_diagnostic
from services.collectors.fetchers import CollectorFetcher, TransportError, build_fetcher
from services.collectors.playwright_fallback import PlaywrightFallbackCollector
from services.collectors.source_config import ModuleSourceConfig


class BaseCollector(Protocol):
    def validate(self) -> None: ...

    def healthcheck(self) -> dict[str, Any]: ...

    async def collect(self) -> CollectResult: ...


class ConfiguredCollectorBase:
    module_code: str
    module_label: str

    def __init__(
        self,
        config: ModuleSourceConfig,
        *,
        fetcher: CollectorFetcher | None = None,
        fallback_collector: PlaywrightFallbackCollector | None = None,
    ) -> None:
        self.config = config
        self.fetcher = fetcher or build_fetcher(config)
        self.fallback_collector = fallback_collector or PlaywrightFallbackCollector(config)

    def validate(self) -> None:
        if self.config.module_code != self.module_code:
            raise ValueError(f"{self.module_label} expected module_code={self.module_code}, got {self.config.module_code}")
        if not self.config.enabled:
            raise ValueError(f"{self.module_label} is disabled")
        if not self.config.source_url:
            raise ValueError(f"{self.module_label} missing source_url")
        if not self.config.source_doc_key:
            raise ValueError(f"{self.module_label} missing source_doc_key")
        if self.config.collector_type not in {"fake", "fixture", "dingtalk", "real"}:
            raise ValueError(f"{self.module_label} unsupported collector_type={self.config.collector_type}")
        if self.config.collector_type == "fake":
            has_payload = any(
                [
                    self.config.has_inline_payload("structured_payload"),
                    self.config.has_inline_payload("state_payload"),
                    self.config.has_inline_payload("fallback_payload"),
                ]
            )
            if not has_payload:
                raise ValueError(f"{self.module_label} fake collector requires at least one inline payload")
        if self.config.collector_type == "fixture":
            configured_paths = [
                self.config.resolve_path("structured_payload_path"),
                self.config.resolve_path("state_payload_path"),
                self.config.resolve_path("fallback_payload_path"),
            ]
            missing_paths = [str(path) for path in configured_paths if path is not None and not path.exists()]
            if missing_paths:
                raise ValueError(f"{self.module_label} fixture payload path missing: {', '.join(missing_paths)}")
            has_payload = any(
                [
                    self.config.has_inline_payload("structured_payload"),
                    self.config.has_inline_payload("state_payload"),
                    self.config.has_inline_payload("fallback_payload"),
                    *configured_paths,
                ]
            )
            if not has_payload:
                raise ValueError(f"{self.module_label} fixture collector requires at least one payload source")
        if self.config.collector_type in {"dingtalk", "real"}:
            if not self.config.get_extra("structured_endpoint") and not self.config.get_extra("state_endpoint"):
                raise ValueError(f"{self.module_label} real transport requires structured_endpoint or state_endpoint")

    def healthcheck(self) -> dict[str, Any]:
        return {
            "ok": True,
            "module_code": self.module_code,
            "collector": self.module_label,
            "collector_type": self.config.collector_type,
            "source_url": self.config.source_url,
            "source_doc_key": self.config.source_doc_key,
            "source_view_key": self.config.source_view_key,
            "transport_mode": getattr(self.fetcher, "transport_mode", self.config.collector_type),
            "fallback_enabled": self.config.supports_fallback(),
            "structured_configured": bool(
                self.config.has_inline_payload("structured_payload")
                or self.config.resolve_path("structured_payload_path")
                or self.config.get_extra("structured_endpoint")
            ),
            "state_configured": bool(
                self.config.has_inline_payload("state_payload")
                or self.config.resolve_path("state_payload_path")
                or self.config.get_extra("state_endpoint")
            ),
        }

    async def collect(self) -> CollectResult:
        self.validate()
        diagnostics: list[dict[str, Any]] = []
        errors: list[str] = []

        for step, fetcher, data_source in [
            ("structured", self.fetcher.fetch_structured, "structured_api"),
            ("state", self.fetcher.fetch_state, "page_state"),
        ]:
            try:
                payload = await fetcher(self.config)
                if payload is None:
                    diagnostics.append(
                        build_attempt_diagnostic(
                            step=step,
                            attempted=False,
                            success=False,
                            error="not configured",
                            error_type="not_configured",
                            data_source=data_source,
                            transport_mode=getattr(self.fetcher, "transport_mode", self.config.collector_type),
                        )
                    )
                    continue
                normalized_payload = self._normalize_payload(payload)
                row_count = len(normalized_payload["raw_rows"])
                resolved_data_source = normalized_payload.get("data_source") or data_source
                diagnostics.append(
                    build_attempt_diagnostic(
                        step=step,
                        attempted=True,
                        success=row_count > 0,
                        row_count=row_count,
                        data_source=resolved_data_source,
                        transport_mode=getattr(self.fetcher, "transport_mode", self.config.collector_type),
                        meta=normalized_payload.get("raw_meta", {}),
                    )
                )
                if row_count > 0:
                    return self._build_collect_result(
                        data_source=resolved_data_source,
                        sync_status="success",
                        raw_columns=normalized_payload["raw_columns"],
                        raw_rows=normalized_payload["raw_rows"],
                        raw_meta=self._build_raw_meta(diagnostics, resolved_data_source, normalized_payload.get("raw_meta")),
                    )
            except Exception as exc:
                error_type, http_status = self._classify_exception(exc)
                errors.append(f"{step}:{error_type}: {exc}")
                diagnostics.append(
                    build_attempt_diagnostic(
                        step=step,
                        attempted=True,
                        success=False,
                        error=str(exc),
                        error_type=error_type,
                        http_status=http_status,
                        data_source=data_source,
                        transport_mode=getattr(self.fetcher, "transport_mode", self.config.collector_type),
                    )
                )

        fallback_result = await self._collect_fallback(diagnostics)
        if fallback_result.raw_rows:
            return fallback_result

        sync_status = "partial" if any(item["attempted"] for item in diagnostics) else "failed"
        sync_error = " | ".join(errors) if errors else "no rows collected from any source"
        return self._build_collect_result(
            data_source=fallback_result.data_source or "playwright_fallback",
            sync_status=sync_status,
            raw_columns=[],
            raw_rows=[],
            raw_meta=self._build_raw_meta(diagnostics, None, fallback_result.raw_meta),
            sync_error=sync_error,
        )

    async def _collect_fallback(self, diagnostics: list[dict[str, Any]]) -> CollectResult:
        if not self.config.supports_fallback():
            diagnostics.append(
                build_attempt_diagnostic(
                    step="playwright_fallback",
                    attempted=False,
                    success=False,
                    error="fallback disabled",
                    error_type="fallback_disabled",
                    data_source="playwright_fallback",
                    transport_mode="playwright_fallback",
                )
            )
            return self._build_collect_result(
                data_source="playwright_fallback",
                sync_status="failed",
                raw_columns=[],
                raw_rows=[],
                raw_meta=self._build_raw_meta(diagnostics, None, {"fallback_enabled": False}),
                sync_error="playwright fallback disabled",
            )

        fallback_result = await self.fallback_collector.collect()
        diagnostics.append(
            build_attempt_diagnostic(
                step="playwright_fallback",
                attempted=True,
                success=bool(fallback_result.raw_rows),
                row_count=len(fallback_result.raw_rows),
                error=fallback_result.sync_error,
                error_type="fallback_hit" if fallback_result.raw_rows else "fallback_empty",
                data_source="playwright_fallback",
                transport_mode="playwright_fallback",
                meta=fallback_result.raw_meta,
            )
        )
        fallback_result.raw_meta = self._build_raw_meta(diagnostics, "playwright_fallback", fallback_result.raw_meta)
        return fallback_result

    def _build_collect_result(
        self,
        *,
        data_source: str,
        sync_status: str,
        raw_columns: list[Any],
        raw_rows: list[dict[str, Any]],
        raw_meta: dict[str, Any],
        sync_error: str | None = None,
    ) -> CollectResult:
        return CollectResult(
            module_code=self.config.module_code,
            source_url=self.config.source_url,
            source_doc_key=self.config.source_doc_key,
            source_view_key=self.config.source_view_key,
            data_source=data_source,
            sync_status=sync_status,
            sync_error=sync_error,
            raw_columns=raw_columns,
            raw_rows=raw_rows,
            raw_meta=raw_meta,
        )

    def _build_raw_meta(
        self,
        diagnostics: list[dict[str, Any]],
        selected_source: str | None,
        raw_meta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged_raw_meta = dict(raw_meta or {})
        merged_raw_meta.update(
            {
                "collector": self.module_label,
                "collector_type": self.config.collector_type,
                "transport_mode": getattr(self.fetcher, "transport_mode", self.config.collector_type),
                "collector_diagnostics": diagnostics,
                "attempt_chain": [item["step"] for item in diagnostics],
                "selected_source": selected_source,
                "source_config": {
                    "module_code": self.config.module_code,
                    "source_doc_key": self.config.source_doc_key,
                    "source_view_key": self.config.source_view_key,
                },
            }
        )
        return merged_raw_meta

    @staticmethod
    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        raw_columns = payload.get("raw_columns", payload.get("columns", []))
        raw_rows = payload.get("raw_rows", payload.get("rows", []))
        raw_meta = payload.get("raw_meta", payload.get("meta", {}))
        if not isinstance(raw_columns, list):
            raise ValueError("payload columns must be a list")
        if not isinstance(raw_rows, list):
            raise ValueError("payload rows must be a list")
        if not isinstance(raw_meta, dict):
            raise ValueError("payload meta must be an object")
        return {
            "raw_columns": raw_columns,
            "raw_rows": raw_rows,
            "raw_meta": raw_meta,
            "data_source": payload.get("data_source") if isinstance(payload.get("data_source"), str) and payload.get("data_source") else None,
        }

    @staticmethod
    def _classify_exception(exc: Exception) -> tuple[str, int | None]:
        if isinstance(exc, TransportError):
            return exc.error_type, exc.http_status
        return "unexpected_error", None

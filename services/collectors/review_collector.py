from __future__ import annotations

import logging
from typing import Any

from core.config import get_settings
from services.collectors.source_config import ModuleSourceConfig
from services.pts_graphql_client import PtsGraphQLClient
from schemas.sync import CollectResult

logger = logging.getLogger(__name__)


class ReviewCollector:
    """Collector for review module.

    Data source is PTS (not DingTalk), so this implements BaseCollector
    Protocol directly rather than inheriting ConfiguredCollectorBase.

    Accepts a ModuleSourceConfig parameter (like other collectors) to
    stay compatible with SyncService's registry pattern, but internally
    builds a PtsGraphQLClient from Settings for the actual API calls.
    """

    module_code = "review"
    module_label = "ReviewCollector"

    def __init__(self, source_config: ModuleSourceConfig, *, settings=None) -> None:
        self.config = source_config
        self.settings = settings or get_settings()

    def validate(self) -> None:
        token = self.settings.pts_review_api_token or self.settings.pts_api_token
        if not token and not self.settings.pts_cookie_header:
            raise ValueError(
                f"{self.module_label} requires PTS_REVIEW_API_TOKEN or PTS_API_TOKEN or PTS_COOKIE_HEADER to be configured"
            )
        if not self.settings.pts_api_base_url:
            raise ValueError(f"{self.module_label} requires PTS_API_BASE_URL to be configured")

    def healthcheck(self) -> dict[str, Any]:
        review_token = self.settings.pts_review_api_token or self.settings.pts_api_token
        has_api_token = bool(review_token)
        has_cookie = bool(self.settings.pts_cookie_header)
        configured = has_api_token or has_cookie
        return {
            "ok": configured,
            "module_code": self.module_code,
            "collector": self.module_label,
            "collector_type": "pts_api",
            "pts_base_url": self.settings.pts_api_base_url,
            "api_token_configured": has_api_token,
            "cookie_configured": has_cookie,
            "auth_source": "api_token" if has_api_token else ("cookie" if has_cookie else "unconfigured"),
        }

    async def collect(self) -> CollectResult:
        self.validate()

        from services.extractors.review_project_list import extract_pending_projects

        client = self._build_pts_client()
        after_sale_ids = self._parse_after_sale_filter_ids()

        try:
            projects = await extract_pending_projects(client, after_sale_ids=after_sale_ids)
        except Exception as exc:
            logger.exception("%s collect failed", self.module_label)
            return CollectResult(
                module_code=self.module_code,
                source_url=self.config.source_url,
                source_doc_key=self.config.source_doc_key,
                data_source="pts_api",
                sync_status="failed",
                sync_error=str(exc),
                raw_columns=[],
                raw_rows=[],
                raw_meta={"collector": self.module_label, "collector_type": "pts_api", "error": str(exc)},
            )

        # Convert PendingProject objects to dicts for storage
        raw_rows = [p.model_dump() for p in projects]
        raw_columns = list(raw_rows[0].keys()) if raw_rows else []
        raw_meta = {
            "collector": self.module_label,
            "collector_type": "pts_api",
            "project_count": len(projects),
        }

        return CollectResult(
            module_code=self.module_code,
            source_url=self.config.source_url,
            source_doc_key=self.config.source_doc_key,
            source_view_key=self.config.source_view_key,
            data_source="pts_api",
            sync_status="success" if projects else "empty",
            raw_columns=raw_columns,
            raw_rows=raw_rows,
            raw_meta=raw_meta,
        )

    def _build_pts_client(self) -> PtsGraphQLClient:
        token = self.settings.pts_review_api_token or self.settings.pts_api_token
        return PtsGraphQLClient(
            api_base_url=self.settings.pts_api_base_url,
            api_token=token,
        )

    def _parse_after_sale_filter_ids(self) -> list[str] | None:
        """Parse PTS_REVIEW_AFTER_SALE_FILTER_IDS from settings.

        Returns ``None`` when the setting is empty (meaning no filter, return
        all to_after_sale_review projects), or a list of user ID strings.
        """
        raw = self.settings.pts_review_after_sale_filter_ids
        if not raw or not raw.strip():
            return None
        return [uid.strip() for uid in raw.split(",") if uid.strip()]
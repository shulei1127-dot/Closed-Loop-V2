from sqlalchemy import select

from models.module_config import ModuleConfig
from repositories.base import BaseRepository
from services.collectors.source_config import ModuleSourceConfig


class ModuleConfigRepository(BaseRepository):
    def get_by_code(self, module_code: str) -> ModuleConfig | None:
        statement = select(ModuleConfig).where(ModuleConfig.module_code == module_code)
        return self.db.scalar(statement)

    def list_all(self) -> list[ModuleConfig]:
        statement = select(ModuleConfig).order_by(ModuleConfig.module_code.asc())
        return list(self.db.scalars(statement).all())

    def upsert_defaults(self, defaults: list[dict]) -> list[ModuleConfig]:
        existing = {item.module_code: item for item in self.list_all()}
        for item in defaults:
            existing_item = existing.get(item["module_code"])
            if existing_item is not None:
                self._patch_missing_fields(existing_item, item)
                continue
            self.db.add(ModuleConfig(**item))
        self.db.flush()
        return self.list_all()

    def get_source_config(self, module_code: str) -> ModuleSourceConfig | None:
        module_config = self.get_by_code(module_code)
        if module_config is None:
            return None
        return ModuleSourceConfig.from_model(module_config)

    @staticmethod
    def _patch_missing_fields(existing_item: ModuleConfig, defaults: dict) -> None:
        if ModuleConfigRepository._is_stale_placeholder_config(existing_item, defaults):
            existing_item.source_url = defaults["source_url"]
            existing_item.source_doc_key = defaults["source_doc_key"]
            existing_item.source_view_key = defaults["source_view_key"]
            existing_item.collector_type = defaults["collector_type"]
            default_extra = defaults.get("extra_config", {})
            # When collector_type changed, use defaults as base and only preserve
            # non-transport-specific overrides (execute_cron, execute_dry_run, etc.)
            transport_keys = {
                "structured_endpoint", "state_endpoint", "structured_method", "state_method",
                "structured_query_params", "state_query_params", "structured_headers", "state_headers",
                "structured_json_body", "state_json_body", "structured_response_path",
                "state_response_path", "structured_columns_path", "state_columns_path",
                "structured_rows_path", "state_rows_path", "structured_meta_path",
                "state_meta_path", "static_headers", "static_cookies",
                "headers_env", "cookies_env", "token_env", "token_header", "token_prefix",
                "parallelv2_enabled", "parallelv2_direct_access_token_enabled",
                "parallelv2_dynamic_version_enabled", "parallelv2_access_token_endpoint",
                "parallelv2_document_data_endpoint", "parallelv2_document_data_method",
                "parallelv2_document_data_json_body", "parallelv2_endpoint",
                "parallelv2_query_params", "parallelv2_sheet_id", "parallelv2_view_id",
                "parallelv2_doc_key", "parallelv2_dentry_key",
                "parallelv2_structure_payload_path", "parallelv2_token_header",
                "parallelv2_version_path", "parallelv2_access_token_path",
                "record_count_endpoint", "record_count_response_path",
                "playwright_fallback_enabled",
                "dws_cli_base_id", "dws_cli_table_id",
                "dws_cli_row_filter",
            }
            sanitized_existing = {
                k: v for k, v in (existing_item.extra_config or {}).items()
                if k not in transport_keys
            }
            existing_item.extra_config = {**default_extra, **sanitized_existing}
            return

        if not existing_item.module_name:
            existing_item.module_name = defaults["module_name"]
        if not existing_item.source_url:
            existing_item.source_url = defaults["source_url"]
        if not getattr(existing_item, "source_doc_key", None):
            existing_item.source_doc_key = defaults["source_doc_key"]
        if getattr(existing_item, "source_view_key", None) in (None, "") and defaults.get("source_view_key"):
            existing_item.source_view_key = defaults["source_view_key"]
        if not getattr(existing_item, "collector_type", None):
            existing_item.collector_type = defaults["collector_type"]
        existing_item.extra_config = ModuleConfigRepository._merge_extra_config(
            defaults.get("extra_config", {}),
            existing_item.extra_config or {},
        )

    @staticmethod
    def _is_stale_placeholder_config(existing_item: ModuleConfig, defaults: dict) -> bool:
        if existing_item.collector_type == "fixture":
            module_code = existing_item.module_code
            placeholder_doc_key = f"doc_{module_code}_real"
            return (
                existing_item.source_doc_key == placeholder_doc_key
                or str(existing_item.source_url or "").startswith("https://dingtalk.example.com/")
            )
        # Also detect stale config when collector_type has changed in defaults
        if existing_item.collector_type != defaults.get("collector_type"):
            return True
        return False

    @staticmethod
    def _merge_extra_config(default_extra: dict, existing_extra: dict, *, drop_fixture_keys: bool = False) -> dict:
        sanitized_existing = dict(existing_extra or {})
        if drop_fixture_keys:
            for key in (
                "structured_payload",
                "structured_payload_path",
                "state_payload",
                "state_payload_path",
                "fallback_payload",
                "fallback_payload_path",
            ):
                sanitized_existing.pop(key, None)
        merged_extra_config = dict(default_extra or {})
        merged_extra_config.update(sanitized_existing)
        return merged_extra_config

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
        merged_extra_config = dict(defaults.get("extra_config", {}))
        merged_extra_config.update(existing_item.extra_config or {})
        existing_item.extra_config = merged_extra_config

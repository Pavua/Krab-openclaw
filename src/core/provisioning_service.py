# -*- coding: utf-8 -*-
"""
Provisioning Service (Phase E).

Зачем нужен модуль:
1. Дать владельцу поток `draft -> preview -> apply` для агентов и skills.
2. Хранить декларативные каталоги в YAML без ручной правки.
3. Поддержать «мягкую интеграцию» с OpenClaw: сначала локальный каталог,
   затем опциональная синхронизация с внешним рантаймом.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

EntityType = Literal["agent", "skill"]


class ProvisioningService:
    """Сервис управления каталогами агентов и skills."""

    def __init__(
        self,
        agents_catalog_path: str = "config/agents_catalog.yaml",
        skills_catalog_path: str = "config/skills_catalog.yaml",
        drafts_dir: str = "artifacts/provisioning_drafts",
    ):
        self.agents_catalog_path = Path(agents_catalog_path)
        self.skills_catalog_path = Path(skills_catalog_path)
        self.drafts_dir = Path(drafts_dir)

        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_catalog_files()

    def _ensure_catalog_files(self) -> None:
        """Создает YAML-каталоги с дефолтной структурой, если их еще нет."""
        if not self.agents_catalog_path.exists():
            self._write_yaml(
                self.agents_catalog_path,
                {
                    "version": 1,
                    "updated_at": self._now_iso(),
                    "agents": [],
                    "role_templates": [
                        {
                            "role": "coding",
                            "description": "Разработка и рефакторинг кода, тесты, ревью.",
                        },
                        {
                            "role": "crypto-trading-assistant",
                            "description": "Аналитика крипторынка, риск-ограничения, алерты.",
                        },
                        {
                            "role": "telegram-moderation",
                            "description": "Модерация групп, правила, авто-действия.",
                        },
                        {
                            "role": "communication",
                            "description": "Коммуникации, summary, переводы, FAQ-ответы.",
                        },
                    ],
                },
            )

        if not self.skills_catalog_path.exists():
            self._write_yaml(
                self.skills_catalog_path,
                {
                    "version": 1,
                    "updated_at": self._now_iso(),
                    "skills": [],
                    "role_templates": [
                        {
                            "role": "coding",
                            "description": "Скрипты сборки, тестовые пайплайны, quality checks.",
                        },
                        {
                            "role": "crypto-trading-assistant",
                            "description": "Сигналы, риск-профили, авто-отчеты по рынку.",
                        },
                        {
                            "role": "telegram-moderation",
                            "description": "Антиспам, правила групп, обработка нарушений.",
                        },
                        {
                            "role": "communication",
                            "description": "Тональность, summary, классификация запросов.",
                        },
                    ],
                },
            )

    def list_templates(self, entity_type: EntityType) -> list[dict[str, str]]:
        """Возвращает доступные шаблоны ролей для entity."""
        catalog = self._read_catalog(entity_type)
        templates = catalog.get("role_templates", [])
        return templates if isinstance(templates, list) else []

    def create_draft(
        self,
        entity_type: EntityType,
        name: str,
        role: str,
        description: str,
        requested_by: str,
        settings: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Создает draft спецификации и сохраняет его на диск."""
        normalized_type = self._normalize_entity(entity_type)
        normalized_name = (name or "").strip()
        normalized_role = (role or "").strip().lower()

        if not normalized_name:
            raise ValueError("name обязателен")
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]+$", normalized_name):
            raise ValueError("name должен содержать только буквы, цифры, '_' и '-'")
        if not normalized_role:
            raise ValueError("role обязателен")

        now_iso = self._now_iso()
        draft_payload = {
            "entity_type": normalized_type,
            "name": normalized_name,
            "role": normalized_role,
            "description": (description or "").strip() or "Без описания",
            "settings": settings or {},
            "requested_by": (requested_by or "unknown").strip(),
            "created_at": now_iso,
            "status": "draft",
        }

        digest = hashlib.sha1(
            f"{normalized_type}:{normalized_name}:{now_iso}".encode("utf-8")
        ).hexdigest()[:10]
        draft_id = f"{normalized_type[:1]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{digest}"
        draft_payload["draft_id"] = draft_id

        self._write_yaml(self._draft_path(draft_id), draft_payload)
        return draft_payload

    def list_drafts(self, limit: int = 20, status: Optional[str] = None) -> list[dict[str, Any]]:
        """Возвращает список драфтов (последние сверху)."""
        drafts: list[dict[str, Any]] = []
        files = sorted(self.drafts_dir.glob("*.yaml"), reverse=True)
        for file_path in files:
            data = self._read_yaml(file_path)
            if not data:
                continue
            if status and data.get("status") != status:
                continue
            drafts.append(data)
            if len(drafts) >= max(1, limit):
                break
        return drafts

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        """Загружает draft по идентификатору."""
        data = self._read_yaml(self._draft_path(draft_id))
        if not data:
            raise FileNotFoundError(f"Draft {draft_id} не найден")
        return data

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        """
        Метод глубокой валидации draft'а перед применением (R5).
        Возвращает: {"ok": bool, "errors": [], "warnings": [], "next_step": str}
        """
        try:
            draft = self.get_draft(draft_id)
        except Exception as e:
            return {
                "ok": False,
                "errors": [f"Системная ошибка: {e}"],
                "warnings": [],
                "next_step": "Проверьте ID драфта."
            }

        errors = []
        warnings = []
        
        # 1. Валидность entity_type
        entity_type = draft.get("entity_type")
        if entity_type not in {"agent", "skill"}:
            errors.append(f"Недопустимый entity_type: '{entity_type}'")
        
        # 2. Корректность name и role
        name = draft.get("name")
        if not name or len(name) < 2:
            errors.append("Имя (name) слишком короткое или пустое")
        
        role = draft.get("role")
        if not role:
            errors.append("Роль (role) не указана")
        
        # 3. Проверка settings
        settings = draft.get("settings")
        if settings is not None and not isinstance(settings, dict):
            errors.append("Поле 'settings' должно быть объектом (dict)")

        # 4. Проверка присутствия роли в шаблонах
        if not errors:
            templates = self.list_templates(entity_type)
            known_roles = {t.get("role") for t in templates if t.get("role")}
            if role not in known_roles:
                warnings.append(f"Роль '{role}' отсутствует в каталоге шаблонов (role_templates)")

        # 5. Проверка конфликта с существующей записью
        if not errors:
            catalog = self._read_catalog(entity_type)
            key = "agents" if entity_type == "agent" else "skills"
            items = catalog.get(key, [])
            existing = next((item for item in items if item.get("name") == name), None)
            if existing:
                warnings.append(f"Запись с именем '{name}' уже есть в каталоге. Apply обновит её.")

        is_ok = len(errors) == 0
        return {
            "ok": is_ok,
            "errors": errors,
            "warnings": warnings,
            "next_step": "Можно выполнять apply" if is_ok else "Исправьте ошибки в draft"
        }

    def preview_diff(self, draft_id: str) -> dict[str, Any]:
        """Формирует diff между текущей записью каталога и draft-версией."""
        draft = self.get_draft(draft_id)
        entity_type = self._normalize_entity(draft.get("entity_type", "agent"))

        catalog = self._read_catalog(entity_type)
        key = "agents" if entity_type == "agent" else "skills"
        current_items = catalog.get(key, [])
        if not isinstance(current_items, list):
            current_items = []

        existing = next((item for item in current_items if item.get("name") == draft.get("name")), None)
        candidate = self._draft_to_catalog_entry(draft)

        before_text = yaml.safe_dump(existing or {}, allow_unicode=True, sort_keys=False)
        after_text = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False)
        diff = "\n".join(
            difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile="current",
                tofile=f"draft:{draft_id}",
                lineterm="",
            )
        )

        return {
            "draft": draft,
            "exists": existing is not None,
            "diff": diff or "(изменений нет)",
            "candidate": candidate,
        }

    def apply_draft(self, draft_id: str, confirmed: bool) -> dict[str, Any]:
        """Применяет draft в соответствующий каталог (по явному confirm)."""
        if not confirmed:
            raise PermissionError("Для apply требуется явное подтверждение")

        draft = self.get_draft(draft_id)
        if draft.get("status") == "applied":
            return {
                "ok": True,
                "status": "already_applied",
                "draft_id": draft_id,
                "entity_type": draft.get("entity_type"),
                "name": draft.get("name"),
            }

        entity_type = self._normalize_entity(draft.get("entity_type", "agent"))
        catalog = self._read_catalog(entity_type)
        key = "agents" if entity_type == "agent" else "skills"
        items = catalog.get(key, [])
        if not isinstance(items, list):
            items = []

        entry = self._draft_to_catalog_entry(draft)

        replaced = False
        for idx, item in enumerate(items):
            if item.get("name") == entry.get("name"):
                items[idx] = entry
                replaced = True
                break
        if not replaced:
            items.append(entry)

        catalog[key] = items
        catalog["updated_at"] = self._now_iso()

        catalog_path = self._catalog_path(entity_type)
        self._write_yaml(catalog_path, catalog)

        draft["status"] = "applied"
        draft["applied_at"] = self._now_iso()
        self._write_yaml(self._draft_path(draft_id), draft)

        return {
            "ok": True,
            "status": "updated" if replaced else "created",
            "draft_id": draft_id,
            "entity_type": entity_type,
            "name": entry.get("name"),
            "catalog_path": str(catalog_path),
        }

    def _draft_to_catalog_entry(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Преобразует draft в нормализованную запись каталога."""
        return {
            "name": draft.get("name", "unnamed"),
            "role": draft.get("role", "general"),
            "description": draft.get("description", ""),
            "settings": draft.get("settings", {}),
            "source": "provisioning",
            "updated_at": self._now_iso(),
        }

    def _read_catalog(self, entity_type: EntityType) -> dict[str, Any]:
        """Читает соответствующий каталог сущности."""
        return self._read_yaml(self._catalog_path(entity_type)) or {}

    def _catalog_path(self, entity_type: EntityType) -> Path:
        return self.agents_catalog_path if entity_type == "agent" else self.skills_catalog_path

    def _draft_path(self, draft_id: str) -> Path:
        safe_name = f"{draft_id}.yaml"
        return self.drafts_dir / safe_name

    def _normalize_entity(self, entity_type: str) -> EntityType:
        normalized = (entity_type or "").strip().lower()
        if normalized in {"agent", "agents"}:
            return "agent"
        if normalized in {"skill", "skills"}:
            return "skill"
        raise ValueError("entity_type должен быть agent или skill")

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fp:
                payload = yaml.safe_load(fp) or {}
                return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(payload, fp, allow_unicode=True, sort_keys=False)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

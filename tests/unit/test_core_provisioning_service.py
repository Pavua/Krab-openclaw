# -*- coding: utf-8 -*-
"""
Юнит-тесты ``src/core/provisioning_service.py`` — ProvisioningService.

Что покрыто:

1. **Инициализация** — auto-создание каталогов и папки drafts.
2. **list_templates** — возвращает шаблоны для agent и skill.
3. **create_draft** — корректный payload, генерация draft_id, валидация name/role.
4. **list_drafts** — постраничность, фильтрация по статусу.
5. **get_draft** — успех и FileNotFoundError при отсутствии.
6. **validate_draft** — ok=True, ok=False при невалидном entity_type/name/settings.
7. **validate_draft** — предупреждение при дублирующемся имени.
8. **validate_draft** — предупреждение при нестандартной роли.
9. **preview_diff** — diff для нового и существующего агента.
10. **apply_draft** — создание записи в каталоге, смена status → applied.
11. **apply_draft** — замена существующей записи (update).
12. **apply_draft** — idempotency: повторный apply возвращает already_applied.
13. **apply_draft** — PermissionError без confirmed=True.
14. **_normalize_entity** — все допустимые алиасы и ValueError на мусоре.
15. **_read_yaml** — несуществующий файл → {}, битый YAML → {}.
16. **round-trip** — create → apply → list_drafts status=applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core.provisioning_service import ProvisioningService

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_service(tmp_path: Path) -> ProvisioningService:
    """Создаёт изолированный ProvisioningService во временной директории."""
    return ProvisioningService(
        agents_catalog_path=str(tmp_path / "agents_catalog.yaml"),
        skills_catalog_path=str(tmp_path / "skills_catalog.yaml"),
        drafts_dir=str(tmp_path / "drafts"),
    )


def _create_agent_draft(
    svc: ProvisioningService,
    name: str = "my-agent",
    role: str = "coding",
) -> dict[str, Any]:
    return svc.create_draft(
        entity_type="agent",
        name=name,
        role=role,
        description="Тестовый агент",
        requested_by="tester",
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. Инициализация
# ═════════════════════════════════════════════════════════════════════════════


class TestInitialization:
    def test_catalog_files_created_on_init(self, tmp_path: Path) -> None:
        """При создании сервиса YAML-каталоги появляются автоматически."""
        svc = _make_service(tmp_path)
        assert svc.agents_catalog_path.exists()
        assert svc.skills_catalog_path.exists()

    def test_drafts_dir_created_on_init(self, tmp_path: Path) -> None:
        """Папка для драфтов создаётся при инициализации."""
        svc = _make_service(tmp_path)
        assert svc.drafts_dir.is_dir()

    def test_agents_catalog_default_structure(self, tmp_path: Path) -> None:
        """Дефолтный каталог агентов содержит version, agents, role_templates."""
        svc = _make_service(tmp_path)
        with svc.agents_catalog_path.open(encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        assert data["version"] == 1
        assert isinstance(data["agents"], list)
        assert len(data["role_templates"]) >= 4

    def test_skills_catalog_default_structure(self, tmp_path: Path) -> None:
        """Дефолтный каталог skills содержит version, skills, role_templates."""
        svc = _make_service(tmp_path)
        with svc.skills_catalog_path.open(encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        assert data["version"] == 1
        assert isinstance(data["skills"], list)

    def test_existing_catalogs_not_overwritten(self, tmp_path: Path) -> None:
        """Повторная инициализация не перезаписывает существующие каталоги."""
        svc = _make_service(tmp_path)
        # Вручную добавляем агента
        catalog_path = svc.agents_catalog_path
        with catalog_path.open(encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        data["agents"].append({"name": "sentinel", "role": "coding"})
        with catalog_path.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(data, fp, allow_unicode=True)

        # Создаём новый сервис с теми же путями
        svc2 = ProvisioningService(
            agents_catalog_path=str(catalog_path),
            skills_catalog_path=str(svc.skills_catalog_path),
            drafts_dir=str(svc.drafts_dir),
        )
        with svc2.agents_catalog_path.open(encoding="utf-8") as fp:
            data2 = yaml.safe_load(fp)
        names = [a["name"] for a in data2["agents"]]
        assert "sentinel" in names


# ═════════════════════════════════════════════════════════════════════════════
# 2. list_templates
# ═════════════════════════════════════════════════════════════════════════════


class TestListTemplates:
    def test_list_templates_agent_returns_list(self, tmp_path: Path) -> None:
        """list_templates('agent') возвращает список шаблонов."""
        svc = _make_service(tmp_path)
        templates = svc.list_templates("agent")
        assert isinstance(templates, list)
        assert len(templates) >= 4

    def test_list_templates_skill_returns_list(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        templates = svc.list_templates("skill")
        assert isinstance(templates, list)
        assert len(templates) >= 4

    def test_list_templates_contains_coding_role(self, tmp_path: Path) -> None:
        """Шаблон с role='coding' присутствует в каталоге агентов."""
        svc = _make_service(tmp_path)
        roles = {t["role"] for t in svc.list_templates("agent")}
        assert "coding" in roles

    def test_list_templates_returns_copy_of_list(self, tmp_path: Path) -> None:
        """Возвращаемый список — независимая копия каталожных данных."""
        svc = _make_service(tmp_path)
        t1 = svc.list_templates("agent")
        t1.clear()
        t2 = svc.list_templates("agent")
        assert len(t2) >= 4


# ═════════════════════════════════════════════════════════════════════════════
# 3. create_draft
# ═════════════════════════════════════════════════════════════════════════════


class TestCreateDraft:
    def test_create_draft_returns_payload(self, tmp_path: Path) -> None:
        """create_draft возвращает корректный payload."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        assert draft["entity_type"] == "agent"
        assert draft["name"] == "my-agent"
        assert draft["role"] == "coding"
        assert draft["status"] == "draft"
        assert "draft_id" in draft
        assert draft["requested_by"] == "tester"

    def test_create_draft_saved_to_disk(self, tmp_path: Path) -> None:
        """После создания файл драфта существует на диске."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        draft_file = svc.drafts_dir / f"{draft['draft_id']}.yaml"
        assert draft_file.exists()

    def test_create_draft_skill_type(self, tmp_path: Path) -> None:
        """Можно создавать драфты типа skill."""
        svc = _make_service(tmp_path)
        draft = svc.create_draft(
            entity_type="skill",
            name="my-skill",
            role="crypto-trading-assistant",
            description="Тестовый скилл",
            requested_by="owner",
        )
        assert draft["entity_type"] == "skill"
        assert draft["draft_id"].startswith("s_")

    def test_create_draft_agent_id_starts_with_a(self, tmp_path: Path) -> None:
        """draft_id агента начинается с 'a_'."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        assert draft["draft_id"].startswith("a_")

    def test_create_draft_empty_name_raises(self, tmp_path: Path) -> None:
        """Пустое имя → ValueError."""
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="name обязателен"):
            svc.create_draft("agent", "", "coding", "desc", "tester")

    def test_create_draft_invalid_name_characters_raises(self, tmp_path: Path) -> None:
        """Имя с пробелом или спецсимволами → ValueError."""
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="name должен содержать"):
            svc.create_draft("agent", "bad name!", "coding", "desc", "tester")

    def test_create_draft_empty_role_raises(self, tmp_path: Path) -> None:
        """Пустая роль → ValueError."""
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="role обязателен"):
            svc.create_draft("agent", "valid-name", "", "desc", "tester")

    def test_create_draft_with_settings(self, tmp_path: Path) -> None:
        """Переданные settings сохраняются в payload."""
        svc = _make_service(tmp_path)
        settings = {"max_tokens": 4096, "temperature": 0.7}
        draft = svc.create_draft(
            "agent", "cfg-agent", "coding", "desc", "tester", settings=settings
        )
        assert draft["settings"] == settings

    def test_create_draft_default_description(self, tmp_path: Path) -> None:
        """Пустое описание → дефолтное 'Без описания'."""
        svc = _make_service(tmp_path)
        draft = svc.create_draft("agent", "no-desc", "coding", "", "tester")
        assert draft["description"] == "Без описания"


# ═════════════════════════════════════════════════════════════════════════════
# 4. list_drafts
# ═════════════════════════════════════════════════════════════════════════════


class TestListDrafts:
    def test_list_drafts_empty_when_no_drafts(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        assert svc.list_drafts() == []

    def test_list_drafts_returns_created_draft(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        listed = svc.list_drafts()
        assert len(listed) == 1
        assert listed[0]["draft_id"] == draft["draft_id"]

    def test_list_drafts_limit_respected(self, tmp_path: Path) -> None:
        """Параметр limit ограничивает количество возвращаемых драфтов."""
        svc = _make_service(tmp_path)
        for i in range(5):
            svc.create_draft("agent", f"agent-{i}", "coding", "desc", "tester")
        result = svc.list_drafts(limit=3)
        assert len(result) == 3

    def test_list_drafts_filter_by_status(self, tmp_path: Path) -> None:
        """Фильтрация по status=draft возвращает только неприменённые."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc, name="agent-draft")
        svc.apply_draft(draft["draft_id"], confirmed=True)
        result_draft = svc.list_drafts(status="draft")
        result_applied = svc.list_drafts(status="applied")
        assert len(result_draft) == 0
        assert len(result_applied) == 1


# ═════════════════════════════════════════════════════════════════════════════
# 5. get_draft
# ═════════════════════════════════════════════════════════════════════════════


class TestGetDraft:
    def test_get_draft_returns_saved_data(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        created = _create_agent_draft(svc)
        fetched = svc.get_draft(created["draft_id"])
        assert fetched["name"] == "my-agent"
        assert fetched["role"] == "coding"

    def test_get_draft_not_found_raises(self, tmp_path: Path) -> None:
        """Несуществующий draft_id → FileNotFoundError."""
        svc = _make_service(tmp_path)
        with pytest.raises(FileNotFoundError, match="не найден"):
            svc.get_draft("nonexistent_draft_id")


# ═════════════════════════════════════════════════════════════════════════════
# 6. validate_draft
# ═════════════════════════════════════════════════════════════════════════════


class TestValidateDraft:
    def test_validate_valid_draft_ok(self, tmp_path: Path) -> None:
        """Корректный драфт проходит валидацию."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        result = svc.validate_draft(draft["draft_id"])
        assert result["ok"] is True
        assert result["errors"] == []

    def test_validate_invalid_draft_id_returns_error(self, tmp_path: Path) -> None:
        """Несуществующий draft_id → ok=False с сообщением об ошибке."""
        svc = _make_service(tmp_path)
        result = svc.validate_draft("totally_fake_id")
        assert result["ok"] is False
        assert len(result["errors"]) > 0

    def test_validate_invalid_entity_type(self, tmp_path: Path) -> None:
        """Если entity_type не agent/skill — ошибка валидации."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        # Перезаписываем файл с плохим entity_type
        draft_file = svc.drafts_dir / f"{draft['draft_id']}.yaml"
        draft["entity_type"] = "robot"
        with draft_file.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(draft, fp, allow_unicode=True)
        result = svc.validate_draft(draft["draft_id"])
        assert result["ok"] is False
        assert any("entity_type" in e for e in result["errors"])

    def test_validate_short_name_gives_error(self, tmp_path: Path) -> None:
        """name длиной < 2 символа → ошибка."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        draft_file = svc.drafts_dir / f"{draft['draft_id']}.yaml"
        draft["name"] = "x"
        with draft_file.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(draft, fp, allow_unicode=True)
        result = svc.validate_draft(draft["draft_id"])
        assert result["ok"] is False
        assert any("name" in e.lower() or "имя" in e.lower() for e in result["errors"])

    def test_validate_invalid_settings_type_gives_error(self, tmp_path: Path) -> None:
        """settings не-dict → ошибка."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        draft_file = svc.drafts_dir / f"{draft['draft_id']}.yaml"
        draft["settings"] = "bad-string"
        with draft_file.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(draft, fp, allow_unicode=True)
        result = svc.validate_draft(draft["draft_id"])
        assert result["ok"] is False
        assert any("settings" in e for e in result["errors"])

    def test_validate_unknown_role_gives_warning(self, tmp_path: Path) -> None:
        """Нестандартная роль не блокирует применение, но добавляет warning."""
        svc = _make_service(tmp_path)
        draft = svc.create_draft("agent", "custom-role-agent", "unknown-role-xyz", "desc", "tester")
        result = svc.validate_draft(draft["draft_id"])
        assert result["ok"] is True
        assert any(
            "шаблон" in w.lower() or "role_template" in w.lower() or "отсутствует" in w.lower()
            for w in result["warnings"]
        )

    def test_validate_duplicate_name_gives_warning(self, tmp_path: Path) -> None:
        """Имя уже есть в каталоге → warning (не ошибка)."""
        svc = _make_service(tmp_path)
        draft1 = _create_agent_draft(svc, name="dup-agent")
        svc.apply_draft(draft1["draft_id"], confirmed=True)

        draft2 = _create_agent_draft(svc, name="dup-agent")
        result = svc.validate_draft(draft2["draft_id"])
        assert result["ok"] is True
        assert any("dup-agent" in w for w in result["warnings"])

    def test_validate_next_step_contains_apply_when_ok(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        result = svc.validate_draft(draft["draft_id"])
        assert "apply" in result["next_step"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# 7. preview_diff
# ═════════════════════════════════════════════════════════════════════════════


class TestPreviewDiff:
    def test_preview_diff_new_agent(self, tmp_path: Path) -> None:
        """Для нового агента exists=False, diff не пустой."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        result = svc.preview_diff(draft["draft_id"])
        assert result["exists"] is False
        assert "draft" in result
        assert "candidate" in result

    def test_preview_diff_existing_agent_exists_true(self, tmp_path: Path) -> None:
        """После apply preview показывает exists=True."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        svc.apply_draft(draft["draft_id"], confirmed=True)

        draft2 = _create_agent_draft(svc, name="my-agent", role="communication")
        result = svc.preview_diff(draft2["draft_id"])
        assert result["exists"] is True

    def test_preview_diff_candidate_has_correct_fields(self, tmp_path: Path) -> None:
        """Candidate содержит name, role, description, source=provisioning."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        result = svc.preview_diff(draft["draft_id"])
        cand = result["candidate"]
        assert cand["name"] == "my-agent"
        assert cand["role"] == "coding"
        assert cand["source"] == "provisioning"


# ═════════════════════════════════════════════════════════════════════════════
# 8. apply_draft
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyDraft:
    def test_apply_creates_entry_in_catalog(self, tmp_path: Path) -> None:
        """После apply агент появляется в agents_catalog.yaml."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        result = svc.apply_draft(draft["draft_id"], confirmed=True)
        assert result["ok"] is True
        assert result["status"] == "created"

        catalog = svc._read_catalog("agent")
        names = [a["name"] for a in catalog.get("agents", [])]
        assert "my-agent" in names

    def test_apply_updates_existing_entry(self, tmp_path: Path) -> None:
        """Повторный apply с тем же именем обновляет запись (status=updated)."""
        svc = _make_service(tmp_path)
        draft1 = _create_agent_draft(svc, name="upd-agent")
        svc.apply_draft(draft1["draft_id"], confirmed=True)

        draft2 = svc.create_draft("agent", "upd-agent", "communication", "Updated", "tester")
        result = svc.apply_draft(draft2["draft_id"], confirmed=True)
        assert result["status"] == "updated"

        catalog = svc._read_catalog("agent")
        agents = catalog.get("agents", [])
        matched = [a for a in agents if a["name"] == "upd-agent"]
        assert len(matched) == 1
        assert matched[0]["role"] == "communication"

    def test_apply_marks_draft_as_applied(self, tmp_path: Path) -> None:
        """После apply статус драфта меняется на 'applied'."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        svc.apply_draft(draft["draft_id"], confirmed=True)
        refreshed = svc.get_draft(draft["draft_id"])
        assert refreshed["status"] == "applied"
        assert "applied_at" in refreshed

    def test_apply_idempotent_already_applied(self, tmp_path: Path) -> None:
        """Повторный apply того же драфта возвращает already_applied."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        svc.apply_draft(draft["draft_id"], confirmed=True)
        result2 = svc.apply_draft(draft["draft_id"], confirmed=True)
        assert result2["status"] == "already_applied"

    def test_apply_without_confirm_raises(self, tmp_path: Path) -> None:
        """apply_draft(confirmed=False) → PermissionError."""
        svc = _make_service(tmp_path)
        draft = _create_agent_draft(svc)
        with pytest.raises(PermissionError, match="подтверждение"):
            svc.apply_draft(draft["draft_id"], confirmed=False)

    def test_apply_skill_draft(self, tmp_path: Path) -> None:
        """Skill-драфт применяется в skills_catalog."""
        svc = _make_service(tmp_path)
        draft = svc.create_draft("skill", "my-skill", "coding", "desc", "owner")
        result = svc.apply_draft(draft["draft_id"], confirmed=True)
        assert result["ok"] is True
        catalog = svc._read_catalog("skill")
        names = [s["name"] for s in catalog.get("skills", [])]
        assert "my-skill" in names


# ═════════════════════════════════════════════════════════════════════════════
# 9. _normalize_entity
# ═════════════════════════════════════════════════════════════════════════════


class TestNormalizeEntity:
    def test_agent_aliases(self, tmp_path: Path) -> None:
        """'agent' и 'agents' оба нормализуются в 'agent'."""
        svc = _make_service(tmp_path)
        assert svc._normalize_entity("agent") == "agent"
        assert svc._normalize_entity("agents") == "agent"
        assert svc._normalize_entity("AGENT") == "agent"

    def test_skill_aliases(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        assert svc._normalize_entity("skill") == "skill"
        assert svc._normalize_entity("skills") == "skill"
        assert svc._normalize_entity("SKILL") == "skill"

    def test_invalid_entity_type_raises(self, tmp_path: Path) -> None:
        """Неизвестный entity_type → ValueError."""
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="agent или skill"):
            svc._normalize_entity("robot")


# ═════════════════════════════════════════════════════════════════════════════
# 10. _read_yaml / _write_yaml
# ═════════════════════════════════════════════════════════════════════════════


class TestYamlHelpers:
    def test_read_yaml_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """_read_yaml несуществующего файла → {}."""
        svc = _make_service(tmp_path)
        result = svc._read_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_read_yaml_broken_file_returns_empty(self, tmp_path: Path) -> None:
        """Битый YAML → {} без исключения."""
        svc = _make_service(tmp_path)
        broken = tmp_path / "broken.yaml"
        broken.write_text(": !!invalid\n  broken: [yaml", encoding="utf-8")
        result = svc._read_yaml(broken)
        assert result == {}

    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        """_write_yaml + _read_yaml — данные сохраняются корректно."""
        svc = _make_service(tmp_path)
        data = {"key": "value", "nested": {"a": 1}}
        path = tmp_path / "test.yaml"
        svc._write_yaml(path, data)
        loaded = svc._read_yaml(path)
        assert loaded == data

    def test_read_yaml_non_dict_returns_empty(self, tmp_path: Path) -> None:
        """YAML со списком на верхнем уровне → {}."""
        svc = _make_service(tmp_path)
        lst_file = tmp_path / "list.yaml"
        lst_file.write_text("- item1\n- item2\n", encoding="utf-8")
        assert svc._read_yaml(lst_file) == {}


# ═════════════════════════════════════════════════════════════════════════════
# 11. Round-trip: create → apply → list
# ═════════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    def test_full_lifecycle(self, tmp_path: Path) -> None:
        """Полный цикл: создание → валидация → apply → проверка каталога и list_drafts."""
        svc = _make_service(tmp_path)

        draft = svc.create_draft(
            "agent",
            "lifecycle-agent",
            "coding",
            "Описание жизненного цикла",
            "test-owner",
            settings={"max_tokens": 8192},
        )
        draft_id = draft["draft_id"]

        validation = svc.validate_draft(draft_id)
        assert validation["ok"] is True

        preview = svc.preview_diff(draft_id)
        assert preview["exists"] is False

        apply_result = svc.apply_draft(draft_id, confirmed=True)
        assert apply_result["ok"] is True
        assert apply_result["status"] == "created"

        catalog = svc._read_catalog("agent")
        entries = catalog.get("agents", [])
        entry = next((a for a in entries if a["name"] == "lifecycle-agent"), None)
        assert entry is not None
        assert entry["settings"]["max_tokens"] == 8192
        assert entry["source"] == "provisioning"

        applied = svc.list_drafts(status="applied")
        assert any(d["draft_id"] == draft_id for d in applied)

        pending = svc.list_drafts(status="draft")
        assert not any(d["draft_id"] == draft_id for d in pending)

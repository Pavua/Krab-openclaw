# -*- coding: utf-8 -*-
import pytest
from pathlib import Path
from src.core.provisioning_service import ProvisioningService

@pytest.fixture
def prov_service(tmp_path):
    agents_path = tmp_path / "agents_catalog.yaml"
    skills_path = tmp_path / "skills_catalog.yaml"
    drafts_dir = tmp_path / "drafts"
    return ProvisioningService(
        agents_catalog_path=str(agents_path),
        skills_catalog_path=str(skills_path),
        drafts_dir=str(drafts_dir)
    )

def test_validate_draft_success(prov_service):
    """Успешная валидация корректного драфта."""
    draft = prov_service.create_draft(
        entity_type="agent",
        name="test-agent",
        role="coding",
        description="Test desc",
        requested_by="owner"
    )
    
    report = prov_service.validate_draft(draft["draft_id"])
    assert report["ok"] is True
    assert len(report["errors"]) == 0
    assert "Можно выполнять apply" in report["next_step"]

def test_validate_draft_invalid_entity(prov_service):
    """Ошибка: неверный тип сущности."""
    # Обходим create_draft с валидацией, пишем напрямую в файл для теста validate_draft
    draft_id = "a_invalid_type"
    path = prov_service._draft_path(draft_id)
    prov_service._write_yaml(path, {
        "entity_type": "invalid",
        "name": "bad",
        "role": "coding"
    })
    
    report = prov_service.validate_draft(draft_id)
    assert report["ok"] is False
    assert any("Недопустимый entity_type" in e for e in report["errors"])

def test_validate_draft_short_name(prov_service):
    """Ошибка: слишком короткое имя."""
    draft = prov_service.create_draft(
        entity_type="agent",
        name="test-agent", # bypass init validator
        role="coding",
        description="Desc",
        requested_by="owner"
    )
    # Портим имя в драфте
    draft["name"] = "a"
    prov_service._write_yaml(prov_service._draft_path(draft["draft_id"]), draft)
    
    report = prov_service.validate_draft(draft["draft_id"])
    assert report["ok"] is False
    assert any("имя (name) слишком короткое" in e.lower() for e in report["errors"])

def test_validate_draft_unknown_role_warning(prov_service):
    """Предупреждение: неизвестная роль."""
    draft = prov_service.create_draft(
        entity_type="agent",
        name="new-agent",
        role="unknown-role",
        description="Desc",
        requested_by="owner"
    )
    
    report = prov_service.validate_draft(draft["draft_id"])
    assert report["ok"] is True # Валидация проходит, но с警告
    assert any("отсутствует в каталоге шаблонов" in w for w in report["warnings"])

def test_validate_draft_conflict_warning(prov_service):
    """Предупреждение: конфликт имен."""
    # 1. Создаем и применяем первого агента
    draft1 = prov_service.create_draft("agent", "bob", "coding", "desc", "owner")
    prov_service.apply_draft(draft1["draft_id"], confirmed=True)
    
    # 2. Создаем второй драфт с тем же именем
    draft2 = prov_service.create_draft("agent", "bob", "coding", "new desc", "owner")
    
    report = prov_service.validate_draft(draft2["draft_id"])
    assert report["ok"] is True
    assert any("уже есть в каталоге. Apply обновит её" in w for w in report["warnings"])

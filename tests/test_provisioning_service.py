# -*- coding: utf-8 -*-
"""Тесты provisioning-сервиса (Phase E)."""

from pathlib import Path

import pytest
import yaml

from src.core.provisioning_service import ProvisioningService


def test_provisioning_draft_preview_apply(tmp_path: Path) -> None:
    service = ProvisioningService(
        agents_catalog_path=str(tmp_path / "agents_catalog.yaml"),
        skills_catalog_path=str(tmp_path / "skills_catalog.yaml"),
        drafts_dir=str(tmp_path / "drafts"),
    )

    draft = service.create_draft(
        entity_type="agent",
        name="tg-moderator-v2",
        role="telegram-moderation",
        description="Усиленная модерация и summary по запросу",
        requested_by="@owner",
    )

    assert draft["draft_id"]
    assert draft["status"] == "draft"

    preview = service.preview_diff(draft["draft_id"])
    assert preview["draft"]["name"] == "tg-moderator-v2"
    assert "telegram-moderation" in preview["diff"] or preview["diff"]

    apply_result = service.apply_draft(draft["draft_id"], confirmed=True)
    assert apply_result["ok"] is True
    assert apply_result["status"] == "created"

    catalog_path = tmp_path / "agents_catalog.yaml"
    with catalog_path.open("r", encoding="utf-8") as fp:
        catalog = yaml.safe_load(fp)

    names = [item["name"] for item in catalog["agents"]]
    assert "tg-moderator-v2" in names


def test_apply_requires_confirm(tmp_path: Path) -> None:
    service = ProvisioningService(
        agents_catalog_path=str(tmp_path / "agents_catalog.yaml"),
        skills_catalog_path=str(tmp_path / "skills_catalog.yaml"),
        drafts_dir=str(tmp_path / "drafts"),
    )

    draft = service.create_draft(
        entity_type="skill",
        name="chat-summary-focus",
        role="communication",
        description="Фокусный summary для Telegram диалогов",
        requested_by="@owner",
    )

    with pytest.raises(PermissionError):
        service.apply_draft(draft["draft_id"], confirmed=False)

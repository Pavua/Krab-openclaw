# -*- coding: utf-8 -*-
"""Интеграционный smoke: free quota -> paid invalid -> openai -> local."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.openclaw_client import OpenClawClient


@pytest.mark.asyncio
async def test_cloud_failover_chain_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenClawClient()

    # Подменяем tier-ключи, чтобы paid считался невалидным форматом.
    client.gemini_tiers["free"] = "AIzaFREE_VALID_EXAMPLE_KEY_1234567890"
    client.gemini_tiers["paid"] = "AQ.INVALID.PAID.KEY"

    sequence = [
        "429 quota exceeded",
        "401 API keys are not supported by this API",
        "Локальный fallback ответ",
    ]

    async def _fake_once(*, model_id: str, messages_to_send):  # noqa: ANN001
        return sequence.pop(0)

    monkeypatch.setattr(client, "_openclaw_completion_once", _fake_once)
    monkeypatch.setattr(client, "_switch_cloud_tier", AsyncMock(return_value={"ok": False, "error": "invalid_paid_key_type"}))

    from src.model_manager import model_manager

    monkeypatch.setattr(model_manager, "get_best_model", AsyncMock(return_value="google/gemini-2.5-flash"))
    monkeypatch.setattr(model_manager, "is_local_model", lambda _: False)
    monkeypatch.setattr(model_manager, "ensure_model_loaded", AsyncMock(return_value=True))
    monkeypatch.setattr(model_manager, "resolve_preferred_local_model", AsyncMock(return_value="local/zai-org/glm-4.6v-flash"))
    monkeypatch.setattr(client, "_resolve_local_model_for_retry", AsyncMock(return_value="local/zai-org/glm-4.6v-flash"))

    chunks = []
    async for chunk in client.send_message_stream("ping", "chat-integration"):
        chunks.append(chunk)

    final = "".join(chunks)
    assert "fallback" in final.lower() or "локальный" in final.lower()
    assert client.get_tier_state_export()["last_error_code"] in {"model_not_loaded", "unsupported_key_type", "quota_exceeded"}

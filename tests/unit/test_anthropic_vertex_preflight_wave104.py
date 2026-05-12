"""Wave 104: tests для scripts/krab_anthropic_vertex_preflight.py.

Покрытие:
- probe 200 / success → status=ok;
- probe 403/PermissionDenied → status=blocked;
- probe 429/RateLimit → status=blocked;
- unknown exception → status=unknown;
- save_status / load_status round-trip persistence;
- transition blocked→ok triggers blacklist remove + telegram send;
- no transition → blacklist untouched.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Динамический импорт скрипта без модификации sys.path.
_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "krab_anthropic_vertex_preflight.py"
)
_spec = importlib.util.spec_from_file_location(
    "krab_anthropic_vertex_preflight", _SCRIPT
)
assert _spec is not None and _spec.loader is not None
preflight = importlib.util.module_from_spec(_spec)
sys.modules["krab_anthropic_vertex_preflight"] = preflight
_spec.loader.exec_module(preflight)


# ─── Fakes ───────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Имитация anthropic.types.Message с непустым content."""

    def __init__(self) -> None:
        self.content = [type("Block", (), {"text": "pong"})()]


class _FakeClientOk:
    class _Messages:
        @staticmethod
        def create(**_kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    messages = _Messages()


class _PermissionDeniedError(Exception):
    """Имитирует anthropic.PermissionDeniedError по имени класса."""


class _RateLimitError(Exception):
    """Имитирует anthropic.RateLimitError."""


def _make_factory_raising(exc: BaseException) -> Any:
    def _factory() -> Any:
        class _Client:
            class _Messages:
                @staticmethod
                def create(**_kwargs: Any) -> Any:
                    raise exc

            messages = _Messages()

        return _Client()

    return _factory


# ─── Тесты ──────────────────────────────────────────────────────────────────


def test_probe_success_returns_ok() -> None:
    result = preflight.probe_anthropic_vertex(client_factory=lambda: _FakeClientOk())
    assert result["status"] == "ok"
    assert result["error"] is None


def test_probe_permission_denied_returns_blocked() -> None:
    exc = _PermissionDeniedError("403 PERMISSION_DENIED: not allowed")
    result = preflight.probe_anthropic_vertex(
        client_factory=_make_factory_raising(exc)
    )
    assert result["status"] == "blocked"
    assert "PermissionDenied" in (result["error"] or "")


def test_probe_rate_limit_returns_blocked() -> None:
    exc = _RateLimitError("429 RESOURCE_EXHAUSTED: quota exceeded")
    result = preflight.probe_anthropic_vertex(
        client_factory=_make_factory_raising(exc)
    )
    assert result["status"] == "blocked"


def test_probe_unknown_exception_returns_unknown() -> None:
    exc = RuntimeError("network unreachable")
    result = preflight.probe_anthropic_vertex(
        client_factory=_make_factory_raising(exc)
    )
    assert result["status"] == "unknown"
    assert "RuntimeError" in (result["error"] or "")


def test_save_and_load_status_roundtrip(tmp_path: Path) -> None:
    status_path = tmp_path / "anthropic_vertex_status.json"
    payload = {
        "timestamp": "2026-05-12T09:00:00+00:00",
        "vertex_quota_status": "blocked",
        "error": "PermissionDeniedError: 403",
    }
    preflight.save_status(payload, status_path)
    loaded = preflight.load_status(status_path)
    assert loaded == payload
    # atomic write — никаких .tmp следов
    assert not (tmp_path / "anthropic_vertex_status.tmp").exists()


def test_transition_blocked_to_ok_triggers_resume(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    blacklist_path = tmp_path / "blacklist.json"
    # Предыдущий запуск зафиксировал blocked
    preflight.save_status(
        {"vertex_quota_status": "blocked", "timestamp": "2026-05-11T09:00:00+00:00"},
        status_path,
    )
    # Создаём blacklist чтобы было что удалять
    blacklist_path.write_text(
        json.dumps({"blacklist": ["claude-sonnet-4-5"]}), encoding="utf-8"
    )

    sent: list[str] = []

    def _fake_tg(text: str) -> bool:
        sent.append(text)
        return True

    snapshot = preflight.run_preflight(
        client_factory=lambda: _FakeClientOk(),
        status_path=status_path,
        blacklist_path=blacklist_path,
        auto_resume=True,
        alert_telegram=True,
        telegram_sender=_fake_tg,
    )

    assert snapshot["vertex_quota_status"] == "ok"
    assert snapshot["transitioned_blocked_to_ok"] is True
    assert snapshot["blacklist_removed"] is True
    assert snapshot["telegram_sent"] is True
    assert not blacklist_path.exists()
    assert sent and "quota approved" in sent[0]


def test_no_transition_keeps_blacklist(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    blacklist_path = tmp_path / "blacklist.json"
    blacklist_path.write_text(
        json.dumps({"blacklist": ["claude-sonnet-4-5"]}), encoding="utf-8"
    )
    # Предыдущий статус ok — повторный ok не должен снимать blacklist (он и так снят)
    # Здесь основной кейс: повторный blocked → blacklist остаётся.
    preflight.save_status(
        {"vertex_quota_status": "blocked", "timestamp": "2026-05-11T09:00:00+00:00"},
        status_path,
    )

    exc = _PermissionDeniedError("403 still pending")
    sent: list[str] = []

    snapshot = preflight.run_preflight(
        client_factory=_make_factory_raising(exc),
        status_path=status_path,
        blacklist_path=blacklist_path,
        auto_resume=True,
        alert_telegram=True,
        telegram_sender=lambda t: sent.append(t) or True,
    )

    assert snapshot["vertex_quota_status"] == "blocked"
    assert snapshot["transitioned_blocked_to_ok"] is False
    assert snapshot["blacklist_removed"] is False
    assert blacklist_path.exists()
    assert sent == []

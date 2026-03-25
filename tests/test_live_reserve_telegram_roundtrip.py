"""
Тесты для scripts/live_reserve_telegram_roundtrip.py.

Проверяем локальную логику поиска bot reply и content-match без реального Telegram.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "live_reserve_telegram_roundtrip.py"
    spec = importlib.util.spec_from_file_location("live_reserve_telegram_roundtrip", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _msg(message_id: int, author_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        text=text,
        caption=None,
        from_user=SimpleNamespace(id=author_id),
        date="2026-03-13T01:00:00+00:00",
    )


def test_normalize_bot_username_strips_prefix_and_spaces():
    module = _load_module()
    assert module._normalize_bot_username("  @mytest_feb2026_bot  ") == "mytest_feb2026_bot"


def test_reply_contains_expected_marker_matches_case_insensitively():
    module = _load_module()
    assert module._reply_contains_expected_marker(
        "В этом диалоге отвечает reserve Telegram Bot.",
        ("reserve telegram bot",),
    ) is True


def test_find_bot_reply_ignores_older_and_non_bot_messages():
    module = _load_module()
    messages = [
        _msg(41, 999, "старое сообщение бота"),
        _msg(43, 111, "сообщение владельца"),
        _msg(44, 999, "В этом диалоге отвечает reserve Telegram Bot."),
    ]

    found = module._find_bot_reply(messages, sent_message_id=42, bot_user_id=999)
    assert found is not None
    assert found["message_id"] == 44
    assert "reserve Telegram Bot" in found["text"]


def test_find_bot_reply_returns_none_when_new_message_has_no_text():
    module = _load_module()
    message = SimpleNamespace(
        id=77,
        text="",
        caption=None,
        from_user=SimpleNamespace(id=999),
        date="2026-03-13T01:00:00+00:00",
    )
    found = module._find_bot_reply([message], sent_message_id=70, bot_user_id=999)
    assert found is None


def test_preflight_channel_snapshot_reads_nested_summary(monkeypatch):
    module = _load_module()

    def _fake_fetch(_url: str, timeout_sec: float = 10.0):
        return (
            {
                "ok": True,
                "channel_capabilities": {
                    "summary": {
                        "reserve_safe": True,
                        "reserve_transport": "telegram_reserve_bot",
                    }
                },
            },
            None,
        )

    monkeypatch.setattr(module, "_fetch_json", _fake_fetch)
    snapshot = module._preflight_channel_snapshot("http://127.0.0.1:8080")
    assert snapshot["ok"] is True
    assert snapshot["reserve_safe"] is True
    assert snapshot["reserve_transport"] == "telegram_reserve_bot"

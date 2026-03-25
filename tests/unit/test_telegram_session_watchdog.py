# -*- coding: utf-8 -*-
"""
Тесты внешнего watchdog для Telegram userbot и OpenClaw gateway.

Покрываем:
1) прокидывание `WEB_API_KEY` в write-endpoint'ы;
2) truthful классификацию health payload для gateway;
3) безопасную деградацию restart gateway, если бинарник OpenClaw не найден.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import telegram_session_watchdog as watchdog


def test_build_write_headers_includes_web_api_key(monkeypatch) -> None:
    """Watchdog должен отправлять тот же web-key, что и owner panel write-endpoint'ы."""
    monkeypatch.setenv("WEB_API_KEY", "secret")

    headers = watchdog._build_write_headers()  # noqa: SLF001

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Krab-Web-Key"] == "secret"


def test_http_post_sends_web_key_header(monkeypatch) -> None:
    """Низкоуровневый POST helper должен реально добавлять header в Request."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            _ = exc_type, exc, tb
            return False

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode("utf-8")

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        captured["request"] = req
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", _fake_urlopen)

    payload = watchdog._http_post("http://127.0.0.1:8080/api/krab/restart_userbot", payload={"ping": True})  # noqa: SLF001

    req = captured["request"]
    assert payload["ok"] is True
    assert captured["timeout"] == 8.0
    assert req.headers["X-krab-web-key"] == "secret"


def test_is_gateway_ok_accepts_live_payload() -> None:
    """Gateway считается живым и по `ok=true`, и по `status=live`."""
    assert watchdog._is_gateway_ok({"ok": True}) is True  # noqa: SLF001
    assert watchdog._is_gateway_ok({"status": "live"}) is True  # noqa: SLF001
    assert watchdog._is_gateway_ok({"status": "down"}) is False  # noqa: SLF001


def test_try_restart_gateway_skips_when_openclaw_bin_missing(monkeypatch) -> None:
    """Без бинарника OpenClaw watchdog не должен падать и пытаться запускать мусор."""
    monkeypatch.setattr(watchdog, "_resolve_openclaw_bin", lambda: "")

    assert watchdog._try_restart_gateway() is False  # noqa: SLF001


def test_resolve_log_path_prefers_runtime_state_dir(monkeypatch, tmp_path) -> None:
    """Watchdog должен писать лог в per-account runtime-state, а не в общий `/tmp`."""
    runtime_state_dir = tmp_path / "krab_runtime_state"
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(runtime_state_dir))

    log_path = watchdog._resolve_log_path()  # noqa: SLF001

    assert log_path == runtime_state_dir / "krab_session_watchdog.log"
    assert log_path.parent.exists()
    assert str(log_path).startswith(str(runtime_state_dir))


def test_resolve_log_path_falls_back_to_home_runtime_state(monkeypatch, tmp_path) -> None:
    """Без env watchdog должен использовать `~/.openclaw/krab_runtime_state`."""
    monkeypatch.delenv("KRAB_RUNTIME_STATE_DIR", raising=False)
    monkeypatch.setattr(watchdog.Path, "home", staticmethod(lambda: Path(tmp_path)))

    log_path = watchdog._resolve_log_path()  # noqa: SLF001

    assert log_path == tmp_path / ".openclaw" / "krab_runtime_state" / "krab_session_watchdog.log"

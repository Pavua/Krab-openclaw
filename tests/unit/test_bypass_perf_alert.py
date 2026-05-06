"""Wave 35-B: тесты bypass perf alert check.

5 тестов:
1. Insufficient samples (<3) → no alert
2. p95 > threshold → alert sent
3. fail_rate > threshold → alert sent
4. Debounce: second call within 1h → no alert
5. /api/notify failure → graceful (no exception)
"""

from __future__ import annotations

import json
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.bypass_perf_alert_check as alert_mod

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _make_perf_response(by_kind: dict, total_calls: int = 10) -> bytes:
    """Формирует тело ответа /api/bypass/perf."""
    payload = {
        "ok": True,
        "total_calls": total_calls,
        "by_kind": by_kind,
    }
    return json.dumps(payload).encode()


def _mock_urlopen(body: bytes):
    """Возвращает mock context-manager для urllib.request.urlopen."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body)
    return cm


# ---------------------------------------------------------------------------
# Тест 1: Мало сэмплов → нет алерта
# ---------------------------------------------------------------------------

def test_insufficient_samples_no_alert(tmp_path):
    """Если count < 3 для всех kind — алерт не отправляется."""
    by_kind = {
        # 2 сэмпла, p95 выше порога — должны игнорироваться
        "cli": {"count": 2, "p95": 999.0, "fail_rate": 0.9},
    }
    body = _make_perf_response(by_kind, total_calls=2)

    last_file = tmp_path / "bypass_perf_alert_last.json"

    with (
        patch.object(alert_mod, "LAST_ALERT_FILE", last_file),
        patch("urllib.request.urlopen", return_value=_mock_urlopen(body)),
        patch("urllib.request.Request") as mock_req_cls,
    ):
        result = alert_mod.main()

    # urlopen для /api/notify не вызывался (Request не создавался для POST)
    assert result == 0
    # Файл дебаунса не создан
    assert not last_file.exists()


# ---------------------------------------------------------------------------
# Тест 2: p95 превышает порог → алерт отправлен
# ---------------------------------------------------------------------------

def test_p95_threshold_triggers_alert(tmp_path):
    """Если p95 > threshold['p95_sec'] и count >= 3 — алерт отправляется."""
    by_kind = {
        "cli": {"count": 5, "p95": 75.0, "fail_rate": 0.02},  # p95 > 60s
    }
    body = _make_perf_response(by_kind, total_calls=5)

    last_file = tmp_path / "bypass_perf_alert_last.json"
    notify_called = []

    def fake_urlopen(req_or_url, timeout=None):
        # Первый вызов — GET /api/bypass/perf
        if isinstance(req_or_url, str):
            return _mock_urlopen(body)
        # Второй вызов — POST /api/notify
        notify_called.append(req_or_url)
        return _mock_urlopen(b"{}")

    with (
        patch.object(alert_mod, "LAST_ALERT_FILE", last_file),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = alert_mod.main()

    assert result == 0
    # POST /api/notify должен был вызваться
    assert len(notify_called) == 1
    # Файл дебаунса создан
    assert last_file.exists()
    saved = json.loads(last_file.read_text())
    assert "last_alert_ts" in saved
    assert any("p95" in a for a in saved["alerts"])


# ---------------------------------------------------------------------------
# Тест 3: fail_rate превышает порог → алерт отправлен
# ---------------------------------------------------------------------------

def test_fail_rate_threshold_triggers_alert(tmp_path):
    """Если fail_rate > threshold['fail_rate'] и count >= 3 — алерт отправляется."""
    by_kind = {
        "vertex": {"count": 10, "p95": 10.0, "fail_rate": 0.25},  # 25% > 10%
    }
    body = _make_perf_response(by_kind, total_calls=10)

    last_file = tmp_path / "bypass_perf_alert_last.json"
    notify_called = []

    def fake_urlopen(req_or_url, timeout=None):
        if isinstance(req_or_url, str):
            return _mock_urlopen(body)
        notify_called.append(req_or_url)
        return _mock_urlopen(b"{}")

    with (
        patch.object(alert_mod, "LAST_ALERT_FILE", last_file),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = alert_mod.main()

    assert result == 0
    assert len(notify_called) == 1
    saved = json.loads(last_file.read_text())
    assert any("fail_rate" in a for a in saved["alerts"])


# ---------------------------------------------------------------------------
# Тест 4: Дебаунс — повторный вызов в течение часа не алертит
# ---------------------------------------------------------------------------

def test_debounce_prevents_second_alert(tmp_path):
    """Второй вызов в пределах ALERT_DEBOUNCE_SEC → алерт не отправляется."""
    by_kind = {
        "cli": {"count": 5, "p95": 75.0, "fail_rate": 0.02},
    }
    body = _make_perf_response(by_kind, total_calls=5)

    last_file = tmp_path / "bypass_perf_alert_last.json"
    # Имитируем что алерт был совсем недавно (30 минут назад)
    last_file.write_text(
        json.dumps({"last_alert_ts": time.time() - 1800, "alerts": ["old alert"]})
    )

    notify_called = []

    def fake_urlopen(req_or_url, timeout=None):
        if isinstance(req_or_url, str):
            return _mock_urlopen(body)
        notify_called.append(req_or_url)
        return _mock_urlopen(b"{}")

    with (
        patch.object(alert_mod, "LAST_ALERT_FILE", last_file),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = alert_mod.main()

    assert result == 0
    # POST /api/notify не должен был вызываться
    assert len(notify_called) == 0


# ---------------------------------------------------------------------------
# Тест 5: /api/notify возвращает ошибку → graceful, нет исключения
# ---------------------------------------------------------------------------

def test_notify_failure_is_graceful(tmp_path):
    """Если POST /api/notify бросает исключение — main() не падает."""
    by_kind = {
        "gemma": {"count": 8, "p95": 45.0, "fail_rate": 0.0},  # p95 > 30s
    }
    body = _make_perf_response(by_kind, total_calls=8)

    last_file = tmp_path / "bypass_perf_alert_last.json"

    def fake_urlopen(req_or_url, timeout=None):
        if isinstance(req_or_url, str):
            # GET /api/bypass/perf — успешно
            return _mock_urlopen(body)
        # POST /api/notify — ошибка соединения
        raise OSError("connection refused")

    with (
        patch.object(alert_mod, "LAST_ALERT_FILE", last_file),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        # Не должно бросать исключение
        result = alert_mod.main()

    # main() должна завершиться без ошибки
    assert result == 0
    # Файл дебаунса должен быть создан (мы всё-таки обнаружили нарушение)
    assert last_file.exists()

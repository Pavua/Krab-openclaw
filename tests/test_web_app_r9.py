# -*- coding: utf-8 -*-
"""Тесты для эндпоинтов OpenClaw Channels (R9)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from src.modules.web_app import WebApp

# Используем Dummy классы из основного файла тестов (в реальности импортируем или дублируем для изоляции)
class _DummyRouter:
    def get_model_info(self): return {}

class _DummyBlackBox:
    def get_stats(self): return {}


class _DummyProc:
    """Простой async-процесс для тестов subprocess без AsyncMock side-effects."""

    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""

@pytest.fixture
def client():
    deps = {
        "router": _DummyRouter(),
        "black_box": _DummyBlackBox(),
    }
    app = WebApp(deps=deps, port=8000)
    return TestClient(app.app)

@pytest.mark.asyncio
async def test_openclaw_channels_status_success(client):
    """Проверка успешного получения статуса с варнингами."""
    mock_stdout = b"Channels Health:\nWarnings:\n- Channel A is slow\n- Channel B unreachable\nSummary: 2 issues"
    
    mock_proc = _DummyProc(stdout=mock_stdout, returncode=0)
    
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        response = client.get("/api/openclaw/channels/status")
        
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert "Channel A is slow" in payload["warnings"]
        assert "Channel B unreachable" in payload["warnings"]
        mock_exec.assert_called_with(
            "openclaw", "channels", "status", "--probe",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

@pytest.mark.asyncio
async def test_openclaw_channels_status_timeout(client):
    """Проверка обработки таймаута при запросе статуса."""
    mock_proc = MagicMock()
    # Важно: communicate НЕ async в этом тесте, чтобы не создавать корутину,
    # которую wait_for (замоканный на TimeoutError) не успеет await-нуть.
    mock_proc.communicate = MagicMock(return_value=(b"", b""))
    
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            response = client.get("/api/openclaw/channels/status")
            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is False
            assert payload["error"] == "openclaw_timeout"

@pytest.mark.asyncio
async def test_openclaw_runtime_repair_auth(client, monkeypatch):
    """Проверка защиты API-ключом для runtime-repair."""
    monkeypatch.setenv("WEB_API_KEY", "secret_r9")
    
    # Без ключа
    resp = client.post("/api/openclaw/channels/runtime-repair")
    assert resp.status_code == 403
    
    # С верным ключом
    # Важно: используем минимальный DummyProc без AsyncMock,
    # чтобы не порождать не-awaited coroutine в teardown pytest.
    mock_proc = _DummyProc(stdout=b"Repair started\nDone", returncode=0)
    
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        resp = client.post(
            "/api/openclaw/channels/runtime-repair",
            headers={"X-Krab-Web-Key": "secret_r9"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "Done" in resp.json()["output"]

@pytest.mark.asyncio
async def test_openclaw_signal_guard_run_success(client, monkeypatch):
    """Проверка успешного запуска signal guard."""
    monkeypatch.setenv("WEB_API_KEY", "guard_key")
    
    mock_proc = _DummyProc(stdout=b"Guard check: OK", returncode=0)
    
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        resp = client.post(
            "/api/openclaw/channels/signal-guard-run",
            headers={"X-Krab-Web-Key": "guard_key"}
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert "OK" in payload["output"]

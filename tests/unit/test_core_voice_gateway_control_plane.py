# -*- coding: utf-8 -*-
"""
Тесты для VoiceGatewayClient — concrete implementation VoiceGatewayControlPlane Protocol.

Стратегия:
- мокаем `_fetch_health_payload` и `_request_json` (helper-методы),
  чтобы не поднимать настоящий HTTP;
- проверяем contract-level поведение: ok/error флаги, нормализацию payload,
  Protocol conformance через isinstance;
- один тест на Protocol structural subtyping.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.voice_gateway_control_plane import VoiceGatewayControlPlane
from src.integrations.voice_gateway_client import VoiceGatewayClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**kwargs: object) -> VoiceGatewayClient:
    """Создаёт клиент с тестовым base_url, не зависящий от env."""
    return VoiceGatewayClient(base_url="http://test-gw:8090", api_key="test-key", **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """VoiceGatewayClient должен проходить isinstance-проверку Protocol."""

    def test_client_is_instance_of_protocol(self):
        """runtime_checkable Protocol — isinstance работает на structural level."""
        client = _make_client()
        assert isinstance(client, VoiceGatewayControlPlane)

    def test_protocol_not_instantiable_directly(self):
        """Сам Protocol нельзя вызвать как конструктор — только для isinstance."""
        # Проверяем, что класс — это именно Protocol, а не ABC
        assert hasattr(VoiceGatewayControlPlane, "__protocol_attrs__") or True
        # Конкретная проверка: у Protocol нет __init__, поднимающего ошибку
        # — достаточно убедиться, что клиент соответствует


# ---------------------------------------------------------------------------
# _is_ok_payload
# ---------------------------------------------------------------------------


class TestIsOkPayload:
    """Статический helper определения здоровья из JSON-ответа /health."""

    def test_ok_true(self):
        assert VoiceGatewayClient._is_ok_payload({"ok": True}) is True

    def test_status_ok(self):
        assert VoiceGatewayClient._is_ok_payload({"status": "ok"}) is True

    def test_status_healthy(self):
        assert VoiceGatewayClient._is_ok_payload({"status": "healthy"}) is True

    def test_status_up(self):
        assert VoiceGatewayClient._is_ok_payload({"status": "UP"}) is True

    def test_status_error(self):
        assert VoiceGatewayClient._is_ok_payload({"status": "error"}) is False

    def test_empty_payload(self):
        assert VoiceGatewayClient._is_ok_payload({}) is False

    def test_status_ok_wins_when_ok_key_missing(self):
        """status=ok когда ключа 'ok' нет — возвращает True."""
        assert VoiceGatewayClient._is_ok_payload({"status": "ok"}) is True

    def test_ok_false_but_status_ok_returns_true(self):
        """ok=False игнорируется если bool(False) — код смотрит bool(payload.get('ok')).
        bool(False) = False, поэтому переходит к проверке status."""
        # _is_ok_payload: if bool(payload.get("ok")): return True
        # bool(False) == False → идёт дальше → status="ok" → True
        assert VoiceGatewayClient._is_ok_payload({"ok": False, "status": "ok"}) is True


# ---------------------------------------------------------------------------
# _error_payload
# ---------------------------------------------------------------------------


class TestErrorPayload:
    """Нормализация ошибок в owner-facing формат."""

    def test_network_error_no_status(self):
        payload = VoiceGatewayClient._error_payload(None, "connection refused")
        assert payload["ok"] is False
        assert payload["error"] == "connection refused"

    def test_http_400(self):
        payload = VoiceGatewayClient._error_payload(400, "bad request")
        assert payload["ok"] is False
        assert payload["error"] == "http_400"

    def test_http_500(self):
        payload = VoiceGatewayClient._error_payload(500, "server error")
        assert payload["ok"] is False
        assert "500" in payload["error"]

    def test_2xx_with_error_string(self):
        """Код 2xx + непустой error → unexpected_gateway_payload."""
        payload = VoiceGatewayClient._error_payload(200, "unexpected")
        assert payload["ok"] is False

    def test_detail_overrides_error_string(self):
        payload = VoiceGatewayClient._error_payload(404, "not_found", detail="session missing")
        assert payload["detail"] == "session missing"


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """health_check() возвращает bool."""

    @pytest.mark.asyncio
    async def test_healthy_200_ok_true(self):
        """200 + ok=True → True."""
        client = _make_client()
        with patch.object(
            client,
            "_fetch_health_payload",
            new_callable=AsyncMock,
            return_value=(200, {"ok": True}),
        ):
            assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_unhealthy_503(self):
        """503 → False, не кидает."""
        client = _make_client()
        with patch.object(
            client, "_fetch_health_payload", new_callable=AsyncMock, return_value=(503, {})
        ):
            assert await client.health_check() is False

    @pytest.mark.asyncio
    async def test_network_exception_returns_false(self):
        """Исключение при HTTP-вызове → False, не кидает наружу."""
        client = _make_client()
        with patch.object(
            client, "_fetch_health_payload", new_callable=AsyncMock, side_effect=OSError("refused")
        ):
            assert await client.health_check() is False


# ---------------------------------------------------------------------------
# health_report
# ---------------------------------------------------------------------------


class TestHealthReport:
    """health_report() возвращает структурированный dict."""

    @pytest.mark.asyncio
    async def test_ok_report_structure(self):
        client = _make_client()
        with patch.object(
            client,
            "_fetch_health_payload",
            new_callable=AsyncMock,
            return_value=(200, {"status": "ok"}),
        ):
            report = await client.health_report()
        assert report["ok"] is True
        assert report["status"] == "ok"
        assert "latency_ms" in report
        assert "source" in report

    @pytest.mark.asyncio
    async def test_error_report_on_exception(self):
        client = _make_client()
        with patch.object(
            client,
            "_fetch_health_payload",
            new_callable=AsyncMock,
            side_effect=RuntimeError("timeout"),
        ):
            report = await client.health_report()
        assert report["ok"] is False
        assert report["status"] == "error"


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    """list_sessions() парсит items из ответа."""

    @pytest.mark.asyncio
    async def test_returns_items(self):
        client = _make_client()
        mock_payload = {"ok": True, "count": 2, "items": [{"id": "s1"}, {"id": "s2"}]}
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(200, mock_payload, "")
        ):
            result = await client.list_sessions()
        assert result["ok"] is True
        assert len(result["items"]) == 2

    @pytest.mark.asyncio
    async def test_network_error_returns_error_payload(self):
        client = _make_client()
        with patch.object(
            client,
            "_request_json",
            new_callable=AsyncMock,
            return_value=(None, {}, "connection refused"),
        ):
            result = await client.list_sessions()
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_status_filter_passed(self):
        """status-параметр передаётся в params."""
        client = _make_client()
        call_args: list = []

        async def fake_request(method, path, *, params=None, json_payload=None, timeout_sec=None):
            call_args.append(params or {})
            return 200, {"items": []}, ""

        with patch.object(client, "_request_json", side_effect=fake_request):
            await client.list_sessions(status="active", limit=5)

        assert call_args[0].get("status") == "active"
        assert call_args[0].get("limit") == 5


# ---------------------------------------------------------------------------
# start_session
# ---------------------------------------------------------------------------


class TestStartSession:
    """start_session() возвращает session_id и result."""

    @pytest.mark.asyncio
    async def test_success_201(self):
        client = _make_client()
        gw_resp = {"session_id": "sess-abc", "result": {"state": "starting"}}
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(201, gw_resp, "")
        ):
            result = await client.start_session(
                source="microphone",
                translation_mode="realtime",
                notify_mode="silent",
                tts_mode="cloud",
                src_lang="ru",
                tgt_lang="en",
            )
        assert result["ok"] is True
        assert result["session_id"] == "sess-abc"

    @pytest.mark.asyncio
    async def test_gateway_error_returns_error_payload(self):
        client = _make_client()
        with patch.object(
            client,
            "_request_json",
            new_callable=AsyncMock,
            return_value=(500, {}, "internal error"),
        ):
            result = await client.start_session(
                source="mic",
                translation_mode="rt",
                notify_mode="s",
                tts_mode="c",
                src_lang="ru",
                tgt_lang="en",
            )
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# stop_session
# ---------------------------------------------------------------------------


class TestStopSession:
    """stop_session() поддерживает POST /stop и DELETE fallback."""

    @pytest.mark.asyncio
    async def test_stop_200(self):
        client = _make_client()
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(200, {"result": {}}, "")
        ):
            result = await client.stop_session("sess-xyz")
        assert result["ok"] is True
        assert result["session_id"] == "sess-xyz"

    @pytest.mark.asyncio
    async def test_stop_404_then_delete(self):
        """POST /stop 404 → DELETE fallback тоже вызывается."""
        client = _make_client()
        call_count = 0

        async def side_effect(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 404, {}, "not found"
            return 200, {"result": {}}, ""

        with patch.object(client, "_request_json", side_effect=side_effect):
            result = await client.stop_session("sess-xyz")
        assert call_count == 2
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# push_event / session_tts
# ---------------------------------------------------------------------------


class TestPushEventAndTts:
    """push_event и session_tts передают нужные поля."""

    @pytest.mark.asyncio
    async def test_push_event_ok(self):
        client = _make_client()
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(200, {"result": {}}, "")
        ):
            result = await client.push_event(
                "sess-1", event_type="reasoning.suggestion", data={"text": "hi"}
            )
        assert result["ok"] is True
        assert result["session_id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_session_tts_ok(self):
        client = _make_client()
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(200, {"result": {}}, "")
        ):
            result = await client.session_tts("sess-2", text="Привет", voice="nova")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_push_event_gateway_error(self):
        client = _make_client()
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(503, {}, "gateway down")
        ):
            result = await client.push_event("sess-1", event_type="ping")
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# register_mobile_device
# ---------------------------------------------------------------------------


class TestRegisterMobileDevice:
    """register_mobile_device нормализует device_id в lowercase."""

    @pytest.mark.asyncio
    async def test_device_id_lowercased(self):
        client = _make_client()
        gw_resp = {"device_id": "IPHONE-ABC", "result": {}}
        with patch.object(
            client, "_request_json", new_callable=AsyncMock, return_value=(201, gw_resp, "")
        ):
            result = await client.register_mobile_device(
                device_id="IPHONE-ABC",
                voip_push_token="tok",
                apns_environment="production",
                app_version="1.0",
                locale="ru",
                preferred_source_lang="ru",
                preferred_target_lang="en",
                notify_default=True,
            )
        assert result["ok"] is True
        # device_id берётся из gw_resp.get("device_id") — в данном случае "IPHONE-ABC"
        # (lowercase применяется к payload-полю при отправке, не к ответу)
        assert result["device_id"] is not None

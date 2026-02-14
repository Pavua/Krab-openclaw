"""Тесты клиента VoiceGatewayClient (контракт путей и payload)."""

from __future__ import annotations

import unittest

from src.core.voice_gateway_client import VoiceGatewayClient


class SpyVoiceGatewayClient(VoiceGatewayClient):
    """Тестовый клиент: перехватывает вызовы _request без сети."""

    def __init__(self) -> None:
        super().__init__(base_url="http://127.0.0.1:8090", api_key="")
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request(self, method: str, path: str, payload=None):  # type: ignore[override]
        self.calls.append((method, path, payload))
        return {"ok": True, "result": {"method": method, "path": path, "payload": payload}}


class VoiceGatewayClientTests(unittest.IsolatedAsyncioTestCase):
    """Проверяет новые методы клиента и нормализацию параметров."""

    async def test_list_quick_phrases_path(self) -> None:
        client = SpyVoiceGatewayClient()
        await client.list_quick_phrases(source_lang="es", target_lang="ru", category="base", limit=999)
        method, path, payload = client.calls[-1]
        self.assertEqual(method, "GET")
        self.assertIn("/v1/quick-phrases", path)
        self.assertIn("source_lang=es", path)
        self.assertIn("target_lang=ru", path)
        self.assertIn("category=base", path)
        self.assertIn("limit=200", path)  # ограничение сверху
        self.assertIsNone(payload)

    async def test_tune_runtime_payload(self) -> None:
        client = SpyVoiceGatewayClient()
        await client.tune_runtime(
            "vs_123",
            buffering_mode="low_latency",
            target_latency_ms=260,
            vad_sensitivity=0.71,
        )
        method, path, payload = client.calls[-1]
        self.assertEqual(method, "PATCH")
        self.assertEqual(path, "/v1/sessions/vs_123/runtime")
        self.assertEqual(payload["buffering_mode"], "low_latency")
        self.assertEqual(payload["target_latency_ms"], 260)
        self.assertAlmostEqual(payload["vad_sensitivity"], 0.71)

    async def test_get_diagnostics_why(self) -> None:
        client = SpyVoiceGatewayClient()
        await client.get_diagnostics_why("vs_abc")
        method, path, payload = client.calls[-1]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/v1/sessions/vs_abc/diagnostics/why")
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()

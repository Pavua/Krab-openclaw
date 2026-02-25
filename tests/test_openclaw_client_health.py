# -*- coding: utf-8 -*-
"""Юнит-тесты расширенного health-report OpenClawClient."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from src.core.openclaw_client import OpenClawClient


class FakeOpenClawClient(OpenClawClient):
    """Подменяет сетевой слой для детерминированных тестов."""

    def __init__(self):
        super().__init__(base_url="http://localhost:18789", api_key="")
        self.responses = {
            "auth": {"ok": True, "path": "/auth/providers/health", "tried": ["/v1/auth/providers/health", "/auth/providers/health"], "status": 200, "data": {"providers": ["openai-codex", "google-gemini-cli"]}},
            "browser": {"ok": True, "path": "/browser/health", "tried": ["/v1/browser/health", "/browser/health"], "status": 200, "data": {"ready": True}},
            "tools": {"ok": True, "path": "/tools/registry", "tried": ["/v1/tools", "/tools/registry"], "status": 200, "data": {"tools": ["web_search", "web_fetch"]}},
        }

    async def health_check(self) -> bool:  # type: ignore[override]
        return True

    async def _probe_first_available(self, paths, timeout=5):  # type: ignore[override]
        if "/v1/auth/providers/health" in paths:
            return self.responses["auth"]
        if "/v1/browser/health" in paths:
            return self.responses["browser"]
        return self.responses["tools"]

    async def invoke_tool(self, tool_name: str, args: dict):  # type: ignore[override]
        return {
            "details": {
                "results": [
                    {"title": "ok", "url": "https://example.com", "description": "test"}
                ]
            }
        }

    async def _request_json(  # type: ignore[override]
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout: int = 15,
    ):
        if path in {"/v1/browser/smoke", "/browser/smoke", "/v1/automation/browser/smoke"}:
            return {
                "ok": True,
                "status": 200,
                "path": path,
                "data": {"url": (payload or {}).get("url", "https://example.com"), "title": "Browser OK"},
            }
        return {
            "ok": False,
            "status": 404,
            "path": path,
            "data": {"error": "not_found"},
        }

    def _inspect_local_lmstudio_profile(self):  # type: ignore[override]
        return {
            "path": "/tmp/auth-profiles.json",
            "present": True,
            "provider_hint": "lmstudio",
            "error": "",
        }


class FakeBrowserToolFallbackClient(FakeOpenClawClient):
    async def _request_json(  # type: ignore[override]
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout: int = 15,
    ):
        # Эмулируем недоступность endpoint browser smoke.
        if path in {"/v1/browser/smoke", "/browser/smoke", "/v1/automation/browser/smoke"}:
            return {"ok": False, "status": 404, "path": path, "data": {"error": "not_found"}}
        return {"ok": True, "status": 200, "path": path, "data": {}}

    async def invoke_tool(self, tool_name: str, args: dict):  # type: ignore[override]
        if tool_name == "web_fetch":
            return {"content": [{"text": "ok"}], "details": {"title": "Fetched"}}
        return {"error": "tool_not_found"}


class FakeHtmlAuthClient(FakeOpenClawClient):
    async def _probe_first_available(self, paths, timeout=5):  # type: ignore[override]
        if "/v1/auth/providers/health" in paths:
            return {
                "ok": True,
                "path": "/v1/auth/providers/health",
                "tried": ["/v1/auth/providers/health"],
                "status": 200,
                "data": {"raw": "<!doctype html><html>control</html>"},
            }
        return await super()._probe_first_available(paths, timeout=timeout)


class FakeMissingLmstudioProfileClient(FakeOpenClawClient):
    def _inspect_local_lmstudio_profile(self):  # type: ignore[override]
        return {
            "path": "/tmp/auth-profiles.json",
            "present": False,
            "provider_hint": "lmstudio",
            "error": "lmstudio_profile_missing",
        }


class FakeResearchWorkflowClient(OpenClawClient):
    """Фейковый клиент для проверки web-research пайплайна без сети."""

    def __init__(self, search_payload):
        super().__init__(base_url="http://localhost:18789", api_key="")
        self.search_payload = search_payload
        self.invocations = []
        self.last_messages = []
        self.last_model = ""

    async def invoke_tool(self, tool_name: str, args: dict):  # type: ignore[override]
        self.invocations.append((tool_name, args))
        if tool_name == "web_search":
            return self.search_payload
        if tool_name == "web_fetch":
            return {
                "details": {"title": "Fetched title"},
                "content": [{"text": "Детальный контент страницы для уточнения фактов."}],
            }
        return {"error": "unsupported_tool"}

    async def chat_completions(self, messages, model: str = "google/gemini-1.5-flash"):  # type: ignore[override]
        self.last_messages = messages
        self.last_model = model
        return "REPORT_OK"


class OpenClawClientHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_json_network_error_is_safe(self):
        client = OpenClawClient(base_url="http://127.0.0.1:1", api_key="")
        result = await client._request_json("GET", "/health", timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 0)
        self.assertIn("error", result)

    async def test_request_json_empty_exception_text_uses_exception_class(self):
        """Если текст исключения пустой, клиент должен вернуть имя класса ошибки."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")

        class BrokenSession:
            async def __aenter__(self):
                raise asyncio.TimeoutError()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("src.core.openclaw_client.aiohttp.ClientSession", return_value=BrokenSession()):
            result = await client._request_json("GET", "/health", timeout=1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 0)
        self.assertEqual(result.get("error"), "TimeoutError")

    async def test_chat_completions_sanitizes_runtime_artifacts(self):
        """Артефакты begin/end_of_box и action-not-found не должны уходить пользователю."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")

        async def fake_request(method: str, path: str, payload=None, timeout=15):
            return {
                "ok": True,
                "status": 200,
                "data": {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    "<|begin_of_box|>\n"
                                    "[[reply_to:69409]] <|begin_of_box|> Я здесь и готов помочь. not found\n"
                                    "<|end_of_box|>\n"
                                    "Нормальный ответ."
                                )
                            }
                        }
                    ]
                },
            }

        with patch.object(client, "_request_json", side_effect=fake_request):
            result = await client.chat_completions(
                [{"role": "user", "content": "ping"}],
                model="google/gemini-2.5-flash",
            )

        lowered = result.lower()
        self.assertNotIn("begin_of_box", lowered)
        self.assertNotIn("reply_to", lowered)
        self.assertNotIn("not found", lowered)
        self.assertIn("Нормальный ответ.", result)

    async def test_chat_completions_retries_after_autoswitch_on_quota(self):
        """При quota-ошибке клиент должен autoswitch на paid и повторить запрос в том же вызове."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")

        calls = {"count": 0}

        async def fake_request(method: str, path: str, payload=None, timeout=15):
            calls["count"] += 1
            if calls["count"] == 1:
                return {
                    "ok": False,
                    "status": 429,
                    "data": {"error": {"message": "resource_exhausted: quota exceeded"}},
                }
            return {
                "ok": True,
                "status": 200,
                "data": {
                    "choices": [
                        {"message": {"content": "Ответ после autoswitch."}}
                    ]
                },
            }

        with (
            patch.object(client, "_request_json", side_effect=fake_request),
            patch.object(client, "try_autoswitch_to_paid", return_value=True),
        ):
            result = await client.chat_completions(
                [{"role": "user", "content": "ping"}],
                model="google/gemini-2.5-flash",
            )

        self.assertEqual(calls["count"], 2)
        self.assertEqual(result, "Ответ после autoswitch.")

    async def test_chat_completions_circuit_open_no_never_awaited_warning(self):
        """Circuit OPEN не должен оставлять не-await-нутую корутину."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        client._breaker.record_failure("gateway down")
        client._breaker.record_failure("gateway down")
        client._breaker.record_failure("gateway down")
        client._breaker.record_failure("gateway down")
        client._breaker.record_failure("gateway down")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            response = await client.chat_completions(
                [{"role": "user", "content": "ping"}],
                model="google/gemini-2.5-flash",
                timeout_seconds=1,
            )

        self.assertIn("Circuit OPEN", response)
        never_awaited = [item for item in caught if "never awaited" in str(item.message).lower()]
        self.assertEqual(never_awaited, [])

    async def test_cloud_provider_diagnostics_reports_missing_keys(self):
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "", 
            "GOOGLE_API_KEY": "", 
            "OPENAI_API_KEY": "", 
            "OPENCLAW_API_KEY": "",
            "GEMINI_API_KEY_FREE": "",
            "GEMINI_API_KEY_PAID": ""
        }, clear=False):
            with patch.object(OpenClawClient, "_get_auth_profile_api_key", return_value=""):
                client = OpenClawClient(base_url="http://localhost:18789", api_key="")
                diag = await client.get_cloud_provider_diagnostics(["google", "openai"])
        self.assertFalse(diag["ok"])
        self.assertEqual(diag["providers"]["google"]["error_code"], "missing_api_key")
        self.assertEqual(diag["providers"]["openai"]["error_code"], "missing_api_key")

    async def test_cloud_provider_diagnostics_uses_probe_classification(self):
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "gm-test-1234", "OPENAI_API_KEY": "sk-test-5678"}, clear=False):
            async def fake_probe(model: str):
                if model.startswith("google/"):
                    return "Google API 403: Your API key was reported as leaked."
                return None

            with (
                patch.object(client, "_probe_provider_health_hint", side_effect=fake_probe),
                patch.object(client, "_probe_gateway_api_health", return_value={"ok": True, "error_code": "ok", "summary": "ok"}),
            ):
                diag = await client.get_cloud_provider_diagnostics(["google", "openai"])

        self.assertFalse(diag["ok"])
        self.assertEqual(diag["providers"]["google"]["error_code"], "api_key_leaked")
        self.assertFalse(diag["providers"]["google"]["retryable"])
        self.assertTrue(diag["providers"]["openai"]["ok"])

    async def test_health_check_rejects_html_payload(self):
        """health_check не должен считать HTML-страницу валидным API health."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        with (
            patch.dict("os.environ", {"OPENCLAW_HEALTH_CLI_FALLBACK": "0"}, clear=False),
            patch.object(
                client,
                "_request_json",
                return_value={"ok": True, "status": 200, "data": {"raw": "<!doctype html><html>ui</html>"}},
            ),
        ):
            result = await client.health_check()
        self.assertFalse(result)

    async def test_health_check_accepts_html_payload_when_cli_probe_ok(self):
        """Если HTTP отдаёт HTML, но CLI probe подтверждает runtime, health_check должен вернуть True."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        with (
            patch.object(
                client,
                "_request_json",
                return_value={"ok": True, "status": 200, "data": {"raw": "<!doctype html><html>ui</html>"}},
            ),
            patch(
                "src.core.openclaw_client.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["openclaw", "channels", "status", "--probe"],
                    returncode=0,
                    stdout="Gateway reachable.\n",
                    stderr="",
                ),
            ),
        ):
            result = await client.health_check()
        self.assertTrue(result)

    async def test_health_check_rejects_html_payload_when_cli_probe_failed(self):
        """Если HTTP отдаёт HTML и CLI probe не подтвердил runtime, health_check должен вернуть False."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        with (
            patch.object(
                client,
                "_request_json",
                return_value={"ok": True, "status": 200, "data": {"raw": "<!doctype html><html>ui</html>"}},
            ),
            patch(
                "src.core.openclaw_client.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["openclaw", "channels", "status", "--probe"],
                    returncode=1,
                    stdout="Gateway unreachable.\n",
                    stderr="",
                ),
            ),
        ):
            result = await client.health_check()
        self.assertFalse(result)

    async def test_health_check_accepts_models_fallback_when_health_sparse(self):
        """Если /health «пустой», но /v1/models валиден — health_check возвращает True."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")

        async def fake_request(method: str, path: str, payload=None, timeout=15):
            if path == "/health":
                return {"ok": True, "status": 200, "data": {}}
            if path == "/v1/models":
                return {"ok": True, "status": 200, "data": {"data": [{"id": "google/gemini-2.5-flash"}]}}
            return {"ok": False, "status": 404, "data": {}}

        with patch.object(client, "_request_json", side_effect=fake_request):
            result = await client.health_check()
        self.assertTrue(result)

    async def test_cloud_provider_diagnostics_marks_gateway_api_unavailable(self):
        """Даже при валидном ключе диагностика должна падать, если gateway API нерабочий."""
        client = OpenClawClient(base_url="http://localhost:18789", api_key="")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "gm-test-1234"}, clear=False):
            with (
                patch.object(client, "_probe_provider_health_hint", return_value=None),
                patch.object(
                    client,
                    "_probe_gateway_api_health",
                    return_value={
                        "ok": False,
                        "error_code": "gateway_api_unavailable",
                        "summary": "gateway вернул HTML вместо JSON API",
                        "retryable": False,
                    },
                ),
            ):
                diag = await client.get_cloud_provider_diagnostics(["google"])

        self.assertFalse(diag["ok"])
        self.assertEqual(diag["providers"]["google"]["error_code"], "gateway_api_unavailable")
        self.assertFalse(diag["providers"]["google"]["ok"])

    async def test_get_auth_provider_health(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["path"], "/auth/providers/health")
        self.assertEqual(payload["status"], 200)
        self.assertIn("openai-codex", payload["payload"]["providers"])
        self.assertTrue(payload["ready_for_subscriptions"])
        self.assertEqual(payload["missing_required"], [])
        self.assertEqual(payload["unhealthy_required"], [])
        self.assertTrue(payload["providers"]["openai-codex"]["healthy"])
        self.assertEqual(payload["status_reason"], "ok")

    async def test_get_tools_overview_count(self):
        client = FakeOpenClawClient()
        payload = await client.get_tools_overview()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["tools_count"], 2)

    async def test_get_health_report_aggregates_sections(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_health_report()
        self.assertTrue(report["gateway"])
        self.assertTrue(report["auth"]["available"])
        self.assertTrue(report["browser"]["available"])
        self.assertTrue(report["tools"]["available"])
        self.assertTrue(report["ready_for_subscriptions"])

    async def test_get_auth_provider_health_detects_missing_required(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli,qwen-portal-auth"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertIn("qwen-portal-auth", payload["missing_required"])
        self.assertFalse(payload["ready_for_subscriptions"])

    async def test_get_auth_provider_health_marks_route_unavailable_for_html_payload(self):
        client = FakeHtmlAuthClient()
        payload = await client.get_auth_provider_health()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["status_reason"], "gateway_route_unavailable")

    async def test_get_auth_provider_health_detects_missing_lmstudio_profile(self):
        client = FakeMissingLmstudioProfileClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            payload = await client.get_auth_provider_health()
        self.assertFalse(payload["ready_for_subscriptions"])
        self.assertEqual(payload["status_reason"], "auth_missing_lmstudio_profile")

    async def test_get_deep_health_report_ready(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_deep_health_report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["tool_smoke"]["ok"])

    async def test_get_remediation_plan_no_issues(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {
                "OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli",
                "OPENCLAW_API_KEY": "sk-test",
            },
            clear=False,
        ):
            plan = await client.get_remediation_plan()
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["open_items"], 0)
        self.assertEqual(plan["steps"][0]["id"], "no_action_needed")

    async def test_get_remediation_plan_with_missing_provider(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {
                "OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli,qwen-portal-auth",
                "OPENCLAW_API_KEY": "sk-test",
            },
            clear=False,
        ):
            plan = await client.get_remediation_plan()
        self.assertFalse(plan["ready"])
        self.assertGreater(plan["open_items"], 0)
        step_ids = {item["id"] for item in plan["steps"]}
        self.assertIn("enable_provider_qwen-portal-auth", step_ids)

    async def test_run_browser_smoke_via_endpoint(self):
        client = FakeOpenClawClient()
        smoke = await client.run_browser_smoke(url="https://example.com")
        self.assertTrue(smoke["ok"])
        self.assertEqual(smoke["channel"], "endpoint")
        self.assertIn("endpoint_attempts", smoke)

    async def test_run_browser_smoke_via_tool_fallback(self):
        client = FakeBrowserToolFallbackClient()
        smoke = await client.run_browser_smoke(url="https://example.com")
        self.assertTrue(smoke["ok"])
        self.assertEqual(smoke["channel"], "tool")
        self.assertEqual(smoke["tool"], "web_fetch")

    async def test_get_browser_smoke_report(self):
        client = FakeOpenClawClient()
        with patch.dict(
            "os.environ",
            {"OPENCLAW_REQUIRED_AUTH_PROVIDERS": "openai-codex,google-gemini-cli"},
            clear=False,
        ):
            report = await client.get_browser_smoke_report(url="https://example.com")
        self.assertIn("base", report)
        self.assertIn("browser_smoke", report)
        self.assertTrue(report["browser_smoke"]["ok"])

    async def test_execute_agent_task_deep_fetches_pages_for_top_sources(self):
        client = FakeResearchWorkflowClient(
            {
                "details": {
                    "results": [
                        {"title": "A", "url": "https://a.example", "description": "desc-a", "published": "2026-02-18"},
                        {"title": "B", "url": "https://b.example", "description": "desc-b", "published": "2026-02-18"},
                        {"title": "C", "url": "https://c.example", "description": "desc-c", "published": "2026-02-18"},
                    ]
                }
            }
        )
        result = await client.execute_agent_task("проверка deep режима", agent_id="deep")
        self.assertEqual(result, "REPORT_OK")
        fetch_calls = [call for call in client.invocations if call[0] == "web_fetch"]
        self.assertEqual(len(fetch_calls), 2)  # deep-профиль ограничен двумя fetch-запросами
        prompt = client.last_messages[1]["content"]
        self.assertIn("Подробности страницы", prompt)
        self.assertIn("[A](https://a.example)", prompt)

    async def test_execute_agent_task_parses_results_from_content_wrapper(self):
        client = FakeResearchWorkflowClient(
            {
                "content": [
                    {
                        "text": (
                            "{\"results\": [{\"title\": \"Wrapped\", \"url\": \"https://wrapped.example\", "
                            "\"description\": \"wrapped-desc\", \"published\": \"2026-02-18\"}]}"
                        )
                    }
                ]
            }
        )
        result = await client.execute_agent_task("обернутый ответ web_search", agent_id="research_fast")
        self.assertEqual(result, "REPORT_OK")
        prompt = client.last_messages[1]["content"]
        self.assertIn("[Wrapped](https://wrapped.example)", prompt)

    async def test_set_tier_paid_syncs_openclaw_google_key_and_restarts_gateway(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "openclaw.json"
            agent_models_path = root / "models.json"
            seed = {
                "models": {
                    "providers": {
                        "google": {"apiKey": "free-key"},
                    }
                }
            }
            config_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
            agent_models_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")

            with patch.dict(
                "os.environ",
                {
                    "GEMINI_API_KEY_FREE": "free-key",
                    "GEMINI_API_KEY_PAID": "paid-key",
                    "OPENCLAW_CONFIG_PATH": str(config_path),
                    "OPENCLAW_AGENT_MODELS_PATH": str(agent_models_path),
                    "OPENCLAW_TIER_SYNC_PAID_KEY": "1",
                    "OPENCLAW_TIER_SYNC_RESTART_GATEWAY": "1",
                    "OPENCLAW_TIER_SYNC_COOLDOWN_SEC": "5",
                },
                clear=False,
            ):
                client = OpenClawClient(base_url="http://localhost:18789", api_key="")
                with patch(
                    "src.core.openclaw_client.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["openclaw", "gateway", "restart"],
                        returncode=0,
                        stdout="ok",
                        stderr="",
                    ),
                ) as restart_mock:
                    switched = client.set_tier("paid")

            self.assertTrue(switched)
            self.assertTrue(client._paid_tier_applied)  # type: ignore[attr-defined]
            self.assertEqual(client.active_tier, "paid")
            self.assertEqual(restart_mock.call_count, 1)
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8"))["models"]["providers"]["google"]["apiKey"],
                "paid-key",
            )
            self.assertEqual(
                json.loads(agent_models_path.read_text(encoding="utf-8"))["models"]["providers"]["google"]["apiKey"],
                "paid-key",
            )

    async def test_set_tier_free_does_not_restart_gateway(self):
        with patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY_FREE": "free-key",
                "GEMINI_API_KEY_PAID": "paid-key",
            },
            clear=False,
        ):
            client = OpenClawClient(base_url="http://localhost:18789", api_key="")
            with patch("src.core.openclaw_client.subprocess.run") as restart_mock:
                switched = client.set_tier("free")
        self.assertTrue(switched)
        restart_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

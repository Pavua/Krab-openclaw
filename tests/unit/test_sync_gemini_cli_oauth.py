"""
Тесты repair-сценария Gemini CLI OAuth -> OpenClaw.

Зачем:
- не допустить регресс, при котором sync снова пишет неполный профиль без
  `projectId` и оставляет stale `usageStats`;
- отдельно проверить, что `loadCodeAssist` уходит с платформой
  `PLATFORM_UNSPECIFIED`, а не с ломаным `MACOS`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

try:
    from scripts import sync_gemini_cli_oauth as module
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    pytest.skip("scripts.sync_gemini_cli_oauth not available", allow_module_level=True)


class _FakeResponse:
    """Минимальный HTTP-ответ для тестов urllib."""

    def __init__(self, payload: dict[str, object], status: int = 200):
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_sync_openclaw_auth_store_writes_project_and_clears_usage(tmp_path: Path) -> None:
    """Sync должен писать projectId/email и очищать stale usageStats."""
    auth_store = tmp_path / "auth-profiles.json"
    auth_store.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "google-gemini-cli:default": {
                        "type": "oauth",
                        "provider": "google-gemini-cli",
                        "access": "old-access",
                        "refresh": "old-refresh",
                        "expires": 1,
                    }
                },
                "usageStats": {
                    "google-gemini-cli:default": {
                        "errorCount": 2,
                        "failureCounts": {"auth": 2},
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = module.sync_openclaw_auth_store(
        auth_store_path=auth_store,
        refreshed=module.RefreshedOAuth(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at_ms=1234567890000,
            scope="Bearer scope",
        ),
        project=module.CodeAssistProject(
            project_id="custom-ridge-75d52",
            endpoint="https://cloudcode-pa.googleapis.com",
            current_tier="standard-tier",
        ),
        email="pavelr7@gmail.com",
    )

    payload = json.loads(auth_store.read_text(encoding="utf-8"))
    profile = payload["profiles"]["google-gemini-cli:default"]

    assert profile["access"] == "new-access"
    assert profile["refresh"] == "new-refresh"
    assert profile["expires"] == 1234567890000
    assert profile["projectId"] == "custom-ridge-75d52"
    assert profile["email"] == "pavelr7@gmail.com"
    assert payload["lastGood"]["google-gemini-cli"] == "google-gemini-cli:default"
    assert "google-gemini-cli:default" not in payload["usageStats"]
    assert result["project_id"] == "custom-ridge-75d52"


def test_discover_code_assist_project_uses_platform_unspecified() -> None:
    """Handshake обязан слать PLATFORM_UNSPECIFIED, иначе Google вернёт 400."""

    def fake_urlopen(request, timeout=20):  # noqa: ANN001
        payload = json.loads(request.data.decode("utf-8"))
        metadata = payload["metadata"]
        if metadata["platform"] != "PLATFORM_UNSPECIFIED":
            raise AssertionError(metadata)
        return _FakeResponse(
            {
                "currentTier": {"id": "standard-tier"},
                "cloudaicompanionProject": "custom-ridge-75d52",
            }
        )

    result = module.discover_code_assist_project(
        access_token="token",
        opener=fake_urlopen,
    )

    assert result.project_id == "custom-ridge-75d52"
    assert result.current_tier == "standard-tier"


def test_extract_gemini_cli_client_credentials_prefers_env(monkeypatch) -> None:
    """Если override уже задан в env, читать oauth2.js не нужно."""
    monkeypatch.setenv("OPENCLAW_GEMINI_OAUTH_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setenv("OPENCLAW_GEMINI_OAUTH_CLIENT_SECRET", "GOCSPX-secret")

    client_id, client_secret = module.extract_gemini_cli_client_credentials()

    assert client_id == "client-id.apps.googleusercontent.com"
    assert client_secret == "GOCSPX-secret"


def test_urlopen_json_surfaces_http_error_body() -> None:
    """HTTP body должен попасть в понятную ошибку repair-скрипта."""
    request = urllib.request.Request("https://example.test")

    def failing_opener(_request, timeout=20):  # noqa: ANN001
        raise urllib.error.HTTPError(
            url="https://example.test",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )

    try:
        module._urlopen_json(request, opener=failing_opener)
    except module.GeminiOauthSyncError as exc:
        assert "HTTP 400" in str(exc)
    else:  # pragma: no cover - защитная ветка
        raise AssertionError("Ожидали GeminiOauthSyncError")

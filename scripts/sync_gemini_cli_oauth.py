"""
Синхронизатор Gemini CLI OAuth -> OpenClaw auth-profiles.

Зачем нужен:
- официальный plugin `google-gemini-cli-auth` в текущем OpenClaw может
  оказаться несовместимым с Google Code Assist handshake;
- сам `gemini` CLI уже умеет хранить OAuth в `~/.gemini/oauth_creds.json`;
- этот скрипт безопасно перечитывает существующий Gemini CLI OAuth, обновляет
  access token через refresh token, подтверждает `projectId` через
  `loadCodeAssist` и синхронизирует профиль в `~/.openclaw/.../auth-profiles.json`.

Связь с проектом:
- используется из `Login Gemini CLI OAuth.command` как основной repair-path;
- не хранит в репозитории client secrets и не invent'ит отдельный OAuth flow;
- опирается на уже установленный `gemini` CLI и на живой runtime OpenClaw.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
GOOGLE_CODE_ASSIST_ENDPOINTS = (
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
)
GOOGLE_CODE_ASSIST_METADATA = {
    "ideType": "ANTIGRAVITY",
    # В марте 2026 Google Code Assist стабильно принимает именно
    # PLATFORM_UNSPECIFIED; значение MACOS даёт INVALID_ARGUMENT.
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI",
}
GOOGLE_CLIENT_ID_ENV_KEYS = (
    "OPENCLAW_GEMINI_OAUTH_CLIENT_ID",
    "GEMINI_CLI_OAUTH_CLIENT_ID",
)
GOOGLE_CLIENT_SECRET_ENV_KEYS = (
    "OPENCLAW_GEMINI_OAUTH_CLIENT_SECRET",
    "GEMINI_CLI_OAUTH_CLIENT_SECRET",
)


class GeminiOauthSyncError(RuntimeError):
    """Поднимается, когда repair-path нельзя завершить безопасно."""


@dataclass(slots=True)
class GeminiCliCredentials:
    """Минимальные OAuth-данные Gemini CLI, которых хватает для sync-flow."""

    access_token: str
    refresh_token: str
    expiry_date: int


@dataclass(slots=True)
class RefreshedOAuth:
    """Результат refresh-токена без лишних секретов в логах."""

    access_token: str
    refresh_token: str
    expires_at_ms: int
    scope: str


@dataclass(slots=True)
class CodeAssistProject:
    """Подтверждённый project/tier из Google Code Assist."""

    project_id: str
    endpoint: str
    current_tier: str


def _env_first(keys: tuple[str, ...]) -> str:
    """Возвращает первое непустое значение env-переменной."""
    for key in keys:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return ""


def _mask_secret(value: str) -> str:
    """Короткая безопасная маска для stdout/json отчёта."""
    raw = str(value or "").strip()
    if len(raw) <= 8:
        return raw[:2] + "..." if raw else ""
    return f"{raw[:4]}...{raw[-4:]}"


def _gemini_oauth_store_path() -> Path:
    """Файл, где установленный Gemini CLI хранит OAuth."""
    return Path.home() / ".gemini" / "oauth_creds.json"


def _openclaw_auth_store_path() -> Path:
    """Канонический auth store агента main."""
    return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"


def _read_json(path: Path) -> dict[str, Any]:
    """Читает JSON-файл и валидирует, что корень — объект."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GeminiOauthSyncError(f"Файл не найден: {path}") from exc
    except (OSError, ValueError) as exc:
        raise GeminiOauthSyncError(f"Не удалось прочитать JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise GeminiOauthSyncError(f"Ожидался JSON-объект: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Пишет JSON атомарно, чтобы не оставлять битый auth store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def load_gemini_cli_credentials(path: Path | None = None) -> GeminiCliCredentials:
    """Загружает access/refresh/expiry из Gemini CLI store."""
    payload = _read_json(path or _gemini_oauth_store_path())
    access_token = str(payload.get("access_token", "") or "").strip()
    refresh_token = str(payload.get("refresh_token", "") or "").strip()
    expiry_date = int(payload.get("expiry_date", 0) or 0)
    if not access_token or not refresh_token or expiry_date <= 0:
        raise GeminiOauthSyncError("В ~/.gemini/oauth_creds.json нет полного OAuth набора.")
    return GeminiCliCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expiry_date=expiry_date,
    )


def _locate_gemini_cli_oauth_js() -> Path:
    """Ищет bundled `oauth2.js` у установленного `gemini` CLI."""
    gemini_bin = shutil.which("gemini")
    if not gemini_bin:
        raise GeminiOauthSyncError("Команда `gemini` не найдена в PATH.")

    resolved = Path(gemini_bin).resolve()
    search_root = resolved.parent.parent
    direct_candidate = (
        search_root
        / "node_modules"
        / "@google"
        / "gemini-cli-core"
        / "dist"
        / "src"
        / "code_assist"
        / "oauth2.js"
    )
    if direct_candidate.exists():
        return direct_candidate

    for candidate in search_root.rglob("oauth2.js"):
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if ".apps.googleusercontent.com" in content and "GOCSPX-" in content:
            return candidate

    raise GeminiOauthSyncError("Не удалось найти bundled Gemini CLI oauth2.js.")


def extract_gemini_cli_client_credentials(oauth_js_path: Path | None = None) -> tuple[str, str]:
    """Достаёт client_id/client_secret из env override или установленного CLI."""
    env_client_id = _env_first(GOOGLE_CLIENT_ID_ENV_KEYS)
    env_client_secret = _env_first(GOOGLE_CLIENT_SECRET_ENV_KEYS)
    if env_client_id and env_client_secret:
        return env_client_id, env_client_secret

    source_path = oauth_js_path or _locate_gemini_cli_oauth_js()
    try:
        content = source_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise GeminiOauthSyncError(f"Не удалось прочитать {source_path}") from exc

    client_id_match = re.search(r"(\d+-[a-z0-9]+\.apps\.googleusercontent\.com)", content)
    client_secret_match = re.search(r"(GOCSPX-[A-Za-z0-9_-]+)", content)
    if not client_id_match or not client_secret_match:
        raise GeminiOauthSyncError("Не удалось извлечь client_id/client_secret из Gemini CLI.")
    return client_id_match.group(1), client_secret_match.group(1)


def _urlopen_json(
    request: urllib.request.Request,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = 20,
) -> dict[str, Any]:
    """Выполняет HTTP-запрос и возвращает JSON, поднимая понятную ошибку."""
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = exc.reason if getattr(exc, "reason", None) else exc.msg
        raise GeminiOauthSyncError(f"HTTP {exc.code} для {request.full_url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise GeminiOauthSyncError(f"Сетевая ошибка для {request.full_url}: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise GeminiOauthSyncError(f"Невалидный ответ от {request.full_url}") from exc
    if not isinstance(payload, dict):
        raise GeminiOauthSyncError(f"Ожидался JSON-объект от {request.full_url}")
    return payload


def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> RefreshedOAuth:
    """Обновляет access token через штатный Google refresh flow."""
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": "google-api-nodejs-client/9.15.1",
        },
        method="POST",
    )
    payload = _urlopen_json(request, opener=opener)
    access_token = str(payload.get("access_token", "") or "").strip()
    expires_in = int(payload.get("expires_in", 0) or 0)
    token_type = str(payload.get("token_type", "") or "").strip()
    scope = str(payload.get("scope", "") or "").strip()
    new_refresh = str(payload.get("refresh_token", "") or "").strip() or refresh_token
    if not access_token or expires_in <= 0:
        raise GeminiOauthSyncError("Google refresh ответил без access_token/expires_in.")
    # Оставляем небольшой буфер, чтобы UI/runtime не видели near-expiry токен.
    expires_at_ms = int(time.time() * 1000) + max(0, expires_in - 300) * 1000
    return RefreshedOAuth(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_at_ms=expires_at_ms,
        scope=f"{token_type} {scope}".strip(),
    )


def get_user_email(
    *,
    access_token: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    """Пытается достать email аккаунта для более информативного профиля."""
    request = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    payload = _urlopen_json(request, opener=opener)
    return str(payload.get("email", "") or "").strip()


def discover_code_assist_project(
    *,
    access_token: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
    env_project: str | None = None,
) -> CodeAssistProject:
    """Подтверждает project/tier у Google Code Assist через `loadCodeAssist`."""
    env_project_id = str(env_project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID") or "").strip()
    body_payload: dict[str, Any] = {
        "metadata": dict(GOOGLE_CODE_ASSIST_METADATA),
    }
    if env_project_id:
        body_payload["cloudaicompanionProject"] = env_project_id
        body_payload["metadata"]["duetProject"] = env_project_id
    body = json.dumps(body_payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "gl-node/openclaw-sync",
        "Client-Metadata": json.dumps(GOOGLE_CODE_ASSIST_METADATA),
    }
    last_error: GeminiOauthSyncError | None = None
    for endpoint in GOOGLE_CODE_ASSIST_ENDPOINTS:
        request = urllib.request.Request(
            f"{endpoint}/v1internal:loadCodeAssist",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            payload = _urlopen_json(request, opener=opener)
        except GeminiOauthSyncError as exc:
            last_error = exc
            continue

        current_tier = str(((payload.get("currentTier") or {}) if isinstance(payload.get("currentTier"), dict) else {}).get("id", "") or "").strip()
        project_raw = payload.get("cloudaicompanionProject")
        if isinstance(project_raw, dict):
            project_id = str(project_raw.get("id", "") or "").strip()
        else:
            project_id = str(project_raw or "").strip()
        if project_id:
            return CodeAssistProject(
                project_id=project_id,
                endpoint=endpoint,
                current_tier=current_tier or "unknown",
            )
        if env_project_id:
            return CodeAssistProject(
                project_id=env_project_id,
                endpoint=endpoint,
                current_tier=current_tier or "unknown",
            )
        raise GeminiOauthSyncError(
            f"Google Code Assist не вернул projectId на {endpoint}."
        )

    if last_error:
        raise last_error
    raise GeminiOauthSyncError("Не удалось подтвердить projectId через loadCodeAssist.")


def sync_openclaw_auth_store(
    *,
    auth_store_path: Path,
    refreshed: RefreshedOAuth,
    project: CodeAssistProject,
    email: str = "",
) -> dict[str, Any]:
    """Обновляет OpenClaw auth-profiles.json для `google-gemini-cli`."""
    if auth_store_path.exists():
        payload = _read_json(auth_store_path)
    else:
        payload = {"version": 1}

    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        payload["profiles"] = profiles

    last_good = payload.get("lastGood")
    if not isinstance(last_good, dict):
        last_good = {}
        payload["lastGood"] = last_good

    usage_stats = payload.get("usageStats")
    if not isinstance(usage_stats, dict):
        usage_stats = {}
        payload["usageStats"] = usage_stats

    profiles["google-gemini-cli:default"] = {
        "type": "oauth",
        "provider": "google-gemini-cli",
        "access": refreshed.access_token,
        "refresh": refreshed.refresh_token,
        "expires": refreshed.expires_at_ms,
        "projectId": project.project_id,
        **({"email": email} if email else {}),
    }
    last_good["google-gemini-cli"] = "google-gemini-cli:default"
    usage_stats.pop("google-gemini-cli:default", None)

    _write_json_atomic(auth_store_path, payload)
    return {
        "profile": "google-gemini-cli:default",
        "provider": "google-gemini-cli",
        "project_id": project.project_id,
        "tier": project.current_tier,
        "endpoint": project.endpoint,
        "expires_at_ms": refreshed.expires_at_ms,
        "email": email,
    }


def run_sync() -> dict[str, Any]:
    """Полный refresh+sync сценарий без ручного browser flow."""
    gemini_creds = load_gemini_cli_credentials()
    client_id, client_secret = extract_gemini_cli_client_credentials()
    refreshed = refresh_access_token(
        refresh_token=gemini_creds.refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )
    project = discover_code_assist_project(access_token=refreshed.access_token)
    email = ""
    try:
        email = get_user_email(access_token=refreshed.access_token)
    except GeminiOauthSyncError:
        # Email полезен для диагностики, но отсутствие email не должно ломать repair.
        email = ""
    result = sync_openclaw_auth_store(
        auth_store_path=_openclaw_auth_store_path(),
        refreshed=refreshed,
        project=project,
        email=email,
    )
    result.update(
        {
            "auth_store": str(_openclaw_auth_store_path()),
            "gemini_store": str(_gemini_oauth_store_path()),
            "client_id_masked": _mask_secret(client_id),
            "access_token_masked": _mask_secret(refreshed.access_token),
        }
    )
    return result


def main() -> int:
    """CLI-вход: печатает JSON-отчёт для `.command` и smoke-проверок."""
    try:
        result = run_sync()
    except GeminiOauthSyncError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""
Web App Module (Phase 15+).
Сервер для Dashboard и web-управления экосистемой Krab.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import structlog
import uvicorn
from fastapi import Body, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from src.config import config  # noqa: E402
from src.core.access_control import (  # noqa: E402
    PARTIAL_ACCESS_COMMANDS,
    load_acl_runtime_state,
    update_acl_subject,
)
from src.core.ecosystem_health import EcosystemHealthService  # noqa: E402
from src.core.inbox_service import inbox_service  # noqa: E402
from src.core.lm_studio_auth import build_lm_studio_auth_headers  # noqa: E402
from src.core.mcp_registry import (  # noqa: E402
    LMSTUDIO_MCP_PATH,
    build_lmstudio_mcp_json,
    get_managed_mcp_servers,
    resolve_managed_server_launch,
)
from src.core.model_aliases import (  # noqa: E402
    MODEL_FRIENDLY_ALIASES,
    normalize_model_alias,
    parse_model_set_request,
    render_model_presets_text,
)
from src.core.openclaw_runtime_signal_truth import (  # noqa: E402
    discover_gateway_signal_log,
    runtime_auth_failed_providers_from_signal_log,
)
from src.core.observability import (  # noqa: E402
    build_ops_response,
    get_observability_snapshot,
    metrics,
    timeline,
)

logger = structlog.get_logger("WebApp")


class WebApp:
    """Web-панель Krab с API статуса экосистемы."""

    def __init__(self, deps: dict, port: int = 8000, host: str = "0.0.0.0"):
        self.app = FastAPI(title="Krab Web Panel", version="v8")
        self.deps = deps
        self.port = int(port)
        self.host = host
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._web_root = Path(__file__).resolve().parents[1] / "web"
        self._index_path = self._web_root / "index.html"
        self._nano_theme_path = self._web_root / "prototypes" / "nano" / "nano_theme.css"
        self._assistant_rate_state: dict[str, list[float]] = {}
        self._idempotency_state: dict[str, tuple[float, dict]] = {}
        # Короткий runtime-cache LM Studio snapshot.
        # Нужен, чтобы пачка одновременных `/stats`, `/health/lite`,
        # `/model/local/status` не превращалась в шквал одинаковых GET /models.
        self._lmstudio_snapshot_cache: tuple[float, dict[str, Any]] | None = None
        self._lmstudio_snapshot_lock = asyncio.Lock()
        # Отдельный короткий cache для всего runtime-lite snapshot.
        # Он режет повторные `health/lite` вызовы, которые сами по себе могут быть
        # частыми из UI/watchdog, но не должны каждый раз заново собирать local truth.
        self._runtime_lite_cache: tuple[float, dict[str, Any]] | None = None
        self._runtime_lite_lock = asyncio.Lock()
        self._setup_routes()

    def _public_base_url(self) -> str:
        """Возвращает внешний base URL панели."""
        explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if explicit:
            return explicit
        display_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
        return f"http://{display_host}:{self.port}"

    def _web_api_key(self) -> str:
        """Возвращает API-ключ web write-endpoints (может быть пустым)."""
        return os.getenv("WEB_API_KEY", "").strip()

    def _assert_write_access(self, header_key: str, token: str) -> None:
        """Проверяет доступ к write-эндпоинтам web API."""
        expected = self._web_api_key()
        if not expected:
            return

        provided = (header_key or "").strip() or (token or "").strip()
        if provided != expected:
            raise HTTPException(status_code=403, detail="forbidden: invalid WEB_API_KEY")

    @staticmethod
    def _project_root() -> Path:
        """Возвращает корень проекта Krab."""
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _tail_text(text: str, max_chars: int = 2000) -> str:
        """Возвращает хвост текста с ограничением длины."""
        payload = str(text or "")
        if len(payload) <= max_chars:
            return payload
        return payload[-max_chars:]

    @staticmethod
    def _mask_secret(value: str) -> str:
        """Маскирует секрет для UI/логов: видны только префикс и суффикс."""
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= 6:
            return "*" * len(text)
        return f"{text[:3]}...{text[-3:]}"

    @staticmethod
    def _openclaw_gateway_token_from_config() -> str:
        """Читает gateway auth token из ~/.openclaw/openclaw.json (если доступен)."""
        cfg_path = Path.home() / ".openclaw" / "openclaw.json"
        try:
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        gateway = payload.get("gateway")
        if not isinstance(gateway, dict):
            return ""
        auth = gateway.get("auth")
        if not isinstance(auth, dict):
            return ""
        token = str(auth.get("token") or "").strip()
        return token

    @classmethod
    def _openclaw_gateway_auth_headers(cls) -> dict[str, str]:
        """
        Возвращает auth headers для прямых HTTP-проб OpenClaw gateway/browser relay.

        Почему это отдельный helper:
        - browser relay на `:18791` защищён тем же gateway token;
        - без заголовка получаем ложный `401 auth_required`, хотя relay и dedicated browser
          уже могут быть полностью живы;
        - web-панель должна проверять runtime truth в том же auth-контуре, что и CLI.
        """
        token = cls._openclaw_gateway_token_from_config()
        if not token:
            token = str(os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or "").strip()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _openclaw_models_config_path() -> Path:
        """Путь к runtime source-of-truth моделей OpenClaw."""
        return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"

    @staticmethod
    def _openclaw_config_path() -> Path:
        """Путь к основному runtime-конфигу OpenClaw."""
        return Path.home() / ".openclaw" / "openclaw.json"

    @staticmethod
    def _openclaw_auth_profiles_path() -> Path:
        """Путь к auth-profiles OpenClaw."""
        return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"

    @classmethod
    def _openclaw_agent_config_path(cls) -> Path:
        """Путь к main agent.json OpenClaw, который тоже должен идти в ногу с primary."""
        return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "agent.json"

    @staticmethod
    def _json_backup_path(path: Path, *, label: str) -> Path:
        """Формирует timestamp backup path рядом с исходным JSON-файлом."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        safe_label = re.sub(r"[^a-z0-9_-]+", "_", str(label or "backup").strip().lower()) or "backup"
        return path.with_suffix(path.suffix + f".bak_{safe_label}_{stamp}")

    @classmethod
    def _backup_json_file(cls, path: Path, *, label: str) -> str:
        """Создаёт backup JSON-конфига перед записью, чтобы откат был тривиальным."""
        if not path.exists():
            return ""
        backup_path = cls._json_backup_path(path, label=label)
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return str(backup_path)

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        """Пишет JSON детерминированно и с финальным переводом строки."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_runtime_model_id(raw_model: Any) -> tuple[str, str]:
        """Нормализует model id/alias до canonical runtime-id."""
        raw = str(raw_model or "").strip()
        if not raw:
            return "", ""
        resolved_model, alias_note = normalize_model_alias(raw)
        canonical = str(resolved_model or "").strip()
        return canonical, str(alias_note or "").strip()

    @staticmethod
    def _normalize_thinking_mode(raw_value: Any, *, allow_blank: bool = False) -> str:
        """Ограничивает thinking к набору режимов, которые уже используются в runtime."""
        normalized = str(raw_value or "").strip().lower()
        if not normalized and allow_blank:
            return ""
        allowed = {"off", "auto", "low", "medium", "high"}
        if normalized not in allowed:
            raise ValueError("runtime_invalid_thinking_mode")
        return normalized

    @staticmethod
    def _normalize_context_tokens(raw_value: Any) -> int:
        """Проверяет contextTokens, чтобы в runtime не уехали мусорные значения."""
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime_invalid_context_tokens") from exc
        if value < 4096 or value > 2_000_000:
            raise ValueError("runtime_invalid_context_tokens")
        return value

    @classmethod
    def _chrome_remote_debugging_helper_path(cls) -> Path:
        """Путь к существующему macOS helper для owner Chrome attach."""
        return cls._project_root() / "new Enable Chrome Remote Debugging.command"

    @classmethod
    def _launch_owner_chrome_remote_debugging(cls) -> dict[str, Any]:
        """
        Открывает owner Chrome flow через существующий проектный helper.

        Почему так:
        - в репозитории уже есть `.command` с понятной инструкцией для пользователя;
        - owner UI должен запускать тот же сценарий, а не дублировать новый launcher;
        - если helper отсутствует, деградируем в прямое открытие `chrome://inspect`.
        """
        helper_path = cls._chrome_remote_debugging_helper_path()
        inspect_url = "chrome://inspect/#remote-debugging"

        try:
            if helper_path.exists():
                subprocess.Popen(
                    ["open", str(helper_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {
                    "ok": True,
                    "launcher": "command",
                    "helper_path": str(helper_path),
                    "opened_url": inspect_url,
                    "next_step": "В обычном Chrome включи Remote Debugging и оставь профиль открытым.",
                }

            chrome_app = "/Applications/Google Chrome.app"
            open_target = chrome_app if Path(chrome_app).exists() else "Google Chrome"
            subprocess.Popen(
                ["open", "-a", open_target, inspect_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {
                "ok": True,
                "launcher": "direct_open",
                "helper_path": str(helper_path),
                "opened_url": inspect_url,
                "next_step": "В обычном Chrome включи Remote Debugging и затем обнови Browser / MCP Readiness.",
            }
        except OSError as exc:
            return {
                "ok": False,
                "error": "chrome_remote_debugging_open_failed",
                "detail": str(exc),
                "helper_path": str(helper_path),
                "opened_url": inspect_url,
            }

    @classmethod
    def _load_openclaw_runtime_config(cls) -> dict[str, Any]:
        """Читает runtime-конфиг OpenClaw; при ошибке возвращает пустой payload."""
        path = cls._openclaw_config_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    @classmethod
    def _load_openclaw_auth_profiles(cls) -> dict[str, Any]:
        """Читает auth-profiles OpenClaw; при ошибке возвращает пустой payload."""
        path = cls._openclaw_auth_profiles_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    @classmethod
    def _load_openclaw_runtime_models(cls) -> dict[str, Any]:
        """Читает runtime-модели OpenClaw; при ошибке возвращает пустой payload."""
        path = cls._openclaw_models_config_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"providers": {}}

    @classmethod
    def _load_openclaw_agent_config(cls) -> dict[str, Any]:
        """Читает main agent.json OpenClaw; при ошибке возвращает пустой payload."""
        path = cls._openclaw_agent_config_path()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _provider_label(provider_name: str) -> str:
        """Человекочитаемый label провайдера для model catalog."""
        normalized = str(provider_name or "").strip().lower()
        labels = {
            "google": "Google",
            "google-antigravity": "Google OAuth (legacy)",
            "google-gemini-cli": "Gemini CLI OAuth",
            "openai": "OpenAI",
            "openai-codex": "OpenAI Codex",
            "lmstudio": "LM Studio",
            "github-copilot": "GitHub Copilot",
            "qwen-portal": "Qwen Portal",
        }
        return labels.get(normalized, normalized or "provider")

    @classmethod
    def _provider_sort_rank(cls, provider_name: str) -> tuple[int, str]:
        """Стабильный порядок провайдеров для owner UI."""
        normalized = str(provider_name or "").strip().lower()
        order = {
            "google-gemini-cli": 10,
            "google": 20,
            "qwen-portal": 30,
            "openai-codex": 40,
            "openai": 50,
            "github-copilot": 60,
            "google-antigravity": 90,
        }
        return (order.get(normalized, 500), normalized)

    @staticmethod
    def _friendly_model_name(model_id: str, raw_name: str = "") -> str:
        """Превращает сырой model id в понятное пользовательское имя."""
        candidate_id = str(model_id or "").strip()
        candidate_name = str(raw_name or "").strip()
        tail = candidate_id.split("/", 1)[-1] if "/" in candidate_id else candidate_id
        lowered_tail = tail.lower()

        overrides = {
            "gpt-5.4": "GPT-5.4",
            "gpt-4.5-preview": "GPT-4.5 Preview",
            "gpt-4o-mini": "GPT-4o Mini",
            "coder-model": "Qwen Coder",
            "vision-model": "Qwen Vision",
            "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
            "gemini-3-pro-preview": "Gemini 3 Pro Preview",
            "gemini-pro-latest": "Gemini Pro Latest",
            "gemini-1.5-pro": "Gemini 1.5 Pro",
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
            "gemini-3-flash": "Gemini 3 Flash",
        }
        if lowered_tail in overrides:
            return overrides[lowered_tail]
        if candidate_name and candidate_name.lower() not in {"chatgpt 4.5 preview"}:
            return candidate_name

        normalized = re.sub(r"[_-]+", " ", tail)
        normalized = re.sub(r"\b([a-z]+)\s+(\d+(?:\.\d+)?)\b", lambda m: f"{m.group(1).title()} {m.group(2)}", normalized)
        normalized = normalized.replace("gpt ", "GPT-").replace("qwen ", "Qwen ").replace("gemini ", "Gemini ")
        normalized = normalized.replace(" pro ", " Pro ").replace(" preview", " Preview").replace(" flash", " Flash")
        normalized = normalized.replace(" lite", " Lite").replace(" vision", " Vision").replace(" coder", " Coder")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized or tail

    @staticmethod
    def _humanize_remaining_ms(remaining_ms: Any) -> str:
        """Нормализует remainingMs в короткий человекочитаемый вид для UI."""
        try:
            raw_ms = int(remaining_ms)
        except (TypeError, ValueError):
            return ""
        if raw_ms == 0:
            return "0м"
        sign = "-" if raw_ms < 0 else ""
        total_minutes = abs(raw_ms) // 60000
        days, rem_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem_minutes, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}д")
        if hours:
            parts.append(f"{hours}ч")
        if minutes or not parts:
            parts.append(f"{minutes}м")
        return sign + " ".join(parts[:2])

    @staticmethod
    def _canonical_runtime_model_id(provider_name: str, raw_model_id: str) -> str:
        """Нормализует raw model id в provider-prefixed вид."""
        provider = str(provider_name or "").strip()
        model_id = str(raw_model_id or "").strip()
        if not model_id:
            return ""
        if "/" in model_id:
            return model_id
        return f"{provider}/{model_id}" if provider else model_id

    @classmethod
    def _provider_repair_helper_path(cls, provider_name: str) -> Path | None:
        """Возвращает helper `.command` для провайдера, если он уже есть или поддерживается."""
        normalized = str(provider_name or "").strip().lower()
        mapping = {
            "google-gemini-cli": cls._project_root() / "Login Gemini CLI OAuth.command",
            "qwen-portal": cls._project_root() / "Login Qwen Portal OAuth.command",
        }
        return mapping.get(normalized)

    @classmethod
    def _provider_ui_metadata(cls, provider_name: str) -> dict[str, Any]:
        """UI-подсказки и repair-рекомендации для конкретного провайдера."""
        normalized = str(provider_name or "").strip().lower()
        helper_path = cls._provider_repair_helper_path(normalized)
        base = {
            "manual_only": False,
            "recommended": True,
            "repair_available": bool(helper_path and helper_path.exists()),
            "repair_label": "",
            "repair_action": "",
            "repair_detail": "",
        }
        if normalized == "google-gemini-cli":
            base.update(
                {
                    "repair_label": "Перелогинить Gemini CLI",
                    "repair_action": "repair_oauth",
                    "repair_detail": "Откроет существующий one-click helper для Gemini CLI OAuth.",
                }
            )
        elif normalized == "qwen-portal":
            base.update(
                {
                    "repair_label": "Перелогинить Qwen Portal",
                    "repair_action": "repair_oauth",
                    "repair_detail": "Откроет Qwen Portal OAuth helper через OpenClaw plugin.",
                }
            )
        elif normalized == "google-antigravity":
            base.update(
                {
                    "recommended": False,
                    "repair_available": bool(cls._provider_repair_helper_path("google-gemini-cli") and cls._provider_repair_helper_path("google-gemini-cli").exists()),
                    "repair_label": "Перейти на Gemini CLI",
                    "repair_action": "migrate_to_gemini_cli",
                    "repair_detail": "Legacy provider. Вместо него используй официальный Gemini CLI OAuth flow.",
                }
            )
        elif normalized == "openai":
            base.update(
                {
                    "manual_only": True,
                    "repair_label": "",
                    "repair_action": "",
                    "repair_detail": "API key модели доступны только для ручного выбора и не должны включаться автоматически.",
                }
            )
        elif normalized == "openai-codex":
            base.update(
                {
                    "repair_detail": "OAuth-модели видимы, но promotion зависит от разрешённых scopes в самом OpenAI Codex OAuth контуре.",
                }
            )
        return base

    @classmethod
    def _openclaw_models_status_snapshot(cls) -> dict[str, dict[str, Any]]:
        """
        Truth-срез из `openclaw models status --json`.

        Нужен, чтобы owner UI опирался на тот же auth/runtime view,
        который уже считает сам OpenClaw, а не на самодельный разбор текста.
        """
        try:
            proc = subprocess.run(
                ["openclaw", "models", "status", "--json"],
                cwd=str(cls._project_root()),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except Exception:
            return {}
        if proc.returncode != 0:
            return {}
        try:
            payload = json.loads(str(proc.stdout or "{}"))
        except (TypeError, ValueError):
            return {}

        auth_root = payload.get("auth") if isinstance(payload, dict) else {}
        providers_meta = auth_root.get("providers") if isinstance(auth_root, dict) else []
        oauth_root = auth_root.get("oauth") if isinstance(auth_root, dict) else {}
        oauth_providers = oauth_root.get("providers") if isinstance(oauth_root, dict) else []

        by_provider: dict[str, dict[str, Any]] = {}
        for item in providers_meta if isinstance(providers_meta, list) else []:
            if not isinstance(item, dict):
                continue
            provider_name = str(item.get("provider", "") or "").strip().lower()
            if not provider_name:
                continue
            entry = by_provider.setdefault(provider_name, {"provider": provider_name})
            effective = item.get("effective") if isinstance(item.get("effective"), dict) else {}
            profiles = item.get("profiles") if isinstance(item.get("profiles"), dict) else {}
            entry.update(
                {
                    "effective_kind": str(effective.get("kind", "") or "").strip(),
                    "effective_detail": str(effective.get("detail", "") or "").strip(),
                    "profile_count": int(profiles.get("count", 0) or 0),
                    "profile_labels": [
                        str(label or "").strip()
                        for label in (profiles.get("labels") or [])
                        if str(label or "").strip()
                    ],
                }
            )

        for item in oauth_providers if isinstance(oauth_providers, list) else []:
            if not isinstance(item, dict):
                continue
            provider_name = str(item.get("provider", "") or "").strip().lower()
            if not provider_name:
                continue
            entry = by_provider.setdefault(provider_name, {"provider": provider_name})
            remaining_ms = item.get("remainingMs")
            try:
                normalized_remaining_ms = int(remaining_ms)
            except (TypeError, ValueError):
                normalized_remaining_ms = None
            entry.update(
                {
                    "oauth_status": str(item.get("status", "") or "").strip().lower(),
                    "oauth_expires_at": item.get("expiresAt"),
                    "oauth_remaining_ms": normalized_remaining_ms,
                    "oauth_remaining_human": cls._humanize_remaining_ms(normalized_remaining_ms),
                    "oauth_profiles": item.get("profiles") if isinstance(item.get("profiles"), list) else [],
                }
            )

        return {
            "raw": payload if isinstance(payload, dict) else {},
            "providers": by_provider,
        }

    @classmethod
    def _openclaw_models_full_catalog(cls) -> dict[str, Any]:
        """Читает `openclaw models list --all --json` и группирует модели по provider."""
        try:
            proc = subprocess.run(
                ["openclaw", "models", "list", "--all", "--json"],
                cwd=str(cls._project_root()),
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except Exception:
            return {"count": 0, "providers": {}}
        if proc.returncode != 0:
            return {"count": 0, "providers": {}}
        try:
            payload = json.loads(str(proc.stdout or "{}"))
        except (TypeError, ValueError):
            return {"count": 0, "providers": {}}

        provider_map: dict[str, list[dict[str, Any]]] = {}
        for item in (payload.get("models") or []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            model_key = str(item.get("key", "") or "").strip()
            if "/" not in model_key:
                continue
            provider_name = model_key.split("/", 1)[0].strip().lower()
            if not provider_name or provider_name in {"local", "lmstudio"}:
                continue
            provider_map.setdefault(provider_name, []).append(item)

        for items in provider_map.values():
            items.sort(
                key=lambda item: (
                    0 if "configured" in [str(tag or "").strip().lower() for tag in (item.get("tags") or [])] else 1,
                    0 if "default" in [str(tag or "").strip().lower() for tag in (item.get("tags") or [])] else 1,
                    str(item.get("name") or item.get("key") or "").lower(),
                )
            )

        return {
            "count": int(payload.get("count", 0) or 0) if isinstance(payload, dict) else 0,
            "providers": provider_map,
        }

    @staticmethod
    def _quota_state_from_failure_counts(failure_counts: dict[str, int] | None) -> dict[str, str]:
        """Грубая, но честная классификация quota-состояния по live failure counts."""
        raw = failure_counts if isinstance(failure_counts, dict) else {}
        lowered_keys = {str(key or "").strip().lower() for key in raw.keys()}
        if any(token in lowered_keys for token in {"quota", "quota_exceeded", "insufficient_quota", "billing_error"}):
            return {
                "quota_state": "blocked",
                "quota_label": "Квота/баланс заблокировали запросы",
            }
        if any(token in lowered_keys for token in {"rate_limit", "rate_limited", "too_many_requests"}):
            return {
                "quota_state": "limited",
                "quota_label": "Провайдер упирался в rate limit",
            }
        return {
            "quota_state": "unknown",
            "quota_label": "Провайдер не публикует остаток квоты",
        }

    @classmethod
    def _build_openclaw_parallelism_truth(
        cls,
        *,
        runtime_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает честное описание parallel / sequential semantics для owner UI.

        Здесь важно не смешивать две разные вещи:
        - queue concurrency для main/subagent lane;
        - отдельные режимы `parallel / sequential`, которые встречаются в других
          runtime-контурах OpenClaw вроде broadcast strategy.

        Для глобальной панели Краба показываем только подтверждённый queue truth,
        чтобы не выдавать это за единый глобальный переключатель.
        """
        payload = runtime_config if isinstance(runtime_config, dict) else cls._load_openclaw_runtime_config()
        agents = payload.get("agents") if isinstance(payload, dict) else {}
        defaults = agents.get("defaults") if isinstance(agents, dict) else {}
        subagents = defaults.get("subagents") if isinstance(defaults, dict) else {}
        if not isinstance(subagents, dict):
            subagents = {}

        def _positive_int(raw_value: Any) -> int | None:
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            return value

        main_max = _positive_int(defaults.get("maxConcurrent"))
        subagent_max = _positive_int(subagents.get("maxConcurrent"))

        detail_parts: list[str] = []
        if isinstance(main_max, int):
            detail_parts.append(f"main lane до {main_max} задач одновременно")
        if isinstance(subagent_max, int):
            detail_parts.append(f"subagent lane до {subagent_max} задач одновременно")

        return {
            "summary_label": "Для main-agent OpenClaw использует queue concurrency, а не единый глобальный переключатель parallel / sequential.",
            "detail_label": (
                "; ".join(detail_parts)
                if detail_parts
                else "В live config отдельный queue cap для main/subagent не найден, но global parallel/sequential switch здесь тоже не подтверждён."
            ),
            "broadcast_note": "Именованные режимы parallel / sequential могут существовать отдельно, например в broadcast strategy, и это не то же самое, что queue cap main-agent.",
            "main_max_concurrent": main_max,
            "subagent_max_concurrent": subagent_max,
        }

    @classmethod
    def _runtime_provider_model_ids_from_config(
        cls,
        provider_name: str,
        *,
        runtime_config: dict[str, Any] | None = None,
        current_slots: dict[str, str] | None = None,
    ) -> list[str]:
        """
        Собирает provider-модели из runtime-конфига, даже если models.json их не описывает.

        Это критично для `google-gemini-cli`: он может быть активным в runtime и auth,
        но отсутствовать в текущем registry-файле OpenClaw.
        """
        normalized_provider = str(provider_name or "").strip().lower()
        if not normalized_provider:
            return []

        payload = runtime_config if isinstance(runtime_config, dict) else cls._load_openclaw_runtime_config()
        agents = payload.get("agents") if isinstance(payload, dict) else {}
        defaults = agents.get("defaults") if isinstance(agents, dict) else {}
        model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
        if not isinstance(model_defaults, dict):
            model_defaults = {}
        model_overrides = defaults.get("models") if isinstance(defaults, dict) else {}
        agents_list = agents.get("list") if isinstance(agents, dict) else []

        discovered: list[str] = []
        seen: set[str] = set()

        def _remember(candidate: str) -> None:
            raw = str(candidate or "").strip()
            if not raw:
                return
            canonical = raw if "/" in raw else f"{normalized_provider}/{raw}"
            if not canonical.startswith(f"{normalized_provider}/"):
                return
            if canonical in seen:
                return
            seen.add(canonical)
            discovered.append(canonical)

        _remember(model_defaults.get("primary", ""))
        for item in (model_defaults.get("fallbacks") or []):
            _remember(str(item or ""))

        if isinstance(model_overrides, dict):
            for model_id in model_overrides:
                _remember(str(model_id or ""))

        for slot_model in (current_slots or {}).values():
            _remember(str(slot_model or ""))

        for agent_payload in agents_list if isinstance(agents_list, list) else []:
            if not isinstance(agent_payload, dict):
                continue
            _remember(str(agent_payload.get("model", "") or ""))

        return discovered

    @classmethod
    def _runtime_signal_failed_providers(cls) -> dict[str, str]:
        """Читает живые auth/scope-fail сигналы gateway-log для truthful catalog."""
        try:
            signal_log = discover_gateway_signal_log(repo_root=cls._project_root())
        except Exception:
            signal_log = Path()
        if not signal_log or not signal_log.exists():
            return {}
        try:
            return runtime_auth_failed_providers_from_signal_log(signal_log)
        except Exception:
            return {}

    @classmethod
    def _runtime_provider_state(
        cls,
        provider_name: str,
        *,
        runtime_models: dict[str, Any] | None = None,
        auth_profiles: dict[str, Any] | None = None,
        runtime_signal_failures: dict[str, str] | None = None,
        status_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Возвращает честный статус провайдера для каталога и routing diagnostics."""
        normalized_provider = str(provider_name or "").strip().lower()
        provider_payload = {}
        providers = runtime_models.get("providers") if isinstance(runtime_models, dict) else {}
        if isinstance(providers, dict):
            provider_payload = providers.get(normalized_provider) if isinstance(providers.get(normalized_provider), dict) else {}

        model_entries = provider_payload.get("models") if isinstance(provider_payload, dict) else []
        runtime_model_ids: list[str] = []
        if isinstance(model_entries, list):
            for item in model_entries:
                if not isinstance(item, dict):
                    continue
                canonical = cls._canonical_runtime_model_id(normalized_provider, str(item.get("id", "") or ""))
                if canonical and canonical not in runtime_model_ids:
                    runtime_model_ids.append(canonical)

        profiles = auth_profiles.get("profiles") if isinstance(auth_profiles, dict) else {}
        if not isinstance(profiles, dict):
            profiles = {}
        usage_stats = auth_profiles.get("usageStats") if isinstance(auth_profiles, dict) else {}
        if not isinstance(usage_stats, dict):
            usage_stats = {}
        status_providers = status_snapshot.get("providers") if isinstance(status_snapshot, dict) else {}
        if not isinstance(status_providers, dict):
            status_providers = {}
        status_meta = status_providers.get(normalized_provider) if isinstance(status_providers.get(normalized_provider), dict) else {}

        profile_names = [
            profile_name
            for profile_name, profile_payload in profiles.items()
            if isinstance(profile_payload, dict) and str(profile_payload.get("provider", "") or "").strip() == normalized_provider
        ]

        disabled_profiles: list[dict[str, str]] = []
        expired_profiles: list[dict[str, str]] = []
        failure_counts: dict[str, int] = {}
        cooldown_active = False
        now_ms = time.time() * 1000.0
        for profile_name in profile_names:
            profile_payload = profiles.get(profile_name)
            usage = usage_stats.get(profile_name)
            if isinstance(profile_payload, dict):
                try:
                    expires_at = float(profile_payload.get("expires", 0) or 0)
                except (TypeError, ValueError):
                    expires_at = 0.0
                if expires_at > 0 and expires_at <= now_ms:
                    expired_profiles.append({"profile": profile_name, "reason": "expired"})
            if not isinstance(usage, dict):
                continue
            disabled_reason = str(usage.get("disabledReason", "") or "").strip()
            if disabled_reason:
                disabled_profiles.append({"profile": profile_name, "reason": disabled_reason})
            try:
                cooldown_until = float(usage.get("cooldownUntil", 0) or 0)
            except (TypeError, ValueError):
                cooldown_until = 0.0
            if cooldown_until > now_ms:
                cooldown_active = True
            failures = usage.get("failureCounts")
            if isinstance(failures, dict):
                for failure_key, failure_value in failures.items():
                    failure_counts[str(failure_key)] = failure_counts.get(str(failure_key), 0) + int(failure_value or 0)

        auth_mode = str(provider_payload.get("auth", "") or "").strip().lower()
        api_key_configured = bool(str(provider_payload.get("apiKey", "") or "").strip())
        effective_kind = str(status_meta.get("effective_kind", "") or "").strip().lower()
        if not auth_mode and profile_names:
            auth_mode = "oauth"
        elif not auth_mode and api_key_configured:
            auth_mode = "api-key"
        elif not auth_mode and effective_kind == "profiles":
            auth_mode = "oauth"
        elif not auth_mode and effective_kind in {"env", "models.json"}:
            auth_mode = "api-key"
        signal_fail_code = str((runtime_signal_failures or {}).get(normalized_provider, "") or "").strip()
        legacy = normalized_provider == "google-antigravity"
        oauth_status = str(status_meta.get("oauth_status", "") or "").strip().lower()
        oauth_remaining_ms = status_meta.get("oauth_remaining_ms")
        oauth_remaining_human = str(status_meta.get("oauth_remaining_human", "") or "").strip()
        oauth_expected = auth_mode == "oauth"
        quota_truth = cls._quota_state_from_failure_counts(failure_counts)

        readiness = "ready"
        readiness_label = "Configured"
        detail = "Провайдер готов к выбору."

        if signal_fail_code == "runtime_missing_scope_model_request":
            readiness = "blocked"
            readiness_label = "Scope fail"
            detail = "Runtime фиксирует `Missing scopes: model.request`; OAuth-модели видны, но как primary сейчас неработоспособны."
        elif disabled_profiles:
            readiness = "blocked"
            readiness_label = "Disabled"
            detail = f"Профиль отключён: {disabled_profiles[0]['reason']}"
        elif oauth_expected and oauth_status == "ok" and isinstance(oauth_remaining_ms, int) and oauth_remaining_ms <= 0:
            readiness = "attention"
            readiness_label = "Re-auth soon"
            detail = "OpenClaw ещё считает OAuth рабочим, но TTL уже на нуле или ниже; лучше сделать повторный логин до следующего флапа."
        elif oauth_expected and oauth_status == "ok" and isinstance(oauth_remaining_ms, int) and oauth_remaining_ms <= 15 * 60 * 1000:
            readiness = "attention"
            readiness_label = "Expiring"
            detail = "OAuth-профиль живой, но подходит к истечению и может скоро потребовать re-auth."
        elif oauth_expected and oauth_status in {"expired", "missing"}:
            readiness = "blocked"
            readiness_label = "Expired"
            detail = "Сам OpenClaw считает OAuth-профиль истёкшим или отсутствующим."
        elif oauth_expected and expired_profiles:
            readiness = "blocked"
            readiness_label = "Expired"
            detail = "OAuth-профиль истёк и требует повторного логина."
        elif cooldown_active:
            readiness = "attention"
            readiness_label = "Cooldown"
            detail = "Провайдер в cooldown после недавних ошибок; выбор возможен, но route нестабилен."
        elif legacy:
            readiness = "attention"
            readiness_label = "Legacy"
            detail = "Legacy OAuth-провайдер. Показываем для совместимости, но не рекомендуем для нового primary."
        elif auth_mode == "oauth" and profile_names:
            readiness = "ready"
            readiness_label = "OAuth OK"
            detail = "OAuth-профиль найден и выглядит рабочим."
        elif auth_mode == "oauth":
            readiness = "attention"
            readiness_label = "OAuth missing"
            detail = "Провайдер ожидает OAuth-профиль, но связанный login пока не найден."
        elif auth_mode == "api-key" and api_key_configured:
            readiness = "ready"
            readiness_label = "API key"
            detail = "API key сконфигурирован."
        elif auth_mode == "api-key":
            readiness = "blocked"
            readiness_label = "API key missing"
            detail = "Провайдер требует API key, но ключ не найден."
        elif runtime_model_ids or profile_names:
            readiness = "ready"
            readiness_label = "Configured"
            detail = "Провайдер описан в runtime."
        else:
            readiness = "blocked"
            readiness_label = "Unavailable"
            detail = "Runtime-провайдер пока не описан."

        return {
            "provider": normalized_provider,
            "configured": bool(runtime_model_ids or profile_names),
            "runtime_models": runtime_model_ids,
            "profiles": profile_names,
            "disabled_profiles": disabled_profiles,
            "expired_profiles": expired_profiles,
            "failure_counts": failure_counts,
            "cooldown_active": cooldown_active,
            "auth_mode": auth_mode or "unknown",
            "api_key_configured": api_key_configured,
            "signal_fail_code": signal_fail_code,
            "readiness": readiness,
            "readiness_label": readiness_label,
            "detail": detail,
            "legacy": legacy,
            "effective_kind": effective_kind,
            "effective_detail": str(status_meta.get("effective_detail", "") or "").strip(),
            "oauth_status": oauth_status,
            "oauth_remaining_ms": oauth_remaining_ms,
            "oauth_remaining_human": oauth_remaining_human,
            "quota_state": str(quota_truth.get("quota_state", "unknown") or "unknown"),
            "quota_label": str(quota_truth.get("quota_label", "") or ""),
        }

    def _launch_local_app(self, target_path: Path) -> dict[str, Any]:
        """
        Запускает локальный `.command`/app через macOS `open` без блокировки web API.

        Нужен для one-click repair из панели: открываем helper в Terminal,
        а дальше пользователь проходит интерактивный OAuth flow в штатном окне.
        """
        target = Path(target_path).resolve()
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "exit_code": 127,
                "error": f"helper_not_found:{target}",
                "launched": False,
                "path": str(target),
            }

        try:
            subprocess.Popen(
                ["open", str(target)],
                cwd=str(self._project_root()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "ok": True,
                "exit_code": 0,
                "error": "",
                "launched": True,
                "path": str(target),
            }
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": 1,
                "error": f"helper_launch_error:{exc}",
                "launched": False,
                "path": str(target),
            }

    @classmethod
    def _active_runtime_model_ids(cls) -> set[str]:
        """
        Возвращает активную цепочку моделей из OpenClaw runtime.

        Что считаем "активным":
        - primary;
        - fallback chain;
        - это и есть боевой слой, который должен доминировать в owner UI,
          чтобы панель не засорялась legacy-провайдерами и старыми платными хвостами.
        """
        runtime_config = cls._load_openclaw_runtime_config()
        agents = runtime_config.get("agents") if isinstance(runtime_config, dict) else {}
        defaults = agents.get("defaults") if isinstance(agents, dict) else {}
        model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
        if not isinstance(model_defaults, dict):
            model_defaults = {}

        active: set[str] = set()
        primary = str(model_defaults.get("primary", "") or "").strip()
        if primary:
            active.add(primary)
        for item in (model_defaults.get("fallbacks") or []):
            candidate = str(item or "").strip()
            if candidate:
                active.add(candidate)
        return active

    @classmethod
    def _build_openclaw_runtime_controls(cls) -> dict[str, Any]:
        """
        Собирает editable runtime-controls для owner UI.

        Это отдельный truth-слой над `~/.openclaw/openclaw.json`, чтобы панель могла
        не только показывать `primary/fallbacks`, но и править:
        - глобальное context window;
        - thinkingDefault;
        - per-model thinking для активной цепочки.
        """
        runtime_config = cls._load_openclaw_runtime_config()
        agents = runtime_config.get("agents") if isinstance(runtime_config, dict) else {}
        defaults = agents.get("defaults") if isinstance(agents, dict) else {}
        model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
        if not isinstance(model_defaults, dict):
            model_defaults = {}
        models_defaults = defaults.get("models") if isinstance(defaults, dict) else {}
        if not isinstance(models_defaults, dict):
            models_defaults = {}

        primary = str(model_defaults.get("primary", "") or "").strip()
        fallbacks = [
            str(item).strip()
            for item in (model_defaults.get("fallbacks") or [])
            if str(item or "").strip()
        ]
        thinking_default = str(defaults.get("thinkingDefault", "off") or "off").strip().lower() or "off"
        try:
            thinking_default = cls._normalize_thinking_mode(thinking_default)
        except ValueError:
            thinking_default = "off"

        try:
            context_tokens = int(defaults.get("contextTokens", 128000) or 128000)
        except (TypeError, ValueError):
            context_tokens = 128000

        chain_items: list[dict[str, Any]] = []
        for index, model_id in enumerate([primary, *fallbacks]):
            if not model_id:
                continue
            model_payload = models_defaults.get(model_id) if isinstance(models_defaults.get(model_id), dict) else {}
            params = model_payload.get("params") if isinstance(model_payload, dict) else {}
            explicit_thinking = str(params.get("thinking", "") or "").strip().lower() if isinstance(params, dict) else ""
            if explicit_thinking:
                try:
                    explicit_thinking = cls._normalize_thinking_mode(explicit_thinking)
                except ValueError:
                    explicit_thinking = ""
            effective_thinking = explicit_thinking or thinking_default
            chain_items.append(
                {
                    "slot_kind": "primary" if index == 0 else "fallback",
                    "slot_index": 0 if index == 0 else index,
                    "slot_label": "Primary" if index == 0 else f"Fallback #{index}",
                    "model_id": model_id,
                    "explicit_thinking": explicit_thinking,
                    "effective_thinking": effective_thinking,
                    "uses_default_thinking": not bool(explicit_thinking),
                }
            )

        return {
            "primary": primary,
            "fallbacks": fallbacks,
            "context_tokens": context_tokens,
            "thinking_default": thinking_default,
            "thinking_modes": ["off", "auto", "low", "medium", "high"],
            "chain_items": chain_items,
            "max_fallback_slots": max(5, len(fallbacks)),
        }

    @classmethod
    def _apply_openclaw_runtime_controls(
        cls,
        *,
        primary_raw: Any,
        fallbacks_raw: list[Any],
        context_tokens_raw: Any,
        thinking_default_raw: Any,
        slot_thinking_raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Применяет глобальную model-chain и runtime-knobs в live OpenClaw config.

        Записываем сразу в:
        - `~/.openclaw/openclaw.json`
        - `~/.openclaw/agents/main/agent/agent.json`

        Так owner UI меняет тот же runtime, который реально используют каналы и userbot.
        """
        primary, primary_alias_note = cls._normalize_runtime_model_id(primary_raw)
        if not primary or "/" not in primary:
            raise ValueError("runtime_primary_model_required")

        fallbacks: list[str] = []
        alias_notes: list[str] = []
        if primary_alias_note:
            alias_notes.append(primary_alias_note)
        seen = {primary}
        for raw_item in list(fallbacks_raw or []):
            model_id, alias_note = cls._normalize_runtime_model_id(raw_item)
            if alias_note:
                alias_notes.append(alias_note)
            if not model_id:
                continue
            if "/" not in model_id:
                raise ValueError("runtime_invalid_fallback_model")
            if model_id in seen:
                continue
            seen.add(model_id)
            fallbacks.append(model_id)

        context_tokens = cls._normalize_context_tokens(context_tokens_raw)
        thinking_default = cls._normalize_thinking_mode(thinking_default_raw)

        normalized_slot_thinking: dict[str, str] = {}
        raw_slot_thinking = slot_thinking_raw if isinstance(slot_thinking_raw, dict) else {}
        for model_id, raw_thinking in raw_slot_thinking.items():
            canonical_model_id, alias_note = cls._normalize_runtime_model_id(model_id)
            if alias_note:
                alias_notes.append(alias_note)
            if not canonical_model_id or canonical_model_id not in {primary, *fallbacks}:
                continue
            normalized_slot_thinking[canonical_model_id] = cls._normalize_thinking_mode(raw_thinking)

        openclaw_path = cls._openclaw_config_path()
        agent_path = cls._openclaw_agent_config_path()
        openclaw_payload = cls._load_openclaw_runtime_config()
        agent_payload = cls._load_openclaw_agent_config()
        if not isinstance(openclaw_payload, dict) or not openclaw_payload:
            raise ValueError("runtime_openclaw_json_missing_or_invalid")
        if not isinstance(agent_payload, dict):
            agent_payload = {}

        agents = openclaw_payload.setdefault("agents", {})
        if not isinstance(agents, dict):
            agents = {}
            openclaw_payload["agents"] = agents

        defaults = agents.setdefault("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
            agents["defaults"] = defaults

        model_cfg = defaults.setdefault("model", {})
        if not isinstance(model_cfg, dict):
            model_cfg = {}
            defaults["model"] = model_cfg

        models_cfg = defaults.setdefault("models", {})
        if not isinstance(models_cfg, dict):
            models_cfg = {}
            defaults["models"] = models_cfg

        changed: dict[str, Any] = {}

        prev_primary = str(model_cfg.get("primary") or "")
        if prev_primary != primary:
            model_cfg["primary"] = primary
            changed["agents.defaults.model.primary"] = {"from": prev_primary, "to": primary}

        prev_fallbacks = model_cfg.get("fallbacks")
        if prev_fallbacks != fallbacks:
            model_cfg["fallbacks"] = list(fallbacks)
            changed["agents.defaults.model.fallbacks"] = {"from": prev_fallbacks, "to": list(fallbacks)}

        prev_context_tokens = defaults.get("contextTokens")
        if prev_context_tokens != context_tokens:
            defaults["contextTokens"] = context_tokens
            changed["agents.defaults.contextTokens"] = {"from": prev_context_tokens, "to": context_tokens}

        prev_thinking_default = str(defaults.get("thinkingDefault") or "")
        if prev_thinking_default != thinking_default:
            defaults["thinkingDefault"] = thinking_default
            changed["agents.defaults.thinkingDefault"] = {"from": prev_thinking_default, "to": thinking_default}

        subagents = defaults.setdefault("subagents", {})
        if not isinstance(subagents, dict):
            subagents = {}
            defaults["subagents"] = subagents
        prev_sub_model = str(subagents.get("model") or "")
        if prev_sub_model != primary:
            subagents["model"] = primary
            changed["agents.defaults.subagents.model"] = {"from": prev_sub_model, "to": primary}

        agents_list = agents.get("list")
        if isinstance(agents_list, list):
            for item in agents_list:
                if not isinstance(item, dict) or str(item.get("id") or "") != "main":
                    continue
                prev_list_model = str(item.get("model") or "")
                if prev_list_model != primary:
                    item["model"] = primary
                    changed["agents.list[main].model"] = {"from": prev_list_model, "to": primary}
                break

        prev_agent_model = str(agent_payload.get("model") or "")
        if prev_agent_model != primary:
            agent_payload["model"] = primary
            changed["agents.main.agent.json.model"] = {"from": prev_agent_model, "to": primary}

        # Явно фиксируем thinking на моделях активной цепочки, чтобы runtime не жил
        # stale-override'ами и global thinking реально применялся.
        for model_id in [primary, *fallbacks]:
            model_payload = models_cfg.setdefault(model_id, {})
            if not isinstance(model_payload, dict):
                model_payload = {}
                models_cfg[model_id] = model_payload
            params = model_payload.setdefault("params", {})
            if not isinstance(params, dict):
                params = {}
                model_payload["params"] = params
            next_thinking = normalized_slot_thinking.get(model_id, thinking_default)
            prev_model_thinking = str(params.get("thinking") or "")
            if prev_model_thinking != next_thinking:
                params["thinking"] = next_thinking
                changed[f"agents.defaults.models[{model_id}].params.thinking"] = {
                    "from": prev_model_thinking,
                    "to": next_thinking,
                }

        backup_openclaw = cls._backup_json_file(openclaw_path, label="webui_runtime")
        backup_agent = cls._backup_json_file(agent_path, label="webui_runtime")
        cls._write_json_file(openclaw_path, openclaw_payload)
        cls._write_json_file(agent_path, agent_payload)

        return {
            "primary": primary,
            "fallbacks": fallbacks,
            "context_tokens": context_tokens,
            "thinking_default": thinking_default,
            "slot_thinking": {
                model_id: normalized_slot_thinking.get(model_id, thinking_default)
                for model_id in [primary, *fallbacks]
            },
            "alias_notes": [note for note in alias_notes if note],
            "changed": changed,
            "backup_openclaw_json": backup_openclaw,
            "backup_agent_json": backup_agent,
        }

    @classmethod
    def _build_runtime_cloud_presets(cls, current_slots: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """
        Строит cloud catalog из runtime OpenClaw models.json.

        Почему это отдельный helper:
        - web-панель не должна invent-ить каталог моделей из старых alias-списков;
        - runtime truth уже живёт в OpenClaw, и UI должен отражать именно его;
        - текущие slot bindings добавляем как fallback, даже если модель ещё не
          описана в runtime registry, чтобы пользователь видел фактическое состояние.
        """
        runtime_models = cls._load_openclaw_runtime_models()
        runtime_config = cls._load_openclaw_runtime_config()
        auth_profiles = cls._load_openclaw_auth_profiles()
        full_catalog = cls._openclaw_models_full_catalog()
        status_snapshot = cls._openclaw_models_status_snapshot()
        providers = runtime_models.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        active_chain = cls._active_runtime_model_ids()
        signal_failures = cls._runtime_signal_failed_providers()

        provider_names: set[str] = {
            str(name or "").strip()
            for name in providers.keys()
            if str(name or "").strip() and str(name or "").strip().lower() not in {"lmstudio", "local"}
        }
        profiles = auth_profiles.get("profiles") if isinstance(auth_profiles, dict) else {}
        if isinstance(profiles, dict):
            for profile_payload in profiles.values():
                if not isinstance(profile_payload, dict):
                    continue
                provider_name = str(profile_payload.get("provider", "") or "").strip()
                if provider_name and provider_name.lower() not in {"lmstudio", "local"}:
                    provider_names.add(provider_name)
        for model_id in active_chain:
            provider_name = str(model_id.split("/", 1)[0] if "/" in model_id else "").strip()
            if provider_name and provider_name.lower() not in {"lmstudio", "local"}:
                provider_names.add(provider_name)
        for model_id in (current_slots or {}).values():
            raw = str(model_id or "").strip()
            provider_name = str(raw.split("/", 1)[0] if "/" in raw else "").strip()
            if provider_name and provider_name.lower() not in {"lmstudio", "local"}:
                provider_names.add(provider_name)
        full_catalog_providers = full_catalog.get("providers") if isinstance(full_catalog, dict) else {}
        if isinstance(full_catalog_providers, dict):
            for provider_name in full_catalog_providers.keys():
                normalized = str(provider_name or "").strip()
                if normalized and normalized.lower() not in {"lmstudio", "local"} and normalized in provider_names:
                    provider_names.add(normalized)

        items_by_id: dict[str, dict[str, Any]] = {}
        for provider_name in sorted(provider_names, key=cls._provider_sort_rank):
            normalized_provider = str(provider_name or "").strip()
            provider_payload = providers.get(normalized_provider) if isinstance(providers.get(normalized_provider), dict) else {}
            provider_state = cls._runtime_provider_state(
                normalized_provider,
                runtime_models=runtime_models,
                auth_profiles=auth_profiles,
                runtime_signal_failures=signal_failures,
                status_snapshot=status_snapshot,
            )
            provider_ui = cls._provider_ui_metadata(normalized_provider)
            configured_model_ids = set(str(item or "").strip() for item in (provider_state.get("runtime_models") or []) if str(item or "").strip())
            configured_model_ids.update(
                cls._runtime_provider_model_ids_from_config(
                    normalized_provider,
                    runtime_config=runtime_config,
                    current_slots=current_slots,
                )
            )
            full_catalog_models = full_catalog_providers.get(normalized_provider) if isinstance(full_catalog_providers, dict) else []
            if isinstance(full_catalog_models, list):
                for model in full_catalog_models:
                    if not isinstance(model, dict):
                        continue
                    canonical_id = str(model.get("key", "") or "").strip()
                    if not canonical_id:
                        continue
                    selected_slots = [
                        slot_name
                        for slot_name, slot_model in (current_slots or {}).items()
                        if str(slot_model or "").strip() == canonical_id
                    ]
                    raw_name = str(model.get("name", "") or "").strip()
                    tags = [
                        str(tag or "").strip()
                        for tag in (model.get("tags") or [])
                        if str(tag or "").strip()
                    ]
                    configured_runtime = bool(
                        canonical_id in configured_model_ids
                        or canonical_id in active_chain
                        or selected_slots
                        or "configured" in {tag.lower() for tag in tags}
                    )
                    items_by_id[canonical_id] = {
                        "id": canonical_id,
                        "provider": normalized_provider,
                        "provider_label": cls._provider_label(normalized_provider),
                        "provider_auth": str(provider_state.get("auth_mode") or "unknown"),
                        "provider_readiness": str(provider_state.get("readiness") or "unknown"),
                        "provider_readiness_label": str(provider_state.get("readiness_label") or "Configured"),
                        "provider_detail": str(provider_state.get("detail") or ""),
                        "provider_quota_state": str(provider_state.get("quota_state") or "unknown"),
                        "provider_quota_label": str(provider_state.get("quota_label") or ""),
                        "provider_effective_kind": str(provider_state.get("effective_kind") or ""),
                        "provider_effective_detail": str(provider_state.get("effective_detail") or ""),
                        "provider_oauth_status": str(provider_state.get("oauth_status") or ""),
                        "provider_oauth_remaining_human": str(provider_state.get("oauth_remaining_human") or ""),
                        "provider_ui": dict(provider_ui),
                        "label": f"{cls._provider_label(normalized_provider)} • {canonical_id.split('/', 1)[-1]}",
                        "name": cls._friendly_model_name(canonical_id, raw_name),
                        "raw_name": raw_name,
                        "actual_model_id": canonical_id.split("/", 1)[-1],
                        "reasoning": bool(model.get("reasoning", False)),
                        "max_tokens": int(model.get("maxTokens", 0) or 0),
                        "context_window": int(model.get("contextWindow", 0) or 0),
                        "input_modes": [
                            str(mode or "").strip()
                            for mode in (model.get("input") or [])
                            if str(mode or "").strip()
                        ],
                        "source": "provider_catalog",
                        "provider_catalog_visible": True,
                        "configured_runtime": configured_runtime,
                        "active_runtime": canonical_id in active_chain,
                        "selected_slots": selected_slots,
                        "legacy": bool(provider_state.get("legacy")),
                        "catalog_tags": tags,
                        "catalog_available": bool(model.get("available", False)),
                    }
            models = provider_payload.get("models") if isinstance(provider_payload, dict) else None
            if isinstance(models, list):
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    raw_model_id = str(model.get("id", "") or "").strip()
                    canonical_id = cls._canonical_runtime_model_id(normalized_provider, raw_model_id)
                    if not canonical_id:
                        continue
                    selected_slots = [
                        slot_name
                        for slot_name, slot_model in (current_slots or {}).items()
                        if str(slot_model or "").strip() == canonical_id
                    ]
                    items_by_id[canonical_id] = {
                        "id": canonical_id,
                        "provider": normalized_provider,
                        "provider_label": cls._provider_label(normalized_provider),
                        "provider_auth": str(provider_state.get("auth_mode") or "unknown"),
                        "provider_readiness": str(provider_state.get("readiness") or "unknown"),
                        "provider_readiness_label": str(provider_state.get("readiness_label") or "Configured"),
                        "provider_detail": str(provider_state.get("detail") or ""),
                        "provider_quota_state": str(provider_state.get("quota_state") or "unknown"),
                        "provider_quota_label": str(provider_state.get("quota_label") or ""),
                        "provider_effective_kind": str(provider_state.get("effective_kind") or ""),
                        "provider_effective_detail": str(provider_state.get("effective_detail") or ""),
                        "provider_oauth_status": str(provider_state.get("oauth_status") or ""),
                        "provider_oauth_remaining_human": str(provider_state.get("oauth_remaining_human") or ""),
                        "provider_ui": dict(provider_ui),
                        "label": f"{cls._provider_label(normalized_provider)} • {canonical_id.split('/', 1)[-1]}",
                        "name": cls._friendly_model_name(canonical_id, str(model.get("name", "") or "")),
                        "raw_name": str(model.get("name", "") or ""),
                        "actual_model_id": canonical_id.split("/", 1)[-1],
                        "reasoning": bool(model.get("reasoning", False)),
                        "max_tokens": int(model.get("maxTokens", 0) or 0),
                        "context_window": int(model.get("contextWindow", 0) or 0),
                        "input_modes": [
                            str(mode or "").strip()
                            for mode in (model.get("input") or [])
                            if str(mode or "").strip()
                        ],
                        "source": "openclaw_runtime",
                        "provider_catalog_visible": False,
                        "configured_runtime": True,
                        "active_runtime": canonical_id in active_chain,
                        "selected_slots": selected_slots,
                        "legacy": bool(provider_state.get("legacy")),
                        "catalog_tags": [],
                        "catalog_available": True,
                    }

            for canonical_id in cls._runtime_provider_model_ids_from_config(
                normalized_provider,
                runtime_config=runtime_config,
                current_slots=current_slots,
            ):
                if not canonical_id or canonical_id in items_by_id:
                    continue
                selected_slots = [
                    slot_name
                    for slot_name, slot_model in (current_slots or {}).items()
                    if str(slot_model or "").strip() == canonical_id
                ]
                items_by_id[canonical_id] = {
                    "id": canonical_id,
                    "provider": normalized_provider,
                    "provider_label": cls._provider_label(normalized_provider),
                    "provider_auth": str(provider_state.get("auth_mode") or "unknown"),
                    "provider_readiness": str(provider_state.get("readiness") or "unknown"),
                    "provider_readiness_label": str(provider_state.get("readiness_label") or "Configured"),
                    "provider_detail": str(provider_state.get("detail") or ""),
                    "provider_quota_state": str(provider_state.get("quota_state") or "unknown"),
                    "provider_quota_label": str(provider_state.get("quota_label") or ""),
                    "provider_effective_kind": str(provider_state.get("effective_kind") or ""),
                    "provider_effective_detail": str(provider_state.get("effective_detail") or ""),
                    "provider_oauth_status": str(provider_state.get("oauth_status") or ""),
                    "provider_oauth_remaining_human": str(provider_state.get("oauth_remaining_human") or ""),
                    "provider_ui": dict(provider_ui),
                    "label": f"{cls._provider_label(normalized_provider)} • {canonical_id.split('/', 1)[-1]}",
                    "name": cls._friendly_model_name(canonical_id),
                    "raw_name": "",
                    "actual_model_id": canonical_id.split("/", 1)[-1],
                    "reasoning": "gpt-5" in canonical_id or canonical_id.endswith("/gpt-5.4"),
                    "max_tokens": 0,
                    "context_window": 0,
                    "input_modes": [],
                    "source": "runtime_config",
                    "provider_catalog_visible": False,
                    "configured_runtime": True,
                    "active_runtime": canonical_id in active_chain,
                    "selected_slots": selected_slots,
                    "legacy": bool(provider_state.get("legacy")),
                    "catalog_tags": [],
                    "catalog_available": True,
                }

        return sorted(
            items_by_id.values(),
            key=lambda item: (
                cls._provider_sort_rank(str(item.get("provider") or "")),
                0 if bool(item.get("configured_runtime")) else 1,
                0 if bool(item.get("active_runtime")) else 1,
                0 if str(item.get("source") or "") in {"openclaw_runtime", "runtime_config"} else 1,
                str(item.get("name") or item.get("id") or "").lower(),
            ),
        )

    @classmethod
    def _build_runtime_quick_presets(
        cls,
        *,
        current_slots: dict[str, str],
        local_override: str,
    ) -> dict[str, dict[str, Any]]:
        """Строит quick presets только из runtime-видимых cloud моделей."""
        del local_override
        available_ids = {
            str(item.get("id", "")).strip()
            for item in cls._build_runtime_cloud_presets(current_slots)
            if str(item.get("id", "")).strip() and bool(item.get("configured_runtime", True))
        }
        if not available_ids:
            available_ids = {
                str(item.get("id", "")).strip()
                for item in cls._build_runtime_cloud_presets(current_slots)
                if str(item.get("id", "")).strip()
            }

        def _pick(*preferred_ids: str) -> str:
            for candidate in preferred_ids:
                normalized = str(candidate or "").strip()
                if normalized and normalized in available_ids:
                    return normalized
            for slot_name in ("chat", "thinking", "pro", "coding"):
                current = str(current_slots.get(slot_name, "") or "").strip()
                if current and current in available_ids:
                    return current
            return next(iter(sorted(available_ids)), "")

        chat_model = _pick(
            str(current_slots.get("chat", "") or ""),
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        thinking_model = _pick(
            str(current_slots.get("thinking", "") or ""),
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        pro_model = _pick(
            str(current_slots.get("pro", "") or ""),
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        coding_model = _pick(
            str(current_slots.get("coding", "") or ""),
            "openai-codex/gpt-5.4",
            "qwen-portal/coder-model",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "google/gemini-2.5-flash-lite",
        )

        return {
            "balanced_auto": {
                "mode": "auto",
                "title": "Balanced Auto",
                "description": "Авто-режим: runtime-слоты держатся ближе к текущей truth-конфигурации.",
                "slots": {
                    "chat": chat_model,
                    "thinking": thinking_model or chat_model,
                    "pro": pro_model or thinking_model or chat_model,
                    "coding": coding_model or pro_model or chat_model,
                },
            },
            "local_focus": {
                "mode": "local",
                "title": "Local Focus",
                "description": "Force local для основного трафика, но cloud fallback остаётся безопасным и предсказуемым.",
                "slots": {
                    "chat": chat_model,
                    "thinking": thinking_model or chat_model,
                    "pro": pro_model or thinking_model or chat_model,
                    "coding": coding_model or pro_model or chat_model,
                },
            },
            "cloud_reasoning": {
                "mode": "cloud",
                "title": "Cloud Reasoning",
                "description": "Force cloud + лучший доступный reasoning/coding runtime-профиль.",
                "slots": {
                    "chat": chat_model,
                    "thinking": thinking_model or pro_model or chat_model,
                    "pro": pro_model or thinking_model or chat_model,
                    "coding": coding_model or pro_model or chat_model,
                },
            },
        }

    @classmethod
    def _build_openclaw_model_routing_status(cls) -> dict[str, Any]:
        """
        Собирает честный read-only статус model routing в OpenClaw runtime.

        Это диагностический слой для owner-панели:
        - откуда берётся текущий primary/fallback chain;
        - в каком состоянии auth-профили провайдеров;
        - готов ли target `GPT-5.4` хотя бы на уровне runtime-конфига.
        """
        runtime_config = cls._load_openclaw_runtime_config()
        runtime_models = cls._load_openclaw_runtime_models()
        auth_profiles = cls._load_openclaw_auth_profiles()
        status_snapshot = cls._openclaw_models_status_snapshot()

        agents = runtime_config.get("agents") if isinstance(runtime_config, dict) else {}
        defaults = agents.get("defaults") if isinstance(agents, dict) else {}
        model_defaults = defaults.get("model") if isinstance(defaults, dict) else {}
        if not isinstance(model_defaults, dict):
            model_defaults = {}
        current_primary = str(model_defaults.get("primary", "") or "").strip()
        current_fallbacks = [
            str(item).strip()
            for item in (model_defaults.get("fallbacks") or [])
            if str(item or "").strip()
        ]

        signal_failures = cls._runtime_signal_failed_providers()
        openai_codex = cls._runtime_provider_state(
            "openai-codex",
            runtime_models=runtime_models,
            auth_profiles=auth_profiles,
            runtime_signal_failures=signal_failures,
            status_snapshot=status_snapshot,
        )
        google_gemini_cli = cls._runtime_provider_state(
            "google-gemini-cli",
            runtime_models=runtime_models,
            auth_profiles=auth_profiles,
            runtime_signal_failures=signal_failures,
            status_snapshot=status_snapshot,
        )
        google_antigravity = cls._runtime_provider_state(
            "google-antigravity",
            runtime_models=runtime_models,
            auth_profiles=auth_profiles,
            runtime_signal_failures=signal_failures,
            status_snapshot=status_snapshot,
        )

        target_primary = str(os.getenv("OPENCLAW_TARGET_PRIMARY_MODEL", "openai-codex/gpt-5.4") or "").strip()
        target_in_runtime = target_primary in set(openai_codex["runtime_models"])
        current_primary_broken = (
            current_primary.startswith("openai-codex/")
            and (
                str(openai_codex.get("signal_fail_code") or "") == "runtime_missing_scope_model_request"
                or (
                    int(openai_codex["failure_counts"].get("model_not_found", 0) or 0) > 0
                    and bool(openai_codex.get("cooldown_active"))
                )
            )
        )
        google_gemini_cli_unavailable = bool(
            google_gemini_cli["disabled_profiles"]
            or google_gemini_cli["expired_profiles"]
            or google_gemini_cli["cooldown_active"]
        )
        antigravity_disabled = bool(google_antigravity["disabled_profiles"])
        antigravity_legacy_present = bool(google_antigravity["configured"])

        warnings: list[str] = []
        if current_primary_broken:
            if str(openai_codex.get("signal_fail_code") or "") == "runtime_missing_scope_model_request":
                warnings.append("Текущий OpenAI primary блокируется по OAuth scopes (`model.request`) и не годится как production primary.")
            else:
                warnings.append("Текущий OpenAI primary падает с model_not_found и не годится как production primary.")
        if google_gemini_cli_unavailable:
            warnings.append(
                "Google Gemini CLI OAuth сейчас не является надёжным fallback: профиль в cooldown/expired и может требовать re-auth."
            )
        if not target_in_runtime:
            warnings.append("GPT-5.4 пока не описан в runtime models.json OpenClaw и не готов к promotion.")
        if antigravity_legacy_present:
            warnings.append(
                "Legacy provider google-antigravity уже удалён в OpenClaw 2026.3.8+ и не должен использоваться как fallback; миграция идёт через google-gemini-cli или google/* API key."
            )
        elif antigravity_disabled:
            warnings.append("Google Antigravity сейчас disabled в auth-profiles и не должен считаться надёжным fallback.")

        temporary_primary = current_primary
        if current_primary_broken:
            temporary_primary = next(
                (
                    candidate
                    for candidate in current_fallbacks
                    if not (
                        candidate.startswith("google-antigravity/")
                        and (antigravity_disabled or antigravity_legacy_present)
                    )
                    and not (
                        candidate.startswith("google-gemini-cli/")
                        and google_gemini_cli_unavailable
                    )
                ),
                "",
            )

        return {
            "current_primary": current_primary,
            "current_fallbacks": current_fallbacks,
            "target_primary_candidate": target_primary,
            "target_primary_in_runtime": target_in_runtime,
            "current_primary_broken": current_primary_broken,
            "temporary_primary_recommendation": temporary_primary,
            "openai_codex": openai_codex,
            "google_gemini_cli": google_gemini_cli,
            "google_antigravity": google_antigravity,
            "google_antigravity_legacy_removed": antigravity_legacy_present,
            "warnings": warnings,
            "workspace": str(defaults.get("workspace", "") or ""),
        }

    @staticmethod
    def _openclaw_cli_env() -> dict[str, str]:
        """
        Формирует env для вызовов `openclaw` CLI из web-панели.

        Почему:
        - `openclaw channels status --probe` должен использовать тот же token,
        что и runtime/gateway, иначе probe может давать ложный
        `gateway not reachable` при живом сокете.
        """
        env = dict(os.environ)
        gateway_token = WebApp._openclaw_gateway_token_from_config()
        if not gateway_token:
            gateway_token = str(os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or "").strip()
        if gateway_token:
            env["OPENCLAW_GATEWAY_TOKEN"] = gateway_token
        return env

    @staticmethod
    def _clone_jsonish_dict(payload: dict[str, Any]) -> dict[str, Any]:
        """Возвращает безопасственную неглубокую копию dict/list payload для runtime-cache."""
        cloned: dict[str, Any] = {}
        for key, value in dict(payload or {}).items():
            if isinstance(value, list):
                cloned[key] = list(value)
            elif isinstance(value, dict):
                cloned[key] = dict(value)
            else:
                cloned[key] = value
        return cloned

    @staticmethod
    def _float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
        """Читает float из env с безопасным clamp и без размазывания try/except по коду."""
        raw = str(os.getenv(name, str(default)) or str(default)).strip()
        try:
            value = float(raw)
        except Exception:
            value = float(default)
        return max(float(min_value), min(float(value), float(max_value)))

    @classmethod
    def _lmstudio_snapshot_ttl_sec(cls) -> float:
        """Базовый TTL short-cache для LM Studio snapshot."""
        return cls._float_env(
            "WEB_LMSTUDIO_SNAPSHOT_TTL_SEC",
            10.0,
            min_value=0.0,
            max_value=30.0,
        )

    @classmethod
    def _lmstudio_snapshot_ttl_sec_for_state(cls, state: str) -> float:
        """
        Возвращает TTL snapshot-кэша с поправкой на состояние local runtime.

        Почему state-aware TTL:
        - когда модель уже загружена, truth почти не меняется каждую секунду;
        - именно loaded-state даёт наибольший log-noise в LM Studio при частых refresh панели;
        - для down/idle оставляем более короткий TTL, чтобы UI не залипал при подъёме/падении рантайма.
        """
        normalized = str(state or "").strip().lower()
        base_ttl = cls._lmstudio_snapshot_ttl_sec()
        if normalized == "loaded":
            return cls._float_env(
                "WEB_LMSTUDIO_SNAPSHOT_TTL_LOADED_SEC",
                max(base_ttl, 60.0),
                min_value=0.0,
                max_value=120.0,
            )
        if normalized == "idle":
            return cls._float_env(
                "WEB_LMSTUDIO_SNAPSHOT_TTL_IDLE_SEC",
                max(base_ttl, 20.0),
                min_value=0.0,
                max_value=60.0,
            )
        if normalized == "down":
            return cls._float_env(
                "WEB_LMSTUDIO_SNAPSHOT_TTL_DOWN_SEC",
                min(base_ttl, 5.0),
                min_value=0.0,
                max_value=15.0,
            )
        return base_ttl

    def _invalidate_lmstudio_snapshot_cache(self) -> None:
        """Сбрасывает snapshot-cache после write-операций load/unload."""
        self._lmstudio_snapshot_cache = None
        self._runtime_lite_cache = None

    @classmethod
    def _runtime_lite_ttl_sec_for_state(cls, lm_state: str) -> float:
        """
        TTL для агрегированного runtime-lite snapshot.

        Почему отдельный cache поверх LM snapshot:
        - `health/lite` дёргают чаще всего и именно он формирует фоновые probe-пачки;
        - даже когда LM snapshot уже кэширован, многократная сборка одного и того же
          runtime payload не даёт пользы;
        - loaded-state можно держать чуть дольше без заметной потери UX.
        """
        normalized = str(lm_state or "").strip().lower()
        if normalized == "loaded":
            return cls._float_env(
                "WEB_RUNTIME_LITE_TTL_LOADED_SEC",
                60.0,
                min_value=0.0,
                max_value=120.0,
            )
        if normalized == "idle":
            return cls._float_env(
                "WEB_RUNTIME_LITE_TTL_IDLE_SEC",
                20.0,
                min_value=0.0,
                max_value=60.0,
            )
        if normalized == "down":
            return cls._float_env(
                "WEB_RUNTIME_LITE_TTL_DOWN_SEC",
                5.0,
                min_value=0.0,
                max_value=15.0,
            )
        return cls._float_env(
            "WEB_RUNTIME_LITE_TTL_SEC",
            10.0,
            min_value=0.0,
            max_value=30.0,
        )

    def _run_local_script(
        self,
        script_path: Path,
        *,
        timeout_seconds: int = 90,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Единый раннер локальных .command-скриптов для web API.

        Возвращает нормализованный payload без выброса исключений наружу:
        {
          ok: bool,
          exit_code: int,
          stdout_tail: str,
          error: str
        }
        """
        target = Path(script_path).resolve()
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "exit_code": 127,
                "stdout_tail": "",
                "error": f"script_not_found:{target}",
            }

        cmd = [str(target)] + [str(item) for item in (args or [])]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._project_root()),
                capture_output=True,
                text=True,
                check=False,
                timeout=int(max(5, timeout_seconds)),
            )
            merged = "\n".join(
                item for item in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if item
            )
            return {
                "ok": proc.returncode == 0,
                "exit_code": int(proc.returncode),
                "stdout_tail": self._tail_text(merged, max_chars=2000),
                "error": "",
            }
        except subprocess.TimeoutExpired as exc:
            timeout_tail = self._tail_text(
                "\n".join(
                    item
                    for item in [
                        (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
                        (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")),
                    ]
                    if item
                ),
                max_chars=2000,
            )
            return {
                "ok": False,
                "exit_code": 124,
                "stdout_tail": timeout_tail,
                "error": "script_timeout",
            }
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": 1,
                "stdout_tail": "",
                "error": f"script_run_error:{exc}",
            }

    def _latest_path_by_glob(self, pattern: str) -> Path | None:
        """Возвращает самый свежий путь по glob-паттерну внутри проекта."""
        root = self._project_root()
        items = sorted(
            root.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return items[0] if items else None

    @staticmethod
    def _bool_env(value: str, default: bool = False) -> bool:
        """Безопасно нормализует булево значение из env/строки."""
        raw = str(value or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _git_snapshot(self) -> dict[str, Any]:
        """Снимает минимальный git-срез (ветка/head/short status) для handoff."""
        def _run_git(args: list[str]) -> str:
            try:
                proc = subprocess.run(
                    ["git", *args],
                    cwd=str(self._project_root()),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if proc.returncode != 0:
                    return ""
                return str(proc.stdout or "").strip()
            except Exception:
                return ""

        return {
            "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "head": _run_git(["rev-parse", "HEAD"]),
            "status_short": _run_git(["status", "--short", "--branch"]),
        }

    def _telegram_session_snapshot(self) -> dict[str, Any]:
        """
        Возвращает файловый snapshot Telegram session SQLite.

        Почему так:
        - `WebApp` не держит прямую ссылку на живой `KraabUserbot`,
          поэтому для lite/handoff читаем факт состояния через файловый слой.
        """
        project_root = self._project_root()
        session_name = str(os.getenv("TELEGRAM_SESSION_NAME", "kraab") or "kraab").strip() or "kraab"
        session_dir = project_root / "data" / "sessions"
        session_file = session_dir / f"{session_name}.session"
        wal_file = session_dir / f"{session_name}.session-wal"
        shm_file = session_dir / f"{session_name}.session-shm"
        journal_file = session_dir / f"{session_name}.session-journal"
        lock_files = sorted(str(item.name) for item in session_dir.glob(f"{session_name}*.lock"))

        sqlite_ok: bool | None = None
        sqlite_error = ""
        if session_file.exists():
            try:
                conn = sqlite3.connect(str(session_file), timeout=0.7)
                cur = conn.cursor()
                cur.execute("PRAGMA quick_check;")
                row = cur.fetchone()
                sqlite_ok = bool(row and str(row[0]).lower() == "ok")
                conn.close()
            except Exception as exc:
                sqlite_ok = False
                sqlite_error = str(exc)

        if not session_file.exists():
            state = "missing"
        elif sqlite_ok is False:
            state = "corrupted"
        elif wal_file.exists() or shm_file.exists() or journal_file.exists():
            state = "open_or_unclean"
        else:
            state = "ready"

        return {
            "state": state,
            "session_name": session_name,
            "session_path": str(session_file),
            "session_exists": session_file.exists(),
            "session_size_bytes": int(session_file.stat().st_size) if session_file.exists() else 0,
            "wal_exists": wal_file.exists(),
            "shm_exists": shm_file.exists(),
            "journal_exists": journal_file.exists(),
            "lock_files": lock_files,
            "sqlite_quick_check_ok": sqlite_ok,
            "sqlite_error": sqlite_error,
        }

    @staticmethod
    def _normalize_telegram_session_truth(
        session_snapshot: dict[str, Any], userbot_state: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Нормализует файловое состояние session через живой runtime userbot.

        Почему это нужно:
        - sidecar-файлы SQLite (`-journal/-wal/-shm`) штатно появляются, пока Pyrogram
          держит базу открытой;
        - для owner UI и `/api/health/lite` важно показывать реальную деградацию, а не
          пугать пользователя ложным `open_or_unclean` на живом userbot.
        """
        snapshot = dict(session_snapshot or {})
        raw_state = str(snapshot.get("state") or "unknown").strip() or "unknown"
        startup_state = str((userbot_state or {}).get("startup_state") or "").strip().lower()
        client_connected = bool((userbot_state or {}).get("client_connected"))
        sqlite_ok = snapshot.get("sqlite_quick_check_ok")
        sidecars_present = bool(
            snapshot.get("wal_exists") or snapshot.get("shm_exists") or snapshot.get("journal_exists")
        )

        snapshot["state_file_raw"] = raw_state
        snapshot["state_source"] = "file"

        # Если userbot уже живой и SQLite проходит quick_check, sidecar-файлы считаем
        # признаком активной сессии, а не "грязного" завершения.
        if (
            raw_state == "open_or_unclean"
            and startup_state == "running"
            and client_connected
            and sqlite_ok is not False
            and sidecars_present
        ):
            snapshot["state"] = "ready"
            snapshot["state_source"] = "runtime+file"
            snapshot["state_reason"] = "sqlite_sidecars_expected_while_userbot_running"
            snapshot["state_detail"] = (
                "Userbot подключён; sidecar-файлы SQLite считаются штатным признаком открытой сессии."
            )
        elif raw_state == "ready":
            snapshot["state_reason"] = "sqlite_ready"
        elif raw_state == "open_or_unclean":
            snapshot["state_reason"] = "sqlite_sidecars_without_live_userbot"
        return snapshot

    async def _probe_lmstudio_model_snapshot(self) -> dict[str, Any]:
        """
        Быстрая проверка состояния локальной модели через LM Studio API.

        Возвращает state:
        - `loaded`   -> есть загруженные инстансы;
        - `idle`     -> сервер доступен, но инстансов нет;
        - `down`     -> API недоступен/ошибка транспорта.
        """
        base_url = str(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234") or "").strip().rstrip("/")
        if not base_url:
            return {"state": "down", "base_url": "", "loaded_count": 0, "loaded_models": [], "error": "lm_url_missing"}

        endpoints = [f"{base_url}/api/v1/models", f"{base_url}/v1/models"]
        errors: list[str] = []
        headers = build_lm_studio_auth_headers()

        for endpoint in endpoints:
            try:
                # Для локального LM Studio probe отключаем trust_env/verify:
                # это исключает ложные `FileNotFoundError` из системных cert/proxy
                # и не влияет на безопасность, т.к. endpoint строго локальный/LAN.
                async with httpx.AsyncClient(
                    timeout=2.5,
                    trust_env=False,
                    verify=False,
                    headers=headers or None,
                ) as client:
                    resp = await client.get(endpoint)
                if resp.status_code != 200:
                    errors.append(f"{endpoint}:status={resp.status_code}")
                    continue
                payload = resp.json()
                models = payload.get("models", payload.get("data", []))
                loaded_models: list[str] = []
                for item in models or []:
                    key = str(item.get("key") or item.get("id") or "").strip()
                    instances = item.get("loaded_instances", [])
                    if isinstance(instances, list) and instances:
                        if key:
                            loaded_models.append(key)
                        for inst in instances:
                            inst_id = str((inst or {}).get("id") or "").strip()
                            if inst_id:
                                loaded_models.append(inst_id)
                loaded_models = list(dict.fromkeys(loaded_models))
                return {
                    "state": "loaded" if loaded_models else "idle",
                    "base_url": base_url,
                    "loaded_count": len(loaded_models),
                    "loaded_models": loaded_models,
                    "error": "",
                }
            except Exception as exc:
                errors.append(f"{endpoint}:{exc}")

        return {
            "state": "down",
            "base_url": base_url,
            "loaded_count": 0,
            "loaded_models": [],
            "error": self._tail_text("\n".join(errors), max_chars=400),
        }

    async def _lmstudio_model_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Возвращает LM Studio snapshot с коротким TTL-cache и дедупликацией burst-запросов.

        Почему это решение:
        - web-панель почти одновременно спрашивает `/stats`, `/health/lite`,
          `/model/local/status`, а все они читают один и тот же `/models`;
        - нам нужна живая truth-модель, но без лишнего log-noise и без десятков
          одинаковых GET при одном refresh панели.
        """
        now = time.time()

        if not force_refresh and self._lmstudio_snapshot_cache is not None:
            cached_ts, cached_payload = self._lmstudio_snapshot_cache
            ttl_sec = self._lmstudio_snapshot_ttl_sec_for_state(
                str(cached_payload.get("state") or "")
            )
            if (now - cached_ts) <= ttl_sec:
                return self._clone_jsonish_dict(cached_payload)

        async with self._lmstudio_snapshot_lock:
            now = time.time()
            if not force_refresh and self._lmstudio_snapshot_cache is not None:
                cached_ts, cached_payload = self._lmstudio_snapshot_cache
                ttl_sec = self._lmstudio_snapshot_ttl_sec_for_state(
                    str(cached_payload.get("state") or "")
                )
                if (now - cached_ts) <= ttl_sec:
                    return self._clone_jsonish_dict(cached_payload)

            payload = await self._probe_lmstudio_model_snapshot()
            self._lmstudio_snapshot_cache = (time.time(), self._clone_jsonish_dict(payload))
            return self._clone_jsonish_dict(payload)

    async def _resolve_local_runtime_truth(self, router_obj: Any, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Возвращает authoritative truth для локального runtime.

        Почему это нужно:
        - `router.active_local_model` может отставать от реального состояния LM Studio;
        - web UI должен показывать факт загрузки модели, а не stale-кэш после
          внешнего переключения через helper/UI LM Studio.
        """
        probe = await self._lmstudio_model_snapshot(force_refresh=force_refresh)
        mm = getattr(router_obj, "_mm", None)
        probe_state = str(probe.get("state") or "down").strip().lower()
        probe_loaded = [
            str(item).strip()
            for item in (probe.get("loaded_models") or [])
            if str(item or "").strip()
        ]

        current_model = str(getattr(router_obj, "active_local_model", "") or "").strip()
        loaded_models: list[str] = []
        errors: list[str] = []

        if mm is not None:
            if hasattr(mm, "get_current_model"):
                try:
                    current_model = str(mm.get_current_model() or current_model).strip()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"get_current_model:{exc}")
            # Повторный вызов `model_manager.get_loaded_models()` делаем только когда
            # source-of-truth probe не смог дать полезную картину. Иначе web-запрос
            # сам создавал второй лишний GET `/api/v1/models` к LM Studio.
            should_query_manager_loaded = force_refresh or (probe_state == "down" and not probe_loaded)
            if hasattr(mm, "get_loaded_models") and should_query_manager_loaded:
                try:
                    try:
                        raw_loaded = await mm.get_loaded_models(force_refresh=force_refresh)
                    except TypeError:
                        raw_loaded = await mm.get_loaded_models()
                    if isinstance(raw_loaded, list):
                        loaded_models.extend(
                            [
                                str(item).strip()
                                for item in raw_loaded
                                if str(item or "").strip()
                            ]
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"get_loaded_models:{exc}")

        merged_loaded = list(dict.fromkeys(probe_loaded + loaded_models))

        active_model = ""
        if current_model and current_model in merged_loaded:
            active_model = current_model
        elif merged_loaded:
            # Предпочитаем каноничный model key, а не instance-id вида `model:2`.
            plain_candidates = [item for item in merged_loaded if ":" not in item]
            active_model = plain_candidates[0] if plain_candidates else merged_loaded[0]
        elif probe_state == "loaded" and current_model:
            # Крайний страховочный fallback: probe уже сказал "loaded", но список
            # оказался пустым из-за нестандартного payload. Тогда сохраняем текущую
            # модель без повторного network round-trip.
            active_model = current_model
        runtime_reachable = probe_state in {"loaded", "idle"}
        is_loaded = probe_state == "loaded" or bool(merged_loaded)

        if is_loaded:
            state = "loaded"
        elif runtime_reachable:
            state = "idle"
        else:
            state = "down"

        engine_raw = str(getattr(router_obj, "local_engine", "unknown") or "unknown").strip()
        runtime_url = str(probe.get("base_url") or "").strip()
        if not runtime_url:
            runtime_url = str(getattr(router_obj, "lm_studio_url", "") or "").strip()

        return {
            "state": state,
            "probe_state": probe_state,
            "runtime_reachable": runtime_reachable,
            "is_loaded": is_loaded,
            "active_model": active_model,
            "loaded_models": merged_loaded,
            "engine": engine_raw,
            "runtime_url": runtime_url or "n/a",
            "error": self._tail_text(
                "\n".join([item for item in [str(probe.get("error") or "").strip(), *errors] if item]),
                max_chars=400,
            ),
        }

    def _build_cloud_keys_payload(self, openclaw_obj: Any, router_obj: Any | None = None) -> dict[str, Any]:
        """
        Собирает совместимый cloud-диагностический payload для `/api/stats`.

        Почему:
        - compat-роутер после refactor не всегда отдает `cloud_keys`;
        - UI уже завязан на эти поля и без них рисует ложные WARN/Missing;
        - наружу отдаем только маски, булевы флаги и нормализованный error state.
        """
        gateway_token = self._openclaw_gateway_token_from_config()
        if not gateway_token:
            gateway_token = str(
                os.getenv(
                    "OPENCLAW_GATEWAY_TOKEN",
                    os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", "")),
                )
                or ""
            ).strip()

        token_info: dict[str, Any] = {}
        tier_state: dict[str, Any] = {}
        mm_cloud_state: dict[str, Any] = {}
        if openclaw_obj and hasattr(openclaw_obj, "get_token_info"):
            try:
                raw_token_info = openclaw_obj.get_token_info() or {}
                if isinstance(raw_token_info, dict):
                    token_info = dict(raw_token_info)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_cloud_keys_token_info_failed", error=str(exc))
        if openclaw_obj and hasattr(openclaw_obj, "get_tier_state_export"):
            try:
                raw_tier_state = openclaw_obj.get_tier_state_export() or {}
                if isinstance(raw_tier_state, dict):
                    tier_state = dict(raw_tier_state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_cloud_keys_tier_state_failed", error=str(exc))
        mm = getattr(router_obj, "_mm", None) if router_obj is not None else None
        if mm is not None and hasattr(mm, "get_cloud_runtime_state_export"):
            try:
                raw_mm_cloud_state = mm.get_cloud_runtime_state_export() or {}
                if isinstance(raw_mm_cloud_state, dict):
                    mm_cloud_state = dict(raw_mm_cloud_state)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_cloud_keys_mm_state_failed", error=str(exc))

        active_tier = str(
            tier_state.get("active_tier") or token_info.get("active_tier") or "free"
        ).strip().lower() or "free"
        tiers = token_info.get("tiers") if isinstance(token_info.get("tiers"), dict) else {}
        active_tier_info = tiers.get(active_tier) if isinstance(tiers.get(active_tier), dict) else {}
        if not active_tier_info and isinstance(tiers.get("free"), dict):
            active_tier_info = tiers.get("free") or {}

        current_google_masked = str(
            token_info.get("current_google_key_masked")
            or active_tier_info.get("masked_key")
            or ""
        ).strip()
        gemini_configured = bool(active_tier_info.get("is_configured")) or bool(current_google_masked)

        provider_status = str(tier_state.get("last_provider_status") or "").strip().lower()
        last_probe_at = float(tier_state.get("last_probe_at") or 0.0)
        last_error_code = str(
            tier_state.get("last_error_code") or token_info.get("last_error_code") or ""
        ).strip()
        last_error_message = str(tier_state.get("last_error_message") or "").strip()
        mm_provider_status = str(mm_cloud_state.get("last_provider_status") or "").strip().lower()
        mm_last_probe_at = float(mm_cloud_state.get("last_probe_at") or 0.0)
        mm_last_error_code = str(mm_cloud_state.get("last_error_code") or "").strip()
        mm_last_error_message = str(mm_cloud_state.get("last_error_message") or "").strip()
        mm_active_tier = str(mm_cloud_state.get("active_tier") or "").strip().lower()

        error_is_fresh = False
        if last_probe_at > 0:
            error_is_fresh = (time.time() - last_probe_at) <= 900.0
        mm_error_is_fresh = False
        if mm_last_probe_at > 0:
            mm_error_is_fresh = (time.time() - mm_last_probe_at) <= 900.0

        # Если OpenClawClient ещё не обновил tier-state, но ModelManager discovery уже
        # увидел auth/quota/network ошибку, используем этот state как fallback-истину.
        if (
            mm_error_is_fresh
            and mm_last_error_code
            and provider_status not in {"ok", "auth", "unauthorized", "forbidden", "quota", "error", "timeout"}
            and not (last_error_code and error_is_fresh)
        ):
            provider_status = mm_provider_status or provider_status
            last_probe_at = mm_last_probe_at
            last_error_code = mm_last_error_code
            last_error_message = mm_last_error_message
            error_is_fresh = True
            if mm_active_tier:
                active_tier = mm_active_tier

        if provider_status == "ok":
            gemini_has_error = False
        elif provider_status in {"auth", "unauthorized", "forbidden", "error"}:
            gemini_has_error = True
        else:
            gemini_has_error = bool(last_error_code) and error_is_fresh

        last_error_summary = ""
        if gemini_has_error:
            last_error_summary = last_error_message or last_error_code or "cloud_error"

        return {
            "openclaw": {
                "is_configured": bool(gateway_token),
                "masked_key": self._mask_secret(gateway_token),
            },
            "gemini": {
                "is_configured": gemini_configured,
                "masked_key": current_google_masked,
                "has_error": gemini_has_error,
                "active_tier": active_tier,
            },
            "last_error": {
                "has_error": gemini_has_error,
                "code": last_error_code or "",
                "summary": last_error_summary,
            },
        }

    @staticmethod
    def _build_cloud_tier_payload(cloud_keys: dict[str, Any]) -> dict[str, Any]:
        """
        Нормализует idle/runtime truth для карточки Cloud Tier Status.

        Почему:
        - отсутствие последнего cloud-route не означает `None`;
        - UI должен видеть активный tier (`free`/`paid`) даже в idle-сценарии;
        - при отсутствии Gemini, но наличии gateway, показываем OpenClaw как активный cloud-источник.
        """
        gemini = cloud_keys.get("gemini") if isinstance(cloud_keys.get("gemini"), dict) else {}
        openclaw = cloud_keys.get("openclaw") if isinstance(cloud_keys.get("openclaw"), dict) else {}
        last_error = cloud_keys.get("last_error") if isinstance(cloud_keys.get("last_error"), dict) else {}

        configured_labels: list[str] = []
        if bool(gemini.get("is_configured")):
            configured_labels.append("Gemini")
        if bool(openclaw.get("is_configured")):
            configured_labels.append("OpenClaw")

        active_tier = str(gemini.get("active_tier") or "").strip().lower()
        if bool(gemini.get("is_configured")) and active_tier:
            active_display = active_tier.upper()
        elif bool(openclaw.get("is_configured")):
            active_display = "OPENCLAW"
        else:
            active_display = "None"

        return {
            "active_tier": active_tier,
            "active_display": active_display,
            "configured_labels": configured_labels,
            "configured_count": len(configured_labels),
            "has_error": bool(last_error.get("has_error")),
            "last_error_code": str(last_error.get("code") or ""),
            "last_error_summary": str(last_error.get("summary") or ""),
        }

    async def _build_stats_router_payload(self, router_obj: Any) -> dict[str, Any]:
        """
        Собирает совместимый router payload для `/api/stats`.

        Сохраняем старые поля `get_model_info()`, но поверх них подмешиваем
        runtime truth, чтобы summary/UI не расходились с реальным LM Studio.
        """
        router_info: dict[str, Any] = {}
        if hasattr(router_obj, "get_model_info"):
            try:
                raw_router_info = router_obj.get_model_info() or {}
                if isinstance(raw_router_info, dict):
                    router_info = dict(raw_router_info)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_router_info_failed", error=str(exc))

        router_models = router_info.get("models")
        if not isinstance(router_models, dict):
            router_models = getattr(router_obj, "models", {}) or {}
        if isinstance(router_models, dict):
            current_chat_model = str(router_models.get("chat", "") or "").strip()
            if current_chat_model:
                router_info["current_model"] = current_chat_model
                router_info["models"] = {str(k): str(v) for k, v in router_models.items()}

        local_truth = await self._resolve_local_runtime_truth(router_obj)
        openclaw = self.deps.get("openclaw_client")

        last_route: dict[str, Any] = {}
        if hasattr(router_obj, "get_last_route"):
            try:
                raw_last_route = router_obj.get_last_route() or {}
                if isinstance(raw_last_route, dict):
                    last_route = dict(raw_last_route)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_router_last_route_failed", error=str(exc))
        if (not last_route or not str(last_route.get("model") or "").strip()) and openclaw and hasattr(openclaw, "get_last_runtime_route"):
            try:
                raw_last_runtime_route = openclaw.get_last_runtime_route() or {}
                if isinstance(raw_last_runtime_route, dict):
                    last_route = dict(raw_last_runtime_route)
            except Exception as exc:  # noqa: BLE001
                logger.warning("web_stats_openclaw_last_route_failed", error=str(exc))

        cloud_keys = router_info.get("cloud_keys")
        if not isinstance(cloud_keys, dict) or not cloud_keys:
            cloud_keys = self._build_cloud_keys_payload(openclaw, router_obj)
        cloud_tier = self._build_cloud_tier_payload(cloud_keys)

        local_model = str(local_truth.get("active_model") or "").strip()
        local_engine = str(
            local_truth.get("engine")
            or router_info.get("local_engine")
            or getattr(router_obj, "local_engine", "")
            or ""
        ).strip()
        runtime_url = str(
            local_truth.get("runtime_url")
            or router_info.get("lm_studio_url")
            or getattr(router_obj, "lm_studio_url", "")
            or ""
        ).strip()

        return {
            **router_info,
            "local_model": local_model,
            "active_local_model": local_model,
            "loaded_local_models": list(local_truth.get("loaded_models") or []),
            "local_runtime_state": str(local_truth.get("state") or "down"),
            "local_runtime_probe_state": str(local_truth.get("probe_state") or "down"),
            "local_runtime_error": str(local_truth.get("error") or ""),
            "is_local_available": bool(local_truth.get("runtime_reachable")),
            "local_engine": local_engine,
            "lm_studio_url": runtime_url,
            "last_route": last_route if isinstance(last_route, dict) else {},
            "cloud_keys": cloud_keys,
            "cloud_tier": cloud_tier,
            "scheduler_enabled": bool(getattr(config, "SCHEDULER_ENABLED", False)),
        }

    def _derive_openclaw_auth_state(
        self,
        *,
        last_runtime_route: dict[str, Any],
        tier_state: dict[str, Any],
    ) -> str:
        """
        Возвращает нормализованный auth-state для UI:
        `missing`, `unauthorized`, `ok`, `configured`.
        """
        token = str(
            os.getenv(
                "OPENCLAW_GATEWAY_TOKEN",
                os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", "")),
            )
            or ""
        ).strip()
        if not token:
            return "missing"

        auth_error_codes = {"auth_invalid", "unsupported_key_type", "openclaw_auth_unauthorized"}
        provider_status = str(tier_state.get("last_provider_status") or "").strip().lower()
        # Если runtime-probe провайдера явно "ok", не залипаем на архивных last_error_code.
        if provider_status == "ok":
            return "ok"

        route_error = str(last_runtime_route.get("error_code") or "").strip().lower()
        if route_error in auth_error_codes:
            return "unauthorized"

        tier_error = str(tier_state.get("last_error_code") or "").strip().lower()
        tier_last_probe_at = float(tier_state.get("last_probe_at") or 0.0)
        tier_auth_fresh = False
        if tier_last_probe_at > 0:
            tier_auth_fresh = (time.time() - tier_last_probe_at) <= 900.0
        if tier_error in auth_error_codes and tier_auth_fresh:
            return "unauthorized"

        if provider_status in {"auth", "unauthorized", "forbidden"}:
            return "unauthorized"

        route_detail = str(last_runtime_route.get("route_detail") or "").strip().lower()
        if "401" in route_detail or "unauthorized" in route_detail or "forbidden" in route_detail:
            return "unauthorized"
        return "configured"

    async def _build_runtime_lite_snapshot_uncached(self) -> dict[str, Any]:
        """Собирает легковесный runtime-срез для `/api/health/lite` без cache."""
        openclaw = self.deps.get("openclaw_client")
        kraab_userbot = self.deps.get("kraab_userbot")
        last_runtime_route = {}
        tier_state = {}
        telegram_userbot_state: dict[str, Any] = {}
        if openclaw and hasattr(openclaw, "get_last_runtime_route"):
            try:
                last_runtime_route = dict(openclaw.get_last_runtime_route() or {})
            except Exception:
                last_runtime_route = {}
        if openclaw and hasattr(openclaw, "get_tier_state_export"):
            try:
                tier_state = dict(openclaw.get_tier_state_export() or {})
            except Exception:
                tier_state = {}
        if kraab_userbot and hasattr(kraab_userbot, "get_runtime_state"):
            try:
                telegram_userbot_state = dict(kraab_userbot.get_runtime_state() or {})
            except Exception:
                telegram_userbot_state = {}

        telegram_session = self._normalize_telegram_session_truth(
            self._telegram_session_snapshot(),
            telegram_userbot_state,
        )
        lmstudio = await self._lmstudio_model_snapshot()
        openclaw_auth_state = self._derive_openclaw_auth_state(
            last_runtime_route=last_runtime_route,
            tier_state=tier_state,
        )

        return {
            "telegram_session_state": telegram_session.get("state", "unknown"),
            "telegram_session": telegram_session,
            "lmstudio_model_state": lmstudio.get("state", "unknown"),
            "lmstudio": lmstudio,
            "openclaw_auth_state": openclaw_auth_state,
            "last_runtime_route": last_runtime_route,
            "openclaw_tier_state": tier_state,
            "telegram_userbot": telegram_userbot_state,
            "scheduler_enabled": bool(getattr(config, "SCHEDULER_ENABLED", False)),
            "inbox_summary": inbox_service.get_summary(),
            "voice_gateway_configured": bool(
                str(os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090") or "").strip()
            ),
        }

    async def _collect_runtime_lite_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Возвращает runtime-lite snapshot с коротким TTL-cache.

        Почему не только LM snapshot:
        - UI и внешние health/watch клиенты чаще всего дёргают именно `/api/health/lite`;
        - при loaded-state этого достаточно, чтобы не опрашивать LM Studio и соседние
          runtime-срезы на каждый одинаковый тик;
        - cache сбрасывается на write-path load/unload, так что stale-окно контролируемое.
        """
        now = time.time()

        if not force_refresh and self._runtime_lite_cache is not None:
            cached_ts, cached_payload = self._runtime_lite_cache
            ttl_sec = self._runtime_lite_ttl_sec_for_state(
                str(cached_payload.get("lmstudio_model_state") or "")
            )
            if (now - cached_ts) <= ttl_sec:
                return self._clone_jsonish_dict(cached_payload)

        async with self._runtime_lite_lock:
            now = time.time()
            if not force_refresh and self._runtime_lite_cache is not None:
                cached_ts, cached_payload = self._runtime_lite_cache
                ttl_sec = self._runtime_lite_ttl_sec_for_state(
                    str(cached_payload.get("lmstudio_model_state") or "")
                )
                if (now - cached_ts) <= ttl_sec:
                    return self._clone_jsonish_dict(cached_payload)

            payload = await self._build_runtime_lite_snapshot_uncached()
            self._runtime_lite_cache = (time.time(), self._clone_jsonish_dict(payload))
            return self._clone_jsonish_dict(payload)

    def _assistant_rate_limit_per_min(self) -> int:
        """Возвращает лимит запросов assistant API в минуту на одного клиента."""
        raw = os.getenv("WEB_ASSISTANT_RATE_LIMIT_PER_MIN", "30").strip()
        try:
            value = int(raw)
        except Exception:
            value = 30
        return max(1, value)

    def _enforce_assistant_rate_limit(self, client_key: str) -> None:
        """Простой in-memory rate-limit для web-native assistant."""
        now = time.time()
        window_sec = 60.0
        limit = self._assistant_rate_limit_per_min()
        key = client_key or "anonymous"
        bucket = self._assistant_rate_state.setdefault(key, [])
        # Оставляем только события за последнюю минуту.
        bucket[:] = [ts for ts in bucket if (now - ts) <= window_sec]
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"assistant_rate_limited: limit={limit}/min for client={key}",
            )
        bucket.append(now)

    def _idempotency_ttl_sec(self) -> int:
        """TTL кэша idempotency в секундах."""
        raw = os.getenv("WEB_IDEMPOTENCY_TTL_SEC", "300").strip()
        try:
            value = int(raw)
        except Exception:
            value = 300
        return max(30, value)

    def _idempotency_get(self, namespace: str, key: str) -> dict | None:
        """Возвращает кэшированный ответ по idempotency key, если не истек TTL."""
        if not key:
            return None
        now = time.time()
        ttl = self._idempotency_ttl_sec()
        lookup_key = f"{namespace}:{key}"
        entry = self._idempotency_state.get(lookup_key)
        if not entry:
            return None
        ts, payload = entry
        if (now - ts) > ttl:
            self._idempotency_state.pop(lookup_key, None)
            return None
        data = dict(payload)
        data["idempotent_replay"] = True
        return data

    def _idempotency_set(self, namespace: str, key: str, payload: dict) -> None:
        """Сохраняет ответ по idempotency key."""
        if not key:
            return
        lookup_key = f"{namespace}:{key}"
        self._idempotency_state[lookup_key] = (time.time(), dict(payload))

    @staticmethod
    def _parse_openclaw_channels_probe(raw_output: str) -> dict[str, Any]:
        """
        Нормализует stdout `openclaw channels status --probe` в структуру для UI.

        Возвращает:
        - `channels`: список каналов c полями `name`, `status`, `meta`;
        - `warnings`: список предупреждений;
        - `gateway_reachable`: bool.
        """
        channels: list[dict[str, Any]] = []
        warnings: list[str] = []
        capture_warnings = False
        gateway_reachable = False

        for line in str(raw_output or "").splitlines():
            clean_line = line.strip()
            if not clean_line:
                continue

            low = clean_line.lower()
            if "gateway reachable" in low:
                gateway_reachable = True

            if "warnings:" in low:
                capture_warnings = True
                continue

            if capture_warnings:
                if clean_line.startswith("-"):
                    warnings.append(clean_line.lstrip("- ").strip())
                    continue
                # Если после блока Warnings пошла иная секция — завершаем захват.
                if ":" in clean_line and not clean_line.startswith("http"):
                    capture_warnings = False
                else:
                    continue

            if not clean_line.startswith("- "):
                continue
            if "warnings:" in clean_line.lower():
                continue

            body = clean_line[2:].strip()
            if not body:
                continue

            if ":" in body:
                left, right = body.split(":", 1)
            else:
                left, right = body, ""

            name = left.strip()
            meta = right.strip()
            meta_low = meta.lower()

            # Явный `works` из probe считаем сильнее промежуточного transport-хвоста
            # вроде `disconnected`, иначе UI даёт ложный FAIL в момент успешного reconnect.
            if "works" in meta_low:
                status = "OK"
            elif (
                "not configured" in meta_low
                or "error:" in meta_low
                or "stopped" in meta_low
                or "disconnected" in meta_low
                or "failed" in meta_low
            ):
                status = "FAIL"
            elif "warn" in meta_low:
                status = "WARN"
            elif (
                "running" in meta_low
                or "connected" in meta_low
                or "enabled" in meta_low
            ):
                status = "OK"
            else:
                status = "WARN"

            channels.append(
                {
                    "name": name,
                    "status": status,
                    "meta": meta,
                }
            )

        return {
            "channels": channels,
            "warnings": warnings,
            "gateway_reachable": gateway_reachable,
        }

    @staticmethod
    def _parse_openclaw_gateway_probe(raw_output: str) -> dict[str, Any]:
        """
        Нормализует stdout `openclaw gateway probe`.

        Возвращает:
        - `gateway_reachable`: bool;
        - `local_target`: строка ws://... если найдено;
        - `detail`: краткая причина/комментарий.
        """
        text = str(raw_output or "")
        lower = text.lower()
        gateway_reachable = "reachable: yes" in lower
        local_target = ""
        detail = ""

        for line in text.splitlines():
            clean = line.strip()
            low = clean.lower()
            if clean.startswith("Local loopback ") and "ws://" in clean:
                local_target = clean.replace("Local loopback ", "", 1).strip()
            if "connect: failed -" in low:
                detail = clean
            elif "connect: ok" in low:
                detail = clean
            elif low.startswith("reachable: ") and not detail:
                detail = clean

        if not detail:
            detail = "gateway_probe_no_detail"

        return {
            "gateway_reachable": gateway_reachable,
            "local_target": local_target,
            "detail": detail,
        }

    @staticmethod
    def _classify_browser_http_probe(status_code: int | None, error_text: str = "") -> dict[str, Any]:
        """
        Классифицирует HTTP-пробу browser relay в прозрачное runtime-состояние.

        Состояния:
        - `authorized`: relay доступен и авторизован, но это ещё не доказательство attach;
        - `auth_required`: relay живой, но требует авторизацию (401/403);
        - `unavailable`: relay недоступен/ошибка.
        """
        code = int(status_code) if isinstance(status_code, int) else None
        err = str(error_text or "").strip()

        if code == 200:
            return {
                "state": "authorized",
                "reachable": True,
                "auth_required": False,
                "status_code": code,
                "detail": "browser relay authorized (200)",
            }
        if code in {401, 403}:
            return {
                "state": "auth_required",
                "reachable": True,
                "auth_required": True,
                "status_code": code,
                "detail": f"browser relay auth required ({code})",
            }
        if code is not None:
            return {
                "state": "unavailable",
                "reachable": False,
                "auth_required": False,
                "status_code": code,
                "detail": f"browser relay unexpected status ({code})",
            }
        return {
            "state": "unavailable",
            "reachable": False,
            "auth_required": False,
            "status_code": None,
            "detail": err or "browser relay probe failed",
        }

    @staticmethod
    def _merge_existing_mcp_servers(
        existing_payload: dict[str, Any],
        managed_servers: dict[str, Any],
        managed_names: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Обновляет managed MCP-сервера, сохраняя посторонние custom-записи."""
        existing_servers = dict(existing_payload.get("mcpServers", {}) or {})
        managed_name_set = set(managed_names)
        preserved = sorted(name for name in existing_servers if name not in managed_name_set)
        merged_servers = {
            name: payload
            for name, payload in existing_servers.items()
            if name not in managed_name_set
        }
        merged_servers.update(managed_servers)
        return {"mcpServers": merged_servers}, preserved

    @classmethod
    def _inspect_lmstudio_mcp_sync(cls) -> dict[str, Any]:
        """
        Проверяет, совпадает ли live `~/.lmstudio/mcp.json` с managed реестром.

        Это часть readiness, потому что GUI-клиенты вроде LM Studio могут жить
        на старом `mcp.json`, даже если проектный registry уже обновлён.
        """
        target_path = Path(LMSTUDIO_MCP_PATH).expanduser()
        managed_payload, summary = build_lmstudio_mcp_json(
            include_optional_missing=False,
            include_high_risk=False,
        )

        if not target_path.exists():
            return {
                "status": "missing",
                "path": str(target_path),
                "included": list(summary.get("included", [])),
                "skipped_missing": list(summary.get("skipped_missing", [])),
                "skipped_risk": list(summary.get("skipped_risk", [])),
                "preserved_existing": [],
                "detail": "LM Studio mcp.json ещё не создан.",
            }

        try:
            current_payload = json.loads(target_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            return {
                "status": "error",
                "path": str(target_path),
                "included": list(summary.get("included", [])),
                "skipped_missing": list(summary.get("skipped_missing", [])),
                "skipped_risk": list(summary.get("skipped_risk", [])),
                "preserved_existing": [],
                "detail": f"Не удалось прочитать LM Studio mcp.json: {exc}",
            }

        expected_payload, preserved = cls._merge_existing_mcp_servers(
            current_payload,
            managed_payload.get("mcpServers", {}),
            list(summary.get("managed_names", [])),
        )
        status = "synced" if current_payload == expected_payload else "drift"
        detail = "LM Studio mcp.json синхронизирован с managed registry."
        if status == "drift":
            detail = "LM Studio mcp.json расходится с managed registry и требует sync."

        return {
            "status": status,
            "path": str(target_path),
            "included": list(summary.get("included", [])),
            "skipped_missing": list(summary.get("skipped_missing", [])),
            "skipped_risk": list(summary.get("skipped_risk", [])),
            "preserved_existing": preserved,
            "detail": detail,
        }

    async def _run_openclaw_cli_json(
        self,
        args: list[str],
        *,
        timeout_sec: float = 12.0,
    ) -> tuple[dict[str, Any], str]:
        """Запускает `openclaw ... --json` и безопасно возвращает `(payload, error)`."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._openclaw_cli_env(),
            )
        except Exception as exc:
            return {}, f"cli_spawn_failed: {exc}"

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            return {}, "cli_timeout"

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if int(proc.returncode or 0) != 0:
            return {}, stderr_text or stdout_text or f"exit_code={proc.returncode}"
        if not stdout_text:
            return {}, ""
        try:
            payload = json.loads(stdout_text)
        except ValueError:
            return {}, f"invalid_json_output: {self._tail_text(stdout_text, max_chars=240)}"
        if not isinstance(payload, dict):
            return {"raw": payload}, ""
        return payload, ""

    async def _collect_stable_browser_cli_runtime(
        self,
        *,
        relay_reachable: bool,
        auth_required: bool,
        attempts: int = 3,
        settle_delay_sec: float = 0.8,
    ) -> tuple[dict[str, Any], str, dict[str, Any], str]:
        """
        Снимает browser status/tabs с коротким settle-окном против transient CLI-флапов.

        Почему это нужно:
        - сразу после gateway/browser reconnect OpenClaw CLI может кратко вернуть
          `running=false` и `tabs=[]`, хотя relay уже авторизован и через секунду
          приходит в норму;
        - owner readiness не должен мигать ложным `tab_not_connected` на этом окне.
        """
        safe_attempts = max(1, int(attempts))
        status_payload: dict[str, Any] = {}
        status_error = ""
        tabs_payload: dict[str, Any] = {}
        tabs_error = ""

        for attempt in range(safe_attempts):
            status_payload, status_error = await self._run_openclaw_cli_json(
                ["browser", "--json", "status"],
                timeout_sec=12.0,
            )
            tabs_payload, tabs_error = await self._run_openclaw_cli_json(
                ["browser", "--json", "tabs"],
                timeout_sec=12.0,
            )

            tabs = tabs_payload.get("tabs") if isinstance(tabs_payload, dict) else []
            if not isinstance(tabs, list):
                tabs = []
            running = bool(status_payload.get("running"))

            if status_error or tabs_error:
                break
            if not relay_reachable or auth_required:
                break
            if running or tabs:
                break
            if attempt + 1 < safe_attempts:
                await asyncio.sleep(max(0.0, float(settle_delay_sec)))

        return status_payload, status_error, tabs_payload, tabs_error

    async def _collect_openclaw_browser_smoke_report(self, url: str = "https://example.com") -> dict[str, Any]:
        """Собирает browser smoke report в одном месте для reuse в нескольких endpoint'ах."""
        gateway_probe_raw = ""
        gateway_probe_error = ""
        gateway_reachable = False
        local_target = ""
        gateway_detail = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "gateway",
                "probe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._openclaw_cli_env(),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                gateway_probe_raw = stdout.decode("utf-8", errors="replace")
                parsed_probe = self._parse_openclaw_gateway_probe(gateway_probe_raw)
                gateway_reachable = bool(parsed_probe.get("gateway_reachable"))
                local_target = str(parsed_probe.get("local_target") or "")
                gateway_detail = str(parsed_probe.get("detail") or "")
            except asyncio.TimeoutError:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                gateway_probe_error = "gateway_probe_timeout"
        except Exception as exc:
            gateway_probe_error = f"gateway_probe_failed: {exc}"

        browser_http_status: int | None = None
        browser_http_error = ""
        try:
            browser_headers = self._openclaw_gateway_auth_headers()
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.get("http://127.0.0.1:18791/", headers=browser_headers)
            browser_http_status = int(resp.status_code)
        except Exception as exc:
            browser_http_error = str(exc)

        browser_probe = self._classify_browser_http_probe(browser_http_status, browser_http_error)
        browser_http_reachable = bool(browser_probe.get("reachable"))
        browser_http_state = str(browser_probe.get("state") or "unavailable")
        browser_auth_required = bool(browser_probe.get("auth_required"))
        # Сам HTTP 200 подтверждает auth/доступность relay, но attach вкладки проверяем
        # отдельно через browser status/tabs и action probe.
        tab_attached = False
        relay_reachable = browser_http_reachable

        smoke_ok = bool(gateway_reachable and browser_http_reachable)
        detail_parts: list[str] = []
        if gateway_detail:
            detail_parts.append(f"gateway={gateway_detail}")
        if gateway_probe_error:
            detail_parts.append(gateway_probe_error)
        detail_parts.append(str(browser_probe.get("detail") or "browser relay state unknown"))
        if browser_http_error:
            detail_parts.append(f"browser_http_error={browser_http_error}")

        return {
            "browser_smoke": {
                "ok": smoke_ok,
                "channel": "endpoint" if smoke_ok else "none",
                "tool": "gateway_probe+http_probe",
                "path": url,
                "gateway_reachable": gateway_reachable,
                "browser_http_reachable": browser_http_reachable,
                "browser_http_state": browser_http_state,
                "browser_auth_required": browser_auth_required,
                "relay_reachable": relay_reachable,
                "tab_attached": tab_attached,
                "local_target": local_target,
                "detail": "; ".join(detail_parts) if detail_parts else "n/a",
            },
            "raw": {
                "gateway_probe": self._tail_text(gateway_probe_raw, max_chars=4000),
                "gateway_probe_error": gateway_probe_error,
                "browser_http_status": browser_http_status,
                "browser_http_error": browser_http_error,
            },
        }

    @staticmethod
    def _infer_browser_runtime_contour(browser_status: dict[str, Any]) -> dict[str, Any]:
        """
        Нормализует live browser runtime в owner/debug-контур.

        Почему это важно:
        - `running=true` и `tabs>0` сами по себе не говорят, attach-нут ли мы
          к обычному Chrome владельца или крутим отдельный debug profile;
        - handoff требует правдивого разделения `Мой Chrome` и `Debug browser`,
          чтобы UI не выдавал dedicated relay за owner attach.
        """
        raw_attach_only = browser_status.get("attachOnly")
        attach_only = raw_attach_only if isinstance(raw_attach_only, bool) else None
        profile = str(browser_status.get("profile") or "")
        chosen_browser = str(browser_status.get("chosenBrowser") or "")
        detected_browser = str(browser_status.get("detectedBrowser") or "")
        user_data_dir = str(browser_status.get("userDataDir") or "")
        normalized_user_data_dir = user_data_dir.replace("\\", "/")

        is_dedicated_profile = False
        if "/.openclaw/browser/" in normalized_user_data_dir:
            is_dedicated_profile = True
        elif attach_only is False and profile == "openclaw":
            is_dedicated_profile = True

        active_contour = "unknown"
        active_contour_label = "Не определён"
        if attach_only is True:
            active_contour = "my_chrome"
            active_contour_label = "Мой Chrome"
        elif is_dedicated_profile:
            active_contour = "debug_browser"
            active_contour_label = "Debug browser"
        elif attach_only is False:
            active_contour = "browser_process"
            active_contour_label = "Browser relay"

        return {
            "attach_only": attach_only,
            "profile": profile,
            "chosen_browser": chosen_browser,
            "detected_browser": detected_browser,
            "user_data_dir": user_data_dir,
            "is_dedicated_profile": is_dedicated_profile,
            "active_contour": active_contour,
            "active_contour_label": active_contour_label,
        }

    @staticmethod
    def _classify_browser_stage(
        browser_status: dict[str, Any],
        tabs_payload: dict[str, Any],
        smoke: dict[str, Any],
        *,
        browser_status_error: str = "",
        tabs_error: str = "",
    ) -> dict[str, Any]:
        """Превращает raw browser probes в staged readiness для owner UI."""
        tabs = tabs_payload.get("tabs") if isinstance(tabs_payload, dict) else []
        if not isinstance(tabs, list):
            tabs = []

        running = bool(browser_status.get("running"))
        cdp_ready = bool(browser_status.get("cdpReady"))
        relay_reachable = bool(smoke.get("relay_reachable") or smoke.get("browser_http_reachable"))
        auth_required = bool(smoke.get("browser_auth_required"))
        tab_attached = bool(smoke.get("tab_attached"))
        browser_http_state = str(smoke.get("browser_http_state") or "unavailable")
        tabs_count = len(tabs)
        contour = WebApp._infer_browser_runtime_contour(browser_status)
        active_contour = str(contour.get("active_contour") or "unknown")
        active_contour_label = str(contour.get("active_contour_label") or "Не определён")
        attached_by_runtime = bool(
            (running and cdp_ready and relay_reachable and tabs_count > 0 and not auth_required) or tab_attached
        )
        owner_attach_confirmed = bool(active_contour == "my_chrome" and attached_by_runtime)
        debug_attach_confirmed = bool(active_contour == "debug_browser" and attached_by_runtime)

        warnings: list[str] = []
        blockers: list[str] = []
        next_step = "Проверить конфигурацию OpenClaw browser."
        state = "unknown"
        readiness = "attention"
        stage_label = "Неизвестно"
        summary = str(smoke.get("detail") or "browser readiness unavailable")

        if browser_status_error and not relay_reachable:
            state = "status_error"
            readiness = "blocked"
            stage_label = "CLI status недоступен"
            blockers.append(browser_status_error)
            next_step = "Проверь `openclaw browser status` и доступность runtime CLI."
        elif debug_attach_confirmed:
            state = "debug_attached"
            readiness = "attention"
            stage_label = "Подключён Debug browser"
            summary = "Dedicated browser relay жив, но это не attach к обычному Chrome владельца."
            warnings.append("Сейчас активен dedicated debug browser, а не обычный Chrome владельца.")
            next_step = "Если нужен owner browser, включи Remote Debugging в обычном Chrome и переподключи `chrome-profile`."
        elif attached_by_runtime:
            state = "attached"
            readiness = "ready"
            if owner_attach_confirmed:
                stage_label = "Мой Chrome подключён"
                summary = "Owner browser attach подтверждён."
                next_step = "Контур владельца готов: можно выполнять browser/MCP сценарии."
            else:
                stage_label = "Вкладка подключена"
                summary = "Browser relay готов к automation."
                next_step = "Контур готов: можно выполнять browser/MCP сценарии."
        elif auth_required:
            state = "auth_required"
            readiness = "attention"
            if active_contour == "debug_browser":
                stage_label = "Нужна авторизация Debug browser"
                warnings.append("Dedicated debug browser отвечает, но требует авторизацию/attach.")
                next_step = "Авторизуй debug browser или переключись на attach к обычному Chrome."
            else:
                stage_label = "Нужна авторизация relay"
                warnings.append("Browser relay отвечает, но требует авторизацию/attach.")
                next_step = "Открой Chrome с relay и авторизуй browser session."
        elif tabs_error:
            state = "tabs_error"
            readiness = "blocked"
            stage_label = "Не удалось прочитать вкладки"
            blockers.append(tabs_error)
            next_step = "Проверь `openclaw browser tabs --json`."
        elif relay_reachable and tabs_count == 0:
            state = "tab_not_connected"
            readiness = "attention"
            if active_contour == "debug_browser":
                stage_label = "Debug browser без вкладки"
                warnings.append("Dedicated debug browser жив, но owner Chrome ещё не attach-нут.")
                next_step = "Открой вкладку в debug browser или переключись на attach к обычному Chrome."
            else:
                stage_label = "Нет подключённой вкладки"
                warnings.append("Relay жив, но вкладка ещё не attach-нута.")
                next_step = "Открой вкладку в Chrome и attach через расширение OpenClaw."
        elif not running:
            state = "stopped"
            readiness = "blocked"
            stage_label = "Browser relay остановлен"
            blockers.append("OpenClaw browser сейчас не запущен.")
            next_step = "Запусти `openclaw browser start` или включи `OPENCLAW_BROWSER_AUTOSTART=1`."
        elif not cdp_ready and not relay_reachable:
            state = "starting"
            readiness = "blocked"
            stage_label = "Browser relay поднимается"
            blockers.append("Chrome уже запущен, но CDP/relay ещё не готовы.")
            next_step = "Подожди запуск relay или проверь порт/профиль Chrome."
        elif relay_reachable:
            state = "relay_ready"
            readiness = "attention"
            stage_label = "Relay жив, action flow не завершён"
            warnings.append("HTTP relay доступен, но attach вкладки не подтверждён.")
            next_step = "Проверь attach/авторизацию текущей вкладки."
        else:
            state = "unavailable"
            readiness = "blocked"
            stage_label = "Relay недоступен"
            blockers.append(str(smoke.get("detail") or "browser relay unavailable"))
            next_step = "Проверь gateway probe, browser relay и локальные порты OpenClaw."

        if running and not cdp_ready:
            warnings.append("OpenClaw browser запущен, но CDP ещё не готов.")
        if not running and relay_reachable:
            warnings.append("CLI сообщает running=false, но HTTP relay уже отвечает. Возможен stale status в OpenClaw CLI.")
        if tabs_count > 0 and not tab_attached and state != "attached":
            warnings.append("CLI видит вкладки, но HTTP relay пока не подтвердил attach.")
        if active_contour == "debug_browser" and not owner_attach_confirmed:
            warnings.append("Runtime сейчас смотрит в dedicated OpenClaw profile, а не в обычный Chrome владельца.")

        return {
            "state": state,
            "readiness": readiness,
            "stage_label": stage_label,
            "summary": summary,
            "next_step": next_step,
            "warnings": warnings,
            "blockers": blockers,
            "runtime": {
                "running": running,
                "cdp_ready": cdp_ready,
                "profile": str(contour.get("profile") or ""),
                "cdp_url": str(browser_status.get("cdpUrl") or ""),
                "cdp_port": browser_status.get("cdpPort"),
                "tabs_count": tabs_count,
                "detected_browser": str(contour.get("detected_browser") or ""),
                "chosen_browser": str(contour.get("chosen_browser") or ""),
                "user_data_dir": str(contour.get("user_data_dir") or ""),
                "attach_only": contour.get("attach_only"),
                "is_dedicated_profile": bool(contour.get("is_dedicated_profile")),
                "active_contour": active_contour,
                "active_contour_label": active_contour_label,
                "owner_attach_confirmed": owner_attach_confirmed,
                "debug_attach_confirmed": debug_attach_confirmed,
            },
            "smoke": {
                "relay_reachable": relay_reachable,
                "gateway_reachable": bool(smoke.get("gateway_reachable")),
                "tab_attached": tab_attached,
                "auth_required": auth_required,
                "browser_http_state": browser_http_state,
                "local_target": str(smoke.get("local_target") or ""),
                "detail": str(smoke.get("detail") or ""),
            },
        }

    @staticmethod
    def _managed_mcp_category(name: str) -> str:
        """Грубая категория managed MCP-сервера для UI-группировки."""
        normalized = str(name or "").strip().lower()
        if "browser" in normalized:
            return "browser"
        if normalized in {"filesystem", "filesystem-home", "memory", "shell"}:
            return "core"
        if normalized in {"lmstudio", "openai-chat"}:
            return "llm"
        return "integrations"

    @classmethod
    def _build_mcp_readiness_snapshot(cls, browser: dict[str, Any]) -> dict[str, Any]:
        """Собирает MCP readiness поверх managed registry и LM Studio sync-state."""
        registry = get_managed_mcp_servers()
        required_names = {"filesystem", "memory", "openclaw-browser"}
        sync_state = cls._inspect_lmstudio_mcp_sync()

        servers: list[dict[str, Any]] = []
        ready_count = 0
        attention_count = 0
        blocked_count = 0
        required_ready = 0
        required_attention = 0
        required_blocked = 0
        optional_warnings: list[str] = []

        for name in sorted(registry):
            launch = resolve_managed_server_launch(name)
            missing_env = list(launch.get("missing_env", []))
            manual_setup = list(launch.get("manual_setup", []))
            category = cls._managed_mcp_category(name)
            required_for_owner_browser = name in required_names

            state = "ready_to_launch"
            readiness = "ready"
            detail = "Конфигурация готова к запуску."

            if missing_env:
                state = "missing_env"
                readiness = "blocked" if required_for_owner_browser else "attention"
                detail = f"Отсутствуют обязательные переменные: {', '.join(missing_env)}"
            elif name == "openclaw-browser":
                state = str(browser.get("state") or "unknown")
                readiness = str(browser.get("readiness") or "attention")
                detail = str(browser.get("summary") or browser.get("next_step") or "Browser relay state unknown.")
            elif manual_setup:
                state = "manual_setup_required"
                readiness = "attention"
                detail = manual_setup[0]

            item = {
                "name": name,
                "category": category,
                "required_for_owner_browser": required_for_owner_browser,
                "readiness": readiness,
                "state": state,
                "description": str(launch.get("description") or ""),
                "risk": str(launch.get("risk") or "medium"),
                "missing_env": missing_env,
                "manual_setup": manual_setup,
                "detail": detail,
            }
            servers.append(item)

            if readiness == "ready":
                ready_count += 1
                if required_for_owner_browser:
                    required_ready += 1
            elif readiness == "blocked":
                blocked_count += 1
                if required_for_owner_browser:
                    required_blocked += 1
            else:
                attention_count += 1
                if required_for_owner_browser:
                    required_attention += 1
                elif detail:
                    optional_warnings.append(f"{name}: {detail}")

        readiness = "ready"
        if required_blocked > 0:
            readiness = "blocked"
        elif required_attention > 0 or str(sync_state.get("status") or "") != "synced":
            readiness = "attention"

        warnings: list[str] = []
        if str(sync_state.get("status") or "") == "drift":
            warnings.append("LM Studio mcp.json расходится с managed registry.")
        elif str(sync_state.get("status") or "") == "missing":
            warnings.append("LM Studio mcp.json ещё не создан.")
        elif str(sync_state.get("status") or "") == "error":
            warnings.append(str(sync_state.get("detail") or "LM Studio mcp.json unreadable"))
        warnings.extend(optional_warnings[:4])

        detail = "Managed MCP registry синхронизирован и готов."
        if readiness == "blocked":
            detail = "Есть блокирующие проблемы в обязательных MCP-серверах для owner browser контура."
        elif readiness == "attention":
            detail = "Базовый MCP-контур собран, но ещё есть drift/setup-шаги."

        return {
            "readiness": readiness,
            "detail": detail,
            "warnings": warnings,
            "sync": sync_state,
            "summary": {
                "total": len(servers),
                "ready": ready_count,
                "attention": attention_count,
                "blocked": blocked_count,
                "required_total": len(required_names),
                "required_ready": required_ready,
                "required_attention": required_attention,
                "required_blocked": required_blocked,
            },
            "servers": servers,
        }

    @staticmethod
    def _build_browser_access_paths(browser: dict[str, Any], mcp: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Строит два канонических пути доступа к браузеру без изобретения нового стека.

        Нам важно явно показать пользователю:
        - что уже работает через OpenClaw relay / расширение;
        - что даёт более полный DevTools-путь через обычный Chrome.
        """
        runtime = browser.get("runtime") if isinstance(browser, dict) else {}
        runtime = runtime if isinstance(runtime, dict) else {}
        summary = str(browser.get("summary") or browser.get("next_step") or "Browser path unavailable")
        next_step = str(browser.get("next_step") or "")
        active_contour = str(runtime.get("active_contour") or "unknown")
        active_label = str(runtime.get("active_contour_label") or "Не определён")
        relay_running = bool(runtime.get("running"))
        relay_ready = bool(browser.get("readiness") == "ready")

        relay_detail = summary
        if active_contour == "debug_browser":
            relay_detail = "Active contour: Debug browser. " + summary
        elif active_contour == "my_chrome":
            relay_detail = "Active contour: Мой Chrome. " + summary

        servers = mcp.get("servers") if isinstance(mcp, dict) else []
        if not isinstance(servers, list):
            servers = []
        chrome_profile = next(
            (item for item in servers if isinstance(item, dict) and str(item.get("name") or "") == "chrome-profile"),
            {},
        )
        chrome_manual_setup = chrome_profile.get("manual_setup") if isinstance(chrome_profile, dict) else []
        if not isinstance(chrome_manual_setup, list):
            chrome_manual_setup = []
        chrome_next_step = str(
            chrome_manual_setup[0]
            if chrome_manual_setup
            else chrome_profile.get("detail") or "Путь Chrome DevTools пока не подтверждён."
        )

        return [
            {
                "name": "OpenClaw relay",
                "kind": "openclaw_relay",
                "readiness": str(browser.get("readiness") or "attention"),
                "state": str(browser.get("state") or "unknown"),
                "active": bool(relay_running),
                "active_label": active_label if relay_running else "Не активен",
                "detail": relay_detail,
                "next_step": next_step,
                "preferred_for": "Быстрый доступ через OpenClaw relay/extension.",
                "confirmed": relay_ready,
            },
            {
                "name": "Chrome DevTools",
                "kind": "chrome_devtools",
                "readiness": str(chrome_profile.get("readiness") or "attention"),
                "state": str(chrome_profile.get("state") or "unknown"),
                "active": bool(runtime.get("owner_attach_confirmed")),
                "active_label": "Мой Chrome" if bool(runtime.get("owner_attach_confirmed")) else "Не подтверждён",
                "detail": str(chrome_profile.get("detail") or "Обычный Chrome профиль пока не подтверждён."),
                "next_step": chrome_next_step,
                "preferred_for": "Полный DevTools-контур поверх обычного Chrome профиля.",
                "confirmed": bool(runtime.get("owner_attach_confirmed")),
            },
        ]

    def _web_attachment_max_bytes(self) -> int:
        """Максимальный размер вложения web-панели в байтах."""
        raw = os.getenv("WEB_ATTACHMENT_MAX_MB", "12").strip()
        try:
            value_mb = float(raw)
        except Exception:
            value_mb = 12.0
        value_mb = max(1.0, min(value_mb, 200.0))
        return int(value_mb * 1024 * 1024)

    @staticmethod
    def _sanitize_attachment_name(name: str) -> str:
        """Очищает имя файла до безопасного ASCII-вида."""
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip())
        safe = safe.strip("._")
        return safe or "attachment.bin"

    @staticmethod
    def _trim_prompt_text(text: str, max_chars: int = 24000) -> tuple[str, bool]:
        """Обрезает текст для prompt-контекста, чтобы не перегружать запрос."""
        content = str(text or "")
        if len(content) <= max_chars:
            return content, False
        return content[:max_chars], True

    def _extract_pdf_text(self, raw_bytes: bytes) -> str:
        """Извлекает текст из PDF (если установлен pypdf)."""
        try:
            import pypdf  # type: ignore
        except Exception:
            return ""
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            parts: list[str] = []
            for page in reader.pages[:20]:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    parts.append(page_text)
            return "\n\n".join(parts).strip()
        except Exception:
            return ""

    def _extract_docx_text(self, raw_bytes: bytes) -> str:
        """Извлекает текст из DOCX (если установлен python-docx)."""
        try:
            from docx import Document  # type: ignore
        except Exception:
            return ""
        try:
            document = Document(io.BytesIO(raw_bytes))
            lines = [str(p.text).strip() for p in document.paragraphs if str(p.text).strip()]
            return "\n".join(lines).strip()
        except Exception:
            return ""

    def _build_attachment_prompt(self, *, file_name: str, content_type: str, raw_bytes: bytes, stored_path: Path) -> dict:
        """
        Преобразует загруженный файл в prompt-совместимый контекст.
        Поддержка:
        - text/* и популярные текстовые расширения;
        - PDF / DOCX -> извлечение текста (best effort);
        - image/video/archive -> метаданные + путь к сохранённому файлу.
        """
        ext = Path(file_name).suffix.lower()
        size_bytes = int(len(raw_bytes))
        size_kb = round(size_bytes / 1024.0, 2)
        fingerprint = hashlib.sha256(raw_bytes).hexdigest()[:16]

        text_extensions = {
            ".txt", ".md", ".json", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx",
            ".yaml", ".yml", ".xml", ".html", ".htm", ".log", ".ini", ".toml", ".env",
        }
        is_text_like = content_type.startswith("text/") or ext in text_extensions

        extracted = ""
        kind = "metadata"
        if is_text_like:
            extracted = raw_bytes.decode("utf-8", errors="replace")
            kind = "text"
        elif ext == ".pdf":
            extracted = self._extract_pdf_text(raw_bytes)
            kind = "pdf_text" if extracted else "pdf_metadata"
        elif ext == ".docx":
            extracted = self._extract_docx_text(raw_bytes)
            kind = "docx_text" if extracted else "docx_metadata"
        elif content_type.startswith("image/"):
            kind = "image_metadata"
        elif content_type.startswith("video/"):
            kind = "video_metadata"
        elif ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
            kind = "archive_metadata"

        if extracted:
            trimmed, was_trimmed = self._trim_prompt_text(extracted, max_chars=24000)
            suffix = (
                "\n\n[...контент обрезан для стабильности web-prompt]"
                if was_trimmed else ""
            )
            prompt_snippet = (
                f"Контекст из файла `{file_name}`:\n"
                f"```text\n{trimmed}{suffix}\n```"
            )
        else:
            prompt_snippet = (
                f"Вложение `{file_name}` ({content_type}, {size_kb} KB, sha256:{fingerprint}) "
                f"сохранено локально по пути `{stored_path}`.\n"
                "Если нужно, сначала попроси извлечь/проанализировать содержимое этого типа файла."
            )

        return {
            "kind": kind,
            "file_name": file_name,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256_short": fingerprint,
            "stored_path": str(stored_path),
            "prompt_snippet": prompt_snippet,
            "has_extracted_text": bool(extracted),
        }

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            if self._index_path.exists():
                return FileResponse(self._index_path)
            return HTMLResponse("<h1>Krab Web Panel</h1><p>index.html не найден</p>")

        @self.app.get("/nano_theme.css")
        @self.app.get("/prototypes/nano/nano_theme.css")
        async def nano_theme_css():
            """
            Отдает основной CSS web-панели.

            Дублируем оба URL, чтобы панель стабильно работала и при открытии
            через локальный HTTP, и при старых ссылках после обновлений.
            """
            if self._nano_theme_path.exists():
                return FileResponse(self._nano_theme_path, media_type="text/css")
            raise HTTPException(status_code=404, detail="nano_theme_css_not_found")

        @self.app.get("/api/stats")
        async def get_stats():
            router = self.deps["router"]
            black_box = self.deps.get("black_box")
            rag = router.rag if hasattr(router, "rag") else None
            return {
                "router": await self._build_stats_router_payload(router),
                "black_box": black_box.get_stats() if black_box and hasattr(black_box, "get_stats") else {"enabled": False},
                "rag": rag.get_stats() if rag and hasattr(rag, "get_stats") else {"enabled": False, "count": 0},
            }

        @self.app.get("/api/health")
        async def get_health():
            """Единый health статусов для web-панели."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            lite_snapshot = await self._collect_runtime_lite_snapshot()
            lm_state = str(lite_snapshot.get("lmstudio_model_state") or "unknown").strip().lower()
            local_ok = lm_state in {"loaded", "idle"}
            ecosystem = EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
                local_health_override={
                    "ok": local_ok,
                    "status": "ok" if local_ok else (lm_state or "down"),
                    "degraded": not local_ok,
                    "latency_ms": 0,
                    "source": "web_app.lite_snapshot",
                },
            )
            report = await ecosystem.collect()
            return {
                "status": "ok",
                "checks": {
                    "openclaw": bool(report["checks"]["openclaw"]["ok"]),
                    "local_lm": local_ok,
                    "voice_gateway": bool(report["checks"]["voice_gateway"]["ok"]),
                    "krab_ear": bool(report["checks"]["krab_ear"]["ok"]),
                },
                "degradation": str(report["degradation"]),
                "risk_level": str(report["risk_level"]),
                "chain": report["chain"],
            }

        @self.app.get("/api/health/lite")
        async def get_health_lite():
            """
            Быстрый liveness-check web-панели.

            Важно:
            - не тянет deep ecosystem probes;
            - используется daemon-скриптами и uptime-watch для проверки
              «жив ли HTTP-процесс», а не «все ли внешние зависимости сейчас быстрые».
            """
            runtime = await self._collect_runtime_lite_snapshot()
            return {
                "ok": True,
                "status": "up",
                "telegram_session_state": runtime.get("telegram_session_state"),
                "telegram_userbot_state": (
                    (runtime.get("telegram_userbot") or {}).get("startup_state")
                ),
                "telegram_userbot_error_code": (
                    (runtime.get("telegram_userbot") or {}).get("startup_error_code")
                ),
                "lmstudio_model_state": runtime.get("lmstudio_model_state"),
                "openclaw_auth_state": runtime.get("openclaw_auth_state"),
                "last_runtime_route": runtime.get("last_runtime_route"),
                "scheduler_enabled": runtime.get("scheduler_enabled"),
                "inbox_summary": runtime.get("inbox_summary"),
                "voice_gateway_configured": runtime.get("voice_gateway_configured"),
            }

        @self.app.get("/api/transcriber/status")
        async def transcriber_status():
            """
            Операционный статус транскрибатора.
            Нужен для быстрого понимания: жив ли voice-контур и включена ли crash-защита STT.
            """
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            perceptor = self.deps.get("perceptor")

            openclaw_ok = False
            voice_gateway_ok = False
            krab_ear_ok = False
            try:
                openclaw_ok = bool(await openclaw.health_check()) if openclaw else False
            except Exception:
                openclaw_ok = False
            try:
                voice_gateway_ok = bool(await voice_gateway.health_check()) if voice_gateway else False
            except Exception:
                voice_gateway_ok = False
            try:
                krab_ear_ok = bool(await krab_ear.health_check()) if krab_ear else False
            except Exception:
                krab_ear_ok = False

            def _env_on(key: str, default: str = "0") -> bool:
                return str(os.getenv(key, default)).strip().lower() in {"1", "true", "yes", "on"}

            stt_isolated_worker = _env_on("STT_ISOLATED_WORKER", "1")
            perceptor_isolated_worker = bool(getattr(perceptor, "stt_isolated_worker", stt_isolated_worker))
            stt_worker_timeout = int(str(os.getenv("STT_WORKER_TIMEOUT_SECONDS", "240")).strip() or "240")

            readiness = "ready" if (voice_gateway_ok and perceptor_isolated_worker) else (
                "degraded" if voice_gateway_ok else "down"
            )
            recommendations: list[str] = []
            if not voice_gateway_ok:
                recommendations.append("Запусти ./transcriber_doctor.command --heal")
            if not perceptor_isolated_worker:
                recommendations.append("Включи STT_ISOLATED_WORKER=1 и перезапусти Krab")
            if not recommendations:
                recommendations.append("Система транскрибации в рабочем режиме")

            return {
                "ok": True,
                "status": {
                    "readiness": readiness,
                    "openclaw_ok": openclaw_ok,
                    "voice_gateway_ok": voice_gateway_ok,
                    "krab_ear_ok": krab_ear_ok,
                    "stt_isolated_worker": perceptor_isolated_worker,
                    "stt_worker_timeout_seconds": stt_worker_timeout,
                    "voice_gateway_url": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                    "whisper_model": str(getattr(perceptor, "whisper_model", "")),
                    "audio_warmup_enabled": _env_on("PERCEPTOR_AUDIO_WARMUP", "0"),
                    "recommendations": recommendations,
                },
            }

        @self.app.get("/api/inbox/status")
        async def inbox_status():
            """Возвращает persisted summary owner-visible inbox/escalation слоя."""
            return {
                "ok": True,
                "summary": inbox_service.get_summary(),
            }

        @self.app.get("/api/inbox/items")
        async def inbox_items(
            status: str = Query(default="open"),
            kind: str = Query(default=""),
            limit: int = Query(default=20),
        ):
            """Возвращает inbox items с простыми фильтрами для owner UI/API."""
            return {
                "ok": True,
                "items": inbox_service.list_items(status=status, kind=kind, limit=limit),
            }

        @self.app.post("/api/inbox/update")
        async def inbox_update(
            payload: dict[str, Any] = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Позволяет owner UI подтверждать или закрывать inbox item."""
            self._assert_write_access(x_krab_web_key, token)
            item_id = str(payload.get("item_id") or "").strip()
            status = str(payload.get("status") or "").strip().lower()
            if not item_id:
                raise HTTPException(status_code=400, detail="inbox_empty_item_id")
            try:
                result = inbox_service.set_item_status(item_id, status=status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not result.get("ok"):
                raise HTTPException(status_code=404, detail=str(result.get("error") or "inbox_item_not_found"))
            return {
                "ok": True,
                "result": result,
            }

        @self.app.get("/api/policy")
        async def get_policy():
            """Возвращает runtime-политику AI (queue/guardrails/reactions)."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime:
                return {"ok": False, "error": "ai_runtime_not_configured"}
            return {"ok": True, "policy": ai_runtime.get_policy_snapshot()}

        @self.app.get("/api/queue")
        async def get_queue():
            """Возвращает состояние per-chat очередей автообработки."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime or not hasattr(ai_runtime, "queue_manager"):
                return {"ok": False, "error": "queue_not_configured"}
            return {"ok": True, "queue": ai_runtime.queue_manager.get_stats()}

        @self.app.get("/api/ctx")
        async def get_ctx(chat_id: int | None = Query(default=None)):
            """Snapshot контекста последнего запроса (по чату или все чаты)."""
            ai_runtime = self.deps.get("ai_runtime")
            if not ai_runtime:
                return {"ok": False, "error": "ai_runtime_not_configured"}
            if chat_id is None:
                if not hasattr(ai_runtime, "get_context_snapshots"):
                    return {"ok": False, "error": "ctx_not_supported"}
                return {"ok": True, "contexts": ai_runtime.get_context_snapshots()}
            return {"ok": True, "context": ai_runtime.get_context_snapshot(int(chat_id))}

        @self.app.get("/api/reactions/stats")
        async def get_reactions_stats(chat_id: int | None = Query(default=None)):
            """Сводка по реакциям (общая или по чату)."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "stats": reaction_engine.get_reaction_stats(chat_id=chat_id)}

        @self.app.get("/api/mood/{chat_id}")
        async def get_chat_mood(chat_id: int):
            """Возвращает mood-профиль конкретного чата."""
            reaction_engine = self.deps.get("reaction_engine")
            if not reaction_engine:
                return {"ok": False, "error": "reaction_engine_not_configured"}
            return {"ok": True, "mood": reaction_engine.get_chat_mood(chat_id)}

        @self.app.get("/api/links")
        async def get_links():
            """Ссылки по экосистеме в одном месте."""
            base = self._public_base_url()
            return {
                "dashboard": base,
                "stats_api": f"{base}/api/stats",
                "health_api": f"{base}/api/health",
                "health_lite_api": f"{base}/api/health/lite",
                "ecosystem_health_api": f"{base}/api/ecosystem/health",
                "links_api": f"{base}/api/links",
                "openclaw_cloud_api": f"{base}/api/openclaw/cloud",
                "runtime_handoff_api": f"{base}/api/runtime/handoff",
                "runtime_recover_api": f"{base}/api/runtime/recover",
                "context_checkpoint_api": f"{base}/api/context/checkpoint",
                "context_transition_pack_api": f"{base}/api/context/transition-pack",
                "context_latest_api": f"{base}/api/context/latest",
                "voice_gateway": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                "openclaw": os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789"),
            }

        @self.app.get("/api/openclaw/runtime-config")
        async def openclaw_runtime_config():
            """
            Runtime-конфиг OpenClaw для UI.
            Важно: секрет не отдаём целиком, только masked + флаг присутствия.
            """
            base_url = str(getattr(config, "OPENCLAW_URL", "") or "http://127.0.0.1:18789").strip().rstrip("/")
            raw_key = str(self._openclaw_gateway_token_from_config() or "").strip()
            key_present = False
            key_masked = ""
            key_kind = "missing"
            if raw_key:
                key_present = True
                if raw_key.startswith("{"):
                    key_kind = "tiered_json"
                    key_masked = "tiered-json-configured"
                else:
                    key_kind = "plain"
                    key_masked = self._mask_secret(raw_key)

            return {
                "ok": True,
                "openclaw_base_url": base_url,
                "gateway_token_present": key_present,
                "gateway_token_masked": key_masked,
                "gateway_token_kind": key_kind,
                "gateway_auth_state": "configured" if key_present else "missing",
                "runtime_policy": {
                    "force_cloud": bool(getattr(config, "FORCE_CLOUD", False)),
                    "local_fallback_enabled": bool(getattr(config, "LOCAL_FALLBACK_ENABLED", True)),
                    "native_reasoning_mode": str(
                        getattr(config, "LM_STUDIO_NATIVE_REASONING_MODE", "off") or "off"
                    ).strip().lower(),
                    "photo_force_cloud": bool(getattr(config, "USERBOT_FORCE_CLOUD_FOR_PHOTO", True)),
                    "output_tokens": {
                        "text": int(getattr(config, "USERBOT_MAX_OUTPUT_TOKENS", 1200) or 1200),
                        "photo": int(getattr(config, "USERBOT_PHOTO_MAX_OUTPUT_TOKENS", 420) or 420),
                    },
                    "history_budget": {
                        "dialog_messages": int(getattr(config, "HISTORY_WINDOW_MESSAGES", 50) or 50),
                        "dialog_max_chars": getattr(config, "HISTORY_WINDOW_MAX_CHARS", None),
                        "local_messages": int(getattr(config, "LOCAL_HISTORY_WINDOW_MESSAGES", 18) or 18),
                        "local_max_chars": getattr(config, "LOCAL_HISTORY_WINDOW_MAX_CHARS", None),
                        "retry_messages": int(getattr(config, "RETRY_HISTORY_WINDOW_MESSAGES", 8) or 8),
                        "retry_max_chars": int(getattr(config, "RETRY_HISTORY_WINDOW_MAX_CHARS", 4000) or 4000),
                        "retry_message_max_chars": int(getattr(config, "RETRY_MESSAGE_MAX_CHARS", 1200) or 1200),
                    },
                    "timeouts_sec": {
                        "chunk": float(getattr(config, "OPENCLAW_CHUNK_TIMEOUT_SEC", 180.0) or 180.0),
                        "first_chunk": float(
                            getattr(config, "OPENCLAW_FIRST_CHUNK_TIMEOUT_SEC", 420.0) or 420.0
                        ),
                        "photo_first_chunk": float(
                            getattr(config, "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", 540.0) or 540.0
                        ),
                    },
                },
            }

        @self.app.post("/api/context/checkpoint")
        async def context_checkpoint(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Создает checkpoint для перехода в новый чат (anti-413).
            Вызывает one-click скрипт и возвращает путь к свежему артефакту.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = self._project_root() / "new_chat_checkpoint.command"
            run = self._run_local_script(script_path, timeout_seconds=120)
            if not bool(run.get("ok")):
                detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
                raise HTTPException(status_code=500, detail=f"context_checkpoint_failed:{detail}")

            artifact = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            if artifact is None:
                raise HTTPException(status_code=500, detail="context_checkpoint_failed:no_artifact")

            return {
                "ok": True,
                "artifact_type": "checkpoint",
                "artifact_path": str(artifact),
                "stdout_tail": str(run.get("stdout_tail") or ""),
                "exit_code": int(run.get("exit_code", 0)),
            }

        @self.app.post("/api/context/transition-pack")
        async def context_transition_pack(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Собирает transition-pack для восстановления состояния в новом чате.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = self._project_root() / "build_transition_pack.command"
            run = self._run_local_script(script_path, timeout_seconds=180)
            if not bool(run.get("ok")):
                detail = str(run.get("error") or f"exit_code={run.get('exit_code', 1)}")
                raise HTTPException(status_code=500, detail=f"context_transition_pack_failed:{detail}")

            pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            if pack_dir is None:
                raise HTTPException(status_code=500, detail="context_transition_pack_failed:no_pack_dir")

            transfer_prompt = pack_dir / "TRANSFER_PROMPT_RU.md"
            files_to_attach = pack_dir / "FILES_TO_ATTACH.txt"
            return {
                "ok": True,
                "artifact_type": "transition_pack",
                "pack_dir": str(pack_dir),
                "transfer_prompt_path": str(transfer_prompt) if transfer_prompt.exists() else None,
                "files_to_attach_path": str(files_to_attach) if files_to_attach.exists() else None,
                "stdout_tail": str(run.get("stdout_tail") or ""),
                "exit_code": int(run.get("exit_code", 0)),
            }

        @self.app.get("/api/context/latest")
        async def context_latest():
            """
            Возвращает ссылки на последние anti-413 артефакты.
            """
            checkpoint = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            transfer_prompt = (pack_dir / "TRANSFER_PROMPT_RU.md") if pack_dir else None
            files_to_attach = (pack_dir / "FILES_TO_ATTACH.txt") if pack_dir else None
            return {
                "ok": True,
                "latest_checkpoint_path": str(checkpoint) if checkpoint else None,
                "latest_pack_dir": str(pack_dir) if pack_dir else None,
                "latest_transfer_prompt_path": str(transfer_prompt) if transfer_prompt and transfer_prompt.exists() else None,
                "latest_files_to_attach_path": str(files_to_attach) if files_to_attach and files_to_attach.exists() else None,
            }

        @self.app.get("/api/runtime/handoff")
        async def runtime_handoff():
            """
            Единый runtime-снимок для безопасной миграции в новый чат (Anti-413).

            Формат intentionally machine-readable, чтобы его можно было:
            - сохранить в артефакты;
            - приложить в новый диалог без ручной реконструкции контекста.
            """
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")

            async def _safe_health(client: Any, *, timeout_sec: float = 3.5) -> dict[str, Any]:
                if not client or not hasattr(client, "health_check"):
                    return {"ok": False, "state": "not_configured", "error": ""}
                try:
                    result = await asyncio.wait_for(client.health_check(), timeout=timeout_sec)
                    return {"ok": bool(result), "state": "up" if bool(result) else "down", "error": ""}
                except asyncio.TimeoutError:
                    return {"ok": False, "state": "down", "error": "timeout"}
                except Exception as exc:
                    return {"ok": False, "state": "down", "error": str(exc)}

            runtime_lite = await self._collect_runtime_lite_snapshot()
            openclaw_health = await _safe_health(openclaw, timeout_sec=3.0)
            voice_health = await _safe_health(voice_gateway, timeout_sec=3.0)
            krab_ear_health = await _safe_health(krab_ear, timeout_sec=3.0)

            cloud_runtime: dict[str, Any] = {"available": False, "error": "not_supported"}
            if openclaw and hasattr(openclaw, "get_cloud_runtime_check"):
                try:
                    cloud_report = await asyncio.wait_for(openclaw.get_cloud_runtime_check(), timeout=18.0)
                    cloud_runtime = {"available": True, "report": cloud_report}
                except asyncio.TimeoutError:
                    cloud_runtime = {"available": False, "error": "timeout"}
                except Exception as exc:
                    cloud_runtime = {"available": False, "error": str(exc)}

            latest_bundle = self._latest_path_by_glob("artifacts/handoff_*")
            latest_checkpoint = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            latest_pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            latest_transfer_prompt = (
                str(latest_pack_dir / "TRANSFER_PROMPT_RU.md")
                if latest_pack_dir and (latest_pack_dir / "TRANSFER_PROMPT_RU.md").exists()
                else None
            )

            return {
                "ok": True,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "project_root": str(self._project_root()),
                "git": self._git_snapshot(),
                "health_lite": {
                    "ok": True,
                    "status": "up",
                    "telegram_session_state": runtime_lite.get("telegram_session_state"),
                    "lmstudio_model_state": runtime_lite.get("lmstudio_model_state"),
                    "openclaw_auth_state": runtime_lite.get("openclaw_auth_state"),
                    "last_runtime_route": runtime_lite.get("last_runtime_route"),
                    "inbox_summary": runtime_lite.get("inbox_summary"),
                },
                "runtime": runtime_lite,
                "inbox_summary": inbox_service.get_summary(),
                "services": {
                    "openclaw": openclaw_health,
                    "voice_gateway": voice_health,
                    "krab_ear": krab_ear_health,
                },
                "cloud_runtime": cloud_runtime,
                "masked_secrets": {
                    "openclaw_token": self._mask_secret(
                        os.getenv(
                            "OPENCLAW_GATEWAY_TOKEN",
                            os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", "")),
                        )
                    ),
                    "web_api_key": self._mask_secret(os.getenv("WEB_API_KEY", "")),
                    "gemini_free": self._mask_secret(os.getenv("GEMINI_API_KEY_FREE", "")),
                    "gemini_paid": self._mask_secret(os.getenv("GEMINI_API_KEY_PAID", "")),
                    "openai_api_key": self._mask_secret(os.getenv("OPENAI_API_KEY", "")),
                },
                "artifacts": {
                    "latest_handoff_bundle_dir": str(latest_bundle) if latest_bundle else None,
                    "latest_context_checkpoint": str(latest_checkpoint) if latest_checkpoint else None,
                    "latest_transition_pack_dir": str(latest_pack_dir) if latest_pack_dir else None,
                    "latest_transfer_prompt": latest_transfer_prompt,
                },
            }

        @self.app.post("/api/runtime/recover")
        async def runtime_recover(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Безопасный recovery-плейбук для runtime-контуров.

            Что делает:
            1) `openclaw_runtime_repair.command` (по умолчанию включен),
            2) `sync_openclaw_models.command` (по умолчанию включен),
            3) optional manual tier switch (`force_tier=free|paid`),
            4) optional cloud runtime probe (`probe_cloud_runtime=true`),
            5) возвращает post-check снимок.
            """
            self._assert_write_access(x_krab_web_key, token)
            data = payload or {}
            run_repair = (
                data.get("run_openclaw_runtime_repair", True)
                if isinstance(data.get("run_openclaw_runtime_repair", True), bool)
                else self._bool_env(str(data.get("run_openclaw_runtime_repair", "1")), True)
            )
            run_sync = (
                data.get("run_sync_openclaw_models", True)
                if isinstance(data.get("run_sync_openclaw_models", True), bool)
                else self._bool_env(str(data.get("run_sync_openclaw_models", "1")), True)
            )
            probe_cloud = (
                data.get("probe_cloud_runtime", False)
                if isinstance(data.get("probe_cloud_runtime", False), bool)
                else self._bool_env(str(data.get("probe_cloud_runtime", "0")), False)
            )
            force_tier = str(data.get("force_tier", "") or "").strip().lower()

            steps: list[dict[str, Any]] = []

            if run_repair:
                repair_result = self._run_local_script(
                    self._project_root() / "openclaw_runtime_repair.command",
                    timeout_seconds=120,
                )
                steps.append(
                    {
                        "step": "openclaw_runtime_repair",
                        "ok": bool(repair_result.get("ok")),
                        "exit_code": int(repair_result.get("exit_code", 1)),
                        "error": str(repair_result.get("error") or ""),
                        "stdout_tail": str(repair_result.get("stdout_tail") or ""),
                    }
                )
            else:
                steps.append({"step": "openclaw_runtime_repair", "ok": True, "skipped": True})

            if run_sync:
                sync_result = self._run_local_script(
                    self._project_root() / "sync_openclaw_models.command",
                    timeout_seconds=120,
                )
                steps.append(
                    {
                        "step": "sync_openclaw_models",
                        "ok": bool(sync_result.get("ok")),
                        "exit_code": int(sync_result.get("exit_code", 1)),
                        "error": str(sync_result.get("error") or ""),
                        "stdout_tail": str(sync_result.get("stdout_tail") or ""),
                    }
                )
            else:
                steps.append({"step": "sync_openclaw_models", "ok": True, "skipped": True})

            openclaw = self.deps.get("openclaw_client")
            if force_tier in {"free", "paid"}:
                if not openclaw or not hasattr(openclaw, "switch_cloud_tier"):
                    steps.append(
                        {
                            "step": "switch_cloud_tier",
                            "ok": False,
                            "error": "switch_cloud_tier_not_supported",
                            "requested_tier": force_tier,
                        }
                    )
                else:
                    try:
                        tier_result = await openclaw.switch_cloud_tier(force_tier)
                        steps.append(
                            {
                                "step": "switch_cloud_tier",
                                "ok": bool(tier_result.get("ok")),
                                "requested_tier": force_tier,
                                "result": tier_result,
                            }
                        )
                    except Exception as exc:
                        steps.append(
                            {
                                "step": "switch_cloud_tier",
                                "ok": False,
                                "requested_tier": force_tier,
                                "error": str(exc),
                            }
                        )

            cloud_runtime: dict[str, Any] | None = None
            if probe_cloud:
                if not openclaw or not hasattr(openclaw, "get_cloud_runtime_check"):
                    cloud_runtime = {"available": False, "error": "cloud_runtime_check_not_supported"}
                else:
                    try:
                        probe = await asyncio.wait_for(openclaw.get_cloud_runtime_check(), timeout=18.0)
                        cloud_runtime = {"available": True, "report": probe}
                    except asyncio.TimeoutError:
                        cloud_runtime = {"available": False, "error": "timeout"}
                    except Exception as exc:
                        cloud_runtime = {"available": False, "error": str(exc)}

            runtime_after = await self._collect_runtime_lite_snapshot()
            ok = all(bool(item.get("ok")) for item in steps)
            return {
                "ok": ok,
                "steps": steps,
                "runtime_after": runtime_after,
                "cloud_runtime": cloud_runtime,
            }

        @self.app.get("/api/openclaw/channels/status")
        async def openclaw_channels_status():
            """
            Выполняет 'openclaw channels status --probe' и возвращает
            сырой вывод + распарсенные предупреждения.
            """
            try:
                # [R9] Безопасный запуск через asyncio subprocess с таймаутом.
                proc = await asyncio.create_subprocess_exec(
                    "openclaw", "channels", "status", "--probe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=self._openclaw_cli_env(),
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        try:
                            proc.terminate()
                        except ProcessLookupError:
                            pass
                    return {
                        "ok": False,
                        "error": "openclaw_timeout",
                        "detail": "Запрос статуса каналов превысил 45 сек.",
                    }

                raw_output = stdout.decode("utf-8", errors="replace")

                parsed = self._parse_openclaw_channels_probe(raw_output)
                warnings = list(parsed.get("warnings") or [])
                if not warnings:
                    # Дополнительно ищем строки с WARN вне блока Warnings.
                    for line in raw_output.splitlines():
                        if "WARN" in line.upper():
                            warnings.append(line.strip())

                return {
                    "ok": proc.returncode == 0,
                    "raw": raw_output,
                    "warnings": warnings,
                    "exit_code": proc.returncode,
                    "channels": parsed.get("channels") or [],
                    "gateway_reachable": bool(parsed.get("gateway_reachable")),
                }
            except Exception as exc:
                logger.error("openclaw_status_failed", error=str(exc))
                return {
                    "ok": False,
                    "error": "system_error",
                    "detail": f"Не удалось выполнить openclaw: {exc}",
                }

        @self.app.post("/api/openclaw/channels/runtime-repair")
        async def openclaw_runtime_repair(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Запуск скрипта восстановления рантайма OpenClaw.
            Требует WEB_API_KEY.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = str(self._project_root() / "openclaw_runtime_repair.command")

            try:
                proc = await asyncio.create_subprocess_exec(
                    script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                output = stdout.decode("utf-8", errors="replace")
                return {
                    "ok": proc.returncode == 0,
                    "output": output,
                    "exit_code": proc.returncode,
                }
            except asyncio.TimeoutError:
                return {"ok": False, "error": "timeout", "detail": "Скрипт выполнялся слишком долго (60с)"}
            except Exception as exc:
                return {"ok": False, "error": "system_error", "detail": str(exc)}

        @self.app.post("/api/openclaw/channels/signal-guard-run")
        async def openclaw_signal_guard_run(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Однократный запуск Ops Guard для проверки сигналов.
            Требует WEB_API_KEY.
            """
            self._assert_write_access(x_krab_web_key, token)
            script_path = "/Users/pablito/Antigravity_AGENTS/Краб/scripts/signal_ops_guard.command"

            try:
                # Запускаем с флагом --once для разовой проверки
                proc = await asyncio.create_subprocess_exec(
                    script_path, "--once",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                output = stdout.decode("utf-8", errors="replace")
                return {
                    "ok": proc.returncode == 0,
                    "output": output,
                    "exit_code": proc.returncode,
                }
            except asyncio.TimeoutError:
                return {"ok": False, "error": "timeout", "detail": "Signal Guard выполнялся слишком долго (60с)"}
            except Exception as exc:
                return {"ok": False, "error": "system_error", "detail": str(exc)}

        @self.app.get("/api/ecosystem/health")
        async def ecosystem_health():
            """[R11] Расширенный health-отчет 3-проектной экосистемы с метриками ресурсов."""
            health_service = self.deps.get("health_service")
            if not health_service:
                # Fallback для совместимости, если сервис не в депсах
                router = self.deps["router"]
                openclaw = self.deps.get("openclaw_client")
                voice_gateway = self.deps.get("voice_gateway_client")
                krab_ear = self.deps.get("krab_ear_client")
                health_service = EcosystemHealthService(
                    router=router,
                    openclaw_client=openclaw,
                    voice_gateway_client=voice_gateway,
                    krab_ear_client=krab_ear,
                )
            report = await health_service.collect()
            return {"ok": True, "report": report}

        @self.app.get("/api/system/diagnostics")
        async def system_diagnostics():
            """[R11] Глубокая диагностика сервера (RAM, CPU, Бюджет, Локальные LLM)."""
            router = self.deps.get("router")
            if not router:
                 return {"ok": False, "error": "router_not_found"}

            # Получаем свежие данные через health_service
            health_service = self.deps.get("health_service")
            if not health_service:
                health_service = EcosystemHealthService(router=router)

            health_data = await health_service.collect()
            local_truth = await self._resolve_local_runtime_truth(router)

            status = "ok"
            if not bool(local_truth.get("runtime_reachable")):
                status = "degraded"
                if getattr(router, "active_tier", "") == "default":
                    status = "failed"
            elif getattr(router, "active_tier", "") == "paid":
                status = "degraded"

            return {
                "ok": True,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resources": health_data.get("resources", {}),
                "budget": health_data.get("budget", {}),
                "local_ai": {
                    "engine": local_truth.get("engine", getattr(router, "local_engine", "unknown")),
                    "model": local_truth.get("active_model", ""),
                    "available": bool(local_truth.get("runtime_reachable")),
                    "loaded_models": local_truth.get("loaded_models", []),
                },
                "watchdog": {
                    "last_recoveries": getattr(self.deps.get("watchdog"), "last_recovery_attempt", {})
                }
            }

        @self.app.get("/api/ops/diagnostics")
        async def ops_diagnostics():
            """[R12] Унифицированный операционный отчет (алиас system/diagnostics с расширением)."""
            return await system_diagnostics()

        @self.app.get("/api/ops/metrics")
        async def ops_metrics():
            """Export internal metrics."""
            return {"ok": True, "metrics": metrics.get_snapshot()}

        @self.app.get("/api/ops/timeline")
        @self.app.get("/api/timeline")
        async def ops_timeline(limit: int = 200, min_severity: Optional[str] = None, channel: Optional[str] = None):
            """Export recent event timeline."""
            return {"ok": True, "events": timeline.get_events(limit=limit, min_severity=min_severity, channel=channel)}

        @self.app.get("/api/sla")
        async def get_sla_metrics():
            """Returns dynamic SLA metrics for the NOC-lite UI (Latency p50/p95, Success Rate)."""
            snap = metrics.get_snapshot()
            counters = snap.get("counters", {})
            latencies = snap.get("latencies", {"p50_ms": 0.0, "p95_ms": 0.0})

            # Calculate basic success rate based on counters (this is a simplified sliding window approximation).
            total_success = counters.get("local_success", 0) + counters.get("cloud_success", 0)
            total_fail = counters.get("local_failures", 0) + counters.get("cloud_failures", 0)
            total = total_success + total_fail
            success_rate = (total_success / total * 100.0) if total > 0 else 100.0

            fail_fast_count = counters.get("force_cloud_failfast_total", 0)

            return {
                "ok": True,
                "latency_p50_ms": latencies.get("p50_ms", 0.0),
                "latency_p95_ms": latencies.get("p95_ms", 0.0),
                "success_rate_pct": round(success_rate, 2),
                "fail_fast_count": fail_fast_count,
            }

        @self.app.get("/api/ops/runtime_snapshot")
        async def ops_runtime_snapshot():
            """Deep observability snapshot linking all states."""
            router = self.deps.get("router")
            if not router:
                return {"ok": False, "error": "router_not_found"}
            local_truth = await self._resolve_local_runtime_truth(router)

            task_queue = self.deps.get("queue")
            queue_stats = task_queue.get_metrics() if getattr(task_queue, "get_metrics", None) else {}

            openclaw = router.openclaw_client
            tier_state = openclaw.get_tier_state_export() if getattr(openclaw, "get_tier_state_export", None) else {}

            return {
                "ok": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "router_state": {
                    "is_local_available": bool(local_truth.get("runtime_reachable")),
                    "active_local_model": local_truth.get("active_model", ""),
                    "loaded_local_models": local_truth.get("loaded_models", []),
                    "active_tier": getattr(router, "active_tier", "default"),
                    "local_failures": router._stats.get("local_failures", 0),
                    "cloud_failures": router._stats.get("cloud_failures", 0)
                },
                "tier_state": tier_state,
                "breaker_state": {
                    "preflight_cache": {k: {"expires_in": v[0] - time.time(), "error": v[1]} for k, v in getattr(router, "_preflight_cache", {}).items() if v[0] > time.time()}
                },
                "queue_depth": queue_stats.get("active_tasks", 0),
                "queue_stats": queue_stats,
                "observability": get_observability_snapshot()
            }

        @self.app.post("/api/ops/models")
        async def ops_models_control(
            payload: Dict[str, Any] = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            [R12] Управление жизненным циклом локальных моделей.
            Payload: {"action": "load"|"unload"|"unload_all", "model": "model_name"}
            """
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps.get("router")
            if not router:
                return {"ok": False, "error": "router_not_found"}

            action = payload.get("action")
            model_name = payload.get("model")

            try:
                if action == "load":
                    if not model_name:
                        return {"ok": False, "error": "model_name_required"}
                    success = await router.load_local_model(model_name)
                    return {"ok": success, "action": action, "model": model_name}

                elif action == "unload":
                    if not model_name:
                        return {"ok": False, "error": "model_name_required"}
                    success = await router.unload_model_manual(model_name)
                    return {"ok": success, "action": action, "model": model_name}

                elif action == "unload_all":
                    await router.unload_models_manual()
                    return {"ok": True, "action": action}

                else:
                    return {"ok": False, "error": "invalid_action", "supported": ["load", "unload", "unload_all"]}
            except Exception as e:
                logger.error("ops_models_control_failed", error=str(e))
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        @self.app.get("/api/ecosystem/health/export")
        async def ecosystem_health_export():
            """Экспортирует расширенный ecosystem health report в JSON-файл."""
            router = self.deps["router"]
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")
            payload = await EcosystemHealthService(
                router=router,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            ).collect()
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ecosystem_health_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/model/recommend")
        async def model_recommend(profile: str = Query(default="chat", description="Профиль задачи")):
            router = self.deps["router"]
            return router.get_profile_recommendation(profile)

        @self.app.post("/api/model/preflight")
        async def model_preflight(payload: dict = Body(...)):
            """
            Возвращает preflight-план задачи до выполнения:
            профиль, канал/модель, confirm-step, риски и cost hint.
            """
            router = self.deps["router"]
            if not hasattr(router, "get_task_preflight"):
                return {"ok": False, "error": "task_preflight_not_supported"}

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))

            preflight = router.get_task_preflight(
                prompt=prompt,
                task_type=task_type,
                preferred_model=preferred_model_str,
                confirm_expensive=confirm_expensive,
            )
            return {"ok": True, "preflight": preflight}

        @self.app.get("/api/model/local/status")
        async def model_local_status():
            """Возвращает статус локального рантайма LLM."""
            router = self.deps["router"]
            truth = await self._resolve_local_runtime_truth(router)
            active_model = str(truth.get("active_model") or "").strip()
            engine_raw = str(truth.get("engine") or "unknown").strip()
            runtime_url = str(truth.get("runtime_url") or "n/a").strip()
            lifecycle_status = "loaded" if bool(truth.get("is_loaded")) else "not_loaded"

            return {
                "ok": True,
                # Каноничный формат для frontend R10.
                "status": lifecycle_status,
                "model_name": active_model or "",
                "engine": engine_raw,
                "url": runtime_url or "n/a",
                # Backward compatibility для существующих клиентов.
                "details": {
                    "available": bool(truth.get("runtime_reachable")),
                    "engine": engine_raw,
                    "active_model": active_model,
                    "is_loaded": lifecycle_status == "loaded",
                    "url": runtime_url or "n/a",
                    "loaded_models": truth.get("loaded_models", []),
                    "probe_state": truth.get("probe_state", "down"),
                    "error": truth.get("error", ""),
                },
                # Старый вложенный формат оставляем на переходный период.
                "status_legacy": {
                    "available": bool(truth.get("runtime_reachable")),
                    "engine": engine_raw,
                    "active_model": active_model,
                    "is_loaded": lifecycle_status == "loaded",
                    "url": runtime_url or "n/a",
                    "loaded_models": truth.get("loaded_models", []),
                    "probe_state": truth.get("probe_state", "down"),
                    "error": truth.get("error", ""),
                },
            }

        @self.app.post("/api/model/local/load-default")
        async def model_local_load_default(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Загружает предпочтительную локальную модель (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            preferred = str(getattr(router, "local_preferred_model", "") or "").strip()
            if not preferred:
                # Страховка для compat-роутеров/старых инстансов, где поле могло
                # не быть проброшено, хотя canonical preferred model уже есть в config.
                fallback_preferred = str(getattr(config, "LOCAL_PREFERRED_MODEL", "") or "").strip()
                if fallback_preferred.lower() not in {"", "auto", "smallest"}:
                    preferred = fallback_preferred
            if not preferred:
                return {"ok": False, "error": "no_preferred_model_configured"}

            # Используем существующий механизм smart_load
            success = await router._smart_load(preferred, reason="web_forced")
            self._invalidate_lmstudio_snapshot_cache()
            return {"ok": success, "model": preferred}

        @self.app.post("/api/model/local/unload")
        async def model_local_unload(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Выгружает все локальные модели для освобождения памяти (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]

            freed_gb = 0.0
            if hasattr(router, "_evict_idle_models"):
                # Вызываем с огромным нужным объемом или просто через unload_local_model
                # Но проще через unload_local_model если мы знаем active_model
                active = getattr(router, "active_local_model", None)
                if active:
                    success = await router.unload_local_model(active)
                    if success:
                        router.active_local_model = None
                        self._invalidate_lmstudio_snapshot_cache()
                        return {"ok": True, "unloaded": active}

                # Если активной нет, но есть загруженные (по данным _evict_idle_models)
                freed_gb = await router._evict_idle_models(needed_gb=100.0) # Попытаемся выгрузить всё
                self._invalidate_lmstudio_snapshot_cache()

            return {"ok": True, "freed_gb_estimate": round(freed_gb, 1)}

        @self.app.get("/api/model/explain")
        async def model_explain(
            task_type: str = Query(default="chat", description="Тип задачи для preflight"),
            prompt: str = Query(default="", description="Опциональный prompt для preflight explain"),
            preferred_model: str = Query(default="", description="Опциональная предпочтительная модель"),
            confirm_expensive: bool = Query(default=False, description="Флаг подтверждения дорогого cloud пути"),
        ):
            """
            Explainability endpoint: почему выбран канал/модель.

            Возвращает:
            - last route (route_reason/route_detail);
            - policy snapshot;
            - preflight (если передан prompt).
            """
            router = self.deps["router"]
            normalized_prompt = str(prompt or "").strip()
            normalized_task_type = str(task_type or "chat").strip().lower() or "chat"
            preferred_model_str = str(preferred_model or "").strip() or None

            if hasattr(router, "get_route_explain"):
                explain = router.get_route_explain(
                    prompt=normalized_prompt,
                    task_type=normalized_task_type,
                    preferred_model=preferred_model_str,
                    confirm_expensive=bool(confirm_expensive),
                )
                return {"ok": True, "explain": explain}

            # Fallback для старого роутера без get_route_explain.
            last_route = router.get_last_route() if hasattr(router, "get_last_route") else {}
            preflight = None
            if normalized_prompt and hasattr(router, "get_task_preflight"):
                preflight = router.get_task_preflight(
                    prompt=normalized_prompt,
                    task_type=normalized_task_type,
                    preferred_model=preferred_model_str,
                    confirm_expensive=bool(confirm_expensive),
                )
            return {
                "ok": True,
                "explain": {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "last_route": last_route if isinstance(last_route, dict) else {},
                    "reason": {
                        "code": str(last_route.get("route_reason", "")).strip() or "unknown",
                        "detail": str(last_route.get("route_detail", "")).strip(),
                        "human": "Роутер не поддерживает расширенный explain; показан базовый срез.",
                    },
                    "policy": {
                        "force_mode": str(getattr(router, "force_mode", "auto")),
                        "routing_policy": str(getattr(router, "routing_policy", "unknown")),
                        "cloud_soft_cap_reached": bool(getattr(router, "cloud_soft_cap_reached", False)),
                        "local_available": bool(getattr(router, "is_local_available", False)),
                    },
                    "preflight": preflight,
                    "explainability_score": 40 if last_route else 0,
                    "transparency_level": "low" if not last_route else "medium",
                },
            }

        def _normalize_force_mode(force_mode: str) -> str:
            """Нормализует внутренние force_* режимы в UI-вид: auto/local/cloud."""
            normalized = str(force_mode or "").strip().lower()
            if normalized in {"force_local", "local"}:
                return "local"
            if normalized in {"force_cloud", "cloud"}:
                return "cloud"
            return "auto"

        async def _build_model_catalog(router_obj) -> dict:
            """
            Собирает каталог моделей и текущих настроек для web-панели.
            Нужен для кнопочного UX без ручных `!model` команд.
            """
            cloud_slots_raw = getattr(router_obj, "models", {}) or {}
            cloud_slots = (
                {str(k): str(v) for k, v in cloud_slots_raw.items()}
                if isinstance(cloud_slots_raw, dict)
                else {}
            )
            slot_list = sorted(cloud_slots.keys()) if cloud_slots else ["chat", "thinking", "pro", "coding"]
            force_mode = _normalize_force_mode(getattr(router_obj, "force_mode", "auto"))
            local_truth = await self._resolve_local_runtime_truth(router_obj)
            local_engine = str(local_truth.get("engine") or getattr(router_obj, "local_engine", "") or "")
            local_active_model = str(local_truth.get("active_model") or "")
            local_available = bool(local_truth.get("runtime_reachable"))
            loaded_model_ids = {
                str(item).strip()
                for item in (local_truth.get("loaded_models") or [])
                if str(item or "").strip()
            }

            local_models: list[dict] = []
            local_models_error = ""
            if hasattr(router_obj, "list_local_models_verbose"):
                try:
                    raw_local_models = await router_obj.list_local_models_verbose()
                    if isinstance(raw_local_models, list):
                        for item in raw_local_models:
                            if not isinstance(item, dict):
                                continue
                            model_id = str(item.get("id", "")).strip()
                            if not model_id:
                                continue
                            local_models.append(
                                {
                                    "id": model_id,
                                    "loaded": bool(
                                        model_id == local_active_model
                                        or model_id in loaded_model_ids
                                        or item.get("loaded", False)
                                    ),
                                    "type": str(item.get("type", "llm")),
                                    "size_human": str(item.get("size_human", "n/a")),
                                }
                            )
                except Exception as exc:  # noqa: BLE001
                    local_models_error = str(exc)

            if local_active_model and not any(str(item.get("id")) == local_active_model for item in local_models):
                local_models.insert(
                    0,
                    {
                        "id": local_active_model,
                        "loaded": True,
                        "type": "llm",
                        "size_human": "n/a",
                    },
                )

            cloud_inventory: list[dict[str, Any]] = []
            cloud_presets: list[dict[str, Any]] = []
            alias_items: list[dict[str, str]] = []
            try:
                cloud_inventory = self._build_runtime_cloud_presets(cloud_slots)
                cloud_presets = [
                    item
                    for item in cloud_inventory
                    if bool(item.get("configured_runtime", True))
                ]
                runtime_model_ids = {str(item.get("id", "")).strip() for item in cloud_presets if str(item.get("id", "")).strip()}
                for alias_key in sorted(MODEL_FRIENDLY_ALIASES.keys()):
                    resolved_id, _ = normalize_model_alias(alias_key)
                    if resolved_id not in runtime_model_ids:
                        continue
                    alias_items.append(
                        {
                            "alias": alias_key,
                            "model": resolved_id,
                        }
                    )
            except Exception:
                cloud_inventory = []
                cloud_presets = []
                alias_items = []

            if not cloud_inventory:
                cloud_inventory = self._build_runtime_cloud_presets({})
            if not cloud_presets:
                cloud_presets = [
                    item
                    for item in cloud_inventory
                    if bool(item.get("configured_runtime", True))
                ]
            if not cloud_presets:
                cloud_presets = list(cloud_inventory)

            local_override = local_active_model or str(
                getattr(router_obj, "active_local_model", "") or getattr(config, "LOCAL_PREFERRED_MODEL", "") or ""
            ).strip()
            if not local_override:
                local_override = "nvidia/nemotron-3-nano"

            quick_presets_map = self._build_runtime_quick_presets(
                current_slots=cloud_slots,
                local_override=local_override,
            )
            quick_presets = [
                {
                    "id": preset_id,
                    "title": str(preset_payload.get("title", preset_id)),
                    "description": str(preset_payload.get("description", "")),
                }
                for preset_id, preset_payload in quick_presets_map.items()
            ]
            routing_status = self._build_openclaw_model_routing_status()
            runtime_controls = self._build_openclaw_runtime_controls()

            router_usage_summary = {}
            if hasattr(router_obj, "get_usage_summary"):
                try:
                    router_usage_summary = dict(router_obj.get_usage_summary() or {})
                except Exception:
                    router_usage_summary = {}

            cloud_provider_groups_map: dict[str, dict[str, Any]] = {}
            for item in cloud_inventory:
                provider_name = str(item.get("provider", "") or "").strip()
                if not provider_name:
                    continue
                group = cloud_provider_groups_map.setdefault(
                    provider_name,
                    {
                        "provider": provider_name,
                        "provider_label": str(item.get("provider_label", provider_name)),
                        "provider_auth": str(item.get("provider_auth", "unknown")),
                        "provider_readiness": str(item.get("provider_readiness", "unknown")),
                        "provider_readiness_label": str(item.get("provider_readiness_label", "Configured")),
                        "provider_detail": str(item.get("provider_detail", "")),
                        "provider_quota_state": str(item.get("provider_quota_state", "unknown")),
                        "provider_quota_label": str(item.get("provider_quota_label", "")),
                        "provider_effective_kind": str(item.get("provider_effective_kind", "")),
                        "provider_effective_detail": str(item.get("provider_effective_detail", "")),
                        "provider_oauth_status": str(item.get("provider_oauth_status", "")),
                        "provider_oauth_remaining_human": str(item.get("provider_oauth_remaining_human", "")),
                        "provider_ui": dict(item.get("provider_ui") or {}),
                        "legacy": bool(item.get("legacy")),
                        "models": [],
                    },
                )
                group["models"].append(item)

            cloud_provider_groups = [
                {
                    **group,
                    "model_count": len(group["models"]),
                    "configured_model_count": sum(
                        1 for model in group["models"] if bool(model.get("configured_runtime"))
                    ),
                    "catalog_only_model_count": sum(
                        1 for model in group["models"] if not bool(model.get("configured_runtime"))
                    ),
                    "active_count": sum(1 for model in group["models"] if bool(model.get("active_runtime"))),
                    "selected_slots": sorted(
                        {
                            slot_name
                            for model in group["models"]
                            for slot_name in (model.get("selected_slots") or [])
                            if str(slot_name or "").strip()
                        }
                    ),
                }
                for group in sorted(
                    cloud_provider_groups_map.values(),
                    key=lambda item: self._provider_sort_rank(str(item.get("provider") or "")),
                )
            ]
            parallelism_truth = self._build_openclaw_parallelism_truth()

            return {
                "force_mode": force_mode,
                "slots": slot_list,
                "cloud_slots": cloud_slots,
                "local_engine": local_engine,
                "local_available": local_available,
                "local_active_model": local_active_model,
                "local_models": local_models,
                "local_models_error": local_models_error,
                "cloud_presets": cloud_presets,
                "cloud_inventory": cloud_inventory,
                "cloud_provider_groups": cloud_provider_groups,
                "aliases": alias_items,
                "quick_presets": quick_presets,
                "runtime_model_count": len(cloud_presets),
                "cloud_inventory_count": len(cloud_inventory),
                "runtime_registry_source": "openclaw_models_json+openclaw_models_list_all",
                "router_usage": router_usage_summary,
                "routing_status": routing_status,
                "runtime_controls": runtime_controls,
                "parallelism_truth": parallelism_truth,
                "catalog_guidance": {
                    "primary_flow": "Сначала выбери режим и пресет. Точный слот меняй только в advanced override.",
                    "openai_manual_only": True,
                },
            }

        @self.app.get("/api/model/catalog")
        async def model_catalog():
            """Каталог моделей/режимов для web-панели с кнопочным управлением."""
            router = self.deps["router"]
            return {"ok": True, "catalog": await _build_model_catalog(router)}

        @self.app.post("/api/model/provider-action")
        async def model_provider_action(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Запускает provider-specific repair/migration action из owner-панели."""
            self._assert_write_access(x_krab_web_key, token)

            provider = str(payload.get("provider", "") or "").strip().lower()
            action = str(payload.get("action", "") or "").strip().lower()
            if not provider:
                raise HTTPException(status_code=400, detail="provider_action_provider_required")
            if not action:
                raise HTTPException(status_code=400, detail="provider_action_action_required")

            provider_ui = self._provider_ui_metadata(provider)
            expected_action = str(provider_ui.get("repair_action", "") or "").strip().lower()
            if action not in {"repair_oauth", "migrate_to_gemini_cli"}:
                raise HTTPException(status_code=400, detail=f"provider_action_unsupported:{action}")
            if not expected_action or action != expected_action:
                raise HTTPException(
                    status_code=400,
                    detail=f"provider_action_not_available:{provider}:{action}",
                )

            helper_provider = "google-gemini-cli" if action == "migrate_to_gemini_cli" else provider
            helper_path = self._provider_repair_helper_path(helper_provider)
            if not helper_path or not helper_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"provider_action_helper_missing:{helper_provider}",
                )

            launch = self._launch_local_app(helper_path)
            if not launch.get("ok"):
                raise HTTPException(
                    status_code=500,
                    detail=str(launch.get("error") or "provider_action_launch_failed"),
                )

            detail = str(provider_ui.get("repair_detail", "") or "").strip()
            if action == "migrate_to_gemini_cli":
                message = "✅ Открыт helper миграции на Gemini CLI OAuth."
            else:
                message = f"✅ Открыт helper для провайдера `{provider}`."

            return {
                "ok": True,
                "provider": provider,
                "action": action,
                "message": message,
                "detail": detail,
                "launch": launch,
            }

        @self.app.get("/api/openclaw/model-routing/status")
        async def openclaw_model_routing_status():
            """Read-only статус runtime model routing для owner-панели."""
            routing = self._build_openclaw_model_routing_status()
            openclaw = self.deps.get("openclaw_client")
            last_runtime_route: dict[str, Any] = {}
            if openclaw and hasattr(openclaw, "get_last_runtime_route"):
                try:
                    last_runtime_route = dict(openclaw.get_last_runtime_route() or {})
                except Exception:
                    last_runtime_route = {}

            route_model = str(last_runtime_route.get("model", "") or "").strip()
            route_provider = str(last_runtime_route.get("provider", "") or "").strip()
            route_reason = str(last_runtime_route.get("route_reason", "") or "").strip()
            route_detail = str(last_runtime_route.get("route_detail", "") or "").strip()
            route_status = str(last_runtime_route.get("status", "") or "").strip().lower()
            current_primary = str(routing.get("current_primary", "") or "").strip()
            live_primary_verified = bool(
                route_status == "ok"
                and route_model
                and route_model == current_primary
            )
            live_fallback_active = bool(
                route_status == "ok"
                and route_model
                and current_primary
                and route_model != current_primary
                and "fallback" in route_detail.lower()
            )
            if route_status == "ok" and route_model:
                routing["live_active_model"] = route_model
                routing["live_active_provider"] = route_provider
                routing["live_active_route_reason"] = route_reason
                routing["live_active_route_detail"] = route_detail
            if live_primary_verified:
                routing["current_primary_broken"] = False
                routing["temporary_primary_recommendation"] = current_primary
                warnings = routing.get("warnings")
                if isinstance(warnings, list):
                    routing["warnings"] = [
                        item
                        for item in warnings
                        if "openai primary падает с model_not_found" not in str(item).lower()
                    ]
                routing["live_primary_verified"] = True
                routing["live_fallback_active"] = False
            elif live_fallback_active:
                routing["current_primary_broken"] = True
                routing["temporary_primary_recommendation"] = route_model
                warnings = routing.get("warnings")
                if isinstance(warnings, list):
                    fallback_warning = (
                        f"Сейчас active route идёт через fallback `{route_model}`, "
                        f"а не через configured primary `{current_primary}`."
                    )
                    if fallback_warning not in warnings:
                        warnings.insert(0, fallback_warning)
                routing["live_primary_verified"] = False
                routing["live_fallback_active"] = True
            else:
                routing["live_primary_verified"] = False
                routing["live_fallback_active"] = False

            return {
                "ok": True,
                "routing": routing,
            }

        @self.app.get("/api/userbot/acl/status")
        async def userbot_acl_status():
            """Read-only runtime ACL userbot."""
            return {
                "ok": True,
                "acl": {
                    "path": str(config.USERBOT_ACL_FILE),
                    "owner_username": str(getattr(config, "OWNER_USERNAME", "") or ""),
                    "state": load_acl_runtime_state(),
                    "partial_commands": sorted(PARTIAL_ACCESS_COMMANDS),
                },
            }

        @self.app.post("/api/userbot/acl/update")
        async def userbot_acl_update(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Обновляет runtime ACL userbot через owner web-key."""
            self._assert_write_access(x_krab_web_key, token)
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="acl_update_body_required")
            action = str(body.get("action") or "").strip().lower()
            level = str(body.get("level") or "").strip().lower()
            subject = str(body.get("subject") or "").strip()
            if action not in {"grant", "revoke"}:
                raise HTTPException(status_code=400, detail="acl_update_invalid_action")
            try:
                result = update_acl_subject(level, subject, add=(action == "grant"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "ok": True,
                "acl": {
                    "action": action,
                    "level": result["level"],
                    "subject": result["subject"],
                    "changed": bool(result["changed"]),
                    "path": str(result["path"]),
                    "state": result["state"],
                    "partial_commands": sorted(PARTIAL_ACCESS_COMMANDS),
                },
            }

        @self.app.get("/api/openclaw/model-compat/probe")
        async def openclaw_model_compat_probe(
            model: str = Query(default=""),
            reasoning: str = Query(default="high"),
            skip_reasoning: bool = Query(default=False),
        ):
            """Read-only compatibility probe для target-модели через текущий OpenClaw gateway."""
            payload = _run_openclaw_model_compat_probe(
                model=model,
                reasoning=reasoning,
                skip_reasoning=skip_reasoning,
            )
            return {"ok": True, "probe": payload}

        @self.app.post("/api/model/apply")
        async def model_apply(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Применяет изменения модели/режима из web UI без ручных команд."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            black_box = self.deps.get("black_box")

            action = str(payload.get("action", "")).strip().lower()
            if not action:
                raise HTTPException(status_code=400, detail="model_apply_action_required")

            result_payload: dict[str, object] = {}
            message_text = "✅ Изменения применены."

            if action == "set_mode":
                mode = str(payload.get("mode", "auto")).strip().lower() or "auto"
                if mode not in {"auto", "local", "cloud"}:
                    raise HTTPException(status_code=400, detail="model_apply_invalid_mode")
                if not hasattr(router, "set_force_mode"):
                    raise HTTPException(status_code=400, detail="model_apply_set_mode_not_supported")
                update_result = router.set_force_mode(mode)
                result_payload = {
                    "mode": _normalize_force_mode(getattr(router, "force_mode", "auto")),
                    "router_response": str(update_result),
                }
                message_text = f"✅ Режим обновлен: {result_payload['mode']}"

            elif action == "set_slot_model":
                slot = str(payload.get("slot", "")).strip().lower()
                raw_model = str(payload.get("model", "")).strip()
                if not slot or not raw_model:
                    raise HTTPException(status_code=400, detail="model_apply_slot_and_model_required")
                if not hasattr(router, "models") or not isinstance(getattr(router, "models"), dict):
                    raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")
                if slot not in router.models:
                    available = ", ".join(sorted(router.models.keys()))
                    raise HTTPException(
                        status_code=400,
                        detail=f"model_apply_unknown_slot: {slot}; available={available}",
                    )
                resolved_model, alias_note = normalize_model_alias(raw_model)
                old_model = str(router.models.get(slot, ""))
                router.models[slot] = resolved_model
                result_payload = {
                    "slot": slot,
                    "old_model": old_model,
                    "new_model": resolved_model,
                    "alias_note": alias_note,
                }
                message_text = f"✅ Слот `{slot}`: `{old_model}` → `{resolved_model}`"

            elif action == "apply_preset":
                preset_id = str(payload.get("preset", "")).strip().lower()
                if not preset_id:
                    raise HTTPException(status_code=400, detail="model_apply_preset_required")
                if not hasattr(router, "models") or not isinstance(getattr(router, "models"), dict):
                    raise HTTPException(status_code=400, detail="model_apply_slots_not_supported")

                local_override = str(payload.get("local_model", "")).strip() or str(
                    getattr(router, "active_local_model", "") or ""
                )
                if not local_override:
                    local_override = os.getenv("LOCAL_PREFERRED_MODEL", "nvidia/nemotron-3-nano").strip() or "nvidia/nemotron-3-nano"

                presets = self._build_runtime_quick_presets(
                    current_slots={str(k): str(v) for k, v in router.models.items()},
                    local_override=local_override,
                )
                chosen = presets.get(preset_id)
                if not chosen:
                    raise HTTPException(status_code=400, detail=f"model_apply_unknown_preset: {preset_id}")

                applied_changes: list[dict[str, str]] = []
                for slot, model_id in dict(chosen.get("slots", {})).items():
                    if slot not in router.models:
                        continue
                    resolved_model, _ = normalize_model_alias(str(model_id))
                    previous = str(router.models.get(slot, ""))
                    router.models[slot] = resolved_model
                    applied_changes.append(
                        {
                            "slot": str(slot),
                            "old_model": previous,
                            "new_model": resolved_model,
                        }
                    )

                target_mode = str(payload.get("mode_override", "") or chosen.get("mode", "auto")).strip().lower() or "auto"
                if hasattr(router, "set_force_mode"):
                    router.set_force_mode(target_mode)

                result_payload = {
                    "preset": preset_id,
                    "mode": _normalize_force_mode(getattr(router, "force_mode", "auto")),
                    "changes": applied_changes,
                }
                message_text = f"✅ Пресет `{preset_id}` применён ({len(applied_changes)} слотов)."

            elif action == "set_runtime_chain":
                primary_raw = payload.get("primary")
                fallbacks_raw = payload.get("fallbacks") if isinstance(payload.get("fallbacks"), list) else []
                context_tokens_raw = payload.get("context_tokens")
                thinking_default_raw = payload.get("thinking_default", "off")
                slot_thinking_raw = payload.get("slot_thinking")
                try:
                    applied = self._apply_openclaw_runtime_controls(
                        primary_raw=primary_raw,
                        fallbacks_raw=list(fallbacks_raw),
                        context_tokens_raw=context_tokens_raw,
                        thinking_default_raw=thinking_default_raw,
                        slot_thinking_raw=slot_thinking_raw if isinstance(slot_thinking_raw, dict) else {},
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

                self._runtime_lite_cache = None
                result_payload = {
                    "runtime": applied,
                    "routing_status": self._build_openclaw_model_routing_status(),
                    "runtime_controls": self._build_openclaw_runtime_controls(),
                }
                backup_hint = ""
                if applied.get("backup_openclaw_json"):
                    backup_hint = " backup создан."
                message_text = (
                    f"✅ Глобальная цепочка OpenClaw обновлена: `{applied['primary']}` + "
                    f"{len(applied['fallbacks'])} fallback(s).{backup_hint}"
                )

            else:
                raise HTTPException(status_code=400, detail=f"model_apply_unknown_action: {action}")

            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event("web_model_apply", f"action={action} result={message_text}")

            return {
                "ok": True,
                "action": action,
                "message": message_text,
                "result": result_payload,
                "catalog": await _build_model_catalog(router),
            }

        @self.app.get("/api/model/feedback")
        async def model_feedback_summary(
            profile: str | None = Query(default=None),
            top: int = Query(default=5, ge=1, le=20),
        ):
            """Сводка оценок качества роутинга моделей."""
            router = self.deps["router"]
            if not hasattr(router, "get_feedback_summary"):
                return {"ok": False, "error": "feedback_summary_not_supported"}
            normalized_profile = str(profile).strip().lower() if profile is not None else None
            return {
                "ok": True,
                "feedback": router.get_feedback_summary(profile=normalized_profile, top=top),
            }

        @self.app.post("/api/model/feedback")
        async def model_feedback_submit(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Принимает оценку качества ответа (1-5) для самообучающегося роутинга."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "submit_feedback"):
                return {"ok": False, "error": "feedback_submit_not_supported"}

            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("model_feedback_submit", idem_key)
            if cached:
                return cached

            score = payload.get("score")
            profile = payload.get("profile")
            model_name = payload.get("model")
            channel = payload.get("channel")
            note = payload.get("note", "")

            try:
                result = router.submit_feedback(
                    score=int(score),
                    profile=str(profile).strip().lower() if profile is not None else None,
                    model_name=str(model_name).strip() if model_name is not None else None,
                    channel=str(channel).strip().lower() if channel is not None else None,
                    note=str(note).strip(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"feedback_submit_failed: {exc}") from exc

            response_payload = {"ok": True, "result": result}
            self._idempotency_set("model_feedback_submit", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/ops/usage")
        async def ops_usage():
            """Агрегированный usage-срез роутера моделей."""
            router = self.deps["router"]
            if hasattr(router, "get_usage_summary"):
                return {"ok": True, "usage": router.get_usage_summary()}
            return {"ok": False, "error": "usage_summary_not_supported"}

        @self.app.get("/api/ops/cost-report")
        async def ops_cost_report(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Оценочный отчет по затратам local/cloud маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_cost_report"):
                return {"ok": True, "report": router.get_cost_report(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "cost_report_not_supported"}

        @self.app.get("/api/ops/runway")
        async def ops_runway(
            credits_usd: float = Query(default=300.0, ge=0.0, le=1000000.0),
            horizon_days: int = Query(default=80, ge=1, le=3650),
            reserve_ratio: float = Query(default=0.1, ge=0.0, le=0.95),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """План расхода кредитов: burn-rate, runway и safe calls/day."""
            router = self.deps["router"]
            if hasattr(router, "get_credit_runway_report"):
                return {
                    "ok": True,
                    "runway": router.get_credit_runway_report(
                        credits_usd=credits_usd,
                        horizon_days=horizon_days,
                        reserve_ratio=reserve_ratio,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                }
            return {"ok": False, "error": "ops_runway_not_supported"}

        @self.app.get("/api/ops/executive-summary")
        async def ops_executive_summary(monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000)):
            """Компактный ops executive summary: KPI + риски + рекомендации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_executive_summary"):
                return {"ok": True, "summary": router.get_ops_executive_summary(monthly_calls_forecast=monthly_calls_forecast)}
            return {"ok": False, "error": "ops_executive_summary_not_supported"}

        @self.app.get("/api/ops/report")
        async def ops_report(
            history_limit: int = Query(default=20, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый ops отчет: usage + alerts + costs + history."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_report"):
                return {
                    "ok": True,
                    "report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                }
            return {"ok": False, "error": "ops_report_not_supported"}

        @self.app.get("/api/ops/report/export")
        async def ops_report_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует полный ops report в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            report = router.get_ops_report(
                history_limit=history_limit,
                monthly_calls_forecast=monthly_calls_forecast,
            )
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_report_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(report, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/bundle")
        async def ops_bundle(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Единый bundle: ops report + health snapshot."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False
            return {
                "ok": True,
                "bundle": {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ops_report": router.get_ops_report(
                        history_limit=history_limit,
                        monthly_calls_forecast=monthly_calls_forecast,
                    ),
                    "health": {
                        "openclaw": openclaw_ok,
                        "local_lm": local_ok,
                        "voice_gateway": voice_ok,
                    },
                },
            }

        @self.app.get("/api/ops/bundle/export")
        async def ops_bundle_export(
            history_limit: int = Query(default=50, ge=1, le=200),
            monthly_calls_forecast: int = Query(default=5000, ge=0, le=200000),
        ):
            """Экспортирует единый ops bundle в JSON-файл."""
            router = self.deps["router"]
            if not hasattr(router, "get_ops_report"):
                return {"ok": False, "error": "ops_report_not_supported"}
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            local_ok = await router.check_local_health()
            openclaw_ok = await openclaw.health_check() if openclaw else False
            voice_ok = await voice_gateway.health_check() if voice_gateway else False

            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ops_report": router.get_ops_report(
                    history_limit=history_limit,
                    monthly_calls_forecast=monthly_calls_forecast,
                ),
                "health": {
                    "openclaw": openclaw_ok,
                    "local_lm": local_ok,
                    "voice_gateway": voice_ok,
                },
            }
            ops_dir = Path("artifacts/ops")
            ops_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            out_path = ops_dir / f"ops_bundle_web_{stamp}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            return FileResponse(
                str(out_path),
                media_type="application/json",
                filename=out_path.name,
            )

        @self.app.get("/api/ops/alerts")
        async def ops_alerts():
            """Операционные алерты по расходам и маршрутизации."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_alerts"):
                return {"ok": True, "alerts": router.get_ops_alerts()}
            return {"ok": False, "error": "ops_alerts_not_supported"}

        @self.app.get("/api/ops/history")
        async def ops_history(limit: int = Query(default=30, ge=1, le=200)):
            """История ops snapshot-ов (alerts/status over time)."""
            router = self.deps["router"]
            if hasattr(router, "get_ops_history"):
                return {"ok": True, "history": router.get_ops_history(limit=limit)}
            return {"ok": False, "error": "ops_history_not_supported"}

        @self.app.post("/api/ops/maintenance/prune")
        async def ops_prune(
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Очищает ops history по retention-параметрам."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "prune_ops_history"):
                return {"ok": False, "error": "ops_prune_not_supported"}
            max_age_days = int(payload.get("max_age_days", 30))
            keep_last = int(payload.get("keep_last", 100))
            try:
                result = router.prune_ops_history(max_age_days=max_age_days, keep_last=keep_last)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.post("/api/ops/ack/{code}")
        async def ops_ack(
            code: str,
            payload: dict = Body(default={}),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Подтверждает alert код оператором."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "acknowledge_ops_alert"):
                return {"ok": False, "error": "ops_ack_not_supported"}
            actor = str(payload.get("actor", "web_api")).strip() or "web_api"
            note = str(payload.get("note", "")).strip()
            try:
                result = router.acknowledge_ops_alert(code=code, actor=actor, note=note)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.delete("/api/ops/ack/{code}")
        async def ops_unack(
            code: str,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Снимает подтверждение alert кода."""
            self._assert_write_access(x_krab_web_key, token)
            router = self.deps["router"]
            if not hasattr(router, "clear_ops_alert_ack"):
                return {"ok": False, "error": "ops_unack_not_supported"}
            try:
                result = router.clear_ops_alert_ack(code=code)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result}

        @self.app.post("/api/assistant/attachment")
        async def assistant_attachment_upload(
            file: UploadFile = File(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Загружает вложение для web-assistant и возвращает prompt-snippet.
            Поддерживает текст/PDF/DOCX (извлечение текста best effort),
            а также изображения/видео/архивы (метаданные + локальный путь).
            """
            self._assert_write_access(x_krab_web_key, token)
            black_box = self.deps.get("black_box")

            if not file:
                raise HTTPException(status_code=400, detail="assistant_attachment_file_required")
            original_name = str(file.filename or "").strip()
            if not original_name:
                raise HTTPException(status_code=400, detail="assistant_attachment_filename_required")

            raw = await file.read()
            if not raw:
                raise HTTPException(status_code=400, detail="assistant_attachment_empty_file")

            max_bytes = self._web_attachment_max_bytes()
            if len(raw) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"assistant_attachment_too_large: max={max_bytes} bytes",
                )

            safe_name = self._sanitize_attachment_name(original_name)
            guessed_type = mimetypes.guess_type(safe_name)[0] or ""
            content_type = str(file.content_type or guessed_type or "application/octet-stream")

            uploads_dir = Path("artifacts/web_uploads")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            short_hash = hashlib.sha256(raw).hexdigest()[:10]
            stored_name = f"{ts}_{short_hash}_{safe_name}"
            stored_path = uploads_dir / stored_name
            stored_path.write_bytes(raw)

            attachment = self._build_attachment_prompt(
                file_name=safe_name,
                content_type=content_type,
                raw_bytes=raw,
                stored_path=stored_path,
            )

            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_assistant_attachment",
                    f"name={safe_name} type={content_type} size={len(raw)} kind={attachment.get('kind')}",
                )

            return {"ok": True, "attachment": attachment}

        @self.app.get("/api/assistant/capabilities")
        async def assistant_capabilities():
            """Возвращает возможности web-native assistant режима."""
            return {
                "mode": "web_native",
                "endpoint": "/api/assistant/query",
                "preflight_endpoint": "/api/model/preflight",
                "feedback_endpoint": "/api/model/feedback",
                "model_catalog_endpoint": "/api/model/catalog",
                "model_apply_endpoint": "/api/model/apply",
                "attachment_endpoint": "/api/assistant/attachment",
                "auth": "X-Krab-Web-Key header or token query (if WEB_API_KEY configured)",
                "task_types": ["chat", "coding", "reasoning", "creative", "moderation", "security", "infra", "review"],
                "notes": [
                    "Работает без Telegram-интерфейса.",
                    "Использует тот же роутер моделей и policy, что и Telegram-бот.",
                    "Для критичных задач можно передать `confirm_expensive=true`.",
                    "Оценки качества 1-5 можно отправлять через /api/model/feedback.",
                    "Модельные слоты и режимы можно менять через /api/model/apply.",
                    "Файлы можно загружать через /api/assistant/attachment.",
                ],
            }

        @self.app.post("/api/assistant/query")
        async def assistant_query(
            request: Request,
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_krab_client: str = Header(default="", alias="X-Krab-Client"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """
            Выполняет AI-запрос напрямую через web-панель (без Telegram чата).
            Это must-have для web-first сценариев управления Крабом.
            """
            self._assert_write_access(x_krab_web_key, token)
            client_ip = request.client.host if request.client else "unknown"
            client_key = (x_krab_client or "").strip() or client_ip
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("assistant_query", idem_key)
            if cached:
                return cached
            self._enforce_assistant_rate_limit(client_key)
            router = self.deps.get("router")
            if not router:
                raise HTTPException(status_code=503, detail="router_not_configured")

            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="prompt_required")

            task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
            use_rag = bool(payload.get("use_rag", False))
            preferred_model = payload.get("preferred_model")
            preferred_model_str = str(preferred_model).strip() if preferred_model else None
            confirm_expensive = bool(payload.get("confirm_expensive", False))
            requested_force_mode_raw = str(payload.get("force_mode", "")).strip().lower()
            requested_force_mode = requested_force_mode_raw if requested_force_mode_raw in {"auto", "local", "cloud"} else ""

            def _is_model_status_question(text: str) -> bool:
                low = str(text or "").strip().lower()
                if not low:
                    return False
                patterns = [
                    "на какой модел",
                    "какой моделью",
                    "какая модель",
                    "на чем работаешь",
                    "через какую модель",
                    "what model",
                    "which model",
                ]
                return any(p in low for p in patterns)

            def _build_model_status_from_route(route: dict[str, object]) -> str:
                channel = str(route.get("channel", "unknown"))
                model = str(route.get("model", "unknown"))
                provider = str(route.get("provider", "unknown"))
                tier = str(route.get("active_tier", "-"))
                return (
                    "🧭 Фактический runtime-маршрут:\n"
                    f"- Канал: `{channel}`\n"
                    f"- Модель: `{model}`\n"
                    f"- Провайдер: `{provider}`\n"
                    f"- Cloud tier: `{tier}`"
                )

            # Web UX-хелпер: поддержка команд вида `.model ...` и `!model ...`
            # прямо из web-assistant input. Иначе команда уходила в LLM как обычный prompt.
            command_prompt = prompt
            if command_prompt.startswith(".model"):
                command_prompt = f"!{command_prompt[1:]}"

            if command_prompt.startswith("!model"):
                try:
                    tokens = shlex.split(command_prompt[1:])
                except Exception:
                    tokens = command_prompt[1:].split()

                if not tokens or tokens[0].lower() != "model":
                    raise HTTPException(status_code=400, detail="assistant_model_command_invalid")

                subcommand = tokens[1].strip().lower() if len(tokens) >= 2 else ""
                if subcommand in {"presets", "catalog", "quick"}:
                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": render_model_presets_text(),
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

                if subcommand in {"local", "cloud", "auto"} and hasattr(router, "set_force_mode"):
                    result = router.set_force_mode(subcommand)
                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": f"✅ Режим обновлен: {result}",
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

                if subcommand == "set":
                    # Короткий формат: !model set <model_id> -> slot=chat.
                    if len(tokens) == 3:
                        tokens = [tokens[0], tokens[1], "chat", tokens[2]]
                    parsed = parse_model_set_request(tokens, list(router.models.keys()))
                    if not parsed.get("ok"):
                        response_payload = {
                            "ok": True,
                            "mode": "web_native",
                            "task_type": task_type,
                            "profile": "chat",
                            "command_mode": True,
                            "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                            "reply": str(parsed.get("error") or "❌ Некорректная команда"),
                        }
                        self._idempotency_set("assistant_query", idem_key, response_payload)
                        return response_payload

                    slot = str(parsed["slot"])
                    model_raw = str(parsed["model_name"])
                    model_resolved, alias_note = normalize_model_alias(model_raw)
                    old_value = str(router.models.get(slot, "—"))
                    router.models[slot] = model_resolved

                    reply_lines = []
                    if parsed.get("warning"):
                        reply_lines.append(str(parsed["warning"]))
                    if alias_note:
                        reply_lines.append(alias_note)
                    reply_lines.append(
                        f"✅ Slot `{slot}` обновлен: `{old_value}` → `{model_resolved}`"
                    )
                    reply_lines.append("Подсказка: `!model` или `!model preflight chat Тест`")

                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router.get_last_route() if hasattr(router, "get_last_route") else {},
                        "reply": "\n".join(reply_lines),
                    }
                    self._idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

            try:
                # Если UI передал force_mode, синхронизируем режим до выполнения запроса.
                if requested_force_mode and hasattr(router, "set_force_mode"):
                    router.set_force_mode(requested_force_mode)
                effective_force_mode = _normalize_force_mode(
                    getattr(router, "force_mode", "auto")
                )

                reply = await router.route_query(
                    prompt=prompt,
                    task_type=task_type,
                    context=[],
                    chat_type="private",
                    is_owner=True,
                    use_rag=use_rag,
                    preferred_model=preferred_model_str,
                    confirm_expensive=confirm_expensive,
                )

                # Local-first аварийная деградация:
                # если cloud-ключ скомпрометирован/отклонён, пробуем принудительный local.
                # В force_cloud это запрещено: режим должен быть строго cloud-only.
                leaked_key_marker = "reported as leaked"
                if (
                    isinstance(reply, str)
                    and leaked_key_marker in reply.lower()
                    and effective_force_mode != "cloud"
                    and hasattr(router, "check_local_health")
                ):
                    local_ok = bool(await router.check_local_health(force=True))
                    if local_ok:
                        previous_mode = str(getattr(router, "force_mode", "auto"))
                        try:
                            router.force_mode = "force_local"
                            local_reply = await router.route_query(
                                prompt=prompt,
                                task_type=task_type,
                                context=[],
                                chat_type="private",
                                is_owner=True,
                                use_rag=use_rag,
                                preferred_model=None,
                                confirm_expensive=confirm_expensive,
                            )
                            if isinstance(local_reply, str) and local_reply.strip():
                                reply = (
                                    "⚠️ Cloud API key отклонён (`reported as leaked`). "
                                    "Переключился на local-first ответ.\n\n"
                                    f"{local_reply}"
                                )
                        finally:
                            router.force_mode = previous_mode
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"assistant_query_failed: {exc}") from exc

            profile = router.classify_task_profile(prompt, task_type) if hasattr(router, "classify_task_profile") else task_type
            recommendation = (
                router.get_profile_recommendation(profile)
                if hasattr(router, "get_profile_recommendation")
                else {"profile": profile}
            )
            if hasattr(router, "get_task_preflight"):
                try:
                    preflight = router.get_task_preflight(
                        prompt=prompt,
                        task_type=task_type,
                        preferred_model=preferred_model_str,
                        confirm_expensive=confirm_expensive,
                    )
                except Exception:
                    preflight = {}
                if isinstance(preflight, dict):
                    execution = preflight.get("execution") if isinstance(preflight.get("execution"), dict) else {}
                    recommended_model = str(execution.get("model") or recommendation.get("model") or recommendation.get("recommended_model") or "").strip()
                    recommended_channel = str(execution.get("channel") or recommendation.get("channel") or "").strip()
                    reason_lines = preflight.get("reasons") if isinstance(preflight.get("reasons"), list) else []
                    recommendation = {
                        **(recommendation if isinstance(recommendation, dict) else {}),
                        "profile": str(preflight.get("profile") or profile),
                        "model": recommended_model,
                        "recommended_model": recommended_model,
                        "channel": recommended_channel,
                        "reasoning": "; ".join(str(item) for item in reason_lines if str(item).strip())
                        or str((recommendation or {}).get("reasoning") or ""),
                        "local_available": bool(
                            preflight.get("local_available", (recommendation or {}).get("local_available", False))
                        ),
                        "force_mode": str(
                            execution.get("force_mode")
                            or (recommendation or {}).get("force_mode")
                            or "auto"
                        ),
                    }
            last_route = (
                router.get_last_route()
                if hasattr(router, "get_last_route")
                else {}
            )
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_assistant_query",
                    f"task_type={task_type} profile={profile} prompt_len={len(prompt)} client={client_key}",
                )
            response_payload = {
                "ok": True,
                "mode": "web_native",
                "task_type": task_type,
                "profile": profile,
                "effective_force_mode": _normalize_force_mode(
                    getattr(router, "force_mode", "auto")
                ),
                "recommendation": recommendation,
                "last_route": last_route,
                "reply": reply,
            }
            # Для вопросов о модели отдаём authoritative-ответ из last_route.
            if _is_model_status_question(prompt) and isinstance(last_route, dict) and last_route.get("model"):
                response_payload["reply"] = _build_model_status_from_route(last_route)
            self._idempotency_set("assistant_query", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/openclaw/report")
        async def openclaw_report():
            """Агрегированный health-report OpenClaw."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_health_report"):
                return {"available": False, "error": "openclaw_report_not_supported"}
            try:
                report = await openclaw.get_health_report()
            except Exception as exc:
                return {"available": False, "error": "openclaw_report_failed", "detail": str(exc)}
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/deep-check")
        async def openclaw_deep_check():
            """Расширенная проверка OpenClaw (включая tool smoke и remediation)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_deep_health_report"):
                return {"available": False, "error": "openclaw_deep_check_not_supported"}
            try:
                report = await openclaw.get_deep_health_report()
            except Exception as exc:
                return {"available": False, "error": "openclaw_deep_check_failed", "detail": str(exc)}
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/remediation-plan")
        async def openclaw_remediation_plan():
            """Пошаговый план исправления OpenClaw контуров."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_remediation_plan"):
                return {"available": False, "error": "openclaw_remediation_not_supported"}
            try:
                report = await openclaw.get_remediation_plan()
            except Exception as exc:
                return {"available": False, "error": "openclaw_remediation_failed", "detail": str(exc)}
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/browser-smoke")
        async def openclaw_browser_smoke(url: str = "https://example.com"):
            """
            Browser relay smoke check с явным attached/not attached статусом.

            Контур:
            1) `openclaw gateway probe` (reachability gateway ws),
            2) HTTP probe browser-server (`http://127.0.0.1:18791/`).
            """
            return {
                "available": True,
                "report": await self._collect_openclaw_browser_smoke_report(url),
            }

        @self.app.post("/api/openclaw/browser/start")
        async def openclaw_browser_start(
            token: str = Query(default=""),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        ):
            """Явно поднимает dedicated OpenClaw browser и возвращает обновлённый readiness snapshot."""
            self._assert_write_access(x_krab_web_key, token)

            start_payload, start_error = await self._run_openclaw_cli_json(
                ["browser", "--json", "start"],
                timeout_sec=20.0,
            )
            if start_error:
                return {
                    "ok": False,
                    "error": "browser_start_failed",
                    "detail": start_error,
                }

            smoke_report = await self._collect_openclaw_browser_smoke_report("https://example.com")
            smoke = dict(smoke_report.get("browser_smoke", {}) or {})
            browser_status, browser_status_error, tabs_payload, tabs_error = (
                await self._collect_stable_browser_cli_runtime(
                    relay_reachable=bool(smoke.get("relay_reachable") or smoke.get("browser_http_reachable")),
                    auth_required=bool(smoke.get("browser_auth_required")),
                    attempts=3,
                    settle_delay_sec=0.8,
                )
            )
            browser = self._classify_browser_stage(
                browser_status,
                tabs_payload,
                smoke,
                browser_status_error=browser_status_error,
                tabs_error=tabs_error,
            )

            return {
                "ok": True,
                "start": start_payload,
                "browser": browser,
                "raw": {
                    "browser_status": browser_status,
                    "browser_status_error": browser_status_error,
                    "tabs": tabs_payload,
                    "tabs_error": tabs_error,
                    "browser_smoke": smoke_report,
                },
            }

        @self.app.post("/api/openclaw/browser/open-owner-chrome")
        async def openclaw_browser_open_owner_chrome(
            token: str = Query(default=""),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        ):
            """Открывает owner Chrome на странице Remote Debugging через существующий helper."""
            self._assert_write_access(x_krab_web_key, token)
            return self._launch_owner_chrome_remote_debugging()

        @self.app.get("/api/openclaw/browser-mcp-readiness")
        async def openclaw_browser_mcp_readiness(url: str = "https://example.com"):
            """Агрегированный staged readiness для owner browser-контура и managed MCP."""
            smoke_report = await self._collect_openclaw_browser_smoke_report(url)
            smoke = dict(smoke_report.get("browser_smoke", {}) or {})
            browser_status, browser_status_error, tabs_payload, tabs_error = (
                await self._collect_stable_browser_cli_runtime(
                    relay_reachable=bool(smoke.get("relay_reachable") or smoke.get("browser_http_reachable")),
                    auth_required=bool(smoke.get("browser_auth_required")),
                    attempts=3,
                    settle_delay_sec=0.8,
                )
            )
            browser = self._classify_browser_stage(
                browser_status,
                tabs_payload,
                smoke,
                browser_status_error=browser_status_error,
                tabs_error=tabs_error,
            )
            mcp = self._build_mcp_readiness_snapshot(browser)
            browser["paths"] = self._build_browser_access_paths(browser, mcp)

            overall = "ready"
            if "blocked" in {str(browser.get("readiness")), str(mcp.get("readiness"))}:
                overall = "blocked"
            elif "attention" in {str(browser.get("readiness")), str(mcp.get("readiness"))}:
                overall = "attention"

            return {
                "available": True,
                "overall": {
                    "readiness": overall,
                    "detail": (
                        "Browser relay и managed MCP готовы."
                        if overall == "ready"
                        else "Есть оставшиеся шаги для browser/MCP readiness."
                    ),
                },
                "browser": browser,
                "mcp": mcp,
                "raw": {
                    "browser_status": browser_status,
                    "browser_status_error": browser_status_error,
                    "tabs": tabs_payload,
                    "tabs_error": tabs_error,
                    "browser_smoke": smoke_report,
                },
            }

        @self.app.get("/api/openclaw/photo-smoke")
        async def openclaw_photo_smoke():
            """
            Легковесная проверка готовности photo/vision маршрута.

            Проверяет:
            1) доступ к model manager через router;
            2) наличие vision-capable локальных моделей;
            3) выбранную модель для `has_photo=True`.
            """
            router = self.deps.get("router")
            if not router:
                return {"available": False, "error": "router_not_configured"}

            mm = getattr(router, "_mm", None)
            if mm is None:
                return {"available": False, "error": "model_manager_not_available"}

            models_count = 0
            local_vision_count = 0
            selected_model = ""
            selected_provider = ""
            selected_local = False
            local_available = bool(getattr(router, "is_local_available", False))
            discovery_error = ""
            selection_error = ""

            try:
                discovered = await asyncio.wait_for(mm.discover_models(), timeout=20.0)
                models_count = len(discovered or [])
                for model in discovered or []:
                    supports_vision = bool(getattr(model, "supports_vision", False))
                    model_type = str(getattr(model, "type", "")).lower()
                    if supports_vision and "local" in model_type:
                        local_vision_count += 1
            except Exception as exc:  # noqa: BLE001
                discovery_error = str(exc)

            try:
                selected_model = str(await asyncio.wait_for(mm.get_best_model(has_photo=True), timeout=20.0) or "")
                selected_local = bool(mm.is_local_model(selected_model)) if selected_model else False
                if "/" in selected_model:
                    selected_provider = selected_model.split("/", 1)[0]
                else:
                    selected_provider = "local" if selected_local else (selected_model or "unknown")
            except Exception as exc:  # noqa: BLE001
                selection_error = str(exc)

            photo_ready = bool(selected_model) and not bool(selection_error)
            if selected_local and local_vision_count == 0:
                photo_ready = False

            detail_parts: list[str] = []
            if selected_model:
                detail_parts.append(f"selected={selected_model}")
            if selected_local:
                detail_parts.append("route=local_vision")
            elif selected_model:
                detail_parts.append("route=cloud_vision_fallback")
            if discovery_error:
                detail_parts.append(f"discovery_error={discovery_error}")
            if selection_error:
                detail_parts.append(f"selection_error={selection_error}")

            return {
                "available": True,
                "report": {
                    "photo_smoke": {
                        "ok": photo_ready,
                        "local_available": local_available,
                        "models_count": models_count,
                        "local_vision_count": local_vision_count,
                        "selected_model": selected_model,
                        "selected_provider": selected_provider,
                        "selected_local": selected_local,
                        "detail": "; ".join(detail_parts) if detail_parts else "n/a",
                    }
                },
            }

        async def _openclaw_cloud_diagnostics_impl(providers: str = ""):
            """Проверка cloud-провайдеров OpenClaw с классификацией ошибок ключей/API."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_cloud_provider_diagnostics"):
                return {"available": False, "error": "cloud_diagnostics_not_supported"}

            providers_list: list[str] | None = None
            raw = (providers or "").strip()
            if raw:
                providers_list = [item.strip().lower() for item in raw.split(",") if item.strip()]
                if not providers_list:
                    providers_list = None
            report = await openclaw.get_cloud_provider_diagnostics(providers=providers_list)
            return {"available": True, "report": report}

        @self.app.get("/api/openclaw/cloud")
        async def openclaw_cloud_diagnostics(providers: str = Query(default="")):
            """Канонический endpoint cloud-диагностики."""
            return await _openclaw_cloud_diagnostics_impl(providers=providers)

        @self.app.get("/api/openclaw/cloud/diagnostics")
        async def openclaw_cloud_diagnostics_legacy(providers: str = Query(default="")):
            """Совместимость со старым UI-клиентом (legacy alias)."""
            return await _openclaw_cloud_diagnostics_impl(providers=providers)

        @self.app.get("/api/openclaw/cloud/runtime-check")
        async def openclaw_cloud_runtime_check():
            """Runtime-check cloud key chain (masked)."""
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"available": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "get_cloud_runtime_check"):
                return {"available": False, "error": "cloud_runtime_check_not_supported"}
            try:
                report = await openclaw.get_cloud_runtime_check()
            except Exception as exc:
                return {"available": False, "error": "cloud_runtime_check_failed", "detail": str(exc)}
            return {"available": True, "report": report}

        @self.app.post("/api/openclaw/cloud/switch-tier")
        async def openclaw_cloud_switch_tier(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Ручное переключение cloud-tier (free/paid) + secrets reload."""
            self._assert_write_access(x_krab_web_key, token)
            openclaw = self.deps.get("openclaw_client")
            if not openclaw:
                return {"ok": False, "error": "openclaw_client_not_configured"}
            if not hasattr(openclaw, "switch_cloud_tier"):
                return {"ok": False, "error": "switch_cloud_tier_not_supported"}

            tier = str((payload or {}).get("tier", "free")).strip().lower()
            if tier not in {"free", "paid"}:
                return {"ok": False, "error": "invalid_tier", "detail": "Допустимо: free|paid"}
            try:
                result = await openclaw.switch_cloud_tier(tier)
                return {"ok": bool(result.get("ok")), "result": result}
            except Exception as exc:
                return {"ok": False, "error": "switch_cloud_tier_failed", "detail": str(exc)}

        @self.app.get("/api/openclaw/cloud/tier/state")
        async def openclaw_cloud_tier_state():
            """
            [R23/R25] Диагностика Cloud Tier State.

            Возвращает текущий активный tier (free/paid/default), статистику
            переключений, метрики (cloud_attempts_total и др.) и конфигурацию.
            Не содержит секретов — только счётчики событий.
            """
            try:
                openclaw = self.deps.get("openclaw_client")
                if not openclaw:
                    return build_ops_response(status="failed", error_code="openclaw_client_not_configured", summary="Openclaw client not configured")
                if not hasattr(openclaw, "get_tier_state_export"):
                    return build_ops_response(status="failed", error_code="tier_state_not_supported", summary="Tier state not supported")
                tier_state = openclaw.get_tier_state_export()
                return build_ops_response(status="ok", data={"tier_state": tier_state})
            except Exception as exc:
                return build_ops_response(status="failed", error_code="system_error", summary=str(exc))

        @self.app.post("/api/openclaw/cloud/tier/reset")
        async def openclaw_cloud_tier_reset(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            [R23/R25] Ручной сброс Cloud Tier на free.

            Требует X-Krab-Web-Key или token (WEB_API_KEY).
            Снимает sticky_paid флаг, не требует перезапуска бота.
            Возвращает: {ok, previous_tier, new_tier, reset_at}.
            """
            try:
                self._assert_write_access(x_krab_web_key, token)
            except HTTPException as exc:
                return build_ops_response(status="failed", error_code="forbidden", summary=exc.detail)

            try:
                openclaw = self.deps.get("openclaw_client")
                if not openclaw:
                    return build_ops_response(status="failed", error_code="openclaw_client_not_configured", summary="Openclaw client not configured")
                if not hasattr(openclaw, "reset_cloud_tier"):
                    return build_ops_response(status="failed", error_code="tier_reset_not_supported", summary="Tier reset not supported")

                result = await openclaw.reset_cloud_tier()
                return build_ops_response(status="ok", data={"result": result})
            except Exception as exc:
                return build_ops_response(status="failed", error_code="tier_reset_error", summary=str(exc))

        def _run_openclaw_model_autoswitch(
            *,
            dry_run: bool,
            profile: str = "",
            toggle: bool = False,
        ) -> dict:
            """
            Запускает autoswitch-утилиту OpenClaw.
            dry_run=True: только диагностика, без изменения конфигурации.
            """
            project_root = Path(__file__).resolve().parents[2]
            script_path = project_root / "scripts" / "openclaw_model_autoswitch.py"
            if not script_path.exists():
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_script_missing")

            python_bin = project_root / ".venv" / "bin" / "python"
            if not python_bin.exists():
                python_bin = Path(sys.executable or "python3")

            cmd = [str(python_bin), str(script_path)]
            requested_profile = str(profile or "").strip().lower()
            if toggle:
                requested_profile = "toggle"
            elif not requested_profile:
                requested_profile = "current" if dry_run else "local-first"
            if requested_profile:
                cmd.extend(["--profile", requested_profile])
            if dry_run:
                cmd.append("--dry-run")

            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_autoswitch_failed: {stderr or stdout or proc.returncode}",
                )

            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if not lines:
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_empty_output")
            try:
                payload = json.loads(lines[-1])
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_autoswitch_invalid_json: {exc}",
                ) from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=500, detail="openclaw_model_autoswitch_invalid_payload")
            return payload

        def _run_openclaw_model_compat_probe(
            *,
            model: str = "",
            reasoning: str = "high",
            skip_reasoning: bool = False,
        ) -> dict:
            """
            Запускает read-only probe совместимости target-модели в OpenClaw runtime.
            """
            project_root = Path(__file__).resolve().parents[2]
            script_path = project_root / "scripts" / "openclaw_model_compat_probe.py"
            if not script_path.exists():
                raise HTTPException(status_code=500, detail="openclaw_model_compat_probe_script_missing")

            python_bin = project_root / ".venv" / "bin" / "python"
            if not python_bin.exists():
                python_bin = Path(sys.executable or "python3")

            cmd = [str(python_bin), str(script_path)]
            normalized_model = str(model or "").strip()
            normalized_reasoning = str(reasoning or "high").strip().lower() or "high"
            if normalized_model:
                cmd.extend(["--model", normalized_model])
            if normalized_reasoning:
                cmd.extend(["--reasoning", normalized_reasoning])
            if skip_reasoning:
                cmd.append("--skip-reasoning")

            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            if proc.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_compat_probe_failed: {stderr or stdout or proc.returncode}",
                )

            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            if not lines:
                raise HTTPException(status_code=500, detail="openclaw_model_compat_probe_empty_output")
            try:
                payload = json.loads(lines[-1])
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"openclaw_model_compat_probe_invalid_json: {exc}",
                ) from exc
            if not isinstance(payload, dict):
                raise HTTPException(status_code=500, detail="openclaw_model_compat_probe_invalid_payload")
            return payload

        @self.app.get("/api/openclaw/model-autoswitch/status")
        async def openclaw_model_autoswitch_status(
            profile: str = Query(default="current"),
        ):
            """Статус autoswitch без изменения runtime-конфига."""
            payload = _run_openclaw_model_autoswitch(dry_run=True, profile=profile, toggle=False)
            return {"ok": True, "autoswitch": payload}

        @self.app.post("/api/openclaw/model-autoswitch/apply")
        async def openclaw_model_autoswitch_apply(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
            profile: str = Query(default=""),
        ):
            """Применяет autoswitch runtime-конфига OpenClaw (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            body: dict[str, Any] = {}
            try:
                body_raw = await request.json()
                if isinstance(body_raw, dict):
                    body = body_raw
            except Exception:
                body = {}

            body_profile = str(body.get("profile") or "").strip()
            body_toggle_raw = body.get("toggle")
            body_toggle = False
            if isinstance(body_toggle_raw, bool):
                body_toggle = body_toggle_raw
            elif body_toggle_raw is not None:
                body_toggle = str(body_toggle_raw).strip().lower() in {"1", "true", "yes", "on"}

            effective_profile = body_profile or profile
            effective_toggle = body_toggle or (not effective_profile)
            payload = _run_openclaw_model_autoswitch(
                dry_run=False,
                profile=effective_profile,
                toggle=effective_toggle,
            )
            return {"ok": True, "autoswitch": payload}

        @self.app.get("/api/openclaw/control-compat/status")
        async def openclaw_control_compat_status():
            """
            [R22] Control Compatibility Diagnostics.

            Дает прозрачный ответ на вопрос: предупреждения OpenClaw Control UI
            (`Unsupported schema node`) — это UI-артефакт или реальный runtime-риск?

            Источники:
            - `openclaw channels status --probe` → runtime_channels_ok
            - `openclaw logs --tail 200` → control_schema_warnings (фильтрация по маркерам)

            Логика impact_level:
            - runtime ok + warnings → "ui_only"   (каналы работают, предупреждение косметическое)
            - runtime fail + warnings → "runtime_risk"  (нужна диагностика)
            - runtime ok, warnings нет → "none"
            """
            # --- Шаг 1: проверяем runtime каналов ---
            runtime_ok = False
            try:
                proc_channels = await asyncio.create_subprocess_exec(
                    "openclaw", "channels", "status", "--probe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_ch, _ = await asyncio.wait_for(proc_channels.communicate(), timeout=30.0)
                    runtime_ok = proc_channels.returncode == 0
                except asyncio.TimeoutError:
                    try:
                        proc_channels.terminate()
                    except ProcessLookupError:
                        pass
                    runtime_ok = False
            except Exception:
                runtime_ok = False

            # --- Шаг 2: получаем последние логи OpenClaw для поиска schema-маркеров ---
            schema_markers = {"unsupported schema node", "schema", "validation"}
            control_schema_warnings: list[str] = []
            try:
                proc_logs = await asyncio.create_subprocess_exec(
                    "openclaw", "logs", "--tail", "200",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_logs, _ = await asyncio.wait_for(proc_logs.communicate(), timeout=10.0)
                    raw_logs = stdout_logs.decode("utf-8", errors="replace")
                    for line in raw_logs.splitlines():
                        line_lower = line.lower()
                        # Ищем строки, содержащие хотя бы один из маркеров схемы
                        if any(marker in line_lower for marker in schema_markers):
                            stripped = line.strip()
                            if stripped:
                                control_schema_warnings.append(stripped)
                except asyncio.TimeoutError:
                    try:
                        proc_logs.terminate()
                    except ProcessLookupError:
                        pass
                    # При таймауте логов — не считаем это runtime-риском
            except Exception:
                # CLI openclaw logs недоступен — просто нет данных для schema-анализа
                pass

            # --- Шаг 3: определяем impact_level и рекомендацию ---
            has_warnings = bool(control_schema_warnings)
            if runtime_ok and has_warnings:
                impact_level = "ui_only"
                recommended_action = (
                    "Предупреждения ограничены UI Control. Runtime каналов работает нормально. "
                    "Для редактирования затронутых полей используй Raw-режим в Control Dashboard."
                )
            elif not runtime_ok and has_warnings:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Обнаружены schema-предупреждения И проблемы runtime. "
                    "Запусти: openclaw doctor --fix  или  ./openclaw_runtime_repair.command"
                )
            elif not runtime_ok:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Runtime каналов недоступен. Schema-предупреждения не обнаружены. "
                    "Запусти: openclaw doctor --fix"
                )
            else:
                impact_level = "none"
                recommended_action = "Все каналы работают нормально. Предупреждений нет."

            return {
                "ok": runtime_ok or not has_warnings,
                "runtime_channels_ok": runtime_ok,
                "runtime_status": "OK" if runtime_ok else "FAIL",
                "control_schema_warnings": control_schema_warnings,
                "has_schema_warning": has_warnings,
                "impact_level": impact_level,
                "recommended_action": recommended_action,
            }

        @self.app.get("/api/openclaw/routing/effective")
        async def openclaw_routing_effective():
            """
            [R22] Routing Effective Source of Truth.

            Единый источник истины о текущем routing-решении Krab:
            откуда оно взялось, какой force_mode активен, почему идём в local или cloud.

            Читает только существующие атрибуты роутера — без внешних вызовов.
            Это позволяет: дебаггинг без отправки запросов в LM Studio/cloud,
            проверку конфигурации, понимание причин route-решений.
            """
            router = self.deps["router"]

            # --- Normalize force_mode ---
            force_mode_raw = str(getattr(router, "force_mode", "auto") or "auto")
            force_mode_eff = _normalize_force_mode(force_mode_raw)

            # --- Определяем default slot и модель ---
            cloud_slots: dict = {}
            raw_models = getattr(router, "models", {}) or {}
            if isinstance(raw_models, dict):
                cloud_slots = {str(k): str(v) for k, v in raw_models.items()}
            # Приоритет: "chat" → первый ключ → пусто
            default_slot = "chat" if "chat" in cloud_slots else (next(iter(cloud_slots), None) or "")
            default_model = cloud_slots.get(default_slot, "")

            # --- Cloud fallback включен если НЕ принудительный local ---
            cloud_fallback_enabled = force_mode_eff != "local"
            last_route: dict[str, Any] = {}
            try:
                getter = getattr(router, "get_last_route", None)
                if callable(getter):
                    candidate = getter() or {}
                    if isinstance(candidate, dict):
                        last_route = candidate
            except Exception:
                last_route = {}

            # --- Строим decision_notes из фактического состояния runtime ---
            local_truth = await self._resolve_local_runtime_truth(router)
            local_engine = str(local_truth.get("engine") or getattr(router, "local_engine", "") or "")
            local_available = bool(local_truth.get("runtime_reachable"))
            active_local_model = str(local_truth.get("active_model") or "")
            routing_policy = str(getattr(router, "routing_policy", "free_first_hybrid") or "free_first_hybrid")
            cloud_cap_reached = bool(getattr(router, "cloud_soft_cap_reached", False))
            last_route_status = str(last_route.get("status") or "").strip().lower()
            last_route_channel = str(last_route.get("channel") or "").strip().lower()
            last_route_model = str(last_route.get("model") or "").strip()

            current_route_uses_cloud = bool(
                last_route_status == "ok" and last_route_channel in {"openclaw_cloud", "cloud"}
            )
            current_fallback_active = False
            if force_mode_eff == "cloud" and cloud_fallback_enabled:
                current_fallback_active = True
            elif not cloud_fallback_enabled:
                current_fallback_active = False
            elif last_route_status == "ok":
                # Cloud =/= fallback.
                # Считаем fallback активным только если фактическая модель маршрута
                # отличается от configured default cloud-model либо пришлось уйти
                # в cloud при force_local=off и отсутствии рабочей local-модели.
                if current_route_uses_cloud and default_model and last_route_model and last_route_model != default_model:
                    current_fallback_active = True
                elif (
                    not current_route_uses_cloud
                    and last_route_model
                    and active_local_model
                    and last_route_model != active_local_model
                ):
                    current_fallback_active = True
            elif not local_available and force_mode_eff not in {"cloud"} and cloud_fallback_enabled:
                current_fallback_active = True

            if not cloud_fallback_enabled:
                cloud_fallback_state = "disabled"
            elif current_fallback_active:
                cloud_fallback_state = "active"
            else:
                cloud_fallback_state = "standby"

            decision_notes: list[str] = []
            if force_mode_raw in {"force_local", "local"}:
                decision_notes.append(
                    f"Принудительный local-режим активен — все запросы идут через {local_engine or 'local'}."
                )
            elif force_mode_raw in {"force_cloud", "cloud"}:
                decision_notes.append(
                    "Принудительный cloud-режим активен — локальный движок пропускается."
                )
            else:
                decision_notes.append(
                    f"Routing policy: {routing_policy} — auto-routing включен."
                )

            if local_available:
                decision_notes.append(
                    f"Локальный движок '{local_engine}' доступен."
                    + (f" Активная модель: '{active_local_model}'." if active_local_model else "")
                )
            else:
                decision_notes.append(
                    "Локальный движок недоступен — fallback только на cloud."
                )

            if cloud_cap_reached:
                decision_notes.append(
                    "Cloud soft-cap достигнут: приоритет переключен на локальный движок."
                )

            if not cloud_fallback_enabled:
                decision_notes.append(
                    "Cloud fallback ОТКЛЮЧЕН: force_local режим запрещает обращение к cloud."
                )
            elif cloud_fallback_state == "active":
                decision_notes.append(
                    "Cloud fallback сейчас задействован как активный маршрут."
                )
            else:
                decision_notes.append(
                    "Cloud fallback доступен как резерв, но сейчас не задействован."
                )

            # Для owner UI важнее фактическая последняя модель маршрута, чем stale router-slot.
            # Иначе после hot-reload/runtime-fallback верхние виджеты показывают правду,
            # а блок "Эффективный роутинг" остаётся на старом default-model.
            active_slot_or_model = (
                last_route_model
                or active_local_model
                or default_model
                or default_slot
            )

            return {
                "ok": True,
                "requested_mode": force_mode_raw,
                "effective_mode": force_mode_eff,
                "active_slot_or_model": active_slot_or_model,
                "cloud_fallback": cloud_fallback_enabled,
                "cloud_fallback_state": cloud_fallback_state,
                "cloud_fallback_active": current_fallback_active,
                "cloud_route_active": current_route_uses_cloud,
                "force_mode_requested": force_mode_raw,
                "force_mode_effective": force_mode_eff,
                "assistant_default_slot": default_slot,
                "assistant_default_model": default_model,
                "cloud_fallback_enabled": cloud_fallback_enabled,
                "decision_notes": decision_notes,
            }

        @self.app.get("/api/provisioning/templates")
        async def provisioning_templates(entity: str = Query(default="agent")):
            """Возвращает шаблоны для provisioning UI/API."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"entity": entity, "templates": provisioning.list_templates(entity)}

        @self.app.get("/api/provisioning/drafts")
        async def provisioning_drafts(
            status: str | None = Query(default=None),
            limit: int = Query(default=20, ge=1, le=200),
        ):
            """Список provisioning draft'ов."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            return {"drafts": provisioning.list_drafts(limit=limit, status=status)}

        @self.app.post("/api/provisioning/drafts")
        async def provisioning_create_draft(
            payload: dict = Body(...),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Создает provisioning draft (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_create_draft", idem_key)
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")

            try:
                draft = provisioning.create_draft(
                    entity_type=payload.get("entity_type", "agent"),
                    name=payload.get("name", ""),
                    role=payload.get("role", ""),
                    description=payload.get("description", ""),
                    requested_by=payload.get("requested_by", "web_api"),
                    settings=payload.get("settings", {}),
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_draft_create",
                    f"entity={payload.get('entity_type', 'agent')} name={payload.get('name', '')}",
                )
            response_payload = {"ok": True, "draft": draft}
            self._idempotency_set("provisioning_create_draft", idem_key, response_payload)
            return response_payload

        @self.app.get("/api/provisioning/preview/{draft_id}")
        async def provisioning_preview(draft_id: str):
            """Показывает diff для draft перед apply."""
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                preview = provisioning.preview_diff(draft_id)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"ok": True, "preview": preview}

        @self.app.post("/api/provisioning/apply/{draft_id}")
        async def provisioning_apply(
            draft_id: str,
            confirm: bool = Query(default=False),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
            token: str = Query(default=""),
        ):
            """Применяет draft в catalog (write endpoint)."""
            self._assert_write_access(x_krab_web_key, token)
            idem_key = (x_idempotency_key or "").strip()
            cached = self._idempotency_get("provisioning_apply", f"{draft_id}:{idem_key}")
            if cached:
                return cached
            provisioning = self.deps.get("provisioning_service")
            if not provisioning:
                raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
            try:
                result = provisioning.apply_draft(draft_id, confirmed=confirm)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            black_box = self.deps.get("black_box")
            if black_box and hasattr(black_box, "log_event"):
                black_box.log_event(
                    "web_provisioning_apply",
                    f"draft_id={draft_id} confirmed={confirm}",
                )
            response_payload = {"ok": True, "result": result}
            self._idempotency_set("provisioning_apply", f"{draft_id}:{idem_key}", response_payload)
            return response_payload

    async def start(self):
        """Запуск сервера в фоне."""
        if self._server_task and not self._server_task.done():
            return

        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
        # Prevent uvicorn from overriding signal handlers (managed by Pyrogram/Main)
        # Note: "server.serve()" will invoke "config.setup_event_loop()" which might still interfere unless configured correctly.
        # But setting explicit loop above helps.
        # Ideally we pass install_signal_handlers=False if supported by Config (it is not a direct arg usually, but passed to Server).
        # Actually Config() has no install_signal_handlers arg. It's on Server.run() usually?
        # No, it IS an argument to Config __init__ in newer versions, or handled via setup.
        # Let's check typical usage.
        # Standard Uvicorn Config has NO install_signal_handlers arg.
        # But uvicorn.Server(config).serve() installs them unless overridden.
        # We can try to prevent it by subclassing or checking if we can pass a flag.
        # Actually Config DOES have it in recent versions? Let's assume standard 0.20+ has it?
        # Let's try passing it. If it fails, we catch TypeError.
        try:
            config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", loop="asyncio")
            # We must monkeypatch to prevent signal install? Or just hope it works?
            # Actually simplest way is to NOT use Server.serve() directly if we can avoid signal handlers?
            # But serve() calls install_signal_handlers().
            # Let's override the install_signal_handlers method of the server instance!
            self._server = uvicorn.Server(config)
            self._server.install_signal_handlers = lambda: None
        except Exception as e:
            logger.warning(f"Could not disable uvicorn signal handlers: {e}")
            self._server = uvicorn.Server(config)

        logger.info(f"🌐 Web App starting at {self._public_base_url()}")
        self._server_task = asyncio.create_task(self._server.serve())

    async def stop(self):
        """Аккуратно останавливает uvicorn сервер."""
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await asyncio.wait([self._server_task], timeout=3)

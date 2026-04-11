# -*- coding: utf-8 -*-
"""
Web App Module (Phase 15+).
Сервер для Dashboard и web-управления экосистемой Krab.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import mimetypes
import os
import re
import shlex
import shutil
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
    get_effective_owner_label,
    get_effective_owner_subjects,
    load_acl_runtime_state,
    update_acl_subject,
)
from src.core.auth_recovery_readiness import (  # noqa: E402
    build_auth_recovery_readiness_snapshot,
    provider_oauth_scope_truth,
    provider_repair_helper_path,
)
from src.core.capability_registry import (  # noqa: E402
    build_capability_registry,
    build_channel_capability_snapshot,
    build_policy_matrix,
    build_system_control_snapshot,
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
from src.core.openclaw_workspace import build_workspace_state_snapshot  # noqa: E402
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
from src.core.operator_identity import current_account_id, current_operator_id  # noqa: E402
from src.core.runtime_policy import current_runtime_mode, provider_runtime_policy  # noqa: E402
from src.core.shared_worktree_permissions import (  # noqa: E402
    normalize_shared_worktree_permissions,
    sample_non_writable_shared_items,
)
from src.core.translator_mobile_onboarding import (  # noqa: E402
    build_translator_mobile_onboarding_packet,
)
from src.core.translator_live_trial_preflight import (  # noqa: E402
    build_translator_live_trial_preflight,
)
from src.core.voice_gateway_control_plane import VoiceGatewayControlPlane  # noqa: E402
from src.integrations.voice_gateway_subscriber import VoiceGatewayEventSubscriber  # noqa: E402

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
        # Catalog моделей может собираться заметно дольше health/status endpoints,
        # поэтому write-path панели использует отдельный короткий cache.
        self._model_catalog_cache: tuple[float, dict[str, Any]] | None = None
        self._vg_subscriber: VoiceGatewayEventSubscriber | None = None
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
        """Ограничивает thinking к набору режимов, которые реально принимает OpenClaw runtime."""
        normalized = str(raw_value or "").strip().lower()
        if not normalized and allow_blank:
            return ""
        # OpenClaw 2026.3.11 переименовал legacy `auto` в `adaptive`.
        # Поддерживаем алиас, чтобы не ломать старые draft'ы UI и уже записанные конфиги.
        normalized = {"auto": "adaptive"}.get(normalized, normalized)
        allowed = {"off", "minimal", "low", "medium", "high", "xhigh", "adaptive"}
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

    @staticmethod
    def _normalize_runtime_max_concurrent(raw_value: Any) -> int:
        """Нормализует queue concurrency для main/subagent без опасных значений."""
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime_invalid_max_concurrent") from exc
        if value < 1 or value > 64:
            raise ValueError("runtime_invalid_max_concurrent")
        return value

    @classmethod
    def _debug_chrome_remote_debugging_helper_path(cls) -> Path:
        """Путь к существующему macOS helper для отдельного debug Chrome."""
        return cls._project_root() / "new Enable Chrome Remote Debugging.command"

    @classmethod
    def _owner_chrome_remote_debugging_helper_path(cls) -> Path:
        """Путь к macOS helper для обычного Chrome владельца."""
        return cls._project_root() / "new Open Owner Chrome Remote Debugging.command"

    @staticmethod
    def _owner_chrome_remote_debugging_log_path() -> Path:
        """Лог helper для ordinary Chrome attach."""
        return Path("/tmp/krab-owner-chrome-remote-debugging.log")

    @classmethod
    def _inspect_owner_chrome_remote_debugging_log(cls) -> dict[str, Any]:
        """
        Пытается извлечь явную причину провала ordinary Chrome attach из helper-лога.

        Зачем:
        - Chrome 146+ может честно отклонять remote debugging для default profile;
        - без этого owner UI выглядит так, будто нужен ещё один relaunch/approve,
          хотя проблема уже подтверждена политикой самого Chrome.
        """
        path = cls._owner_chrome_remote_debugging_log_path()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"status": "missing", "path": str(path), "detail": ""}

        lower = text.lower()
        if "non-default data directory" in lower:
            return {
                "status": "chrome_policy_blocked",
                "path": str(path),
                "detail": (
                    "Chrome отклонил remote debugging для default profile: "
                    "требуется non-default data directory."
                ),
            }
        return {"status": "ok", "path": str(path), "detail": ""}

    @classmethod
    def _launch_owner_chrome_remote_debugging(cls) -> dict[str, Any]:
        """
        Открывает путь подготовки обычного Chrome владельца для attach.

        Почему так:
        - owner UI должен готовить именно обычный Chrome владельца, а не отдельный
          debug profile;
        - `.command` даёт one-click путь для macOS без ручного копирования команд;
        - если helper отсутствует, деградируем в прямое открытие `chrome://inspect`.
        """
        helper_path = cls._owner_chrome_remote_debugging_helper_path()
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
                    "next_step": (
                        "Helper попробует перезапустить обычный Chrome владельца с Remote Debugging на порту 9222. "
                        "Если Chrome 146+ отклонит default profile, owner UI покажет это как policy block."
                    ),
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
                "next_step": "Открой обычный Chrome с Remote Debugging на порту 9222 и затем обнови Browser / MCP Readiness.",
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
            "codex-cli": "Codex CLI",
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
            "codex-cli": 5,
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
        return provider_repair_helper_path(cls._project_root(), str(provider_name or "").strip().lower())

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
                    "repair_available": bool(helper_path and helper_path.exists()),
                    "repair_label": "Перелогинить Antigravity",
                    "repair_action": "repair_oauth",
                    "repair_detail": (
                        "Legacy OAuth-провайдер. Откроет официальный OpenClaw helper "
                        "для Google Antigravity и обновит auth-профиль без ручного CLI."
                    ),
                }
            )
        elif normalized == "openai":
            base.update(
                {
                    # Runtime уже умеет использовать `openai/gpt-4o-mini`
                    # как controlled fallback; UI не должен врать, что это
                    # строго manual-only путь.
                    "manual_only": False,
                    "repair_label": "",
                    "repair_action": "",
                    "repair_detail": "API key модели доступны для ручного выбора и controlled fallback-цепочки.",
                }
            )
        elif normalized == "openai-codex":
            base.update(
                {
                    "repair_available": bool(helper_path and helper_path.exists()),
                    "repair_label": "Перелогинить OpenAI Codex",
                    "repair_action": "repair_oauth",
                    "repair_detail": (
                        "Откроет официальный OpenClaw OAuth helper для OpenAI Codex "
                        "и сразу покажет реально выданные scopes."
                    ),
                }
            )
        elif normalized == "codex-cli":
            base.update(
                {
                    "repair_available": bool(helper_path and helper_path.exists()),
                    "repair_label": "Перелогинить Codex CLI",
                    "repair_action": "repair_oauth",
                    "repair_detail": (
                        "Откроет локальный helper для `codex login --device-auth` "
                        "и покажет текущий статус CLI-сессии."
                    ),
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
    def _build_codex_cli_synthetic_catalog(
        cls,
        full_catalog_providers: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """
        Строит synthetic inventory для `codex-cli`, если OpenClaw не публикует его напрямую.

        Почему это нужно:
        - локальный `codex login status` может быть подтверждён;
        - owner хочет выбирать модели именно через `codex-cli/...`;
        - OpenClaw runtime пока может знать только текущий `codex-cli/gpt-5.4`,
          хотя официальный OpenAI catalog уже раскрывает остальные доступные модели.
        """
        providers = full_catalog_providers if isinstance(full_catalog_providers, dict) else {}
        synthetic_items: list[dict[str, Any]] = []
        seen_tails: set[str] = set()
        for source_provider in ("openai-codex", "openai"):
            models = providers.get(source_provider)
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, dict):
                    continue
                raw_key = str(model.get("key", "") or "").strip()
                if "/" not in raw_key:
                    continue
                model_tail = raw_key.split("/", 1)[1].strip()
                if not model_tail or model_tail in seen_tails:
                    continue
                seen_tails.add(model_tail)
                cloned = dict(model)
                cloned["key"] = f"codex-cli/{model_tail}"
                raw_tags = [
                    str(tag or "").strip()
                    for tag in (model.get("tags") or [])
                    if str(tag or "").strip()
                ]
                filtered_tags = [tag for tag in raw_tags if tag.lower() not in {"configured", "default"}]
                filtered_tags.append("synthetic")
                cloned["tags"] = filtered_tags
                synthetic_items.append(cloned)

        synthetic_items.sort(
            key=lambda item: (
                0 if str(item.get("key", "")).strip().endswith("/gpt-5.4") else 1,
                0 if "codex" in str(item.get("key", "")).strip().lower() else 1,
                str(item.get("name") or item.get("key") or "").lower(),
            )
        )
        return synthetic_items

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
        scope_truth = provider_oauth_scope_truth(
            normalized_provider,
            auth_profiles if isinstance(auth_profiles, dict) else {},
        )

        profile_names = [
            profile_name
            for profile_name, profile_payload in profiles.items()
            if isinstance(profile_payload, dict) and str(profile_payload.get("provider", "") or "").strip() == normalized_provider
        ]

        disabled_profiles: list[dict[str, str]] = []
        expired_profiles: list[dict[str, str]] = []
        healthy_profiles: list[str] = []
        failure_counts: dict[str, int] = {}
        cooldown_active = False
        now_ms = time.time() * 1000.0
        healthy_oauth_remaining_ms: int | None = None
        for profile_name in profile_names:
            profile_payload = profiles.get(profile_name)
            usage = usage_stats.get(profile_name)
            expired = False
            expires_at = 0.0
            if isinstance(profile_payload, dict):
                try:
                    expires_at = float(profile_payload.get("expires", 0) or 0)
                except (TypeError, ValueError):
                    expires_at = 0.0
                if expires_at > 0 and expires_at <= now_ms:
                    expired = True
                    expired_profiles.append({"profile": profile_name, "reason": "expired"})
            disabled_reason = ""
            try:
                cooldown_until = float(usage.get("cooldownUntil", 0) or 0) if isinstance(usage, dict) else 0.0
            except (TypeError, ValueError):
                cooldown_until = 0.0
            if not isinstance(usage, dict):
                if not expired:
                    healthy_profiles.append(profile_name)
                    if expires_at > now_ms:
                        remaining_ms = int(expires_at - now_ms)
                        healthy_oauth_remaining_ms = remaining_ms if healthy_oauth_remaining_ms is None else max(
                            healthy_oauth_remaining_ms,
                            remaining_ms,
                        )
                continue
            disabled_reason = str(usage.get("disabledReason", "") or "").strip()
            if disabled_reason:
                disabled_profiles.append({"profile": profile_name, "reason": disabled_reason})
            if cooldown_until > now_ms and not disabled_reason and not expired:
                cooldown_active = True
            failures = usage.get("failureCounts")
            if isinstance(failures, dict):
                for failure_key, failure_value in failures.items():
                    failure_counts[str(failure_key)] = failure_counts.get(str(failure_key), 0) + int(failure_value or 0)
            if not disabled_reason and not expired:
                healthy_profiles.append(profile_name)
                if expires_at > now_ms:
                    remaining_ms = int(expires_at - now_ms)
                    healthy_oauth_remaining_ms = remaining_ms if healthy_oauth_remaining_ms is None else max(
                        healthy_oauth_remaining_ms,
                        remaining_ms,
                    )

        auth_mode = str(provider_payload.get("auth", "") or "").strip().lower()
        api_key_configured = bool(str(provider_payload.get("apiKey", "") or "").strip())
        effective_kind = str(status_meta.get("effective_kind", "") or "").strip().lower()
        cli_status_text = ""
        codex_cli_present = False
        codex_cli_logged_in = False
        if normalized_provider == "codex-cli":
            codex_bin = shutil.which("codex") or ""
            codex_cli_present = bool(codex_bin)
            if codex_cli_present:
                try:
                    completed = subprocess.run(
                        [codex_bin, "login", "status"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    cli_status_text = str(completed.stdout or completed.stderr or "").strip()
                    codex_cli_logged_in = completed.returncode == 0
                except Exception as exc:  # noqa: BLE001
                    cli_status_text = f"codex_cli_status_failed:{exc}"
            if not auth_mode:
                auth_mode = "cli"
            if not effective_kind and codex_cli_present:
                effective_kind = "cli"
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
        helper_path = provider_repair_helper_path(cls._project_root(), normalized_provider)
        helper_available = bool(helper_path and helper_path.exists())

        if oauth_expected and healthy_profiles:
            if not isinstance(oauth_remaining_ms, int) and healthy_oauth_remaining_ms is not None:
                oauth_remaining_ms = healthy_oauth_remaining_ms
            if str(oauth_status or "") in {"", "missing", "expired"}:
                oauth_status = "ok"
            if not oauth_remaining_human and isinstance(oauth_remaining_ms, int):
                oauth_remaining_human = cls._humanize_remaining_ms(oauth_remaining_ms)

        readiness = "ready"
        readiness_label = "Configured"
        detail = "Провайдер готов к выбору."

        if normalized_provider == "codex-cli":
            if not codex_cli_present:
                readiness = "blocked"
                readiness_label = "CLI missing"
                detail = "Локальный `codex` binary не найден в PATH текущей macOS-учётки."
            elif codex_cli_logged_in:
                readiness = "ready"
                readiness_label = "CLI OK"
                detail = cli_status_text or "Codex CLI найден и login status подтверждён."
            else:
                readiness = "attention"
                readiness_label = "CLI login"
                detail = cli_status_text or "Codex CLI найден, но login status ещё не подтверждён."
        elif signal_fail_code == "runtime_missing_scope_model_request":
            readiness = "blocked"
            readiness_label = "Scope fail"
            observed_scopes = [
                str(item or "").strip()
                for item in (scope_truth.get("scopes") or [])
                if str(item or "").strip()
            ]
            scopes_label = ", ".join(observed_scopes) if observed_scopes else "не раскрыты"
            detail = (
                "Runtime фиксирует `Missing scopes: model.request`; OAuth-модели видны, "
                f"но как primary сейчас неработоспособны. Локальные scopes: `{scopes_label}`."
            )
        elif not healthy_profiles and disabled_profiles:
            readiness = "blocked"
            readiness_label = "Disabled"
            detail = f"Профиль отключён: {disabled_profiles[0]['reason']}"
        elif auth_mode == "oauth" and healthy_profiles:
            if oauth_status == "ok" and isinstance(oauth_remaining_ms, int) and oauth_remaining_ms <= 0:
                readiness = "attention"
                readiness_label = "Re-auth soon"
                detail = "OpenClaw ещё считает OAuth рабочим, но TTL уже на нуле или ниже; лучше сделать повторный логин до следующего флапа."
            elif oauth_status == "ok" and isinstance(oauth_remaining_ms, int) and oauth_remaining_ms <= 15 * 60 * 1000:
                readiness = "attention"
                readiness_label = "Expiring"
                detail = "OAuth-профиль живой, но подходит к истечению и может скоро потребовать re-auth."
            elif cooldown_active:
                readiness = "attention"
                readiness_label = "Cooldown"
                detail = "Провайдер в cooldown после недавних ошибок; выбор возможен, но route нестабилен."
            else:
                readiness = "ready"
                readiness_label = "OAuth OK"
                detail = "OAuth-профиль найден и выглядит рабочим."
        elif oauth_expected and oauth_status in {"expired", "missing"}:
            readiness = "blocked"
            readiness_label = "Expired"
            detail = "Сам OpenClaw считает OAuth-профиль истёкшим или отсутствующим."
        elif not healthy_profiles and oauth_expected and expired_profiles:
            readiness = "blocked"
            readiness_label = "Expired"
            detail = "OAuth-профиль истёк и требует повторного логина."
        elif cooldown_active:
            readiness = "attention"
            readiness_label = "Cooldown"
            detail = "Провайдер в cooldown после недавних ошибок; выбор возможен, но route нестабилен."
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

        if legacy and healthy_profiles and readiness != "blocked":
            if readiness == "ready":
                detail = "Legacy OAuth-провайдер подключён вручную и сейчас рабочий; держим его как дополнительный fallback, а не как единственный production primary."
            else:
                detail = f"Legacy OAuth-провайдер подключён вручную; {detail[0].lower() + detail[1:]}" if detail else "Legacy OAuth-провайдер подключён вручную."

        runtime_policy = provider_runtime_policy(
            normalized_provider,
            readiness=readiness,
            auth_mode=auth_mode,
            oauth_status=oauth_status,
            helper_available=helper_available,
            legacy=legacy,
            cli_login_ready=codex_cli_logged_in,
            quota_state=str(quota_truth.get("quota_state") or "unknown"),
        )

        return {
            "provider": normalized_provider,
            "configured": bool(runtime_model_ids or profile_names),
            "runtime_models": runtime_model_ids,
            "profiles": profile_names,
            "healthy_profiles": healthy_profiles,
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
            "effective_detail": cli_status_text or str(status_meta.get("effective_detail", "") or "").strip(),
            "oauth_status": oauth_status,
            "oauth_remaining_ms": oauth_remaining_ms,
            "oauth_remaining_human": oauth_remaining_human,
            "observed_scopes": list(scope_truth.get("scopes") or []),
            "scope_truth_available": bool(scope_truth.get("scope_truth_available")),
            "has_model_request_scope": bool(scope_truth.get("has_model_request")),
            "quota_state": str(quota_truth.get("quota_state", "unknown") or "unknown"),
            "quota_label": str(quota_truth.get("quota_label", "") or ""),
            "helper_path": str(helper_path) if helper_path else "",
            "helper_available": helper_available,
            **runtime_policy,
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
        - queue concurrency для main/subagent;
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
        try:
            main_max_concurrent = cls._normalize_runtime_max_concurrent(defaults.get("maxConcurrent", 4) or 4)
        except ValueError:
            main_max_concurrent = 4
        subagents = defaults.get("subagents") if isinstance(defaults, dict) else {}
        if not isinstance(subagents, dict):
            subagents = {}
        try:
            subagent_max_concurrent = cls._normalize_runtime_max_concurrent(
                subagents.get("maxConcurrent", 8) or 8
            )
        except ValueError:
            subagent_max_concurrent = 8

        execution_preset = "custom"
        if main_max_concurrent == 1 and subagent_max_concurrent == 1:
            execution_preset = "sequential"
        elif main_max_concurrent == 4 and subagent_max_concurrent == 8:
            execution_preset = "parallel"

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
            "main_max_concurrent": main_max_concurrent,
            "subagent_max_concurrent": subagent_max_concurrent,
            "execution_preset": execution_preset,
            "execution_presets": [
                {
                    "id": "sequential",
                    "label": "Sequential",
                    "main_max_concurrent": 1,
                    "subagent_max_concurrent": 1,
                    "description": "Строго последовательно: один запрос main и один subagent одновременно.",
                },
                {
                    "id": "parallel",
                    "label": "Parallel",
                    "main_max_concurrent": 4,
                    "subagent_max_concurrent": 8,
                    "description": "Безопасный параллельный профиль проекта: main 4, subagent 8.",
                },
                {
                    "id": "custom",
                    "label": "Custom",
                    "main_max_concurrent": main_max_concurrent,
                    "subagent_max_concurrent": subagent_max_concurrent,
                    "description": "Ручная настройка queue caps под конкретный сценарий.",
                },
            ],
            "thinking_modes": ["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"],
            "chain_items": chain_items,
            # Держим минимум 8 слотов, потому что текущий production-профиль проекта
            # уже использует длинную fallback-цепочку и UI должен позволять быстро
            # добавлять/менять запасные модели без ручного JSON-редактирования.
            "max_fallback_slots": max(8, len(fallbacks)),
        }

    @classmethod
    def _apply_openclaw_runtime_controls(
        cls,
        *,
        primary_raw: Any,
        fallbacks_raw: list[Any],
        context_tokens_raw: Any,
        thinking_default_raw: Any,
        execution_preset_raw: Any = "",
        main_max_concurrent_raw: Any = None,
        subagent_max_concurrent_raw: Any = None,
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
        subagents = defaults.setdefault("subagents", {})
        if not isinstance(subagents, dict):
            subagents = {}
            defaults["subagents"] = subagents

        context_tokens = cls._normalize_context_tokens(
            context_tokens_raw if context_tokens_raw is not None else defaults.get("contextTokens", 128000)
        )
        thinking_default = cls._normalize_thinking_mode(
            thinking_default_raw if thinking_default_raw not in {None, ""} else defaults.get("thinkingDefault", "off")
        )

        execution_preset = str(execution_preset_raw or "").strip().lower()
        if execution_preset == "sequential":
            main_max_concurrent = 1
            subagent_max_concurrent = 1
        elif execution_preset == "parallel":
            main_max_concurrent = 4
            subagent_max_concurrent = 8
        else:
            main_max_concurrent = cls._normalize_runtime_max_concurrent(
                main_max_concurrent_raw if main_max_concurrent_raw is not None else defaults.get("maxConcurrent", 4)
            )
            subagent_max_concurrent = cls._normalize_runtime_max_concurrent(
                subagent_max_concurrent_raw if subagent_max_concurrent_raw is not None else subagents.get("maxConcurrent", 8)
            )
            if main_max_concurrent == 1 and subagent_max_concurrent == 1:
                execution_preset = "sequential"
            elif main_max_concurrent == 4 and subagent_max_concurrent == 8:
                execution_preset = "parallel"
            else:
                execution_preset = "custom"

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
        prev_main_max_concurrent = defaults.get("maxConcurrent")
        if prev_main_max_concurrent != main_max_concurrent:
            defaults["maxConcurrent"] = main_max_concurrent
            changed["agents.defaults.maxConcurrent"] = {"from": prev_main_max_concurrent, "to": main_max_concurrent}
        prev_subagent_max_concurrent = subagents.get("maxConcurrent")
        if prev_subagent_max_concurrent != subagent_max_concurrent:
            subagents["maxConcurrent"] = subagent_max_concurrent
            changed["agents.defaults.subagents.maxConcurrent"] = {
                "from": prev_subagent_max_concurrent,
                "to": subagent_max_concurrent,
            }
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
            # Если slot_thinking не содержит эту модель, сохраняем текущее значение
            # из конфига (а не сбрасываем на thinking_default).
            existing_thinking = str(params.get("thinking") or "").strip().lower()
            next_thinking = normalized_slot_thinking.get(
                model_id,
                existing_thinking if existing_thinking else thinking_default,
            )
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
            "execution_preset": execution_preset,
            "main_max_concurrent": main_max_concurrent,
            "subagent_max_concurrent": subagent_max_concurrent,
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
        auth_recovery = build_auth_recovery_readiness_snapshot(
            project_root=cls._project_root(),
            status_payload=status_snapshot.get("raw") if isinstance(status_snapshot, dict) else {},
            auth_profiles_payload=auth_profiles,
            runtime_models_payload=runtime_models,
            runtime_config_payload=runtime_config,
        )
        auth_recovery_by_name = auth_recovery.get("providers_by_name") if isinstance(auth_recovery, dict) else {}
        if not isinstance(auth_recovery_by_name, dict):
            auth_recovery_by_name = {}
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
                if normalized and normalized.lower() not in {"lmstudio", "local"}:
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
            provider_auth_recovery = auth_recovery_by_name.get(normalized_provider)
            if isinstance(provider_auth_recovery, dict):
                provider_ui = {
                    **provider_ui,
                    "auth_recovery": dict(provider_auth_recovery),
                }
            configured_model_ids = set(str(item or "").strip() for item in (provider_state.get("runtime_models") or []) if str(item or "").strip())
            configured_model_ids.update(
                cls._runtime_provider_model_ids_from_config(
                    normalized_provider,
                    runtime_config=runtime_config,
                    current_slots=current_slots,
                )
            )
            if cls._provider_is_catalog_only_stub(
                provider_payload=provider_payload,
                provider_state=provider_state,
                configured_model_ids=configured_model_ids,
            ):
                # Не рисуем фантомные карточки только потому, что в runtime остался
                # пустой stub провайдера без auth и без реально доступных моделей.
                continue
            full_catalog_models = full_catalog_providers.get(normalized_provider) if isinstance(full_catalog_providers, dict) else []
            if normalized_provider == "codex-cli" and not isinstance(full_catalog_models, list):
                full_catalog_models = []
            if normalized_provider == "codex-cli" and not full_catalog_models:
                # OpenClaw пока не всегда публикует отдельный catalog для codex-cli,
                # поэтому даём owner-панели синтетический список на базе OpenAI/OpenAI Codex.
                full_catalog_models = cls._build_codex_cli_synthetic_catalog(full_catalog_providers)
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
                    lowered_tags = {tag.lower() for tag in tags}
                    configured_runtime = bool(
                        canonical_id in configured_model_ids
                        or canonical_id in active_chain
                        or selected_slots
                        or "configured" in lowered_tags
                    )
                    if (
                        not configured_runtime
                        and normalized_provider == "codex-cli"
                        and "synthetic" in lowered_tags
                        and str(provider_state.get("readiness") or "").strip().lower() in {"ready", "attention"}
                    ):
                        # Для codex-cli runtime-каталог может быть пустым, но если CLI уже живой,
                        # synthetic OpenAI-derived модели должны быть доступны к выбору из панели.
                        configured_runtime = True
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
                        "provider_runtime_mode": str(provider_state.get("runtime_mode") or ""),
                        "provider_primary_policy": str(provider_state.get("primary_policy") or ""),
                        "provider_fallback_policy": str(provider_state.get("fallback_policy") or ""),
                        "provider_release_safe": bool(provider_state.get("release_safe")),
                        "provider_login_state": str(provider_state.get("login_state") or ""),
                        "provider_cost_tier": str(provider_state.get("cost_tier") or ""),
                        "provider_stability_score": float(provider_state.get("stability_score") or 0.0),
                        "provider_auth_recovery": dict(provider_auth_recovery or {}),
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
                        "provider_runtime_mode": str(provider_state.get("runtime_mode") or ""),
                        "provider_primary_policy": str(provider_state.get("primary_policy") or ""),
                        "provider_fallback_policy": str(provider_state.get("fallback_policy") or ""),
                        "provider_release_safe": bool(provider_state.get("release_safe")),
                        "provider_login_state": str(provider_state.get("login_state") or ""),
                        "provider_cost_tier": str(provider_state.get("cost_tier") or ""),
                        "provider_stability_score": float(provider_state.get("stability_score") or 0.0),
                        "provider_auth_recovery": dict(provider_auth_recovery or {}),
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
                    "provider_auth_recovery": dict(provider_auth_recovery or {}),
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
    def _provider_is_catalog_only_stub(
        cls,
        provider_payload: dict[str, Any] | None,
        provider_state: dict[str, Any] | None,
        configured_model_ids: set[str] | None = None,
    ) -> bool:
        """
        Отсекает фантомный provider stub без runtime-поддержки.

        Зачем это нужно:
        - OpenClaw может хранить пустой provider entry в models.json;
        - общий provider catalog при этом всё равно знает десятки моделей провайдера;
        - без фильтра owner UI показывает красивую, но ложную карточку "доступного"
          провайдера, хотя runtime даже не умеет его поднять.
        """
        payload = provider_payload if isinstance(provider_payload, dict) else {}
        state = provider_state if isinstance(provider_state, dict) else {}
        runtime_models = payload.get("models") if isinstance(payload.get("models"), list) else []
        profiles = state.get("profiles") if isinstance(state.get("profiles"), list) else []
        auth_mode = str(state.get("auth_mode") or "").strip().lower()
        effective_kind = str(state.get("effective_kind") or "").strip()
        detail = str(state.get("detail") or "").strip()
        readiness_label = str(state.get("readiness_label") or "").strip()

        if runtime_models:
            return False
        if configured_model_ids:
            return False
        if profiles:
            return False
        if auth_mode not in {"", "unknown"}:
            return False
        if effective_kind:
            return False
        return readiness_label == "Unavailable" and detail == "Runtime-провайдер пока не описан."

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
            "codex-cli/gpt-5.4",
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        thinking_model = _pick(
            str(current_slots.get("thinking", "") or ""),
            "codex-cli/gpt-5.4",
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        pro_model = _pick(
            str(current_slots.get("pro", "") or ""),
            "codex-cli/gpt-5.4",
            "openai-codex/gpt-5.4",
            "google-gemini-cli/gemini-3.1-pro-preview",
            "google/gemini-3.1-pro-preview",
            "qwen-portal/coder-model",
            "google/gemini-2.5-flash-lite",
        )
        coding_model = _pick(
            str(current_slots.get("coding", "") or ""),
            "codex-cli/gpt-5.4",
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
        codex_cli = cls._runtime_provider_state(
            "codex-cli",
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

        target_primary = str(os.getenv("OPENCLAW_TARGET_PRIMARY_MODEL", "codex-cli/gpt-5.4") or "").strip()
        target_provider = str(target_primary.split("/", 1)[0] if "/" in target_primary else "").strip().lower()
        target_provider_state = {
            "openai-codex": openai_codex,
            "codex-cli": codex_cli,
            "google-gemini-cli": google_gemini_cli,
            "google-antigravity": google_antigravity,
        }.get(target_provider, {})
        target_in_runtime = target_primary in set(target_provider_state.get("runtime_models") or [])
        current_primary_broken = bool(
            (
                current_primary.startswith("openai-codex/")
                and (
                    str(openai_codex.get("signal_fail_code") or "") == "runtime_missing_scope_model_request"
                    or (
                        int(openai_codex["failure_counts"].get("model_not_found", 0) or 0) > 0
                        and bool(openai_codex.get("cooldown_active"))
                    )
                )
            )
            or (
                current_primary.startswith("codex-cli/")
                and str(codex_cli.get("readiness") or "").strip().lower() not in {"ready"}
            )
        )
        google_gemini_cli_unavailable = bool(
            google_gemini_cli["disabled_profiles"]
            or google_gemini_cli["expired_profiles"]
            or google_gemini_cli["cooldown_active"]
        )
        antigravity_healthy = bool(google_antigravity.get("healthy_profiles"))
        antigravity_disabled = bool(google_antigravity["disabled_profiles"]) and not antigravity_healthy
        antigravity_runtime_available = bool(google_antigravity["runtime_models"] or antigravity_healthy)
        antigravity_legacy_removed = not antigravity_runtime_available

        warnings: list[str] = []
        if current_primary.startswith("codex-cli/") and str(codex_cli.get("readiness") or "").strip().lower() not in {"ready"}:
            warnings.append("Текущий Codex CLI primary не подтверждён на этой macOS-учётке и требует relogin/helper.")
        if current_primary_broken:
            if str(openai_codex.get("signal_fail_code") or "") == "runtime_missing_scope_model_request":
                warnings.append("Текущий OpenAI primary блокируется по OAuth scopes (`model.request`) и не годится как production primary.")
            elif current_primary.startswith("openai-codex/"):
                warnings.append("Текущий OpenAI primary падает с model_not_found и не годится как production primary.")
        if google_gemini_cli_unavailable:
            warnings.append(
                "Google Gemini CLI OAuth сейчас не является надёжным fallback: профиль в cooldown/expired и может требовать re-auth."
            )
        if not target_in_runtime:
            warnings.append("GPT-5.4 пока не описан в runtime models.json OpenClaw и не готов к promotion.")
        if antigravity_legacy_removed:
            warnings.append(
                "Legacy provider google-antigravity уже удалён в OpenClaw 2026.3.8+ и не должен использоваться как fallback; миграция идёт через google-gemini-cli или google/* API key."
            )
        elif antigravity_disabled:
            warnings.append("Google Antigravity сейчас disabled в auth-profiles и не должен считаться надёжным fallback.")
        elif bool(google_antigravity.get("legacy")) and antigravity_healthy:
            warnings.append(
                "Google Antigravity подключён вручную через plugin и может использоваться как дополнительный fallback, но не как единственный production primary."
            )

        temporary_primary = current_primary
        if current_primary_broken:
            temporary_primary = next(
                (
                    candidate
                    for candidate in current_fallbacks
                    if not (
                        candidate.startswith("google-antigravity/")
                        and (antigravity_disabled or antigravity_legacy_removed)
                    )
                    and not (
                        candidate.startswith("google-gemini-cli/")
                        and google_gemini_cli_unavailable
                    )
                    and not (
                        candidate.startswith("codex-cli/")
                        and str(codex_cli.get("readiness") or "").strip().lower() not in {"ready"}
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
            "codex_cli": codex_cli,
            "openai_codex": openai_codex,
            "google_gemini_cli": google_gemini_cli,
            "google_antigravity": google_antigravity,
            "google_antigravity_legacy_removed": antigravity_legacy_removed,
            "warnings": warnings,
            "workspace": str(defaults.get("workspace", "") or ""),
        }

    @classmethod
    def _overlay_live_route_on_openclaw_model_routing_status(
        cls,
        *,
        routing: dict[str, Any],
        last_runtime_route: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Накладывает живую truth последнего runtime-route поверх исторической диагностики."""
        if not isinstance(routing, dict):
            routing = {}
        route_payload = dict(last_runtime_route or {})
        route_model = str(route_payload.get("model", "") or "").strip()
        route_provider = str(route_payload.get("provider", "") or "").strip()
        route_reason = str(route_payload.get("route_reason", "") or "").strip()
        route_detail = str(route_payload.get("route_detail", "") or "").strip()
        route_status = str(route_payload.get("status", "") or "").strip().lower()
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
            provider_key = route_provider.replace("-", "_")
            provider_state = routing.get(provider_key)
            if isinstance(provider_state, dict):
                historical_signal_fail_code = str(provider_state.get("signal_fail_code", "") or "").strip()
                historical_readiness = str(provider_state.get("readiness", "") or "").strip()
                historical_readiness_label = str(provider_state.get("readiness_label", "") or "").strip()
                historical_detail = str(provider_state.get("detail", "") or "").strip()
                if historical_signal_fail_code:
                    provider_state["historical_signal_fail_code"] = historical_signal_fail_code
                if historical_readiness:
                    provider_state["historical_readiness"] = historical_readiness
                if historical_readiness_label:
                    provider_state["historical_readiness_label"] = historical_readiness_label
                if historical_detail:
                    provider_state["historical_detail"] = historical_detail
                provider_state["signal_fail_code"] = ""
                provider_state["readiness"] = "ready"
                provider_state["readiness_label"] = "Live OK"
                live_detail = route_detail or "Ответ получен через OpenClaw API."
                provider_state["detail"] = (
                    f"Последний live route подтвердил configured primary `{current_primary}`. {live_detail}"
                )
            warnings = routing.get("warnings")
            if isinstance(warnings, list):
                routing["warnings"] = [
                    item
                    for item in warnings
                    if "openai primary падает с model_not_found" not in str(item).lower()
                    and "openai primary блокируется по oauth scopes" not in str(item).lower()
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
        return routing

    @classmethod
    def _overlay_routing_provider_truth_on_cloud_inventory(
        cls,
        *,
        cloud_inventory: list[dict[str, Any]],
        routing_status: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Переносит provider-level live truth в inventory и provider cards каталога."""
        for item in cloud_inventory:
            if not isinstance(item, dict):
                continue
            provider_name = str(item.get("provider", "") or "").strip()
            if not provider_name:
                continue
            provider_state = routing_status.get(provider_name.replace("-", "_"))
            if not isinstance(provider_state, dict):
                continue
            item["provider_auth"] = str(provider_state.get("auth_mode") or item.get("provider_auth") or "unknown")
            item["provider_readiness"] = str(provider_state.get("readiness") or item.get("provider_readiness") or "unknown")
            item["provider_readiness_label"] = str(
                provider_state.get("readiness_label") or item.get("provider_readiness_label") or "Configured"
            )
            item["provider_detail"] = str(provider_state.get("detail") or item.get("provider_detail") or "")
            item["provider_quota_state"] = str(
                provider_state.get("quota_state") or item.get("provider_quota_state") or "unknown"
            )
            item["provider_quota_label"] = str(
                provider_state.get("quota_label") or item.get("provider_quota_label") or ""
            )
            item["provider_effective_kind"] = str(
                provider_state.get("effective_kind") or item.get("provider_effective_kind") or ""
            )
            item["provider_effective_detail"] = str(
                provider_state.get("effective_detail") or item.get("provider_effective_detail") or ""
            )
            item["provider_oauth_status"] = str(
                provider_state.get("oauth_status") or item.get("provider_oauth_status") or ""
            )
            item["provider_oauth_remaining_human"] = str(
                provider_state.get("oauth_remaining_human") or item.get("provider_oauth_remaining_human") or ""
            )
        return cloud_inventory

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

    async def _run_openclaw_cli(
        self,
        *args: str,
        timeout: float = 45.0,
        expect_json: bool = False,
    ) -> dict[str, Any]:
        """
        Безопасно запускает `openclaw` CLI и при необходимости парсит JSON-ответ.

        Почему отдельный helper:
        - cron/UI не должен дублировать runtime scheduler;
        - тестам удобнее подменять один seam, чем мокать subprocess по всему модулю;
        - owner-панель получает truthful state ровно из того же CLI-контура,
          который пользователь может вызвать вручную.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._openclaw_cli_env(),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                return {
                    "ok": False,
                    "error": "openclaw_timeout",
                    "detail": f"Команда openclaw {' '.join(args)} превысила {int(timeout)} сек.",
                    "exit_code": None,
                    "raw": "",
                }
        except Exception as exc:
            return {
                "ok": False,
                "error": "openclaw_exec_failed",
                "detail": str(exc),
                "exit_code": None,
                "raw": "",
            }

        raw_output = stdout.decode("utf-8", errors="replace")
        result: dict[str, Any] = {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "raw": raw_output,
        }
        if not expect_json:
            return result

        try:
            result["data"] = json.loads(raw_output or "{}")
        except Exception as exc:
            result["ok"] = False
            result["error"] = "openclaw_json_parse_failed"
            result["detail"] = f"Не удалось распарсить JSON ответа openclaw: {exc}"
        return result

    @staticmethod
    def _normalize_openclaw_cron_job(job: dict[str, Any]) -> dict[str, Any]:
        """
        Сжимает cron job до стабильной UI-формы.

        Оставляем только те поля, которые реально нужны owner-панели, чтобы
        фронт не зависел от всей вложенной схемы OpenClaw.
        """
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        state = job.get("state") if isinstance(job.get("state"), dict) else {}
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        schedule_kind = str(schedule.get("kind") or "unknown").strip().lower()
        schedule_label = "unknown"
        if schedule_kind == "every":
            every_ms = int(schedule.get("everyMs") or 0)
            schedule_label = f"Каждые {every_ms // 1000}с" if every_ms > 0 else "Каждые ?"
            if every_ms and every_ms % 60000 == 0:
                schedule_label = f"Каждые {every_ms // 60000}м"
            elif every_ms and every_ms % 3600000 == 0:
                schedule_label = f"Каждые {every_ms // 3600000}ч"
        elif schedule_kind == "cron":
            expr = str(schedule.get("expr") or "").strip() or "?"
            tz = str(schedule.get("tz") or "").strip()
            schedule_label = f"Cron: {expr}" if not tz else f"Cron: {expr} ({tz})"

        payload_kind = str(payload.get("kind") or "unknown").strip()
        payload_text = str(payload.get("text") or payload.get("message") or "").strip()
        return {
            "id": str(job.get("id") or "").strip(),
            "name": str(job.get("name") or "Без названия").strip(),
            "enabled": bool(job.get("enabled")),
            "agent_id": str(job.get("agentId") or "").strip(),
            "session_target": str(job.get("sessionTarget") or "").strip(),
            "wake_mode": str(job.get("wakeMode") or "").strip(),
            "schedule_kind": schedule_kind,
            "schedule_label": schedule_label,
            "payload_kind": payload_kind,
            "payload_text": payload_text,
            "description": str(job.get("description") or "").strip(),
            "updated_at_ms": int(job.get("updatedAtMs") or 0),
            "created_at_ms": int(job.get("createdAtMs") or 0),
            "last_run_at_ms": int(state.get("lastRunAtMs") or 0),
            "last_status": str(state.get("lastStatus") or state.get("lastRunStatus") or "unknown").strip(),
            "last_error": str(state.get("lastError") or "").strip(),
            "consecutive_errors": int(state.get("consecutiveErrors") or 0),
        }

    async def _collect_openclaw_cron_snapshot(self, *, include_all: bool = True) -> dict[str, Any]:
        """
        Возвращает статус scheduler и список cron jobs из настоящего OpenClaw CLI.
        """
        status_result = await self._run_openclaw_cli(
            "cron",
            "status",
            "--json",
            timeout=35.0,
            expect_json=True,
        )
        if not status_result.get("ok"):
            return {
                "ok": False,
                "error": status_result.get("error") or "cron_status_failed",
                "detail": status_result.get("detail") or status_result.get("raw") or "Не удалось прочитать cron status",
            }

        list_args = ["cron", "list", "--json"]
        if include_all:
            list_args.append("--all")
        jobs_result = await self._run_openclaw_cli(
            *list_args,
            timeout=35.0,
            expect_json=True,
        )
        if not jobs_result.get("ok"):
            return {
                "ok": False,
                "error": jobs_result.get("error") or "cron_jobs_failed",
                "detail": jobs_result.get("detail") or jobs_result.get("raw") or "Не удалось прочитать cron jobs",
            }

        status_payload = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        jobs_payload = jobs_result.get("data") if isinstance(jobs_result.get("data"), dict) else {}
        jobs_raw = jobs_payload.get("jobs") if isinstance(jobs_payload.get("jobs"), list) else []
        jobs = [
            self._normalize_openclaw_cron_job(job)
            for job in jobs_raw
            if isinstance(job, dict)
        ]
        jobs.sort(key=lambda item: ((not item["enabled"]), item["name"].lower(), item["id"]))
        enabled_jobs = sum(1 for item in jobs if item["enabled"])
        disabled_jobs = max(0, len(jobs) - enabled_jobs)
        return {
            "ok": True,
            "status": {
                "enabled": bool(status_payload.get("enabled")),
                "store_path": str(status_payload.get("storePath") or "").strip(),
                "jobs_total_runtime": int(status_payload.get("jobs") or 0),
                "next_wake_at_ms": status_payload.get("nextWakeAtMs"),
            },
            "summary": {
                "total": len(jobs),
                "enabled": enabled_jobs,
                "disabled": disabled_jobs,
                "include_all": bool(include_all),
            },
            "jobs": jobs,
        }

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
    def _clone_jsonish_payload(payload: Any) -> Any:
        """Возвращает глубокую копию JSON-подобного payload для cache/fallback ответов."""
        return copy.deepcopy(payload)

    @staticmethod
    def _float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
        """Читает float из env с безопасным clamp и без размазывания try/except по коду."""
        raw = str(os.getenv(name, str(default)) or str(default)).strip()
        try:
            value = float(raw)
        except Exception:
            value = float(default)
        return max(float(min_value), min(float(value), float(max_value)))

    def _model_catalog_cache_ttl_sec(self) -> float:
        """TTL короткого cache каталога owner UI."""
        return self._float_env(
            "KRAB_WEB_MODEL_CATALOG_CACHE_TTL_SEC",
            45.0,
            min_value=5.0,
            max_value=300.0,
        )

    def _model_apply_catalog_timeout_sec(self) -> float:
        """Сколько ждём post-apply catalog refresh до graceful fallback."""
        return self._float_env(
            "KRAB_WEB_MODEL_APPLY_CATALOG_TIMEOUT_SEC",
            4.0,
            min_value=0.2,
            max_value=30.0,
        )

    def _store_model_catalog_cache(self, payload: dict[str, Any]) -> None:
        """Запоминает свежий catalog snapshot для быстрых повторных запросов UI."""
        if not isinstance(payload, dict):
            return
        self._model_catalog_cache = (
            time.time(),
            self._clone_jsonish_payload(payload),
        )

    def _get_model_catalog_cache(self) -> dict[str, Any] | None:
        """Возвращает свежий catalog cache, если TTL ещё не истёк."""
        cached = self._model_catalog_cache
        if cached is None:
            return None
        cached_ts, cached_payload = cached
        if time.time() - cached_ts > self._model_catalog_cache_ttl_sec():
            return None
        if not isinstance(cached_payload, dict):
            return None
        return self._clone_jsonish_payload(cached_payload)

    def _build_model_catalog_fallback(
        self,
        *,
        runtime_controls: dict[str, Any] | None = None,
        routing_status: dict[str, Any] | None = None,
        degraded_reason: str = "catalog_refresh_degraded",
    ) -> dict[str, Any]:
        """
        Собирает облегчённый catalog, если полный refresh после write-операции затянулся.

        Главная цель этого fallback:
        - не скрывать успешную запись runtime-chain за медленной пересборкой каталога;
        - сохранить для UI последнюю известную inventory truth из cache;
        - поверх cache обязательно наложить уже записанные runtime_controls/routing_status.
        """
        catalog = self._get_model_catalog_cache() or {
            "force_mode": "auto",
            "slots": ["chat", "thinking", "pro", "coding"],
            "cloud_slots": {},
            "local_engine": "",
            "local_available": False,
            "local_active_model": "",
            "local_models": [],
            "local_models_error": "",
            "cloud_presets": [],
            "cloud_inventory": [],
            "cloud_provider_groups": [],
            "aliases": [],
            "quick_presets": [],
            "runtime_model_count": 0,
            "cloud_inventory_count": 0,
            "runtime_registry_source": "cache_fallback",
            "router_usage": {},
            "parallelism_truth": self._build_openclaw_parallelism_truth(),
            "auth_recovery": {"summary": {}, "providers": []},
            "catalog_guidance": {
                "primary_flow": "Каталог временно взят из cache; chain/thinking truth уже обновлены.",
                "openai_manual_only": False,
            },
        }
        if isinstance(runtime_controls, dict):
            catalog["runtime_controls"] = self._clone_jsonish_payload(runtime_controls)
        if isinstance(routing_status, dict):
            catalog["routing_status"] = self._clone_jsonish_payload(routing_status)
        catalog["catalog_refresh_degraded"] = True
        catalog["catalog_refresh_reason"] = str(degraded_reason or "catalog_refresh_degraded")
        return catalog

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

    def _run_project_python_script(
        self,
        script_path: Path,
        *,
        timeout_seconds: int = 90,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Неинтерактивно запускает repo-level Python entrypoint для web write-endpoint'ов.

        Почему это отдельно от `.command`:
        - Finder-friendly launcher'ы часто заканчиваются `read -p`;
        - такой хвост безопасен для человека, но ломает HTTP recovery-flow;
        - owner panel должна вызывать ту же логику без интерактивной паузы.
        """
        target = Path(script_path).resolve()
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "exit_code": 127,
                "stdout_tail": "",
                "error": f"script_not_found:{target}",
            }

        project_root = self._project_root()
        # Единый venv (Py 3.13) в приоритете; legacy .venv — фолбек.
        python_candidates = [
            project_root / "venv" / "bin" / "python",
            project_root / ".venv" / "bin" / "python",
        ]
        python_bin = next((path for path in python_candidates if path.exists() and path.is_file()), None)
        if python_bin is None:
            python_bin = Path(sys.executable)

        cmd = [str(python_bin), str(target)] + [str(item) for item in (args or [])]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
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
                "python_bin": str(python_bin),
                "script_path": str(target),
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
                "python_bin": str(python_bin),
                "script_path": str(target),
            }
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": 1,
                "stdout_tail": "",
                "error": f"script_run_error:{exc}",
                "python_bin": str(python_bin),
                "script_path": str(target),
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

    @staticmethod
    def _default_browser_state_root() -> Path:
        """
        Возвращает канонический root browser-state для текущей macOS-учётки.

        Почему это отдельный helper:
        - multi-account стратегия проекта теперь опирается на split browser state;
        - handoff и readiness должны явно показывать, какой профиль Chrome
          относится именно к текущему `HOME`, а не к соседней учётке.
        """
        env_candidates = (
            "CHROME_USER_DATA_DIR",
            "GOOGLE_CHROME_USER_DATA_DIR",
            "OPENCLAW_BROWSER_PROFILE_DIR",
        )
        for key in env_candidates:
            raw = str(os.getenv(key, "") or "").strip()
            if raw:
                return Path(raw).expanduser()
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"

    @staticmethod
    def _canonical_shared_root() -> Path:
        """Канонический shared repo path для multi-account режима."""
        return Path("/Users/Shared/Antigravity_AGENTS/Краб")

    @staticmethod
    def _active_shared_root() -> Path:
        """Fast-path shared worktree для соседней macOS-учётки."""
        return Path("/Users/Shared/Antigravity_AGENTS/Краб-active")

    @staticmethod
    def _paths_match(left: Path | str, right: Path | str) -> bool:
        """Сравнивает пути по каноническому абсолютному виду без лишних исключений."""
        try:
            left_path = Path(left).expanduser().resolve()
        except OSError:
            left_path = Path(left).expanduser()
        try:
            right_path = Path(right).expanduser().resolve()
        except OSError:
            right_path = Path(right).expanduser()
        return str(left_path) == str(right_path)

    def _workspace_alignment_snapshot(self) -> dict[str, Any]:
        """Фиксирует, совпадает ли текущий project root с каноническим shared worktree."""
        project_root = self._project_root()
        canonical_shared_root = self._canonical_shared_root()
        active_shared_root = self._active_shared_root()
        active_exists = active_shared_root.exists()
        canonical_exists = canonical_shared_root.exists()
        matches_active = active_exists and self._paths_match(project_root, active_shared_root)
        matches_canonical = canonical_exists and self._paths_match(project_root, canonical_shared_root)

        if active_exists:
            recommended_root = active_shared_root
            recommended_reason = "fast_path_active_shared"
        elif canonical_exists:
            recommended_root = canonical_shared_root
            recommended_reason = "canonical_shared_repo"
        else:
            recommended_root = project_root
            recommended_reason = "current_local_root"

        if matches_active:
            status = "ready"
            summary = "Текущий project root уже совпадает с `Краб-active`."
        elif matches_canonical:
            status = "attention" if active_exists else "ready"
            summary = (
                "Работа идёт из канонического shared repo; это допустимо, но fast-path уже опубликован в `Краб-active`."
                if active_exists
                else "Работа идёт из канонического shared repo."
            )
        else:
            status = "attention" if active_exists or canonical_exists else "local_only"
            summary = (
                "Текущий project root не совпадает с рекомендованным shared-root; для соседней учётки safer default — `Краб-active`."
                if active_exists or canonical_exists
                else "Shared roots сейчас недоступны; продолжаем из текущего локального project root."
            )

        return {
            "status": status,
            "current_project_root": str(project_root),
            "canonical_shared_root": str(canonical_shared_root),
            "canonical_shared_root_exists": canonical_exists,
            "active_shared_root": str(active_shared_root),
            "active_shared_root_exists": active_exists,
            "project_root_matches_active_shared": matches_active,
            "project_root_matches_canonical_shared": matches_canonical,
            "recommended_project_root": str(recommended_root),
            "recommended_reason": recommended_reason,
            "summary": summary,
        }

    def _active_shared_permission_health_snapshot(self) -> dict[str, Any]:
        """Показывает, есть ли в `Краб-active` owner-only хвосты для текущей учётки."""
        active_shared_root = self._active_shared_root()
        health = sample_non_writable_shared_items(active_shared_root)
        return {
            "active_shared_root": str(active_shared_root),
            "active_shared_root_exists": active_shared_root.exists(),
            "non_writable_count": int(health.get("non_writable_count") or 0),
            "samples": list(health.get("samples") or []),
            "checked_entries": int(health.get("checked_entries") or 0),
            "status": "attention" if int(health.get("non_writable_count") or 0) > 0 else "ready",
        }

    def _runtime_operator_profile(self) -> dict[str, Any]:
        """
        Возвращает machine-readable профиль текущей учётки/runtime.

        Зачем:
        - соседняя macOS-учётка должна видеть, в каком именно runtime-контуре она
          сейчас работает;
        - handoff bundle должен фиксировать не только сервисы, но и identity/state
          активного оператора.
        """
        home_dir = Path.home()
        operator_name = current_operator_id()
        browser_state_root = self._default_browser_state_root()
        project_root = self._project_root()
        fingerprint = current_account_id()
        runtime_mode = current_runtime_mode()
        workspace_alignment = self._workspace_alignment_snapshot()
        active_shared_permission_health = self._active_shared_permission_health_snapshot()

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator_id": operator_name,
            "operator_name": operator_name,
            "account_id": fingerprint,
            "account_mode": "split_runtime_per_account",
            "runtime_mode": runtime_mode,
            "release_safe_mode": runtime_mode == "release-safe-runtime",
            "home_dir": str(home_dir),
            "project_root": str(project_root),
            "project_exists": project_root.exists(),
            "project_writable": bool(os.access(project_root, os.W_OK)),
            "python_executable": sys.executable,
            "openclaw_home": str(home_dir / ".openclaw"),
            "openclaw_home_exists": (home_dir / ".openclaw").exists(),
            "openclaw_config_path": str(self._openclaw_config_path()),
            "openclaw_models_path": str(self._openclaw_models_config_path()),
            "openclaw_auth_profiles_path": str(self._openclaw_auth_profiles_path()),
            "workspace_main_dir": str(getattr(config, "OPENCLAW_MAIN_WORKSPACE_DIR", "")),
            "userbot_acl_file": str(getattr(config, "USERBOT_ACL_FILE", "")),
            "browser_state_root": str(browser_state_root),
            "browser_state_root_exists": browser_state_root.exists(),
            "owner_chrome_helper_path": str(self._owner_chrome_remote_debugging_helper_path()),
            "debug_chrome_helper_path": str(self._debug_chrome_remote_debugging_helper_path()),
            "web_public_base_url": self._public_base_url(),
            "workspace_alignment": workspace_alignment,
            "active_shared_permission_health": active_shared_permission_health,
            "notes": [
                "Канонический режим для нескольких macOS-учёток: shared repo/docs/artifacts, но split runtime/auth/secrets/browser state.",
                "Если запускаешь проект из соседней учётки, truth нужно проверять по этой карточке, а не по старому handoff на другой HOME.",
                "Параллельные диалоги допустимы, но один mutating implementation dialog на активный runtime-контур остаётся safer default.",
                "Runtime mode должен быть явным: personal-runtime, release-safe-runtime или lab-runtime.",
            ],
        }

    def _assistant_capabilities_snapshot(self) -> dict[str, Any]:
        """Возвращает единый assistant capability-срез для web-native контура."""
        return {
            "ok": True,
            "mode": "web_native",
            "endpoint": "/api/assistant/query",
            "preflight_endpoint": "/api/model/preflight",
            "feedback_endpoint": "/api/model/feedback",
            "model_catalog_endpoint": "/api/model/catalog",
            "model_apply_endpoint": "/api/model/apply",
            "attachment_endpoint": "/api/assistant/attachment",
            "policy_endpoint": "/api/policy",
            "policy_matrix_endpoint": "/api/policy/matrix",
            "registry_endpoint": "/api/capabilities/registry",
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

    async def _ecosystem_capabilities_snapshot(self) -> dict[str, Any]:
        """Собирает truthful capability-срез control plane и внешних сервисов."""
        voice_gateway = self.deps.get("voice_gateway_client")
        krab_ear = self.deps.get("krab_ear_client")

        voice_caps, ear_caps = await asyncio.gather(
            self._safe_client_capabilities_summary(voice_gateway, source="voice_gateway"),
            self._safe_client_capabilities_summary(krab_ear, source="krab_ear"),
        )

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "services": {
                "krab": {
                    "ok": True,
                    "status": "ok",
                    "source": "web_app",
                    "detail": {
                        "mode": "control_plane",
                        "assistant_endpoint": "/api/assistant/query",
                        "assistant_capabilities_endpoint": "/api/assistant/capabilities",
                        "ecosystem_health_endpoint": "/api/ecosystem/health",
                        "ecosystem_capabilities_endpoint": "/api/ecosystem/capabilities",
                        "capability_registry_endpoint": "/api/capabilities/registry",
                        "policy_matrix_endpoint": "/api/policy/matrix",
                    },
                },
                "voice_gateway": voice_caps,
                "krab_ear": ear_caps,
            },
            "notes": [
                "Krab остаётся control plane и не встраивает Ear/Voice рантаймы в монолит.",
                "Krab Ear читается по native IPC-контракту.",
                "Krab Voice Gateway читается по HTTP contract-first endpoint'у /v1/capabilities.",
            ],
        }

    def _policy_matrix_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает policy matrix поверх ACL и live runtime-lite truth."""
        return build_policy_matrix(
            operator_id=current_operator_id(),
            account_id=current_account_id(),
            acl_state=load_acl_runtime_state(),
            web_write_requires_key=bool(self._web_api_key()),
            runtime_lite=runtime_lite or {},
        )

    def _channel_capabilities_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        policy_matrix: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает truthful channel capability snapshot для primary/reserve/runtime каналов."""
        runtime_state = runtime_lite or {}
        policy_payload = policy_matrix if isinstance(policy_matrix, dict) else self._policy_matrix_snapshot(runtime_lite=runtime_state)
        runtime_config = self._load_openclaw_runtime_config()
        runtime_channels = runtime_config.get("channels") if isinstance(runtime_config.get("channels"), dict) else {}
        return build_channel_capability_snapshot(
            operator_profile=self._runtime_operator_profile(),
            runtime_lite=runtime_state,
            runtime_channels_config=runtime_channels,
            policy_matrix=policy_payload,
            workspace_state=(
                runtime_state.get("workspace_state")
                if isinstance(runtime_state.get("workspace_state"), dict)
                else build_workspace_state_snapshot()
            ),
        )

    async def _capability_registry_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает единый capability registry поверх уже подтверждённых truthful-срезов."""
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        operator_profile = self._runtime_operator_profile()
        assistant_caps = self._assistant_capabilities_snapshot()
        policy_matrix = self._policy_matrix_snapshot(runtime_lite=runtime_state)
        channel_capabilities = self._channel_capabilities_snapshot(
            runtime_lite=runtime_state,
            policy_matrix=policy_matrix,
        )
        ecosystem_caps, translator_snapshot = await asyncio.gather(
            self._ecosystem_capabilities_snapshot(),
            self._translator_readiness_snapshot(runtime_lite=runtime_state),
        )
        # Phase 3 Шаг 2: live health checks для browser, macos и mcp_relay
        browser_probe: dict | None = None
        macos_probe: dict | None = None
        mcp_probe: dict | None = None
        try:
            from ..integrations.browser_bridge import browser_bridge as _bb
            browser_probe = await asyncio.wait_for(_bb.health_check(), timeout=5.0)
        except Exception:
            pass
        try:
            from ..integrations.macos_automation import macos_automation as _ma
            macos_probe = await asyncio.wait_for(_ma.health_check(), timeout=5.0)
        except Exception:
            pass
        try:
            from ..mcp_client import mcp_manager as _mcp
            mcp_probe = await asyncio.wait_for(_mcp.health_check(), timeout=3.0)
        except Exception:
            pass
        tor_probe: dict | None = None
        try:
            if bool(getattr(config, "TOR_ENABLED", False)):
                from ..integrations.tor_bridge import health_check as _tor_hc
                tor_probe = await asyncio.wait_for(
                    _tor_hc(socks_port=int(getattr(config, "TOR_SOCKS_PORT", 9050))),
                    timeout=15.0,
                )
        except Exception:
            pass
        system_control = build_system_control_snapshot(
            browser_probe=browser_probe,
            macos_probe=macos_probe,
            mcp_probe=mcp_probe,
            tor_probe=tor_probe,
        )
        return build_capability_registry(
            operator_profile=operator_profile,
            runtime_lite=runtime_state,
            assistant_capabilities=assistant_caps,
            ecosystem_capabilities=ecosystem_caps,
            translator_readiness=translator_snapshot,
            policy_matrix=policy_matrix,
            channel_capabilities=channel_capabilities,
            system_control=system_control,
        )

    async def _safe_client_health_summary(
        self,
        client: Any,
        *,
        source: str,
        timeout_sec: float = 3.5,
    ) -> dict[str, Any]:
        """Безопасно возвращает нормализованный health-summary клиента."""
        if not client:
            return {
                "ok": False,
                "status": "not_configured",
                "source": source,
                "detail": {},
            }
        if hasattr(client, "health_report"):
            try:
                return await asyncio.wait_for(client.health_report(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "status": "timeout",
                    "source": source,
                    "detail": "timeout",
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": "error",
                    "source": source,
                    "detail": str(exc),
                }
        if hasattr(client, "health_check"):
            try:
                ok = bool(await asyncio.wait_for(client.health_check(), timeout=timeout_sec))
                return {
                    "ok": ok,
                    "status": "ok" if ok else "down",
                    "source": source,
                    "detail": {},
                }
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "status": "timeout",
                    "source": source,
                    "detail": "timeout",
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": "error",
                    "source": source,
                    "detail": str(exc),
                }
        return {
            "ok": False,
            "status": "not_supported",
            "source": source,
            "detail": {},
        }

    async def _safe_client_capabilities_summary(
        self,
        client: Any,
        *,
        source: str,
        timeout_sec: float = 4.0,
    ) -> dict[str, Any]:
        """Безопасно возвращает capability summary клиента."""
        if not client or not hasattr(client, "capabilities_report"):
            return {
                "ok": False,
                "status": "not_configured",
                "source": source,
                "detail": {},
            }
        try:
            return await asyncio.wait_for(client.capabilities_report(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "status": "timeout",
                "source": source,
                "detail": "timeout",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": "error",
                "source": source,
                "detail": str(exc),
            }

    async def _translator_readiness_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Собирает агрегированную готовность translator-контура.

        Почему отдельный snapshot:
        - переводчик звонков теперь отдельный продуктовый трек экосистемы;
        - handoff и соседняя учётка должны видеть не фантазии о переводчике,
          а реальное состояние foundation через Krab/Ear/Voice Gateway.
        """
        voice_gateway = self.deps.get("voice_gateway_client")
        krab_ear = self.deps.get("krab_ear_client")
        perceptor = self.deps.get("perceptor")
        kraab_userbot = self.deps.get("kraab_userbot")

        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        operator_profile = self._runtime_operator_profile()
        workspace_state = (
            runtime_state.get("workspace_state")
            if isinstance(runtime_state.get("workspace_state"), dict)
            else {}
        )
        telegram_userbot_state = (
            runtime_state.get("telegram_userbot")
            if isinstance(runtime_state.get("telegram_userbot"), dict)
            else {}
        )
        last_runtime_route = (
            runtime_state.get("last_runtime_route")
            if isinstance(runtime_state.get("last_runtime_route"), dict)
            else {}
        )
        voice_gateway_health, voice_gateway_caps, krab_ear_health, krab_ear_caps = await asyncio.gather(
            self._safe_client_health_summary(voice_gateway, source="voice_gateway"),
            self._safe_client_capabilities_summary(voice_gateway, source="voice_gateway"),
            self._safe_client_health_summary(krab_ear, source="krab_ear"),
            self._safe_client_capabilities_summary(krab_ear, source="krab_ear"),
        )

        perceptor_ready = bool(perceptor) and hasattr(perceptor, "transcribe")
        voice_profile: dict[str, Any] = {}
        if kraab_userbot and hasattr(kraab_userbot, "get_voice_runtime_profile"):
            try:
                voice_profile = dict(kraab_userbot.get_voice_runtime_profile() or {})
            except Exception:
                voice_profile = {}

        voice_gateway_caps_detail = (
            voice_gateway_caps.get("detail")
            if isinstance(voice_gateway_caps.get("detail"), dict)
            else {}
        )
        krab_ear_caps_detail = (
            krab_ear_caps.get("detail")
            if isinstance(krab_ear_caps.get("detail"), dict)
            else {}
        )
        voice_stack_ready = bool(voice_gateway_health.get("ok") and krab_ear_health.get("ok"))
        foundation_ready = bool(perceptor_ready and voice_stack_ready)
        live_voice_ready = bool(foundation_ready and voice_profile.get("enabled"))
        perceptor_whisper_model = str(getattr(perceptor, "whisper_model", "") or "").strip()
        account_runtime_ready = bool(
            telegram_userbot_state.get("client_connected")
            and workspace_state.get("shared_workspace_attached")
            and runtime_state.get("voice_gateway_configured")
        )

        if foundation_ready:
            readiness = "ready"
        elif perceptor_ready or bool(voice_gateway_health.get("ok")) or bool(krab_ear_health.get("ok")):
            readiness = "degraded"
        else:
            readiness = "planned"

        recommendations: list[str] = []
        if not perceptor_ready:
            recommendations.append("Локальный STT/perceptor ещё не подтверждён для translator foundation.")
        if not bool(voice_gateway_health.get("ok")):
            recommendations.append("Krab Voice Gateway должен быть живым, потому что он канонический backend call translator трека.")
        if not bool(krab_ear_health.get("ok")):
            recommendations.append("Krab Ear должен быть доступен как low-latency local perception/STT контур.")
        if voice_profile and not bool(voice_profile.get("enabled")):
            recommendations.append("Voice replies сейчас выключены; voice-first контур translator v1 останется неполным.")
        if not recommendations:
            recommendations.append("Foundation translator-контура подтверждён; можно двигаться к iPhone companion и ordinary-call flow.")

        foundation_checks = {
            "perceptor": {
                "ready": perceptor_ready,
                "status": "ready" if perceptor_ready else "missing",
                "label": "Perceptor / STT",
                "detail": {
                    "whisper_model": perceptor_whisper_model,
                    "isolated_worker": bool(getattr(perceptor, "stt_isolated_worker", False)),
                },
            },
            "voice_gateway": {
                "ready": bool(voice_gateway_health.get("ok")),
                "status": str(voice_gateway_health.get("status") or "unknown"),
                "label": "Krab Voice Gateway",
                "detail": {
                    "source": voice_gateway_health.get("source"),
                    "latency_ms": voice_gateway_health.get("latency_ms"),
                    "contract_version": voice_gateway_caps_detail.get("contract_version"),
                    "service": voice_gateway_caps_detail.get("service"),
                },
            },
            "krab_ear": {
                "ready": bool(krab_ear_health.get("ok")),
                "status": str(krab_ear_health.get("status") or "unknown"),
                "label": "Krab Ear",
                "detail": {
                    "source": krab_ear_health.get("source"),
                    "latency_ms": krab_ear_health.get("latency_ms"),
                    "transport": krab_ear_caps_detail.get("transport"),
                },
            },
            "voice_replies": {
                "ready": bool(voice_profile.get("enabled")),
                "status": "enabled" if voice_profile.get("enabled") else "disabled",
                "label": "Voice replies",
                "detail": {
                    "delivery": voice_profile.get("delivery"),
                    "speed": voice_profile.get("speed"),
                    "voice": voice_profile.get("voice"),
                },
            },
            "voice_ingress": {
                "ready": bool(voice_profile.get("input_transcription_ready")),
                "status": "ready" if voice_profile.get("input_transcription_ready") else "missing",
                "label": "Voice ingress",
                "detail": {
                    "input_transcription_ready": bool(voice_profile.get("input_transcription_ready")),
                    "output_tts_ready": bool(voice_profile.get("output_tts_ready")),
                },
            },
        }

        active_session_payload = {}
        if isinstance(voice_gateway_caps_detail.get("active_session"), dict):
            active_session_payload = dict(voice_gateway_caps_detail.get("active_session") or {})
        elif isinstance(voice_gateway_caps_detail.get("session"), dict):
            active_session_payload = dict(voice_gateway_caps_detail.get("session") or {})

        active_session_status = (
            str(active_session_payload.get("status") or "").strip()
            or ("not_reported" if voice_gateway_caps.get("ok") else "gateway_unavailable")
        )
        active_shared_permission_health = self._active_shared_permission_health_snapshot()
        active_session = {
            "status": active_session_status,
            "session_id": str(
                active_session_payload.get("session_id")
                or active_session_payload.get("id")
                or ""
            ).strip(),
            "label": str(
                active_session_payload.get("label")
                or active_session_payload.get("session_label")
                or ""
            ).strip(),
            "timeline_status": str(
                active_session_payload.get("timeline_status")
                or ("available" if active_session_payload.get("timeline") else "not_reported")
            ).strip(),
            "diagnostics_status": str(
                active_session_payload.get("diagnostics_status")
                or ("available" if active_session_payload.get("diagnostics") else "not_reported")
            ).strip(),
            "device_binding_status": str(
                active_session_payload.get("device_binding_status")
                or active_session_payload.get("device_status")
                or "not_reported"
            ).strip(),
        }

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "readiness": readiness,
            "foundation_ready": foundation_ready,
            "live_voice_ready": live_voice_ready,
            "v1_target": "iphone_companion",
            "canonical_backend": "krab_voice_gateway",
            "foundation_checks": foundation_checks,
            "ordinary_calls": {
                "path": "iphone_companion",
                "status": "foundation_ready" if foundation_ready else "in_progress",
            },
            "internet_calls": {
                "path": "voice_gateway_session_adapters",
                "status": "planned",
            },
            "languages": ["es-ru", "es-en", "en-ru", "auto-detect"],
            "delivery_paths": {
                "debug_install": "xcode_free_signing",
                "daily_use": "altstore_or_sidestore",
                "paid_apple_developer_required": False,
            },
            "active_shared_permission_health": active_shared_permission_health,
            "account_runtime": {
                "status": "ready" if account_runtime_ready else "attention",
                "operator_id": str(operator_profile.get("operator_id") or "").strip(),
                "account_id": str(operator_profile.get("account_id") or "").strip(),
                "account_mode": str(operator_profile.get("account_mode") or "").strip(),
                "runtime_mode": str(operator_profile.get("runtime_mode") or current_runtime_mode()).strip(),
                "release_safe_mode": bool(operator_profile.get("release_safe_mode")),
                "userbot_authorized": bool(telegram_userbot_state.get("client_connected")),
                "userbot_authorized_user": str(telegram_userbot_state.get("authorized_user") or "").strip(),
                "shared_workspace_attached": bool(workspace_state.get("shared_workspace_attached")),
                "shared_memory_ready": bool(workspace_state.get("shared_memory_ready")),
                "scheduler_enabled": bool(runtime_state.get("scheduler_enabled")),
                "voice_gateway_configured": bool(runtime_state.get("voice_gateway_configured")),
                "openclaw_auth_state": str(runtime_state.get("openclaw_auth_state") or "unknown"),
                "current_route_model": str(last_runtime_route.get("model") or "").strip(),
                "current_route_channel": str(last_runtime_route.get("channel") or "").strip(),
            },
            "active_session": active_session,
            "product_surface": {
                "owner_panel_endpoint": "/api/translator/readiness",
                "control_plane_endpoint": "/api/translator/control-plane",
                "delivery_matrix_endpoint": "/api/translator/delivery-matrix",
                "live_trial_preflight_endpoint": "/api/translator/live-trial-preflight",
                "runtime_snapshot_endpoint": "/api/ops/runtime_snapshot",
                "capability_registry_endpoint": "/api/capabilities/registry",
                "policy_matrix_endpoint": "/api/policy/matrix",
                "translator_audit_doc": str(self._project_root() / "docs" / "CALL_TRANSLATOR_AUDIT_RU.md"),
            },
            "services": {
                "krab": {
                    "ok": True,
                    "status": "ok",
                    "source": "web_app",
                    "detail": {
                        "mode": "orchestration_policy_ui",
                        "assistant_capabilities_endpoint": "/api/assistant/capabilities",
                        "ecosystem_capabilities_endpoint": "/api/ecosystem/capabilities",
                    },
                },
                "voice_gateway": voice_gateway_health,
                "voice_gateway_capabilities": voice_gateway_caps,
                "krab_ear": krab_ear_health,
                "krab_ear_capabilities": krab_ear_caps,
            },
            "runtime": {
                "voice_profile": voice_profile,
                "telegram_userbot_state": telegram_userbot_state,
                "runtime_lite_route": last_runtime_route,
            },
            "notes": [
                "Переводчик звонков интегрируется в экосистему Краба, но не merge-ится внутрь OpenClaw как монолит.",
                "Старые RealTimeVoiceTranslator-проекты рассматриваются только как доноры UX и companion flow.",
                "Для ordinary calls v1 целевой delivery path — iPhone companion; internet-call adapters идут следующим слоем.",
            ],
            "recommendations": recommendations,
        }

    async def _translator_control_plane_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Собирает control-plane срез translator session/policy слоя.

        Почему отдельный snapshot:
        - readiness отвечает на вопрос "насколько фундамент жив";
        - control-plane отвечает на вопрос "какая сейчас policy/session truth";
        - owner UI не должен ходить напрямую в Voice Gateway и знать его HTTP-детали.
        """
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_state)
        voice_gateway = self.deps.get("voice_gateway_client")
        workflow = inbox_service.get_workflow_snapshot()
        workflow_summary = workflow.get("summary") if isinstance(workflow, dict) else {}

        gateway_caps = (
            ((readiness.get("services") or {}).get("voice_gateway_capabilities") or {})
            if isinstance(readiness.get("services"), dict)
            else {}
        )
        gateway_caps_detail = gateway_caps.get("detail") if isinstance(gateway_caps.get("detail"), dict) else {}
        session_contract = gateway_caps_detail.get("session") if isinstance(gateway_caps_detail.get("session"), dict) else {}
        translation_contract = (
            gateway_caps_detail.get("translation")
            if isinstance(gateway_caps_detail.get("translation"), dict)
            else {}
        )
        mobile_contract = gateway_caps_detail.get("mobile") if isinstance(gateway_caps_detail.get("mobile"), dict) else {}
        endpoints_contract = (
            ((gateway_caps_detail.get("api") or {}).get("endpoints") or {})
            if isinstance(gateway_caps_detail.get("api"), dict)
            else {}
        )

        sessions_payload: dict[str, Any] = {"ok": False, "error": "voice_gateway_unavailable"}
        quick_phrases_payload: dict[str, Any] = {"ok": False, "error": "voice_gateway_unavailable"}
        diagnostics_payload: dict[str, Any] = {}
        diagnostics_why_payload: dict[str, Any] = {}
        timeline_summary_payload: dict[str, Any] = {}
        session_items_raw: list[dict[str, Any]] = []

        if voice_gateway and hasattr(voice_gateway, "list_sessions"):
            try:
                sessions_payload = await voice_gateway.list_sessions(limit=8)
            except Exception as exc:  # noqa: BLE001
                sessions_payload = {"ok": False, "error": str(exc)}

        if isinstance(sessions_payload.get("items"), list):
            session_items_raw = [
                dict(item)
                for item in sessions_payload.get("items", [])
                if isinstance(item, dict)
            ]

        def _session_updated_sort_key(item: dict[str, Any]) -> float:
            updated_at = str(item.get("updated_at") or item.get("created_at") or "").strip()
            if not updated_at:
                return 0.0
            try:
                normalized = updated_at.replace("Z", "+00:00")
                return datetime.fromisoformat(normalized).timestamp()
            except ValueError:
                return 0.0

        def _session_priority(item: dict[str, Any]) -> tuple[int, float]:
            status = str(item.get("status") or "").strip().lower()
            order = {
                "running": 0,
                "paused": 1,
                "created": 2,
                "failed": 3,
                "stopped": 4,
            }
            return (order.get(status, 9), -_session_updated_sort_key(item))

        session_items = sorted(session_items_raw, key=_session_priority)
        current_session = session_items[0] if session_items else {}
        current_session_id = str(current_session.get("id") or "").strip()
        current_source_lang = str(current_session.get("src_lang") or "").strip().lower() or "auto"
        current_target_lang = str(current_session.get("tgt_lang") or "").strip().lower() or "ru"

        if current_session_id and voice_gateway and hasattr(voice_gateway, "get_diagnostics"):
            diagnostics_tasks: list[Any] = []
            diagnostics_tasks.append(voice_gateway.get_diagnostics(current_session_id))
            diagnostics_tasks.append(
                voice_gateway.get_diagnostics_why(current_session_id)
                if hasattr(voice_gateway, "get_diagnostics_why")
                else asyncio.sleep(0, result={"ok": False, "error": "not_supported"})
            )
            diagnostics_tasks.append(
                voice_gateway.get_timeline_summary(current_session_id)
                if hasattr(voice_gateway, "get_timeline_summary")
                else asyncio.sleep(0, result={"ok": False, "error": "not_supported"})
            )
            diagnostics_payload, diagnostics_why_payload, timeline_summary_payload = await asyncio.gather(*diagnostics_tasks)

        quick_phrase_source_lang = current_source_lang if current_source_lang not in {"", "auto"} else "ru"
        quick_phrase_target_lang = current_target_lang or "es"
        if quick_phrase_source_lang == quick_phrase_target_lang:
            if quick_phrase_source_lang == "ru":
                quick_phrase_target_lang = "es"
            elif quick_phrase_source_lang == "es":
                quick_phrase_target_lang = "ru"

        if voice_gateway and hasattr(voice_gateway, "list_quick_phrases"):
            try:
                quick_phrases_payload = await voice_gateway.list_quick_phrases(
                    source_lang=quick_phrase_source_lang,
                    target_lang=quick_phrase_target_lang,
                    limit=6,
                )
            except Exception as exc:  # noqa: BLE001
                quick_phrases_payload = {"ok": False, "error": str(exc)}

        current_meta = current_session.get("meta") if isinstance(current_session.get("meta"), dict) else {}
        current_diag = diagnostics_payload.get("result") if isinstance(diagnostics_payload.get("result"), dict) else {}
        current_why = diagnostics_why_payload.get("result") if isinstance(diagnostics_why_payload.get("result"), dict) else {}
        current_timeline_summary = (
            timeline_summary_payload.get("result")
            if isinstance(timeline_summary_payload.get("result"), dict)
            else {}
        )

        active_count = sum(1 for item in session_items if str(item.get("status") or "").strip().lower() in {"running", "paused", "created"})
        current_translation_mode = str(current_session.get("translation_mode") or "").strip()
        current_status = str(current_session.get("status") or "").strip().lower()
        current_source = str(current_session.get("source") or "").strip()
        device_bound = bool(current_meta.get("device_bound"))
        if device_bound:
            device_binding_status = "bound"
        elif current_source == "mobile":
            device_binding_status = "pending"
        elif current_session_id:
            device_binding_status = "not_bound"
        else:
            device_binding_status = "not_reported"

        runtime_policy_status = "from_active_session" if current_session_id else (
            "gateway_unavailable" if not bool(gateway_caps.get("ok")) else "not_reported"
        )
        runtime_policy = {
            "status": runtime_policy_status,
            "translation_mode": current_translation_mode,
            "notify_mode": str(current_session.get("notify_mode") or "").strip(),
            "tts_mode": str(current_session.get("tts_mode") or "").strip(),
            "source": current_source,
            "src_lang": current_source_lang,
            "tgt_lang": current_target_lang,
            "language_pair": (
                f"{current_source_lang}-{current_target_lang}"
                if current_session_id
                else ""
            ),
            "bilingual_mode": current_translation_mode == "ru_es_duplex",
            "voice_strategy": "voice-first" if bool(readiness.get("live_voice_ready")) else "subtitles-first",
            "summary_available": bool(translation_contract.get("summary")),
            "quick_phrases_available": bool(translation_contract.get("quick_phrases")),
            "supported_translation_modes": list(session_contract.get("translation_modes") or []),
            "supported_session_sources": list(session_contract.get("sources") or []),
            "runtime_tuning": dict(session_contract.get("runtime_tuning") or {}),
        }

        session_rows = []
        for item in session_items[:6]:
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            session_rows.append(
                {
                    "id": str(item.get("id") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "translation_mode": str(item.get("translation_mode") or "").strip(),
                    "notify_mode": str(item.get("notify_mode") or "").strip(),
                    "tts_mode": str(item.get("tts_mode") or "").strip(),
                    "source": str(item.get("source") or "").strip(),
                    "src_lang": str(item.get("src_lang") or "").strip(),
                    "tgt_lang": str(item.get("tgt_lang") or "").strip(),
                    "device_bound": bool(meta.get("device_bound")),
                    "updated_at": str(item.get("updated_at") or item.get("created_at") or "").strip(),
                }
            )

        current_session_payload = {
            "id": current_session_id,
            "status": str(current_session.get("status") or "").strip() or "not_reported",
            "translation_mode": current_translation_mode,
            "notify_mode": str(current_session.get("notify_mode") or "").strip(),
            "tts_mode": str(current_session.get("tts_mode") or "").strip(),
            "source": current_source,
            "src_lang": current_source_lang if current_session_id else "",
            "tgt_lang": current_target_lang if current_session_id else "",
            "device_binding_status": device_binding_status,
            "device_bound": device_bound,
            "meta": current_meta,
            "diagnostics": current_diag,
            "diagnostics_why": current_why,
            "timeline_summary": current_timeline_summary,
        }

        quick_phrase_items = [
            {
                "id": str(item.get("id") or "").strip(),
                "category": str(item.get("category") or "").strip(),
                "source_text": str(item.get("source_text") or "").strip(),
                "translated_text": str(item.get("translated_text") or "").strip(),
            }
            for item in (quick_phrases_payload.get("items") or [])
            if isinstance(item, dict)
        ]
        runtime_tuning_contract = dict(session_contract.get("runtime_tuning") or {})
        runtime_diag = current_diag.get("runtime") if isinstance(current_diag.get("runtime"), dict) else {}
        supported_sources = [str(item).strip() for item in (session_contract.get("sources") or []) if str(item).strip()]
        supported_translation_modes = [
            str(item).strip() for item in (session_contract.get("translation_modes") or []) if str(item).strip()
        ]
        buffering_modes = [
            str(item).strip() for item in (runtime_tuning_contract.get("buffering_modes") or []) if str(item).strip()
        ]
        draft_defaults = {
            "source": current_source or (supported_sources[0] if supported_sources else "mic"),
            "translation_mode": current_translation_mode or (
                supported_translation_modes[0] if supported_translation_modes else "auto_to_ru"
            ),
            "notify_mode": str(current_session.get("notify_mode") or "").strip() or "auto_on",
            "tts_mode": str(current_session.get("tts_mode") or "").strip() or "hybrid",
            "src_lang": current_source_lang if current_session_id else "auto",
            "tgt_lang": current_target_lang if current_session_id else "ru",
            "buffering_mode": str(runtime_diag.get("buffering_mode") or "").strip()
            or (buffering_modes[0] if buffering_modes else "adaptive"),
            "target_latency_ms": runtime_diag.get("target_latency_ms"),
            "vad_sensitivity": runtime_diag.get("vad_sensitivity"),
            "quick_phrase_source_lang": quick_phrase_source_lang,
            "quick_phrase_target_lang": quick_phrase_target_lang,
            "quick_phrase_voice": "default",
            "quick_phrase_style": "neutral",
        }
        operator_actions = {
            "gateway_available": bool(gateway_caps.get("ok")),
            "current_session_id": current_session_id,
            "current_session_status": current_status or "not_reported",
            "start_available": bool(gateway_caps.get("ok")),
            "policy_update_available": bool(current_session_id),
            "pause_available": current_status == "running",
            "resume_available": current_status in {"paused", "created"},
            "stop_available": bool(current_session_id),
            "runtime_tune_available": bool(current_session_id and runtime_tuning_contract),
            "quick_phrase_available": bool(current_session_id and translation_contract.get("quick_phrases")),
            "supported_status_actions": [
                action
                for action, enabled in (
                    ("pause", current_status == "running"),
                    ("resume", current_status in {"paused", "created"}),
                    ("stop", bool(current_session_id)),
                )
                if enabled
            ],
            "draft_defaults": draft_defaults,
        }

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "readiness": {
                "status": str(readiness.get("readiness") or "unknown"),
                "foundation_ready": bool(readiness.get("foundation_ready")),
                "live_voice_ready": bool(readiness.get("live_voice_ready")),
            },
            "account_runtime": dict(readiness.get("account_runtime") or {}),
            "approval_state": {
                "pending_approvals": int((workflow_summary or {}).get("pending_approvals") or 0),
                "open_escalations": int((workflow_summary or {}).get("open_escalations") or 0),
                "pending_owner_tasks": int((workflow_summary or {}).get("pending_owner_tasks") or 0),
            },
            "gateway_contract": {
                "status": str(gateway_caps.get("status") or "unknown"),
                "ok": bool(gateway_caps.get("ok")),
                "service": str(gateway_caps_detail.get("service") or "").strip(),
                "contract_version": str(gateway_caps_detail.get("contract_version") or "").strip(),
                "translation_modes": list(session_contract.get("translation_modes") or []),
                "session_sources": list(session_contract.get("sources") or []),
                "timeline_supported": bool(((session_contract.get("timeline") or {}) if isinstance(session_contract.get("timeline"), dict) else {}).get("summary")),
                "why_supported": bool(((gateway_caps_detail.get("diagnostics") or {}) if isinstance(gateway_caps_detail.get("diagnostics"), dict) else {}).get("why_endpoint")),
                "device_binding_supported": bool(mobile_contract.get("session_binding")),
                "quick_phrases_supported": bool(translation_contract.get("quick_phrases")),
                "endpoints": {
                    "sessions": str(endpoints_contract.get("sessions") or "/v1/sessions"),
                    "runtime": str(endpoints_contract.get("session_runtime") or "/v1/sessions/{session_id}/runtime"),
                    "diagnostics": str(endpoints_contract.get("session_diagnostics") or "/v1/sessions/{session_id}/diagnostics"),
                    "timeline": str(endpoints_contract.get("session_timeline") or "/v1/sessions/{session_id}/timeline"),
                    "quick_phrases": str(endpoints_contract.get("quick_phrases") or "/v1/quick-phrases"),
                },
            },
            "sessions": {
                "count": len(session_items),
                "active_count": active_count,
                "current_session_id": current_session_id,
                "items": session_rows,
            },
            "current_session": current_session_payload,
            "runtime_policy": runtime_policy,
            "operator_actions": operator_actions,
            "quick_phrases": {
                "status": "ready" if bool(quick_phrases_payload.get("ok")) else "unavailable",
                "source_lang": quick_phrase_source_lang,
                "target_lang": quick_phrase_target_lang,
                "selection_reason": (
                    "active_session_pair" if current_session_id and current_source_lang in {"ru", "es"} and current_target_lang in {"ru", "es"}
                    else "gateway_library_default"
                ),
                "count": len(quick_phrase_items),
                "items": quick_phrase_items,
            },
            "links": {
                "translator_readiness_endpoint": "/api/translator/readiness",
                "translator_control_plane_endpoint": "/api/translator/control-plane",
                "capability_registry_endpoint": "/api/capabilities/registry",
                "policy_matrix_endpoint": "/api/policy/matrix",
            },
        }

    async def _translator_session_inspector_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        current_control_plane: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает расследовательский срез translator session.

        Нужен для operator-facing UI:
        - why-report и timeline не должны жить только внутри Gateway;
        - owner panel должна уметь объяснить деградацию и эскалировать её в inbox;
        - snapshot должен честно работать и при `gateway_unavailable`, и при отсутствии session.
        """
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        control_plane = current_control_plane or await self._translator_control_plane_snapshot(runtime_lite=runtime_state)
        voice_gateway = self.deps.get("voice_gateway_client")
        current_session = (
            dict(control_plane.get("current_session") or {})
            if isinstance(control_plane.get("current_session"), dict)
            else {}
        )
        current_session_id = str(current_session.get("id") or "").strip()
        current_session_status = str(current_session.get("status") or "").strip() or "not_reported"
        gateway_contract = (
            dict(control_plane.get("gateway_contract") or {})
            if isinstance(control_plane.get("gateway_contract"), dict)
            else {}
        )
        gateway_status = str(gateway_contract.get("status") or "unknown").strip() or "unknown"
        readiness = (
            dict(control_plane.get("readiness") or {})
            if isinstance(control_plane.get("readiness"), dict)
            else {}
        )
        timeline_preview_payload: dict[str, Any] = {}
        timeline_stats_payload: dict[str, Any] = {}
        timeline_export_payload: dict[str, Any] = {}

        if current_session_id and voice_gateway:
            tasks: list[Any] = []
            tasks.append(
                voice_gateway.get_timeline(current_session_id, limit=8)
                if hasattr(voice_gateway, "get_timeline")
                else asyncio.sleep(0, result={"ok": False, "error": "not_supported"})
            )
            tasks.append(
                voice_gateway.get_timeline_stats(current_session_id, limit=200)
                if hasattr(voice_gateway, "get_timeline_stats")
                else asyncio.sleep(0, result={"ok": False, "error": "not_supported"})
            )
            tasks.append(
                voice_gateway.export_timeline(current_session_id, format="md", limit=40)
                if hasattr(voice_gateway, "export_timeline")
                else asyncio.sleep(0, result={"ok": False, "error": "not_supported"})
            )
            timeline_preview_payload, timeline_stats_payload, timeline_export_payload = await asyncio.gather(*tasks)

        why_raw = (
            dict(current_session.get("diagnostics_why") or {})
            if isinstance(current_session.get("diagnostics_why"), dict)
            else {}
        )
        why_items = [str(item).strip() for item in (why_raw.get("why") or []) if str(item).strip()]
        if why_raw and not why_items:
            why_items = [str(why_raw.get("detail") or why_raw.get("status") or "").strip()] if (
                str(why_raw.get("detail") or why_raw.get("status") or "").strip()
            ) else []

        timeline_preview_result = (
            dict(timeline_preview_payload.get("result") or {})
            if isinstance(timeline_preview_payload.get("result"), dict)
            else {}
        )
        timeline_stats_result = (
            dict(timeline_stats_payload.get("result") or {})
            if isinstance(timeline_stats_payload.get("result"), dict)
            else {}
        )
        timeline_summary = (
            dict(current_session.get("timeline_summary") or {})
            if isinstance(current_session.get("timeline_summary"), dict)
            else {}
        )
        timeline_items = []
        for item in (timeline_preview_result.get("items") or []):
            if not isinstance(item, dict):
                continue
            timeline_items.append(
                {
                    "ts": str(item.get("ts") or item.get("timestamp") or "").strip(),
                    "kind": str(item.get("kind") or item.get("type") or "").strip(),
                    "text": str(item.get("text") or "").strip(),
                }
            )
        export_text = str(timeline_export_payload.get("result") or "").strip()
        export_preview = "\n".join(export_text.splitlines()[:10]).strip()
        suggested_body_parts = []
        if current_session_id:
            suggested_body_parts.append(f"Session: `{current_session_id}`")
            suggested_body_parts.append(f"Status: `{current_session_status}`")
        if why_items:
            suggested_body_parts.append("Why-report:\n" + "\n".join(f"- {item}" for item in why_items[:4]))
        summary_text = str(timeline_summary.get("summary") or "").strip()
        if summary_text:
            suggested_body_parts.append(f"Timeline summary:\n{summary_text}")
        stats_payload = timeline_stats_result.get("stats") if isinstance(timeline_stats_result.get("stats"), dict) else {}
        if stats_payload:
            stats_line = ", ".join(
                f"{key}={value}"
                for key, value in stats_payload.items()
                if isinstance(value, (int, float))
            )
            if stats_line:
                suggested_body_parts.append(f"Timeline stats: {stats_line}")
        if export_preview:
            suggested_body_parts.append(f"Timeline export preview:\n```md\n{export_preview}\n```")

        if current_session_id:
            inspector_status = "ready" if timeline_preview_result or why_items or timeline_summary else "session_active"
        elif gateway_status in {"error", "timeout", "gateway_unavailable"} or not bool(gateway_contract.get("ok")):
            inspector_status = "gateway_unavailable"
        else:
            inspector_status = "idle"

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": inspector_status,
            "session_id": current_session_id,
            "session_status": current_session_status,
            "gateway_status": gateway_status,
            "readiness_status": str(readiness.get("status") or "").strip(),
            "why_report": {
                "status": (
                    "ready"
                    if why_items
                    else ("gateway_unavailable" if inspector_status == "gateway_unavailable" else "not_reported")
                ),
                "count": len(why_items),
                "items": why_items[:6],
            },
            "timeline": {
                "status": "ready" if timeline_preview_result else (
                    "gateway_unavailable" if inspector_status == "gateway_unavailable" else "not_reported"
                ),
                "count": int(timeline_preview_result.get("count") or len(timeline_items)),
                "summary": summary_text,
                "tasks": [
                    str(item).strip()
                    for item in (timeline_summary.get("tasks") or [])
                    if str(item).strip()
                ][:6],
                "stats": dict(stats_payload) if isinstance(stats_payload, dict) else {},
                "recent_items": timeline_items[:8],
                "export_preview": export_preview,
                "export_format": "md",
            },
            "actions": {
                "rebuild_summary_available": bool(current_session_id),
                "escalate_available": bool(current_session_id),
            },
            "escalation": {
                "can_escalate": bool(current_session_id),
                "suggested_kind": "owner_task",
                "suggested_title": (
                    f"Translator session {current_session_id}: investigate degradation"
                    if current_session_id
                    else "Translator session: investigate degradation"
                ),
                "suggested_body": "\n\n".join(part for part in suggested_body_parts if part).strip(),
                "inbox_summary": inbox_service.get_summary(),
            },
            "links": {
                "control_plane_endpoint": "/api/translator/control-plane",
                "session_inspector_endpoint": "/api/translator/session-inspector",
                "inbox_status_endpoint": "/api/inbox/status",
            },
        }

    async def _translator_mobile_readiness_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        current_control_plane: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает readiness companion/iPhone device слоя переводчика.

        Нужен, чтобы owner panel видела:
        - есть ли у нас зарегистрированные companion-девайсы;
        - можно ли привязать их к текущей session;
        - что реально вернёт device resume snapshot.
        """
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        control_plane = current_control_plane or await self._translator_control_plane_snapshot(runtime_lite=runtime_state)
        voice_gateway = self.deps.get("voice_gateway_client")
        gateway_contract = (
            dict(control_plane.get("gateway_contract") or {})
            if isinstance(control_plane.get("gateway_contract"), dict)
            else {}
        )
        current_session = (
            dict(control_plane.get("current_session") or {})
            if isinstance(control_plane.get("current_session"), dict)
            else {}
        )
        current_session_id = str(current_session.get("id") or "").strip()
        current_session_status = str(current_session.get("status") or "not_reported").strip() or "not_reported"
        current_device_binding_status = str(current_session.get("device_binding_status") or "not_reported").strip()
        mobile_available = bool(gateway_contract.get("device_binding_supported"))

        devices_payload: dict[str, Any] = {"ok": False, "error": "voice_gateway_unavailable"}
        device_snapshot_payload: dict[str, Any] = {}
        devices: list[dict[str, Any]] = []
        selected_device: dict[str, Any] = {}

        if voice_gateway and hasattr(voice_gateway, "list_mobile_devices"):
            try:
                devices_payload = await voice_gateway.list_mobile_devices(limit=8)
            except Exception as exc:  # noqa: BLE001
                devices_payload = {"ok": False, "error": str(exc)}

        if isinstance(devices_payload.get("items"), list):
            devices = [dict(item) for item in devices_payload.get("items") if isinstance(item, dict)]
        devices.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

        bound_device = next(
            (
                item
                for item in devices
                if current_session_id and str(item.get("bound_session_id") or "").strip() == current_session_id
            ),
            None,
        )
        selected_device = dict(bound_device or (devices[0] if devices else {}))
        selected_device_id = str(selected_device.get("device_id") or "").strip().lower()

        if selected_device_id and voice_gateway and hasattr(voice_gateway, "get_mobile_session_snapshot"):
            try:
                device_snapshot_payload = await voice_gateway.get_mobile_session_snapshot(
                    selected_device_id,
                    session_id=current_session_id,
                    limit=8,
                )
            except Exception as exc:  # noqa: BLE001
                device_snapshot_payload = {"ok": False, "error": str(exc)}

        push_enabled_count = sum(1 for item in devices if bool(item.get("push_enabled")))
        bound_count = sum(1 for item in devices if str(item.get("bound_session_id") or "").strip())
        device_snapshot = (
            dict(device_snapshot_payload.get("result") or {})
            if isinstance(device_snapshot_payload.get("result"), dict)
            else {}
        )
        snapshot_timeline = [dict(item) for item in (device_snapshot.get("timeline") or []) if isinstance(item, dict)]
        snapshot_why = (
            dict(device_snapshot.get("why") or {})
            if isinstance(device_snapshot.get("why"), dict)
            else {}
        )
        snapshot_why_items = [str(item).strip() for item in (snapshot_why.get("why") or []) if str(item).strip()]

        if not bool(gateway_contract.get("ok")):
            status = "gateway_unavailable"
        elif devices:
            if bound_device and current_session_id:
                status = "bound"
            elif push_enabled_count:
                status = "registered"
            else:
                status = "attention"
        else:
            status = "not_configured"

        recommended_next_step = "register_companion"
        if status == "gateway_unavailable":
            recommended_next_step = "restore_gateway"
        elif current_session_id and devices and not bound_device:
            recommended_next_step = "bind_device_to_active_session"
        elif bound_device and current_session_status in {"created", "running", "paused"}:
            recommended_next_step = "companion_ready_for_resume"

        draft_defaults = {
            "device_id": selected_device_id,
            "app_version": str(selected_device.get("app_version") or "0.1.0").strip() or "0.1.0",
            "locale": str(selected_device.get("locale") or "ru").strip() or "ru",
            "preferred_source_lang": str(selected_device.get("preferred_source_lang") or "auto").strip() or "auto",
            "preferred_target_lang": str(selected_device.get("preferred_target_lang") or "ru").strip() or "ru",
            "apns_environment": str(selected_device.get("apns_environment") or "development").strip() or "development",
            "notify_default": bool(selected_device.get("notify_default", True)),
        }

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status,
            "delivery_path": "iphone_companion",
            "gateway_status": str(gateway_contract.get("status") or "unknown").strip(),
            "summary": {
                "registered_devices": len(devices),
                "push_enabled_devices": push_enabled_count,
                "bound_devices": bound_count,
                "current_session_id": current_session_id,
                "current_session_status": current_session_status,
                "current_device_binding_status": current_device_binding_status,
            },
            "actions": {
                "register_available": bool(gateway_contract.get("ok")) and mobile_available,
                "bind_available": bool(gateway_contract.get("ok")) and mobile_available and bool(current_session_id) and bool(devices),
                "trial_prep_available": bool(gateway_contract.get("ok")) and mobile_available,
                "remove_available": bool(gateway_contract.get("ok")) and mobile_available and bool(devices),
                "session_snapshot_available": bool(device_snapshot),
                "recommended_next_step": recommended_next_step,
                "draft_defaults": draft_defaults,
            },
            "devices": {
                "status": "ready" if devices_payload.get("ok") else (
                    "gateway_unavailable" if status == "gateway_unavailable" else "unavailable"
                ),
                "count": len(devices),
                "selected_device_id": selected_device_id,
                "items": [
                    {
                        "device_id": str(item.get("device_id") or "").strip(),
                        "locale": str(item.get("locale") or "").strip(),
                        "app_version": str(item.get("app_version") or "").strip(),
                        "apns_environment": str(item.get("apns_environment") or "").strip(),
                        "push_enabled": bool(item.get("push_enabled")),
                        "notify_default": bool(item.get("notify_default", True)),
                        "bound_session_id": str(item.get("bound_session_id") or "").strip(),
                        "updated_at": str(item.get("updated_at") or "").strip(),
                        "preferred_source_lang": str(item.get("preferred_source_lang") or "").strip(),
                        "preferred_target_lang": str(item.get("preferred_target_lang") or "").strip(),
                        "voip_push_token_masked": str(item.get("voip_push_token_masked") or "").strip(),
                    }
                    for item in devices[:6]
                ],
            },
            "selected_device_snapshot": {
                "status": "ready" if device_snapshot else (
                    "gateway_unavailable" if status == "gateway_unavailable" else "not_reported"
                ),
                "device_id": selected_device_id,
                "active_session": bool(device_snapshot.get("active_session")),
                "timeline_count": int(device_snapshot.get("timeline_count") or len(snapshot_timeline)),
                "why_items": snapshot_why_items[:4],
                "timeline_preview": [
                    {
                        "kind": str(item.get("kind") or item.get("type") or "").strip(),
                        "text": str(item.get("text") or "").strip(),
                    }
                    for item in snapshot_timeline[:4]
                ],
            },
            "notes": [
                "iPhone companion остаётся основным delivery path ordinary-call translator v1.",
                "Companion path трактуем как call-assist architecture, а не как свободный захват системного PSTN аудио.",
            ],
            "links": {
                "mobile_readiness_endpoint": "/api/translator/mobile-readiness",
                "control_plane_endpoint": "/api/translator/control-plane",
                "session_inspector_endpoint": "/api/translator/session-inspector",
            },
        }

    async def _translator_delivery_matrix_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        current_readiness: dict[str, Any] | None = None,
        current_control_plane: dict[str, Any] | None = None,
        current_mobile_readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает product truth по delivery/scenario tracks переводчика.

        Этот слой нужен owner panel, чтобы она отвечала не только "что живо",
        но и "какой именно call-track готов, чем он ограничен и что делать дальше".
        """
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = current_readiness or await self._translator_readiness_snapshot(runtime_lite=runtime_state)
        control_plane = current_control_plane or await self._translator_control_plane_snapshot(runtime_lite=runtime_state)
        mobile_readiness = current_mobile_readiness or await self._translator_mobile_readiness_snapshot(
            runtime_lite=runtime_state,
            current_control_plane=control_plane,
        )

        services = dict(readiness.get("services") or {}) if isinstance(readiness.get("services"), dict) else {}
        account_runtime = (
            dict(readiness.get("account_runtime") or {})
            if isinstance(readiness.get("account_runtime"), dict)
            else {}
        )
        current_session = (
            dict(control_plane.get("current_session") or {})
            if isinstance(control_plane.get("current_session"), dict)
            else {}
        )
        gateway_contract = (
            dict(control_plane.get("gateway_contract") or {})
            if isinstance(control_plane.get("gateway_contract"), dict)
            else {}
        )
        mobile_summary = (
            dict(mobile_readiness.get("summary") or {})
            if isinstance(mobile_readiness.get("summary"), dict)
            else {}
        )
        mobile_actions = (
            dict(mobile_readiness.get("actions") or {})
            if isinstance(mobile_readiness.get("actions"), dict)
            else {}
        )
        mobile_devices = (
            dict(mobile_readiness.get("devices") or {})
            if isinstance(mobile_readiness.get("devices"), dict)
            else {}
        )

        gateway_status = str(
            gateway_contract.get("status")
            or (services.get("voice_gateway") or {}).get("status")
            or "unknown"
        ).strip() or "unknown"
        mobile_status = str(mobile_readiness.get("status") or "unknown").strip() or "unknown"
        session_id = str(current_session.get("id") or "").strip()
        session_status = str(current_session.get("status") or "not_reported").strip() or "not_reported"
        selected_device_id = str(mobile_devices.get("selected_device_id") or "").strip()

        ordinary_blockers: list[str] = []
        ordinary_next_steps: list[str] = []

        if gateway_status in {"error", "down", "gateway_unavailable"} or not bool(gateway_contract.get("ok")):
            ordinary_blockers.append("Krab Voice Gateway сейчас недоступен, поэтому ordinary-call track не может перейти в live trial.")
        if not bool(account_runtime.get("userbot_authorized")):
            ordinary_blockers.append("Telegram userbot этой учётки ещё не авторизован, поэтому owner-runtime truth неполный.")
        if not bool(account_runtime.get("shared_workspace_attached")):
            ordinary_blockers.append("Shared workspace не прикреплён; restart-proof state для translator track не подтверждён.")
        if mobile_status == "not_configured":
            ordinary_blockers.append("iPhone companion ещё не зарегистрирован в device registry.")
        elif mobile_status == "registered":
            ordinary_blockers.append("Companion зарегистрирован, но ещё не привязан к активной translator session.")
        elif mobile_status == "attention":
            ordinary_blockers.append("Companion виден частично: push/device binding truth требует донастройки.")

        if gateway_status in {"error", "down", "gateway_unavailable"} or not bool(gateway_contract.get("ok")):
            ordinary_next_steps.append("Поднять Krab Voice Gateway и повторно обновить translator card.")
        if mobile_status == "not_configured":
            ordinary_next_steps.append("Зарегистрировать iPhone companion через owner panel или gateway helper.")
        elif mobile_status == "registered":
            ordinary_next_steps.append("Создать или возобновить translator session и привязать companion к active session.")
        elif mobile_status == "bound":
            ordinary_next_steps.append("Ordinary-call track готов к controlled live trial на companion architecture.")
        if not ordinary_next_steps:
            ordinary_next_steps.append("Уточнить live gateway/mobile truth и повторить readiness refresh.")

        if ordinary_blockers:
            if mobile_status == "registered" and bool(mobile_actions.get("bind_available")):
                ordinary_status = "device_ready"
            else:
                ordinary_status = "blocked"
        elif mobile_status == "bound" and session_status in {"created", "running", "paused"}:
            ordinary_status = "trial_ready"
        elif mobile_status == "registered":
            ordinary_status = "device_ready"
        else:
            ordinary_status = "in_progress"

        internet_blockers = [
            "Internet-call adapters идут вторым слоем после ordinary-call v1 и не считаются предпосылкой для первого релиза.",
        ]
        internet_next_steps = [
            "Сначала подтвердить ordinary-call flow через iPhone companion architecture.",
            "Потом проектировать channel-specific adapters для Telegram, WhatsApp и Meet как отдельный Gateway слой.",
        ]
        if gateway_status in {"error", "down", "gateway_unavailable"} or not bool(gateway_contract.get("ok")):
            internet_blockers.append("Без живого Krab Voice Gateway нельзя подтвердить adapter contracts и realtime event flow.")
            internet_status = "blocked"
        elif ordinary_status in {"trial_ready", "device_ready"}:
            internet_status = "design_ready"
        else:
            internet_status = "planned"

        return {
            "ok": True,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": ordinary_status if ordinary_status == "trial_ready" else (
                "blocked" if ordinary_status == "blocked" or internet_status == "blocked" else "in_progress"
            ),
            "primary_delivery_path": "iphone_companion",
            "canonical_backend": "krab_voice_gateway",
            "gateway_status": gateway_status,
            "ordinary_calls": {
                "status": ordinary_status,
                "path": "iphone_companion",
                "session_source": "mobile",
                "active_session_id": session_id,
                "active_session_status": session_status,
                "mobile_status": mobile_status,
                "selected_device_id": selected_device_id,
                "ready_for_trial": ordinary_status == "trial_ready",
                "summary": {
                    "registered_devices": int(mobile_summary.get("registered_devices") or 0),
                    "bound_devices": int(mobile_summary.get("bound_devices") or 0),
                    "push_enabled_devices": int(mobile_summary.get("push_enabled_devices") or 0),
                },
                "blockers": ordinary_blockers[:4],
                "next_steps": ordinary_next_steps[:4],
            },
            "internet_calls": {
                "status": internet_status,
                "path": "voice_gateway_session_adapters",
                "phase": "after_ordinary_v1",
                "adapters": [
                    {"id": "telegram_call_adapter", "status": "planned"},
                    {"id": "whatsapp_call_adapter", "status": "planned"},
                    {"id": "meet_call_adapter", "status": "planned"},
                ],
                "blockers": internet_blockers[:4],
                "next_steps": internet_next_steps[:4],
            },
            "guardrails": [
                "Ordinary calls v1 идут через iPhone companion / call-assist architecture, а не через предположение о полном захвате системного PSTN аудио.",
                "Internet-call adapters не подменяют ordinary-call track и проектируются только после подтверждения v1 companion flow.",
                "Owner panel обязана показывать truthful blockers и не рисовать fake-ready состояние при down Gateway.",
            ],
            "evidence": [
                "/api/translator/readiness",
                "/api/translator/control-plane",
                "/api/translator/mobile-readiness",
                str(self._project_root() / "docs" / "CALL_TRANSLATOR_AUDIT_RU.md"),
            ],
            "links": {
                "translator_readiness_endpoint": "/api/translator/readiness",
                "translator_control_plane_endpoint": "/api/translator/control-plane",
                "translator_mobile_readiness_endpoint": "/api/translator/mobile-readiness",
            },
        }

    async def _translator_live_trial_preflight_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        current_readiness: dict[str, Any] | None = None,
        current_delivery_matrix: dict[str, Any] | None = None,
        current_mobile_readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает truthful preflight для controlled live trial ordinary-call translator path."""
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = current_readiness or await self._translator_readiness_snapshot(runtime_lite=runtime_state)
        mobile_readiness = current_mobile_readiness or await self._translator_mobile_readiness_snapshot(
            runtime_lite=runtime_state,
        )
        delivery_matrix = current_delivery_matrix or await self._translator_delivery_matrix_snapshot(
            runtime_lite=runtime_state,
            current_readiness=readiness,
            current_mobile_readiness=mobile_readiness,
        )
        return build_translator_live_trial_preflight(
            project_root=self._project_root(),
            runtime_lite=runtime_state,
            translator_readiness=readiness,
            delivery_matrix=delivery_matrix,
            mobile_readiness=mobile_readiness,
        )

    async def _translator_mobile_onboarding_snapshot(
        self,
        *,
        runtime_lite: dict[str, Any] | None = None,
        current_readiness: dict[str, Any] | None = None,
        current_control_plane: dict[str, Any] | None = None,
        current_mobile_readiness: dict[str, Any] | None = None,
        current_delivery_matrix: dict[str, Any] | None = None,
        current_live_trial_preflight: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает truthful onboarding packet для реального выхода на iPhone companion."""
        runtime_state = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = current_readiness or await self._translator_readiness_snapshot(runtime_lite=runtime_state)
        control_plane = current_control_plane or await self._translator_control_plane_snapshot(runtime_lite=runtime_state)
        mobile_readiness = current_mobile_readiness or await self._translator_mobile_readiness_snapshot(
            runtime_lite=runtime_state,
            current_control_plane=control_plane,
        )
        delivery_matrix = current_delivery_matrix or await self._translator_delivery_matrix_snapshot(
            runtime_lite=runtime_state,
            current_readiness=readiness,
            current_control_plane=control_plane,
            current_mobile_readiness=mobile_readiness,
        )
        live_trial_preflight = current_live_trial_preflight or await self._translator_live_trial_preflight_snapshot(
            runtime_lite=runtime_state,
            current_readiness=readiness,
            current_delivery_matrix=delivery_matrix,
            current_mobile_readiness=mobile_readiness,
        )
        return build_translator_mobile_onboarding_packet(
            project_root=self._project_root(),
            runtime_lite=runtime_state,
            translator_readiness=readiness,
            control_plane=control_plane,
            mobile_readiness=mobile_readiness,
            delivery_matrix=delivery_matrix,
            live_trial_preflight=live_trial_preflight,
        )

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

    @staticmethod
    def _overlay_tier_state_on_last_runtime_route(
        last_runtime_route: dict[str, Any],
        tier_state: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Подтягивает truthful `active_tier` в lightweight route snapshot.

        Это нужно, чтобы `health_lite` и `runtime_handoff` не показывали stale
        `free`, если truthful probe уже синхронизировал active tier в runtime state.
        """
        route = dict(last_runtime_route or {})
        tier_payload = dict(tier_state or {})
        channel = str(route.get("channel") or "").strip().lower()
        provider = str(route.get("provider") or "").strip().lower()
        active_tier = str(tier_payload.get("active_tier") or "").strip().lower()

        if channel != "openclaw_cloud":
            return route
        if provider not in {"google", "google-gemini-cli"}:
            return route
        if active_tier not in {"free", "paid"}:
            return route

        route["active_tier"] = active_tier
        return route

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
        last_runtime_route = self._overlay_tier_state_on_last_runtime_route(
            last_runtime_route,
            tier_state,
        )
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
        workspace_state = build_workspace_state_snapshot()
        operator_profile = self._runtime_operator_profile()

        return {
            "runtime_mode": str(operator_profile.get("runtime_mode") or current_runtime_mode()),
            "operator_id": str(operator_profile.get("operator_id") or ""),
            "account_id": str(operator_profile.get("account_id") or ""),
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
            "workspace_state": workspace_state,
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
            # Снимаем `status` и `tabs` параллельно, чтобы readiness не копил
            # лишние последовательные таймауты на каждом settle-цикле.
            (status_payload, status_error), (tabs_payload, tabs_error) = await asyncio.gather(
                self._run_openclaw_cli_json(
                    ["browser", "--json", "status"],
                    timeout_sec=8.0,
                ),
                self._run_openclaw_cli_json(
                    ["browser", "--json", "tabs"],
                    timeout_sec=8.0,
                ),
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

    async def _collect_openclaw_photo_smoke_payload(self) -> dict[str, Any]:
        """
        Собирает payload photo-smoke в одном месте для reuse в нескольких endpoint'ах.

        Почему helper нужен отдельно:
        - owner panel теперь использует этот же smoke-контур из агрегирующего
          `/api/diagnostics/smoke`;
        - дублировать одну и ту же logic-ветку в двух endpoint'ах опасно:
          UI снова может уехать в 404 или начать врать разными payload'ами.
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

    @staticmethod
    def _infer_browser_runtime_contour(browser_status: dict[str, Any]) -> dict[str, Any]:
        """
        Нормализует live browser runtime в owner/debug-контур.

        Почему это важно:
        - `running=true` и `tabs>0` сами по себе не говорят, attach-нут ли мы
          к обычному Chrome владельца или крутим отдельный debug profile;
        - handoff требует правдивого разделения attach к обычному Chrome владельца
          и отдельного `Debug browser`, чтобы UI не выдавал dedicated relay за
          owner attach.
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
            active_contour_label = "Обычный Chrome владельца"
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
        smoke_detail = str(smoke.get("detail") or "")
        scope_limited = "missing scope: operator.read" in smoke_detail.lower()
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
            stage_label = "Активен Debug browser"
            summary = (
                "Сейчас активен отдельный OpenClaw Debug browser. "
                "Это не обычный Chrome владельца и не его профиль/расширения."
            )
            warnings.append("Сейчас активен dedicated debug browser, а не обычный Chrome владельца.")
            next_step = (
                "Если нужен attach к обычному Chrome владельца, включи его отдельно "
                "с Remote Debugging. Эта кнопка owner UI открывает только Debug Chrome."
            )
        elif attached_by_runtime:
            state = "attached"
            readiness = "ready"
            if owner_attach_confirmed:
                stage_label = "Обычный Chrome владельца подключён"
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
                next_step = (
                    "Авторизуй отдельный debug browser. Для обычного Chrome владельца "
                    "используй отдельный attach-path с Remote Debugging."
                )
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
        elif relay_reachable and browser_http_state == "authorized" and scope_limited and tabs_count == 0:
            state = "relay_scope_limited"
            readiness = "attention"
            if active_contour == "debug_browser":
                stage_label = "Debug browser c ограниченным probe"
                warnings.append("Relay уже авторизован, но gateway probe ограничен scope `operator.read`.")
                next_step = (
                    "Если нужен точный staged status вкладок, выдай scope `operator.read` "
                    "или подключи обычный Chrome владельца отдельным attach-path."
                )
            else:
                stage_label = "Relay авторизован, но probe ограничен"
                warnings.append("HTTP relay авторизован, но CLI probe ограничен scope `operator.read`.")
                next_step = "Выдай gateway scope `operator.read` или проверь отдельный attach к обычному Chrome владельца."
        elif relay_reachable and tabs_count == 0:
            state = "tab_not_connected"
            readiness = "attention"
            if active_contour == "debug_browser":
                stage_label = "Debug browser без вкладки"
                warnings.append("Dedicated debug browser жив, но обычный Chrome владельца ещё не attach-нут.")
                next_step = (
                    "Открой вкладку в отдельном debug browser или подними отдельный attach "
                    "к обычному Chrome владельца, если нужен его профиль."
                )
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

    async def _probe_owner_chrome_devtools(self, url: str = "https://example.com") -> dict[str, Any]:
        """
        Проверяет ordinary Chrome path через локальный BrowserBridge/CDP.

        Почему это отдельный probe:
        - owner path не должен вечно жить в `manual_setup_required`, если CDP уже
          реально поднят и умеет делать действие;
        - для пользователя важно различать "helper только открыт" и
          "обычный Chrome уже usable для DevTools/MCP сценариев".
        """
        from ..integrations.browser_bridge import browser_bridge as _browser_bridge

        try:
            attached = await _browser_bridge.is_attached()
        except Exception as exc:
            return {
                "readiness": "blocked",
                "state": "bridge_error",
                "detail": f"Chrome DevTools bridge недоступен: {exc}",
                "next_step": "Проверь обычный Chrome, Remote Debugging на порту 9222 и повтори probe.",
                "attached": False,
                "confirmed": False,
                "tab_count": 0,
                "action_probe": {
                    "ok": False,
                    "state": "bridge_error",
                    "detail": str(exc),
                },
            }

        tabs = await _browser_bridge.list_tabs() if attached else []
        tab_count = len(tabs)
        if not attached:
            helper_log = self._inspect_owner_chrome_remote_debugging_log()
            if str(helper_log.get("status") or "") == "chrome_policy_blocked":
                return {
                    "readiness": "blocked",
                    "state": "chrome_policy_blocked",
                    "detail": str(helper_log.get("detail") or "Chrome policy blocks default-profile remote debugging."),
                    "next_step": (
                        "Для Chrome 146+ ordinary attach к default profile недоступен. "
                        "Используй OpenClaw Debug browser или отдельный non-default Chrome data dir."
                    ),
                    "attached": False,
                    "confirmed": False,
                    "tab_count": 0,
                    "log_path": str(helper_log.get("path") or ""),
                    "action_probe": {
                        "ok": False,
                        "state": "chrome_policy_blocked",
                        "detail": str(helper_log.get("detail") or ""),
                    },
                }
            return {
                "readiness": "attention",
                "state": "manual_setup_required",
                "detail": "Обычный Chrome ещё не attach-нут по CDP на порту 9222.",
                "next_step": "Запусти helper для обычного Chrome, дождись relaunch и затем обнови Browser / MCP Readiness.",
                "attached": False,
                "confirmed": False,
                "tab_count": 0,
                "action_probe": {
                    "ok": False,
                    "state": "not_attached",
                    "detail": "browser_bridge_not_attached",
                },
            }

        action_probe = await _browser_bridge.action_probe(url)
        if bool(action_probe.get("ok")):
            final_url = str(action_probe.get("final_url") or url)
            title = str(action_probe.get("title") or "").strip()
            detail = f"Chrome DevTools action probe выполнен: {final_url}"
            if title:
                detail += f" ({title})"
            return {
                "readiness": "ready",
                "state": "action_probe_ok",
                "detail": detail,
                "next_step": "Обычный Chrome владельца готов для DevTools/MCP сценариев.",
                "attached": True,
                "confirmed": True,
                "tab_count": tab_count,
                "action_probe": action_probe,
            }

        return {
            "readiness": "attention",
            "state": str(action_probe.get("state") or "action_probe_failed"),
            "detail": str(action_probe.get("detail") or "Chrome attach есть, но action probe не завершился."),
            "next_step": "Повтори helper для обычного Chrome и затем обнови Browser / MCP Readiness.",
            "attached": True,
            "confirmed": False,
            "tab_count": tab_count,
            "action_probe": action_probe,
        }

    @classmethod
    def _build_mcp_readiness_snapshot(
        cls,
        browser: dict[str, Any],
        *,
        owner_chrome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
            attached = False
            confirmed = False

            if missing_env:
                state = "missing_env"
                readiness = "blocked" if required_for_owner_browser else "attention"
                detail = f"Отсутствуют обязательные переменные: {', '.join(missing_env)}"
            elif name == "openclaw-browser":
                state = str(browser.get("state") or "unknown")
                readiness = str(browser.get("readiness") or "attention")
                detail = str(browser.get("summary") or browser.get("next_step") or "Browser relay state unknown.")
            elif name == "chrome-profile" and owner_chrome:
                state = str(owner_chrome.get("state") or "unknown")
                readiness = str(owner_chrome.get("readiness") or "attention")
                detail = str(owner_chrome.get("detail") or "Chrome DevTools probe unavailable.")
                attached = bool(owner_chrome.get("attached"))
                confirmed = bool(owner_chrome.get("confirmed"))
                if owner_chrome.get("next_step"):
                    manual_setup = [str(owner_chrome.get("next_step"))]
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
                "attached": attached,
                "confirmed": confirmed,
            }
            if owner_chrome and name == "chrome-profile":
                item["action_probe"] = dict(owner_chrome.get("action_probe") or {})
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
            detail = "Есть блокирующие проблемы в обязательных MCP-серверах для browser-контура владельца."
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
        - что даёт более полный DevTools-путь через обычный Chrome владельца.
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
            relay_detail = "Active contour: Debug browser (отдельное окно OpenClaw). " + summary
        elif active_contour == "my_chrome":
            relay_detail = "Active contour: Обычный Chrome владельца. " + summary

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
        devtools_attached = bool(chrome_profile.get("attached"))
        devtools_confirmed = bool(chrome_profile.get("confirmed"))
        devtools_active_label = "Не подтверждён"
        if devtools_confirmed:
            devtools_active_label = "Обычный Chrome владельца"
        elif devtools_attached:
            devtools_active_label = "Attach есть, probe не завершён"

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
                "preferred_for": "Изолированный relay-контур через отдельный OpenClaw Debug browser.",
                "confirmed": relay_ready,
            },
            {
                "name": "Chrome DevTools",
                "kind": "chrome_devtools",
                "readiness": str(chrome_profile.get("readiness") or "attention"),
                "state": str(chrome_profile.get("state") or "unknown"),
                "active": devtools_attached,
                "active_label": devtools_active_label,
                "detail": str(chrome_profile.get("detail") or "Обычный Chrome профиль пока не подтверждён."),
                "next_step": chrome_next_step,
                "preferred_for": "Полный owner-контур поверх обычного Chrome профиля владельца.",
                "confirmed": devtools_confirmed,
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

    @staticmethod
    def _translator_gateway_error_detail(result: dict[str, Any], *, fallback: str) -> tuple[int, str]:
        """Нормализует ошибку Voice Gateway клиента в HTTP-код и короткий detail."""
        error = str(result.get("error") or fallback).strip() or fallback
        detail_payload = result.get("detail")
        detail = ""
        if isinstance(detail_payload, dict):
            detail = str(detail_payload.get("detail") or detail_payload.get("error") or "").strip()
        elif detail_payload not in (None, ""):
            detail = str(detail_payload).strip()
        if not detail:
            detail = error

        if error in {"session_id_required", "quick_phrase_text_required", "translator_session_required"}:
            return 400, detail
        if error.startswith("http_"):
            try:
                status_code = int(error.split("_", 1)[1])
            except (TypeError, ValueError):
                status_code = 502
            return status_code, detail
        if "connect" in error.lower() or "timed out" in error.lower() or "network" in error.lower():
            return 503, "translator_gateway_unavailable"
        return 503, detail

    async def _start_vg_subscriber(self, session_id: str, voice_gateway: VoiceGatewayControlPlane) -> None:
        """Запускает WS-подписчик на поток сессии Voice Gateway для LLM reasoning."""
        await self._stop_vg_subscriber()
        try:
            from src.integrations.voice_gateway_client import VoiceGatewayClient
            if not isinstance(voice_gateway, VoiceGatewayClient):
                return
            subscriber = VoiceGatewayEventSubscriber(
                base_url=voice_gateway.base_url,
                api_key=voice_gateway.api_key,
            )

            async def _on_stt_final(event_type: str, data: dict[str, Any]) -> None:
                """Обработчик stt.final — отправляет reasoning.context в сессию."""
                text = str(data.get("text") or "").strip()
                if not text or len(text) < 3:
                    return
                try:
                    await voice_gateway.push_event(
                        session_id,
                        event_type="reasoning.context",
                        data={
                            "text": f"STT получен: {text[:100]}",
                            "category": "stt_received",
                        },
                    )
                except Exception as exc:
                    logger.warning("reasoning push failed: %s", exc)

            subscriber.on_stt_final = _on_stt_final
            await subscriber.start(session_id)
            self._vg_subscriber = subscriber
        except Exception as exc:
            logger.warning("Не удалось запустить VG subscriber: %s", exc)

    async def _stop_vg_subscriber(self) -> None:
        """Останавливает WS-подписчик Voice Gateway."""
        if self._vg_subscriber:
            try:
                await self._vg_subscriber.stop()
            except Exception:
                pass
            self._vg_subscriber = None

    def _translator_gateway_client_or_raise(self) -> VoiceGatewayControlPlane:
        """Возвращает Voice Gateway control-plane или бросает 503 при неполном контракте."""
        client = self.deps.get("voice_gateway_client")
        if client is None:
            raise HTTPException(status_code=503, detail="translator_gateway_not_available")
        if not isinstance(client, VoiceGatewayControlPlane):
            raise HTTPException(status_code=503, detail="translator_gateway_control_plane_incomplete")
        return client

    @staticmethod
    def _translator_mobile_gateway_error_detail(result: dict[str, Any], *, fallback: str) -> tuple[int, str]:
        """Нормализует mobile/companion ошибки Voice Gateway для owner-facing API."""
        error = str(result.get("error") or fallback).strip() or fallback
        detail_payload = result.get("detail")
        detail = ""
        if isinstance(detail_payload, dict):
            detail = str(detail_payload.get("detail") or detail_payload.get("error") or "").strip()
        elif detail_payload not in (None, ""):
            detail = str(detail_payload).strip()
        if not detail:
            detail = error
        if error in {"device_id_required", "session_id_required"}:
            return 400, detail
        if error.startswith("http_"):
            try:
                return int(error.split("_", 1)[1]), detail
            except (TypeError, ValueError):
                return 502, detail
        if "connect" in error.lower() or "timed out" in error.lower() or "network" in error.lower():
            return 503, "translator_gateway_unavailable"
        return 503, detail

    async def _translator_resolve_session_context(
        self,
        *,
        requested_session_id: str = "",
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """
        Разрешает session context для write-операций translator-контура.

        Возвращает:
        - `session_id`
        - `runtime_lite`
        - `control_plane`
        """
        runtime_lite = await self._collect_runtime_lite_snapshot()
        control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
        session_id = str(requested_session_id or "").strip() or str(
            ((control_plane.get("sessions") or {}).get("current_session_id") or "")
        ).strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="translator_session_required")
        return session_id, runtime_lite, control_plane

    async def _translator_action_response(
        self,
        *,
        action: str,
        gateway_result: dict[str, Any],
        runtime_lite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Собирает единый ответ write-операций translator-контура с новым truthful snapshot."""
        runtime_payload = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_payload)
        control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_payload)
        session_inspector = await self._translator_session_inspector_snapshot(
            runtime_lite=runtime_payload,
            current_control_plane=control_plane,
        )
        mobile_readiness = await self._translator_mobile_readiness_snapshot(
            runtime_lite=runtime_payload,
            current_control_plane=control_plane,
        )
        delivery_matrix = await self._translator_delivery_matrix_snapshot(
            runtime_lite=runtime_payload,
            current_readiness=readiness,
            current_control_plane=control_plane,
            current_mobile_readiness=mobile_readiness,
        )
        live_trial_preflight = await self._translator_live_trial_preflight_snapshot(
            runtime_lite=runtime_payload,
            current_readiness=readiness,
            current_delivery_matrix=delivery_matrix,
            current_mobile_readiness=mobile_readiness,
        )
        return {
            "ok": True,
            "action": action,
            "session_id": str(gateway_result.get("session_id") or "").strip(),
            "gateway_result": gateway_result.get("result") if isinstance(gateway_result.get("result"), dict) else {},
            "readiness": readiness,
            "control_plane": control_plane,
            "session_inspector": session_inspector,
            "mobile_readiness": mobile_readiness,
            "delivery_matrix": delivery_matrix,
            "live_trial_preflight": live_trial_preflight,
        }

    async def _translator_mobile_action_response(
        self,
        *,
        action: str,
        gateway_result: dict[str, Any],
        runtime_lite: dict[str, Any] | None = None,
        current_readiness: dict[str, Any] | None = None,
        current_control_plane: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Собирает единый ответ для mobile/companion write-операций.

        Почему отдельный helper:
        - mobile lifecycle теперь включает не только `register/bind`, но и orchestration
          вроде trial-prep;
        - все эти операции должны отдавать один и тот же truthful snapshot без
          дублирования кода по route-функциям.
        """
        runtime_payload = runtime_lite or await self._collect_runtime_lite_snapshot()
        readiness = current_readiness or await self._translator_readiness_snapshot(runtime_lite=runtime_payload)
        control_plane = current_control_plane or await self._translator_control_plane_snapshot(runtime_lite=runtime_payload)
        session_inspector = await self._translator_session_inspector_snapshot(
            runtime_lite=runtime_payload,
            current_control_plane=control_plane,
        )
        mobile_readiness = await self._translator_mobile_readiness_snapshot(
            runtime_lite=runtime_payload,
            current_control_plane=control_plane,
        )
        delivery_matrix = await self._translator_delivery_matrix_snapshot(
            runtime_lite=runtime_payload,
            current_readiness=readiness,
            current_control_plane=control_plane,
            current_mobile_readiness=mobile_readiness,
        )
        live_trial_preflight = await self._translator_live_trial_preflight_snapshot(
            runtime_lite=runtime_payload,
            current_readiness=readiness,
            current_delivery_matrix=delivery_matrix,
            current_mobile_readiness=mobile_readiness,
        )
        return {
            "ok": True,
            "action": action,
            "device_id": str(gateway_result.get("device_id") or "").strip(),
            "session_id": str(gateway_result.get("session_id") or "").strip(),
            "gateway_result": gateway_result.get("result") if isinstance(gateway_result.get("result"), dict) else {},
            "readiness": readiness,
            "control_plane": control_plane,
            "session_inspector": session_inspector,
            "mobile_readiness": mobile_readiness,
            "delivery_matrix": delivery_matrix,
            "live_trial_preflight": live_trial_preflight,
        }

    def _setup_routes(self):
        def _no_store_headers() -> dict[str, str]:
            """
            Отключает браузерный кеш для owner-панели.

            Это критично для инцидентного режима: после правок фронта и рестартов
            владелец должен видеть живую версию панели, а не старую HTML-копию из кеша.
            """
            return {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }

        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            # Если есть кастомный index.html — отдаём его (для обратной совместимости).
            if self._index_path.exists():
                return FileResponse(self._index_path, headers=_no_store_headers())
            # Иначе — Gemini-generated landing page с навигацией по sub-dashboards.
            from .web_app_landing_page import LANDING_PAGE_HTML
            return HTMLResponse(
                LANDING_PAGE_HTML,
                headers=_no_store_headers(),
            )

        @self.app.get("/nano_theme.css")
        @self.app.get("/prototypes/nano/nano_theme.css")
        async def nano_theme_css():
            """
            Отдает основной CSS web-панели.

            Дублируем оба URL, чтобы панель стабильно работала и при открытии
            через локальный HTTP, и при старых ссылках после обновлений.
            """
            if self._nano_theme_path.exists():
                return FileResponse(
                    self._nano_theme_path,
                    media_type="text/css",
                    headers=_no_store_headers(),
                )
            raise HTTPException(status_code=404, detail="nano_theme_css_not_found")

        @self.app.post("/api/notify")
        async def notify(
            payload: dict[str, Any] = Body(default_factory=dict),
        ):
            """Отправляет Telegram-сообщение от Краба владельцу.

            Используется внутренними сервисами (inbox watcher, hotkey) для уведомлений.
            Localhost-only, без auth (rate-limited через ThrottleInterval LaunchAgent).
            """
            text = str(payload.get("text") or "").strip()
            if not text:
                raise HTTPException(status_code=400, detail="text_required")
            chat_id = str(payload.get("chat_id") or "").strip() or os.getenv("OPENCLAW_ALERT_TARGET", "")
            if not chat_id:
                raise HTTPException(status_code=400, detail="chat_id_required")
            userbot = self.deps.get("kraab_userbot")
            if userbot is None or not getattr(userbot, "client", None):
                raise HTTPException(status_code=503, detail="userbot_not_ready")
            try:
                await userbot.client.send_message(chat_id, text)
                return {"ok": True, "chat_id": chat_id}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            # B.7 (session 4): telegram_rate_limiter stats для /stats dashboard.
            try:
                from ..core.telegram_rate_limiter import telegram_rate_limiter as _trl
                _rate_limiter_stats = _trl.stats()
            except Exception:
                _rate_limiter_stats = None
            result = {
                "ok": True,
                "status": "up",
                "telegram_session_state": runtime.get("telegram_session_state"),
                "telegram_userbot_state": (
                    (runtime.get("telegram_userbot") or {}).get("startup_state")
                ),
                "telegram_userbot_client_connected": (
                    (runtime.get("telegram_userbot") or {}).get("client_connected")
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
            if _rate_limiter_stats is not None:
                result["telegram_rate_limiter"] = _rate_limiter_stats
            return result

        # ── Stats Dashboard (session 4+, Gemini 3.1 Pro frontend) ──────────

        @self.app.get("/api/stats/caches")
        async def get_stats_caches():
            """
            Агрегированные cache-метрики для /stats dashboard.

            Возвращает counts для chat_ban_cache, chat_capability_cache
            и voice_reply_blocked_chats. Dashboard делает один fetch сюда
            вместо трёх отдельных вызовов.
            """
            try:
                from ..core.chat_ban_cache import chat_ban_cache as _cbc
                ban_entries = _cbc.list_entries()
                ban_count = len(ban_entries)
            except Exception:
                ban_entries = []
                ban_count = 0

            try:
                from ..core.chat_capability_cache import chat_capability_cache as _ccc
                cap_entries = _ccc.list_entries()
                cap_count = len(cap_entries)
                voice_disallowed = sum(
                    1 for e in cap_entries if e.get("voice_allowed") is False
                )
                slow_mode = sum(
                    1 for e in cap_entries
                    if isinstance(e.get("slow_mode_seconds"), (int, float))
                    and e["slow_mode_seconds"] > 0
                )
            except Exception:
                cap_count = 0
                voice_disallowed = 0
                slow_mode = 0

            try:
                userbot = self.deps.get("kraab_userbot")
                blocked = (
                    userbot.get_voice_blocked_chats() if userbot else []
                )
                voice_blocked_count = len(blocked)
            except Exception:
                voice_blocked_count = 0

            return {
                "ban_cache_count": ban_count,
                "capability_cache_count": cap_count,
                "voice_blocked_count": voice_blocked_count,
                "capability_voice_disallowed": voice_disallowed,
                "capability_slow_mode": slow_mode,
            }

        @self.app.get("/stats", response_class=HTMLResponse)
        async def stats_dashboard():
            """Runtime stats dashboard (Gemini 3.1 Pro frontend)."""
            from .web_app_stats_dashboard import STATS_DASHBOARD_HTML
            return HTMLResponse(
                STATS_DASHBOARD_HTML,
                headers=_no_store_headers(),
            )

        @self.app.get("/inbox", response_class=HTMLResponse)
        async def inbox_dashboard():
            """Inbox items dashboard с фильтрами и карточками (Gemini 3.1 Pro)."""
            from .web_app_inbox_dashboard import INBOX_DASHBOARD_HTML
            return HTMLResponse(
                INBOX_DASHBOARD_HTML,
                headers=_no_store_headers(),
            )

        @self.app.get("/costs", response_class=HTMLResponse)
        async def costs_dashboard():
            """Cost analytics dashboard с бюджетом и breakdown (Gemini 3.1 Pro)."""
            from .web_app_costs_dashboard import COSTS_DASHBOARD_HTML
            return HTMLResponse(
                COSTS_DASHBOARD_HTML,
                headers=_no_store_headers(),
            )

        @self.app.get("/swarm", response_class=HTMLResponse)
        async def swarm_dashboard():
            """Swarm multi-agent teams visualizer (Gemini 3.1 Pro)."""
            from .web_app_swarm_dashboard import SWARM_DASHBOARD_HTML
            return HTMLResponse(
                SWARM_DASHBOARD_HTML,
                headers=_no_store_headers(),
            )

        @self.app.get("/prototypes/{page}", response_class=HTMLResponse)
        async def prototype_page(page: str):
            """Доступ к Gemini-generated prototype pages."""
            safe_page = page.replace("..", "").replace("/", "")
            proto = config.BASE_DIR / "src" / "web" / "prototypes" / f"{safe_page}.html"
            if proto.exists():
                return FileResponse(proto, headers=_no_store_headers())
            # Попробуем с _v1 суффиксом
            proto_v1 = config.BASE_DIR / "src" / "web" / "prototypes" / f"{safe_page}_v1.html"
            if proto_v1.exists():
                return FileResponse(proto_v1, headers=_no_store_headers())
            return HTMLResponse(f"<h1>Prototype '{page}' not found</h1>", status_code=404)

        @self.app.get("/translator", response_class=HTMLResponse)
        async def translator_dashboard():
            """Translator status page (Gemini-generated prototype)."""
            proto = config.BASE_DIR / "src" / "web" / "prototypes" / "translator_v1.html"
            if proto.exists():
                return FileResponse(proto, headers=_no_store_headers())
            return HTMLResponse("<h1>Translator page not found</h1>", headers=_no_store_headers())

        # ── Costs + Swarm API endpoints (backend для Gemini dashboards) ────

        @self.app.get("/api/costs/report")
        async def get_costs_report():
            """Отчёт по расходам для /costs dashboard (Gemini field names)."""
            try:
                from ..core.cost_analytics import cost_analytics as _ca
                raw = _ca.build_usage_report_dict()
                # Адаптируем поля под Gemini JS dashboard naming convention.
                total_cost = float(raw.get("cost_session_usd") or 0)
                budget = float(raw.get("monthly_budget_usd") or 0) or 50.0
                total_calls = sum(
                    m.get("calls", 0) for m in (raw.get("by_model") or {}).values()
                )
                report = {
                    "total_cost_usd": total_cost,
                    "total_calls": total_calls,
                    "budget_monthly_usd": budget,
                    "budget_remaining_usd": budget - total_cost,
                    "budget_used_pct": round(total_cost / budget * 100, 2) if budget else 0,
                    "by_model": raw.get("by_model", {}),
                    "period_start": "2026-04-01T00:00:00Z",
                    "period_end": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                    "input_tokens": raw.get("input_tokens", 0),
                    "output_tokens": raw.get("output_tokens", 0),
                }
                return {"ok": True, "report": report}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.get("/api/swarm/status")
        async def get_swarm_status():
            """Статус мультиагентного свёрма для /swarm dashboard."""
            try:
                from ..core.swarm_channels import swarm_channels as _sc
                from ..core.swarm_scheduler import swarm_scheduler as _ss
                from ..core.swarm_memory import swarm_memory as _sm

                teams_data = {}
                for team_name in ["traders", "coders", "analysts", "creative"]:
                    is_active = _sc.is_round_active(team_name) if hasattr(_sc, "is_round_active") else False
                    teams_data[team_name] = {
                        "active": bool(is_active),
                        "rounds_total": 0,
                    }

                memory_count = 0
                try:
                    for team_name in teams_data:
                        entries = _sm.recall(team_name) if hasattr(_sm, "recall") else []
                        memory_count += len(entries) if entries else 0
                except Exception:
                    pass

                scheduler_jobs = 0
                try:
                    if hasattr(_ss, "list_jobs"):
                        scheduler_jobs = len(_ss.list_jobs() or [])
                except Exception:
                    pass

                return {
                    "ok": True,
                    "teams": teams_data,
                    "memory_entries": memory_count,
                    "scheduler_jobs": scheduler_jobs,
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.get("/api/swarm/memory")
        async def get_swarm_memory(team: str = "traders", limit: int = 5):
            """Последние записи памяти свёрма для конкретной команды."""
            try:
                from ..core.swarm_memory import swarm_memory as _sm
                entries = _sm.recall(team, limit=limit) if hasattr(_sm, "recall") else []
                return {
                    "ok": True,
                    "entries": [
                        {
                            "topic": str(e.get("topic", "")),
                            "summary": str(e.get("summary", e.get("content", "")))[:300],
                            "timestamp": str(e.get("timestamp", "")),
                        }
                        for e in (entries or [])[:limit]
                    ] if entries else [],
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        # ── Browser Bridge API ──────────────────────────────────────────────
        from ..integrations.browser_bridge import browser_bridge as _browser_bridge

        browser_bridge_timeout_sec = 8.0

        @self.app.get("/api/browser/status")
        async def browser_status():
            try:
                attached = await asyncio.wait_for(_browser_bridge.is_attached(), timeout=browser_bridge_timeout_sec)
                tabs = await asyncio.wait_for(_browser_bridge.list_tabs(), timeout=browser_bridge_timeout_sec) if attached else []
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc), "attached": False, "tab_count": 0, "active_url": None}
            active_url = tabs[-1]["url"] if tabs else None
            return {"ok": True, "attached": attached, "tab_count": len(tabs), "active_url": active_url}

        @self.app.get("/api/browser/tabs")
        async def browser_tabs():
            try:
                tabs = await asyncio.wait_for(_browser_bridge.list_tabs(), timeout=browser_bridge_timeout_sec)
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc), "tabs": []}
            return tabs

        @self.app.post("/api/browser/navigate")
        async def browser_navigate(body: dict = Body(...)):
            url = str(body.get("url") or "").strip()
            if not url:
                raise HTTPException(status_code=400, detail="url required")
            try:
                current_url = await asyncio.wait_for(_browser_bridge.navigate(url), timeout=browser_bridge_timeout_sec)
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
            return {"ok": True, "current_url": current_url}

        @self.app.post("/api/browser/screenshot")
        async def browser_screenshot():
            try:
                data = await asyncio.wait_for(_browser_bridge.screenshot_base64(), timeout=browser_bridge_timeout_sec)
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
            if data is None:
                return {"ok": False, "error": "screenshot_failed"}
            return {"ok": True, "data": data}

        @self.app.post("/api/browser/read")
        async def browser_read():
            try:
                text = await asyncio.wait_for(_browser_bridge.get_page_text(), timeout=browser_bridge_timeout_sec)
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc), "text": ""}
            return {"ok": True, "text": text}

        @self.app.post("/api/browser/js")
        async def browser_js(body: dict = Body(...)):
            code = str(body.get("code") or "").strip()
            if not code:
                raise HTTPException(status_code=400, detail="code required")
            try:
                result = await asyncio.wait_for(_browser_bridge.execute_js(code), timeout=browser_bridge_timeout_sec)
            except Exception as exc:
                return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
            return {"ok": True, "result": result}

        # ────────────────────────────────────────────────────────────────────

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
            kraab_userbot = self.deps.get("kraab_userbot")

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
            perceptor_ready = bool(perceptor) and hasattr(perceptor, "transcribe")
            perceptor_isolated_worker = bool(getattr(perceptor, "stt_isolated_worker", stt_isolated_worker))
            stt_worker_timeout = int(str(os.getenv("STT_WORKER_TIMEOUT_SECONDS", "240")).strip() or "240")
            voice_stack_ready = bool(voice_gateway_ok and krab_ear_ok)
            voice_profile = {}
            if kraab_userbot and hasattr(kraab_userbot, "get_voice_runtime_profile"):
                try:
                    voice_profile = dict(kraab_userbot.get_voice_runtime_profile() or {})
                except Exception:
                    voice_profile = {}
            live_voice_ready = bool(perceptor_ready and voice_stack_ready and voice_profile.get("enabled"))

            if perceptor_ready and perceptor_isolated_worker and voice_stack_ready:
                readiness = "ready"
            elif perceptor_ready:
                readiness = "degraded"
            else:
                readiness = "down"
            recommendations: list[str] = []
            if not perceptor_ready:
                recommendations.append("Perceptor/STT не подключён: voice notes не будут транскрибироваться")
                recommendations.append("Запусти ./transcriber_doctor.command --heal")
            if perceptor_ready and not perceptor_isolated_worker:
                recommendations.append("Включи STT_ISOLATED_WORKER=1 и перезапусти Krab")
            if not voice_gateway_ok:
                recommendations.append("Voice Gateway недоступен: звонки и live voice-stream будут ограничены")
            if not krab_ear_ok:
                recommendations.append("Krab Ear недоступен: wake/call-часть voice-контура деградировала")
            if voice_profile:
                if not bool(voice_profile.get("enabled")):
                    recommendations.append("Voice replies выключены: входящий voice ingress готов, но ответы голосом отключены")
                elif live_voice_ready:
                    recommendations.append("Voice replies включены: foundation для live voice готова")
            if not recommendations:
                recommendations.append("Система транскрибации в рабочем режиме")

            return {
                "ok": True,
                "status": {
                    "readiness": readiness,
                    "openclaw_ok": openclaw_ok,
                    "voice_gateway_ok": voice_gateway_ok,
                    "krab_ear_ok": krab_ear_ok,
                    "perceptor_ready": perceptor_ready,
                    "stt_isolated_worker": perceptor_isolated_worker,
                    "stt_worker_timeout_seconds": stt_worker_timeout,
                    "voice_gateway_url": os.getenv("VOICE_GATEWAY_URL", "http://127.0.0.1:8090"),
                    "whisper_model": str(getattr(perceptor, "whisper_model", "")),
                    "audio_warmup_enabled": _env_on("PERCEPTOR_AUDIO_WARMUP", "0"),
                    "voice_profile": voice_profile,
                    "live_voice_ready": live_voice_ready,
                    "recommendations": recommendations,
                },
            }

        @self.app.get("/api/voice/runtime")
        async def voice_runtime_status():
            """
            Возвращает сводку по voice-runtime userbot.

            Держим endpoint отдельно от transcriber/status, потому что owner UI и
            будущий live-voice контур должны видеть не только health входящего STT,
            но и текущий профиль доставки ответов.
            """
            kraab_userbot = self.deps.get("kraab_userbot")
            if not kraab_userbot or not hasattr(kraab_userbot, "get_voice_runtime_profile"):
                return {
                    "ok": False,
                    "error": "voice_runtime_not_available",
                }
            profile = dict(kraab_userbot.get_voice_runtime_profile() or {})
            return {
                "ok": True,
                "voice": profile,
            }

        @self.app.post("/api/voice/runtime/update")
        async def voice_runtime_update(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Обновляет voice-runtime profile userbot через owner web-key."""
            self._assert_write_access(x_krab_web_key, token)
            kraab_userbot = self.deps.get("kraab_userbot")
            if not kraab_userbot or not hasattr(kraab_userbot, "update_voice_runtime_profile"):
                raise HTTPException(status_code=503, detail="voice_runtime_not_available")
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="voice_update_body_required")
            profile = dict(
                kraab_userbot.update_voice_runtime_profile(
                    enabled=body.get("enabled") if "enabled" in body else None,
                    speed=body.get("speed") if "speed" in body else None,
                    voice=body.get("voice") if "voice" in body else None,
                    delivery=body.get("delivery") if "delivery" in body else None,
                    persist=True,
                )
                or {}
            )
            return {
                "ok": True,
                "voice": profile,
            }

        @self.app.get("/api/openclaw/cron/status")
        async def openclaw_cron_status():
            """Возвращает truthful snapshot scheduler и recurring jobs из OpenClaw CLI."""
            snapshot = await self._collect_openclaw_cron_snapshot(include_all=True)
            if not snapshot.get("ok"):
                return snapshot
            return snapshot

        @self.app.get("/api/openclaw/cron/jobs")
        async def openclaw_cron_jobs(include_all: bool = Query(default=True)):
            """Возвращает recurring jobs для owner UI без дублирования cron-движка."""
            snapshot = await self._collect_openclaw_cron_snapshot(include_all=bool(include_all))
            if not snapshot.get("ok"):
                return snapshot
            return {
                "ok": True,
                "summary": snapshot.get("summary") or {},
                "jobs": snapshot.get("jobs") or [],
            }

        @self.app.get("/api/inbox/status")
        async def inbox_status():
            """Возвращает persisted summary owner-visible inbox/escalation слоя."""
            workflow = inbox_service.get_workflow_snapshot()
            return {
                "ok": True,
                "summary": workflow.get("summary") or {},
                "workflow": workflow,
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
            note = str(payload.get("note") or "").strip()
            actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
            if not item_id:
                raise HTTPException(status_code=400, detail="inbox_empty_item_id")
            try:
                if status in {"approved", "rejected"}:
                    result = inbox_service.resolve_approval(
                        item_id,
                        approved=(status == "approved"),
                        actor=actor,
                        note=note,
                    )
                else:
                    result = inbox_service.set_item_status(
                        item_id,
                        status=status,
                        actor=actor,
                        note=note,
                    )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not result.get("ok"):
                error = str(result.get("error") or "inbox_item_not_found")
                if error == "inbox_item_not_approval":
                    raise HTTPException(status_code=400, detail=error)
                raise HTTPException(status_code=404, detail=error)
            return {
                "ok": True,
                "result": result,
            }

        @self.app.get("/api/inbox/stale-processing")
        async def inbox_stale_processing(
            kind: str = Query(default="owner_request"),
            limit: int = Query(default=20),
        ):
            """Возвращает stale `acked` item-ы для owner remediation runbook."""
            items = inbox_service.list_stale_processing_items(kind=kind, limit=limit)
            return {
                "ok": True,
                "kind": str(kind or "").strip().lower(),
                "count": len(items),
                "items": items,
            }

        @self.app.get("/api/inbox/stale-open")
        async def inbox_stale_open(
            kind: str = Query(default="owner_request"),
            limit: int = Query(default=20),
        ):
            """Возвращает старые `open` item-ы для owner remediation runbook."""
            items = inbox_service.list_stale_open_items(kind=kind, limit=limit)
            return {
                "ok": True,
                "kind": str(kind or "").strip().lower(),
                "count": len(items),
                "items": items,
            }

        @self.app.post("/api/inbox/stale-processing/remediate")
        async def inbox_stale_processing_remediate(
            payload: dict[str, Any] = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Выполняет безопасный bulk-action только по реально stale `acked` item-ам.

            Endpoint намеренно ограничен финальными статусами `done/cancelled`,
            чтобы owner UI не мог случайно массово прогнать небезопасные
            approval- или произвольные status-переходы.
            """
            self._assert_write_access(x_krab_web_key, token)
            kind = str(payload.get("kind") or "owner_request").strip().lower() or "owner_request"
            final_status = str(payload.get("status") or "cancelled").strip().lower() or "cancelled"
            note = str(payload.get("note") or "").strip()
            actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
            limit = max(1, min(int(payload.get("limit") or 20), 50))
            if final_status not in {"done", "cancelled"}:
                raise HTTPException(status_code=400, detail="inbox_invalid_bulk_stale_status")

            stale_items = inbox_service.list_stale_processing_items(kind=kind, limit=limit)
            result = inbox_service.bulk_update_status(
                item_ids=[str(item.get("item_id") or "").strip() for item in stale_items],
                status=final_status,
                actor=actor,
                note=note or f"bulk_stale_processing_{final_status}",
            )
            if not result.get("ok"):
                error = str(result.get("error") or "inbox_bulk_stale_remediation_failed")
                raise HTTPException(status_code=400, detail=error)
            workflow = inbox_service.get_workflow_snapshot()
            return {
                "ok": True,
                "kind": kind,
                "status": final_status,
                "count": len(stale_items),
                "items": stale_items,
                "result": result,
                "summary": workflow.get("summary") or {},
            }

        @self.app.post("/api/inbox/stale-open/remediate")
        async def inbox_stale_open_remediate(
            payload: dict[str, Any] = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Выполняет безопасный bulk-action только по реально старым `open` item-ам.

            Нужен для legacy-open owner_request/mention, которые уже нельзя
            считать fresh inbox, но которые не ушли в processing.
            """
            self._assert_write_access(x_krab_web_key, token)
            kind = str(payload.get("kind") or "owner_request").strip().lower() or "owner_request"
            final_status = str(payload.get("status") or "cancelled").strip().lower() or "cancelled"
            note = str(payload.get("note") or "").strip()
            actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
            limit = max(1, min(int(payload.get("limit") or 20), 50))
            if final_status not in {"done", "cancelled"}:
                raise HTTPException(status_code=400, detail="inbox_invalid_bulk_stale_open_status")

            stale_items = inbox_service.list_stale_open_items(kind=kind, limit=limit)
            result = inbox_service.bulk_update_status(
                item_ids=[str(item.get("item_id") or "").strip() for item in stale_items],
                status=final_status,
                actor=actor,
                note=note or f"bulk_stale_open_{final_status}",
            )
            if not result.get("ok"):
                error = str(result.get("error") or "inbox_bulk_stale_open_remediation_failed")
                raise HTTPException(status_code=400, detail=error)
            workflow = inbox_service.get_workflow_snapshot()
            return {
                "ok": True,
                "kind": kind,
                "status": final_status,
                "count": len(stale_items),
                "items": stale_items,
                "result": result,
                "summary": workflow.get("summary") or {},
            }

        @self.app.post("/api/inbox/create")
        async def inbox_create(
            payload: dict[str, Any] = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Позволяет owner UI создавать owner-task или approval-request."""
            self._assert_write_access(x_krab_web_key, token)
            kind = str(payload.get("kind") or "").strip().lower()
            title = str(payload.get("title") or "").strip()
            body = str(payload.get("body") or "").strip()
            if kind not in {"owner_task", "approval_request"}:
                raise HTTPException(status_code=400, detail="inbox_create_invalid_kind")
            if not title or not body:
                raise HTTPException(status_code=400, detail="inbox_create_title_body_required")

            severity = str(payload.get("severity") or "info").strip().lower() or "info"
            source = str(payload.get("source") or "owner-ui").strip().lower() or "owner-ui"
            channel_id = str(payload.get("channel_id") or "").strip()
            team_id = str(payload.get("team_id") or "").strip()
            source_item_id = str(payload.get("source_item_id") or "").strip()
            metadata = dict(payload.get("metadata") or {})

            try:
                if kind == "owner_task":
                    if source_item_id:
                        result = inbox_service.escalate_item_to_owner_task(
                            source_item_id=source_item_id,
                            title=title,
                            body=body,
                            task_key=str(payload.get("task_key") or "").strip(),
                            source=source,
                            severity=severity,
                            metadata=metadata,
                        )
                    else:
                        result = inbox_service.upsert_owner_task(
                            title=title,
                            body=body,
                            task_key=str(payload.get("task_key") or "").strip(),
                            source=source,
                            severity=severity,
                            channel_id=channel_id,
                            team_id=team_id,
                            trace_id=str(payload.get("trace_id") or "").strip(),
                            metadata=metadata,
                        )
                else:
                    if source_item_id:
                        result = inbox_service.escalate_item_to_approval_request(
                            source_item_id=source_item_id,
                            title=title,
                            body=body,
                            request_key=str(payload.get("request_key") or "").strip(),
                            source=source,
                            severity=str(payload.get("severity") or "warning").strip().lower() or "warning",
                            approval_scope=str(payload.get("approval_scope") or "owner").strip() or "owner",
                            requested_action=str(payload.get("requested_action") or "").strip(),
                            metadata=metadata,
                        )
                    else:
                        result = inbox_service.upsert_approval_request(
                            title=title,
                            body=body,
                            request_key=str(payload.get("request_key") or "").strip(),
                            source=source,
                            severity=str(payload.get("severity") or "warning").strip().lower() or "warning",
                            channel_id=channel_id,
                            team_id=team_id,
                            trace_id=str(payload.get("trace_id") or "").strip(),
                            approval_scope=str(payload.get("approval_scope") or "owner").strip() or "owner",
                            requested_action=str(payload.get("requested_action") or "").strip(),
                            metadata=metadata,
                        )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not result.get("ok"):
                raise HTTPException(status_code=404, detail=str(result.get("error") or "inbox_item_not_found"))

            return {
                "ok": True,
                "result": result,
            }

        @self.app.post("/api/openclaw/cron/jobs/create")
        async def openclaw_cron_job_create(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Создаёт recurring cron job через нативный `openclaw cron add`."""
            self._assert_write_access(x_krab_web_key, token)
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="cron_create_body_required")

            name = str(body.get("name") or "").strip()
            every = str(body.get("every") or "").strip()
            task_kind = str(body.get("task_kind") or "system").strip().lower()
            payload_text = str(body.get("payload_text") or "").strip()
            session_target = str(body.get("session_target") or "main").strip().lower()
            wake_mode = str(body.get("wake_mode") or "now").strip().lower()
            agent_id = str(body.get("agent_id") or "main").strip()
            thinking = str(body.get("thinking") or "").strip().lower()
            model = str(body.get("model") or "").strip()
            description = str(body.get("description") or "").strip()

            if not name:
                raise HTTPException(status_code=400, detail="cron_name_required")
            if not every:
                raise HTTPException(status_code=400, detail="cron_every_required")
            if not payload_text:
                raise HTTPException(status_code=400, detail="cron_payload_required")
            if task_kind not in {"system", "agent"}:
                raise HTTPException(status_code=400, detail="cron_task_kind_invalid")
            if session_target not in {"main", "isolated"}:
                raise HTTPException(status_code=400, detail="cron_session_target_invalid")
            if wake_mode not in {"now", "next-heartbeat"}:
                raise HTTPException(status_code=400, detail="cron_wake_mode_invalid")

            command: list[str] = [
                "cron",
                "add",
                "--json",
                "--name",
                name,
                "--every",
                every,
                "--session",
                session_target,
                "--wake",
                wake_mode,
            ]
            if description:
                command.extend(["--description", description])
            if bool(body.get("disabled")):
                command.append("--disabled")
            if bool(body.get("announce")):
                command.append("--announce")
            if task_kind == "agent":
                command.extend(["--agent", agent_id or "main", "--message", payload_text])
                if thinking:
                    command.extend(["--thinking", thinking])
                if model:
                    command.extend(["--model", model])
            else:
                command.extend(["--system-event", payload_text])

            create_result = await self._run_openclaw_cli(
                *command,
                timeout=45.0,
                expect_json=True,
            )
            if not create_result.get("ok"):
                return {
                    "ok": False,
                    "error": create_result.get("error") or "cron_create_failed",
                    "detail": create_result.get("detail") or create_result.get("raw") or "Не удалось создать recurring job",
                }

            snapshot = await self._collect_openclaw_cron_snapshot(include_all=True)
            if not snapshot.get("ok"):
                return snapshot
            return {
                "ok": True,
                "created": create_result.get("data") or {},
                "summary": snapshot.get("summary") or {},
                "jobs": snapshot.get("jobs") or [],
                "status": snapshot.get("status") or {},
            }

        @self.app.post("/api/openclaw/cron/jobs/toggle")
        async def openclaw_cron_job_toggle(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Включает или выключает recurring job через OpenClaw CLI."""
            self._assert_write_access(x_krab_web_key, token)
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="cron_toggle_body_required")
            job_id = str(body.get("id") or "").strip()
            enabled = body.get("enabled")
            if not job_id:
                raise HTTPException(status_code=400, detail="cron_id_required")
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="cron_enabled_bool_required")

            command = ["cron", "enable" if enabled else "disable", job_id]
            toggle_result = await self._run_openclaw_cli(
                *command,
                timeout=35.0,
                expect_json=False,
            )
            if not toggle_result.get("ok"):
                return {
                    "ok": False,
                    "error": toggle_result.get("error") or "cron_toggle_failed",
                    "detail": toggle_result.get("detail") or toggle_result.get("raw") or "Не удалось изменить состояние recurring job",
                }

            snapshot = await self._collect_openclaw_cron_snapshot(include_all=True)
            if not snapshot.get("ok"):
                return snapshot
            return {
                "ok": True,
                "detail": toggle_result.get("raw") or "",
                "summary": snapshot.get("summary") or {},
                "jobs": snapshot.get("jobs") or [],
                "status": snapshot.get("status") or {},
            }

        @self.app.post("/api/openclaw/cron/jobs/remove")
        async def openclaw_cron_job_remove(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Удаляет recurring job через OpenClaw CLI."""
            self._assert_write_access(x_krab_web_key, token)
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="cron_remove_body_required")
            job_id = str(body.get("id") or "").strip()
            if not job_id:
                raise HTTPException(status_code=400, detail="cron_id_required")

            remove_result = await self._run_openclaw_cli(
                "cron",
                "rm",
                "--json",
                job_id,
                timeout=35.0,
                expect_json=True,
            )
            if not remove_result.get("ok"):
                return {
                    "ok": False,
                    "error": remove_result.get("error") or "cron_remove_failed",
                    "detail": remove_result.get("detail") or remove_result.get("raw") or "Не удалось удалить recurring job",
                }

            snapshot = await self._collect_openclaw_cron_snapshot(include_all=True)
            if not snapshot.get("ok"):
                return snapshot
            return {
                "ok": True,
                "removed": remove_result.get("data") or {},
                "summary": snapshot.get("summary") or {},
                "jobs": snapshot.get("jobs") or [],
                "status": snapshot.get("status") or {},
            }

        @self.app.get("/api/policy")
        async def get_policy():
            """Возвращает runtime-политику AI (queue/guardrails/reactions)."""
            ai_runtime = self.deps.get("ai_runtime")
            runtime_lite = await self._collect_runtime_lite_snapshot()
            policy_matrix = self._policy_matrix_snapshot(runtime_lite=runtime_lite)
            if not ai_runtime:
                return {
                    "ok": False,
                    "error": "ai_runtime_not_configured",
                    "policy_matrix": policy_matrix,
                }
            return {
                "ok": True,
                "policy": ai_runtime.get_policy_snapshot(),
                "policy_matrix": policy_matrix,
            }

        @self.app.get("/api/policy/matrix")
        async def get_policy_matrix():
            """Возвращает unified policy matrix для owner/full/partial/guest."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            return {
                "ok": True,
                "policy_matrix": self._policy_matrix_snapshot(runtime_lite=runtime_lite),
            }

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

        @self.app.get("/api/runtime/operator-profile")
        async def runtime_operator_profile():
            """Возвращает machine-readable профиль текущей учётки/runtime для multi-account handoff."""
            return {
                "ok": True,
                "profile": self._runtime_operator_profile(),
            }

        @self.app.post("/api/runtime/repair-active-shared-permissions")
        async def runtime_repair_active_shared_permissions(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Нормализует group-write права в `Краб-active` через owner web-key."""
            self._assert_write_access(x_krab_web_key, token)
            active_shared_root = self._active_shared_root()
            repair_summary = normalize_shared_worktree_permissions(active_shared_root)
            permission_health = self._active_shared_permission_health_snapshot()
            return {
                "ok": bool(repair_summary.get("ok")),
                "repair": repair_summary,
                "active_shared_permission_health": permission_health,
            }

        @self.app.get("/api/capabilities/registry")
        async def capability_registry():
            """Возвращает единый capability registry поверх truthful runtime-срезов."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            return await self._capability_registry_snapshot(runtime_lite=runtime_lite)

        @self.app.get("/api/channels/capabilities")
        async def channel_capabilities():
            """Возвращает unified channel capability parity snapshot."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            policy_matrix = self._policy_matrix_snapshot(runtime_lite=runtime_lite)
            return {
                "ok": True,
                "channel_capabilities": self._channel_capabilities_snapshot(
                    runtime_lite=runtime_lite,
                    policy_matrix=policy_matrix,
                ),
            }

        @self.app.get("/api/translator/readiness")
        async def translator_readiness():
            """Возвращает truthful readiness translator-контура внутри экосистемы Краба."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            snapshot = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            snapshot["capability_registry_endpoint"] = "/api/capabilities/registry"
            snapshot["policy_matrix_endpoint"] = "/api/policy/matrix"
            return snapshot

        @self.app.get("/api/translator/status")
        async def translator_status():
            """Лёгкий status endpoint для dashboard /translator page."""
            try:
                profile = self.kraab.get_translator_runtime_profile()
                session = self.kraab.get_translator_session_state()
                return {"ok": True, "profile": profile, "session": session}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.post("/api/translator/session/toggle")
        async def translator_session_toggle(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Start/stop translator session через API."""
            self._assert_write_access(x_krab_web_key, token)
            state = self.kraab.get_translator_session_state()
            if state.get("session_status") == "active":
                new_state = self.kraab.update_translator_session_state(
                    session_status="idle", active_chats=[], last_event="session_stopped_api", persist=True,
                )
                return {"ok": True, "action": "stopped", "status": "idle"}
            profile = self.kraab.get_translator_runtime_profile()
            chat_id = str(payload.get("chat_id") or "").strip()
            active_chats = [chat_id] if chat_id else []
            new_state = self.kraab.update_translator_session_state(
                session_status="active", active_chats=active_chats,
                last_language_pair=profile.get("language_pair"),
                last_event="session_started_api", persist=True,
            )
            return {"ok": True, "action": "started", "status": "active", "active_chats": active_chats}

        @self.app.post("/api/translator/auto")
        async def translator_auto(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Switch to auto-detect mode via API."""
            self._assert_write_access(x_krab_web_key, token)
            self.kraab.update_translator_runtime_profile(language_pair="auto-detect", persist=True)
            return {"ok": True, "language_pair": "auto-detect"}

        @self.app.post("/api/translator/lang")
        async def translator_set_lang(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Сменить языковую пару через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS
            pair = str(payload.get("language_pair") or "").strip().lower()
            if pair not in ALLOWED_LANGUAGE_PAIRS:
                return {"ok": False, "error": f"invalid pair, use: {sorted(ALLOWED_LANGUAGE_PAIRS)}"}
            profile = self.kraab.update_translator_runtime_profile(language_pair=pair, persist=True)
            return {"ok": True, "language_pair": pair}

        @self.app.get("/api/translator/history")
        async def translator_history():
            """История переводов и статистика."""
            try:
                state = self.kraab.get_translator_session_state()
                stats = state.get("stats") or {}
                return {
                    "ok": True,
                    "total_translations": stats.get("total_translations", 0),
                    "total_latency_ms": stats.get("total_latency_ms", 0),
                    "avg_latency_ms": round(stats.get("total_latency_ms", 0) / max(1, stats.get("total_translations", 1))),
                    "last_pair": state.get("last_language_pair", ""),
                    "last_original": state.get("last_translated_original", ""),
                    "last_translation": state.get("last_translated_translation", ""),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.post("/api/translator/translate")
        async def translator_translate(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Прямой перевод текста через API (без voice note)."""
            self._assert_write_access(x_krab_web_key, token)
            text = str(payload.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "text required"}
            src_lang = str(payload.get("src_lang") or "").strip()
            tgt_lang = str(payload.get("tgt_lang") or "ru").strip()
            try:
                from ..core.language_detect import detect_language, resolve_translation_pair
                from ..core.translator_engine import translate_text
                from ..openclaw_client import openclaw_client as _oc
                if not src_lang:
                    src_lang = detect_language(text)
                if not src_lang:
                    return {"ok": False, "error": "language not detected"}
                profile = self.kraab.get_translator_runtime_profile()
                if not tgt_lang or tgt_lang == "auto":
                    src_lang, tgt_lang = resolve_translation_pair(src_lang, profile.get("language_pair", "es-ru"))
                result = await translate_text(text, src_lang, tgt_lang, openclaw_client=_oc)
                return {
                    "ok": True,
                    "original": result.original,
                    "translated": result.translated,
                    "src_lang": result.src_lang,
                    "tgt_lang": result.tgt_lang,
                    "latency_ms": result.latency_ms,
                    "model": result.model_id,
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.get("/api/translator/bootstrap")
        async def translator_bootstrap():
            """
            Возвращает единый bootstrap payload для first-paint translator-карточки.

            Это снижает cold-load стоимость owner panel:
            - один HTTP roundtrip вместо каскада отдельных fetch;
            - повторно используем уже собранные snapshot'ы, а не пересчитываем
              readiness/control/mobile по кругу в соседних endpoint'ах.
            """
            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            readiness["capability_registry_endpoint"] = "/api/capabilities/registry"
            readiness["policy_matrix_endpoint"] = "/api/policy/matrix"
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            session_inspector = await self._translator_session_inspector_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            delivery_matrix = await self._translator_delivery_matrix_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
            )
            live_trial_preflight = await self._translator_live_trial_preflight_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_delivery_matrix=delivery_matrix,
                current_mobile_readiness=mobile_readiness,
            )
            mobile_onboarding = await self._translator_mobile_onboarding_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
                current_delivery_matrix=delivery_matrix,
                current_live_trial_preflight=live_trial_preflight,
            )
            return {
                "ok": True,
                "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "readiness": readiness,
                "control_plane": control_plane,
                "session_inspector": session_inspector,
                "mobile_readiness": mobile_readiness,
                "delivery_matrix": delivery_matrix,
                "live_trial_preflight": live_trial_preflight,
                "mobile_onboarding": mobile_onboarding,
            }

        @self.app.get("/api/translator/control-plane")
        async def translator_control_plane():
            """Возвращает session/policy truth translator-контура через control-plane Краба."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            return await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)

        @self.app.get("/api/translator/session-inspector")
        async def translator_session_inspector():
            """Возвращает why-report, timeline digest и escalation context для translator session."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            return await self._translator_session_inspector_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )

        @self.app.get("/api/translator/mobile-readiness")
        async def translator_mobile_readiness():
            """Возвращает readiness iPhone companion/mobile device слоя переводчика."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            return await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )

        @self.app.get("/api/translator/delivery-matrix")
        async def translator_delivery_matrix():
            """Возвращает product truth по ordinary/internet call tracks переводчика."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            return await self._translator_delivery_matrix_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
            )

        @self.app.get("/api/translator/live-trial-preflight")
        async def translator_live_trial_preflight():
            """Возвращает one-shot truthful preflight для ordinary-call live trial."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            delivery_matrix = await self._translator_delivery_matrix_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
            )
            return await self._translator_live_trial_preflight_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_delivery_matrix=delivery_matrix,
                current_mobile_readiness=mobile_readiness,
            )

        @self.app.get("/api/translator/mobile/onboarding")
        async def translator_mobile_onboarding():
            """Возвращает onboarding packet для реального iPhone companion trial."""
            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            delivery_matrix = await self._translator_delivery_matrix_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
            )
            live_trial_preflight = await self._translator_live_trial_preflight_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_delivery_matrix=delivery_matrix,
                current_mobile_readiness=mobile_readiness,
            )
            return await self._translator_mobile_onboarding_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
                current_delivery_matrix=delivery_matrix,
                current_live_trial_preflight=live_trial_preflight,
            )

        @self.app.post("/api/translator/mobile/onboarding/export")
        async def translator_mobile_onboarding_export(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Собирает и пишет onboarding packet в ops artifacts одним owner-вызовом."""
            self._assert_write_access(x_krab_web_key, token)
            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            delivery_matrix = await self._translator_delivery_matrix_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
            )
            live_trial_preflight = await self._translator_live_trial_preflight_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_delivery_matrix=delivery_matrix,
                current_mobile_readiness=mobile_readiness,
            )
            onboarding = await self._translator_mobile_onboarding_snapshot(
                runtime_lite=runtime_lite,
                current_readiness=readiness,
                current_control_plane=control_plane,
                current_mobile_readiness=mobile_readiness,
                current_delivery_matrix=delivery_matrix,
                current_live_trial_preflight=live_trial_preflight,
            )
            ops_dir = self._project_root() / "artifacts" / "ops"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
            versioned_path = ops_dir / f"translator_mobile_onboarding_{stamp}.json"
            latest_path = ops_dir / "translator_mobile_onboarding_latest.json"
            self._write_json_file(versioned_path, onboarding)
            latest_written = False
            latest_error = ""
            effective_latest_path = latest_path
            try:
                self._write_json_file(latest_path, onboarding)
                latest_written = True
            except OSError as exc:
                latest_error = str(exc)
                raw_user = str(os.getenv("USER") or Path.home().name or "user").strip().lower()
                safe_user = re.sub(r"[^a-z0-9_-]+", "_", raw_user) or "user"
                fallback_latest_path = ops_dir / f"translator_mobile_onboarding_latest_{safe_user}.json"
                try:
                    self._write_json_file(fallback_latest_path, onboarding)
                    effective_latest_path = fallback_latest_path
                    latest_written = True
                except OSError as fallback_exc:
                    latest_error = f"{latest_error}; fallback_failed: {fallback_exc}"
            return {
                "ok": True,
                "action": "export_mobile_onboarding_packet",
                "artifacts": {
                    "latest_path": str(latest_path),
                    "latest_path_effective": str(effective_latest_path),
                    "latest_written": latest_written,
                    "latest_write_error": latest_error,
                    "versioned_path": str(versioned_path),
                },
                "onboarding": onboarding,
            }

        @self.app.post("/api/translator/session/start")
        async def translator_session_start(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Создаёт translator session из owner panel без прямого доступа UI к Voice Gateway."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_session_start_body_required")

            source = str(body.get("source") or "mic").strip() or "mic"
            translation_mode = str(body.get("translation_mode") or "auto_to_ru").strip() or "auto_to_ru"
            notify_mode = str(body.get("notify_mode") or "auto_on").strip() or "auto_on"
            tts_mode = str(body.get("tts_mode") or "hybrid").strip() or "hybrid"
            src_lang = str(body.get("src_lang") or "auto").strip() or "auto"
            tgt_lang = str(body.get("tgt_lang") or "ru").strip() or "ru"
            label = str(body.get("label") or "").strip()
            meta = dict(body.get("meta") or {}) if isinstance(body.get("meta"), dict) else {}
            meta["initiated_by"] = "owner_panel"
            meta["operator_id"] = current_operator_id()
            meta["account_id"] = current_account_id()
            if label:
                meta["session_label"] = label

            result = await voice_gateway.start_session(
                source=source,
                translation_mode=translation_mode,
                notify_mode=notify_mode,
                tts_mode=tts_mode,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                meta=meta,
            )
            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback="translator_session_start_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)

            # E4.2: Запускаем WS-подписчик для LLM reasoning
            new_session_id = str(result.get("session_id") or "").strip()
            if new_session_id:
                await self._start_vg_subscriber(new_session_id, voice_gateway)

            return await self._translator_action_response(action="start_session", gateway_result=result)

        @self.app.post("/api/translator/session/policy")
        async def translator_session_policy_update(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Обновляет policy текущей translator session через owner panel."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_session_policy_body_required")

            session_id, runtime_lite, _control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            patch: dict[str, Any] = {}
            for key in ("translation_mode", "notify_mode", "tts_mode", "src_lang", "tgt_lang"):
                value = body.get(key)
                if value is not None:
                    clean = str(value).strip()
                    if clean:
                        patch[key] = clean
            if not patch:
                raise HTTPException(status_code=400, detail="translator_session_policy_patch_required")

            result = await voice_gateway.patch_session(session_id, **patch)
            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback="translator_session_policy_update_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            return await self._translator_action_response(
                action="update_session_policy",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/session/action")
        async def translator_session_action(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Выполняет lifecycle-действие над translator session: pause/resume/stop."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_session_action_body_required")

            action = str(body.get("action") or "").strip().lower()
            if action not in {"pause", "resume", "stop"}:
                raise HTTPException(status_code=400, detail="translator_session_action_invalid")

            session_id, runtime_lite, _control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            if action == "stop":
                await self._stop_vg_subscriber()
                result = await voice_gateway.stop_session(session_id)
            else:
                target_status = "paused" if action == "pause" else "running"
                result = await voice_gateway.patch_session(session_id, status=target_status)

            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback=f"translator_session_{action}_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            return await self._translator_action_response(
                action=f"{action}_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/session/runtime-tune")
        async def translator_session_runtime_tune(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Обновляет runtime tuning текущей translator session."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_runtime_tune_body_required")

            session_id, runtime_lite, _control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            buffering_mode = str(body.get("buffering_mode") or "").strip() or None
            target_latency_raw = body.get("target_latency_ms")
            vad_raw = body.get("vad_sensitivity")

            target_latency_ms = None
            if target_latency_raw not in (None, ""):
                try:
                    target_latency_ms = int(target_latency_raw)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="translator_target_latency_invalid") from exc

            vad_sensitivity = None
            if vad_raw not in (None, ""):
                try:
                    vad_sensitivity = float(vad_raw)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="translator_vad_sensitivity_invalid") from exc

            if buffering_mode is None and target_latency_ms is None and vad_sensitivity is None:
                raise HTTPException(status_code=400, detail="translator_runtime_tune_patch_required")

            result = await voice_gateway.tune_runtime(
                session_id,
                buffering_mode=buffering_mode,
                target_latency_ms=target_latency_ms,
                vad_sensitivity=vad_sensitivity,
            )
            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback="translator_runtime_tune_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            return await self._translator_action_response(
                action="runtime_tune_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/session/quick-phrase")
        async def translator_session_quick_phrase(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Публикует quick-phrase в текущую translator session через owner panel."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_quick_phrase_body_required")

            session_id, runtime_lite, control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            text = str(body.get("text") or "").strip()
            if not text:
                raise HTTPException(status_code=400, detail="translator_quick_phrase_text_required")

            defaults = (
                ((control_plane.get("operator_actions") or {}).get("draft_defaults") or {})
                if isinstance(control_plane.get("operator_actions"), dict)
                else {}
            )
            source_lang = str(body.get("source_lang") or defaults.get("quick_phrase_source_lang") or "ru").strip() or "ru"
            target_lang = str(body.get("target_lang") or defaults.get("quick_phrase_target_lang") or "es").strip() or "es"
            voice = str(body.get("voice") or defaults.get("quick_phrase_voice") or "default").strip() or "default"
            style = str(body.get("style") or defaults.get("quick_phrase_style") or "neutral").strip() or "neutral"

            result = await voice_gateway.send_quick_phrase(
                session_id,
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                voice=voice,
                style=style,
            )
            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback="translator_quick_phrase_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            return await self._translator_action_response(
                action="quick_phrase_session",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/session/summary")
        async def translator_session_summary(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Принудительно пересобирает session summary через Voice Gateway."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_session_summary_body_required")
            session_id, runtime_lite, _control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            max_items_raw = body.get("max_items", 20)
            try:
                max_items = int(max_items_raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="translator_summary_max_items_invalid") from exc
            result = await voice_gateway.build_summary(session_id, max_items=max_items)
            if not result.get("ok"):
                status_code, detail = self._translator_gateway_error_detail(
                    result,
                    fallback="translator_session_summary_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            return await self._translator_action_response(
                action="build_session_summary",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/session/escalate")
        async def translator_session_escalate(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Эскалирует diagnostics текущей translator session в owner inbox."""
            self._assert_write_access(x_krab_web_key, token)
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_session_escalate_body_required")

            session_id, runtime_lite, control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            inspector = await self._translator_session_inspector_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            escalation = (
                dict(inspector.get("escalation") or {})
                if isinstance(inspector.get("escalation"), dict)
                else {}
            )
            title = str(body.get("title") or escalation.get("suggested_title") or "").strip()
            summary_body = str(body.get("body") or escalation.get("suggested_body") or "").strip()
            if not title or not summary_body:
                raise HTTPException(status_code=400, detail="translator_session_escalation_title_body_required")

            why_items = (
                [str(item).strip() for item in ((inspector.get("why_report") or {}).get("items") or []) if str(item).strip()]
                if isinstance(inspector.get("why_report"), dict)
                else []
            )
            severity = "warning" if why_items or str(inspector.get("status") or "") == "gateway_unavailable" else "info"
            task_key = str(body.get("task_key") or f"translator-session:{session_id}:diagnostics").strip()
            result = inbox_service.upsert_owner_task(
                title=title,
                body=summary_body,
                task_key=task_key,
                source="translator-ui",
                severity=severity,
                team_id="translator",
                trace_id=f"translator-session:{session_id}",
                metadata={
                    "translator_session_id": session_id,
                    "translator_session_status": str(inspector.get("session_status") or "").strip(),
                    "translator_gateway_status": str(inspector.get("gateway_status") or "").strip(),
                    "translator_why_items": why_items,
                    "translator_timeline_stats": (
                        ((inspector.get("timeline") or {}).get("stats") or {})
                        if isinstance(inspector.get("timeline"), dict)
                        else {}
                    ),
                    "source_surface": "owner_panel_translator",
                },
            )
            if not result.get("ok"):
                raise HTTPException(status_code=500, detail=str(result.get("error") or "translator_session_escalation_failed"))
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            return {
                "ok": True,
                "action": "escalate_session",
                "session_id": session_id,
                "inbox_result": result.get("item") if isinstance(result.get("item"), dict) else result,
                "readiness": readiness,
                "control_plane": control_plane,
                "session_inspector": inspector,
                "inbox_summary": inbox_service.get_summary(),
            }

        @self.app.post("/api/translator/mobile/register")
        async def translator_mobile_register(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Регистрирует/обновляет iPhone companion через owner panel."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_mobile_register_body_required")

            result = await voice_gateway.register_mobile_device(
                device_id=str(body.get("device_id") or "").strip(),
                voip_push_token=str(body.get("voip_push_token") or "").strip(),
                apns_environment=str(body.get("apns_environment") or "development").strip() or "development",
                app_version=str(body.get("app_version") or "").strip(),
                locale=str(body.get("locale") or "ru").strip() or "ru",
                preferred_source_lang=str(body.get("preferred_source_lang") or "auto").strip() or "auto",
                preferred_target_lang=str(body.get("preferred_target_lang") or "ru").strip() or "ru",
                notify_default=bool(body.get("notify_default", True)),
            )
            if not result.get("ok"):
                status_code, detail = self._translator_mobile_gateway_error_detail(
                    result,
                    fallback="translator_mobile_register_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)

            runtime_lite = await self._collect_runtime_lite_snapshot()
            return await self._translator_mobile_action_response(
                action="register_mobile_device",
                gateway_result=result,
                runtime_lite=runtime_lite,
            )

        @self.app.post("/api/translator/mobile/trial-prep")
        async def translator_mobile_trial_prep(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Подготавливает companion-trial в один controlled owner flow.

            Сценарий:
            1) берём device из body или из текущего mobile snapshot;
            2) при необходимости делаем upsert регистрации;
            3) если active session ещё нет, создаём `mobile` session;
            4) привязываем device к session;
            5) отдаём единый truthful snapshot после orchestration.

            Важно:
            - без device id endpoint не создаёт session "впустую";
            - это owner-driven orchestration, а не скрытая background automation.
            """
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_mobile_trial_prep_body_required")

            runtime_lite = await self._collect_runtime_lite_snapshot()
            readiness = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )

            requested_device_id = str(body.get("device_id") or "").strip().lower()
            selected_device_id = str(
                ((mobile_readiness.get("devices") or {}).get("selected_device_id") or "")
            ).strip().lower()
            device_id = requested_device_id or selected_device_id
            if not device_id:
                raise HTTPException(status_code=400, detail="device_id_required_for_trial_prep")

            existing_devices = (
                [dict(item) for item in ((mobile_readiness.get("devices") or {}).get("items") or []) if isinstance(item, dict)]
                if isinstance(mobile_readiness.get("devices"), dict)
                else []
            )
            known_device = next(
                (
                    item
                    for item in existing_devices
                    if str(item.get("device_id") or "").strip().lower() == device_id
                ),
                None,
            )

            performed_steps: list[str] = []
            last_gateway_result: dict[str, Any] = {
                "ok": True,
                "device_id": device_id,
            }

            if requested_device_id or known_device is None:
                register_result = await voice_gateway.register_mobile_device(
                    device_id=device_id,
                    voip_push_token=str(body.get("voip_push_token") or "").strip(),
                    apns_environment=str(body.get("apns_environment") or "development").strip() or "development",
                    app_version=str(body.get("app_version") or "").strip(),
                    locale=str(body.get("locale") or "ru").strip() or "ru",
                    preferred_source_lang=str(body.get("preferred_source_lang") or "auto").strip() or "auto",
                    preferred_target_lang=str(body.get("preferred_target_lang") or "ru").strip() or "ru",
                    notify_default=bool(body.get("notify_default", True)),
                )
                if not register_result.get("ok"):
                    status_code, detail = self._translator_mobile_gateway_error_detail(
                        register_result,
                        fallback="translator_mobile_trial_register_failed",
                    )
                    raise HTTPException(status_code=status_code, detail=detail)
                last_gateway_result = register_result
                performed_steps.append("device_registered")

            session_id = str(body.get("session_id") or "").strip() or str(
                ((control_plane.get("sessions") or {}).get("current_session_id") or "")
            ).strip()
            if not session_id:
                start_result = await voice_gateway.start_session(
                    source=str(body.get("source") or "mobile").strip() or "mobile",
                    translation_mode=str(body.get("translation_mode") or "auto_to_ru").strip() or "auto_to_ru",
                    notify_mode=str(body.get("notify_mode") or "auto_on").strip() or "auto_on",
                    tts_mode=str(body.get("tts_mode") or "hybrid").strip() or "hybrid",
                    src_lang=str(body.get("src_lang") or "auto").strip() or "auto",
                    tgt_lang=str(body.get("tgt_lang") or "ru").strip() or "ru",
                    meta={
                        "label": str(body.get("label") or "Companion Trial").strip() or "Companion Trial",
                        "prepared_via": "owner_mobile_trial_prep",
                        "device_id": device_id,
                    },
                )
                if not start_result.get("ok"):
                    status_code, detail = self._translator_gateway_error_detail(
                        start_result,
                        fallback="translator_mobile_trial_start_failed",
                    )
                    raise HTTPException(status_code=status_code, detail=detail)
                session_id = str(start_result.get("session_id") or "").strip()
                last_gateway_result = {
                    "ok": True,
                    "device_id": device_id,
                    "session_id": session_id,
                    "result": start_result.get("result") if isinstance(start_result.get("result"), dict) else {},
                }
                performed_steps.append("session_created")

            bind_result = await voice_gateway.bind_mobile_device(device_id, session_id=session_id)
            if not bind_result.get("ok"):
                status_code, detail = self._translator_mobile_gateway_error_detail(
                    bind_result,
                    fallback="translator_mobile_trial_bind_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)
            last_gateway_result = bind_result
            performed_steps.append("device_bound")

            response = await self._translator_mobile_action_response(
                action="prepare_mobile_trial",
                gateway_result=last_gateway_result,
                runtime_lite=runtime_lite,
                current_readiness=readiness,
            )
            response["performed_steps"] = performed_steps
            return response

        @self.app.post("/api/translator/mobile/bind")
        async def translator_mobile_bind(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Привязывает companion-device к активной translator session через owner panel."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_mobile_bind_body_required")

            device_id = str(body.get("device_id") or "").strip().lower()
            if not device_id:
                raise HTTPException(status_code=400, detail="device_id_required")
            session_id, runtime_lite, control_plane = await self._translator_resolve_session_context(
                requested_session_id=str(body.get("session_id") or "").strip()
            )
            result = await voice_gateway.bind_mobile_device(device_id, session_id=session_id)
            if not result.get("ok"):
                status_code, detail = self._translator_mobile_gateway_error_detail(
                    result,
                    fallback="translator_mobile_bind_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)

            refreshed_control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            return await self._translator_mobile_action_response(
                action="bind_mobile_device",
                gateway_result={
                    "ok": True,
                    "device_id": device_id,
                    "session_id": session_id,
                    "result": result.get("result") if isinstance(result.get("result"), dict) else {},
                },
                runtime_lite=runtime_lite,
                current_control_plane=refreshed_control_plane,
            )

        @self.app.post("/api/translator/mobile/remove")
        async def translator_mobile_remove(
            request: Request,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Удаляет companion-device из registry через owner panel."""
            self._assert_write_access(x_krab_web_key, token)
            voice_gateway = self._translator_gateway_client_or_raise()
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="translator_mobile_remove_body_required")

            runtime_lite = await self._collect_runtime_lite_snapshot()
            control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            mobile_readiness = await self._translator_mobile_readiness_snapshot(
                runtime_lite=runtime_lite,
                current_control_plane=control_plane,
            )
            device_id = str(body.get("device_id") or "").strip().lower() or str(
                ((mobile_readiness.get("devices") or {}).get("selected_device_id") or "")
            ).strip().lower()
            if not device_id:
                raise HTTPException(status_code=400, detail="device_id_required")

            result = await voice_gateway.delete_mobile_device(device_id)
            if not result.get("ok"):
                status_code, detail = self._translator_mobile_gateway_error_detail(
                    result,
                    fallback="translator_mobile_remove_failed",
                )
                raise HTTPException(status_code=status_code, detail=detail)

            refreshed_control_plane = await self._translator_control_plane_snapshot(runtime_lite=runtime_lite)
            return await self._translator_mobile_action_response(
                action="remove_mobile_device",
                gateway_result={
                    "ok": True,
                    "device_id": device_id,
                    "session_id": str(((refreshed_control_plane.get("sessions") or {}).get("current_session_id") or "")).strip(),
                    "result": result.get("result") if isinstance(result.get("result"), dict) else {},
                },
                runtime_lite=runtime_lite,
                current_control_plane=refreshed_control_plane,
            )

        @self.app.get("/api/runtime/handoff")
        async def runtime_handoff(probe_cloud_runtime: str = Query(default="1")):
            """
            Единый runtime-снимок для безопасной миграции в новый чат (Anti-413).

            Формат intentionally machine-readable, чтобы его можно было:
            - сохранить в артефакты;
            - приложить в новый диалог без ручной реконструкции контекста.
            """
            openclaw = self.deps.get("openclaw_client")
            voice_gateway = self.deps.get("voice_gateway_client")
            krab_ear = self.deps.get("krab_ear_client")

            runtime_lite = await self._collect_runtime_lite_snapshot()
            operator_profile = self._runtime_operator_profile()
            translator_snapshot = await self._translator_readiness_snapshot(runtime_lite=runtime_lite)
            capability_registry = await self._capability_registry_snapshot(runtime_lite=runtime_lite)
            openclaw_health = await self._safe_client_health_summary(
                openclaw,
                source="openclaw",
                timeout_sec=3.0,
            )
            voice_health = await self._safe_client_health_summary(
                voice_gateway,
                source="voice_gateway",
                timeout_sec=3.0,
            )
            krab_ear_health = await self._safe_client_health_summary(
                krab_ear,
                source="krab_ear",
                timeout_sec=3.0,
            )

            should_probe_cloud_runtime = self._bool_env(str(probe_cloud_runtime or "1"), True)

            cloud_runtime: dict[str, Any]
            if not should_probe_cloud_runtime:
                cloud_runtime = {"available": False, "skipped": True, "reason": "probe_disabled"}
            elif openclaw and hasattr(openclaw, "get_cloud_runtime_check"):
                try:
                    cloud_report = await asyncio.wait_for(openclaw.get_cloud_runtime_check(), timeout=18.0)
                    cloud_runtime = {"available": True, "report": cloud_report}
                    # После cloud-probe `openclaw_client` может обновить tier/auth truth.
                    # Переснимаем lightweight runtime, чтобы handoff не уносил stale
                    # `configured/free` сразу после restart, когда probe уже увидел real state.
                    runtime_lite = await self._collect_runtime_lite_snapshot(force_refresh=True)
                except asyncio.TimeoutError:
                    cloud_runtime = {"available": False, "error": "timeout"}
                except Exception as exc:
                    cloud_runtime = {"available": False, "error": str(exc)}
            else:
                cloud_runtime = {"available": False, "error": "not_supported"}

            latest_bundle = self._latest_path_by_glob("artifacts/handoff_*")
            latest_checkpoint = self._latest_path_by_glob("artifacts/context_checkpoints/checkpoint_*.md")
            latest_pack_dir = self._latest_path_by_glob("artifacts/context_transition/pack_*")
            latest_transfer_prompt = (
                str(latest_pack_dir / "TRANSFER_PROMPT_RU.md")
                if latest_pack_dir and (latest_pack_dir / "TRANSFER_PROMPT_RU.md").exists()
                else None
            )
            operator_workflow = inbox_service.get_workflow_snapshot()

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
                    "workspace_attached": bool(
                        ((runtime_lite.get("workspace_state") or {}) if isinstance(runtime_lite, dict) else {}).get(
                            "shared_workspace_attached"
                        )
                    ),
                    "last_runtime_route": runtime_lite.get("last_runtime_route"),
                    "inbox_summary": operator_workflow.get("summary") or runtime_lite.get("inbox_summary"),
                },
                "runtime": runtime_lite,
                "inbox_summary": operator_workflow.get("summary") or {},
                "operator_workflow": operator_workflow,
                "operator_profile": operator_profile,
                "capability_registry_summary": capability_registry.get("summary") or {},
                "policy_matrix_summary": (capability_registry.get("policy_matrix") or {}).get("summary") or {},
                "channel_capabilities_summary": (
                    (capability_registry.get("contours") or {}).get("channels", {}).get("summary") or {}
                ),
                "translator_readiness": translator_snapshot,
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
                    "master_plan_doc": str(self._project_root() / "docs" / "MASTER_PLAN_VNEXT_RU.md"),
                    "translator_audit_doc": str(self._project_root() / "docs" / "CALL_TRANSLATOR_AUDIT_RU.md"),
                    "multi_account_doc": str(self._project_root() / "docs" / "MULTI_ACCOUNT_SWITCHOVER_RU.md"),
                    "parallel_dialog_doc": str(self._project_root() / "docs" / "PARALLEL_DIALOG_PROTOCOL_RU.md"),
                },
            }

        @self.app.post("/api/krab/restart_userbot")
        async def restart_userbot(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Перезапускает только Telegram userbot без полного runtime switchover.

            Зачем нужен отдельный endpoint:
            - legacy watchdog уже умеет дёргать именно этот маршрут;
            - перезапуск userbot легче и безопаснее, чем полный restart всего Krab;
            - это закрывает split-state, когда web panel жива, но transport userbot деградировал.
            """
            self._assert_write_access(x_krab_web_key, token)
            kraab_userbot = self.deps.get("kraab_userbot")
            if not kraab_userbot or not hasattr(kraab_userbot, "start") or not hasattr(kraab_userbot, "stop"):
                return {
                    "ok": False,
                    "error": "userbot_restart_unavailable",
                    "detail": "kraab_userbot не поддерживает start/stop для restart endpoint",
                }

            before_state = {}
            if hasattr(kraab_userbot, "get_runtime_state"):
                try:
                    before_state = dict(kraab_userbot.get_runtime_state() or {})
                except Exception:
                    before_state = {}

            try:
                if hasattr(kraab_userbot, "restart"):
                    await kraab_userbot.restart(reason="web_api_restart_userbot")
                else:
                    await kraab_userbot.stop()
                    await kraab_userbot.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("runtime_restart_userbot_failed", error=str(exc))
                return {
                    "ok": False,
                    "error": "restart_failed",
                    "detail": str(exc),
                    "before": before_state,
                }

            after_state = {}
            if hasattr(kraab_userbot, "get_runtime_state"):
                try:
                    after_state = dict(kraab_userbot.get_runtime_state() or {})
                except Exception:
                    after_state = {}

            return {
                "ok": True,
                "action": "restart_userbot",
                "before": before_state,
                "after": after_state,
            }

        # Phase 2: Command API parity — все owner controls через REST, не только Telegram

        @self.app.post("/api/voice/toggle")
        async def voice_toggle(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Toggle voice mode через API."""
            self._assert_write_access(x_krab_web_key, token)
            current = bool(self.kraab.voice_mode)
            new_state = bool(payload.get("enabled", not current))
            self.kraab.voice_mode = new_state
            return {"ok": True, "voice_enabled": new_state}

        @self.app.get("/api/translator/languages")
        async def translator_languages():
            """Доступные языковые пары."""
            from ..core.translator_runtime_profile import ALLOWED_LANGUAGE_PAIRS
            profile = self.kraab.get_translator_runtime_profile()
            return {
                "ok": True,
                "current": profile.get("language_pair", "es-ru"),
                "available": sorted(ALLOWED_LANGUAGE_PAIRS),
            }

        @self.app.get("/api/swarm/teams")
        async def swarm_teams_list():
            """Список swarm команд с ролями."""
            from ..core.swarm_bus import TEAM_REGISTRY
            return {
                "ok": True,
                "teams": {
                    team: [{"name": r["name"], "title": r.get("title", ""), "emoji": r.get("emoji", "")}
                           for r in roles]
                    for team, roles in TEAM_REGISTRY.items()
                },
            }

        @self.app.get("/api/v1/health")
        async def health_v1():
            """Versioned health endpoint для внешних мониторов."""
            try:
                health = await self._collect_runtime_lite_snapshot()
                return {
                    "ok": True,
                    "version": "1",
                    "status": health.get("status", "unknown"),
                    "telegram": health.get("telegram_userbot_state", "unknown"),
                    "gateway": health.get("openclaw_auth_state", "unknown"),
                    "uptime_probe": "pass",
                }
            except Exception as exc:
                return {"ok": False, "version": "1", "error": str(exc)}

        @self.app.get("/api/runtime/summary")
        async def runtime_summary():
            """Единый summary endpoint — полное состояние Краба одним запросом."""
            from ..core.cost_analytics import cost_analytics as _ca
            from ..core.silence_mode import silence_manager
            from ..core.swarm_task_board import swarm_task_board
            from ..core.swarm_team_listener import is_listeners_enabled
            from ..openclaw_client import openclaw_client as _oc
            try:
                health = await self._collect_runtime_lite_snapshot()
            except Exception:
                health = {}
            return {
                "ok": True,
                "health": health,
                "route": _oc.get_last_runtime_route(),
                "costs": _ca.build_usage_report_dict(),
                "translator": {
                    "profile": self.kraab.get_translator_runtime_profile(),
                    "session": self.kraab.get_translator_session_state(),
                },
                "swarm": {
                    "task_board": swarm_task_board.get_board_summary(),
                    "listeners_enabled": is_listeners_enabled(),
                },
                "silence": silence_manager.status(),
                "notify_enabled": bool(getattr(config, "TOOL_NARRATION_ENABLED", True)),
            }

        @self.app.get("/api/commands")
        async def list_commands():
            """Список доступных Telegram команд."""
            return {"ok": True, "commands": [
                {"cmd": "!status", "desc": "статус системы"},
                {"cmd": "!model", "desc": "маршрутизация модели"},
                {"cmd": "!clear", "desc": "очистить историю"},
                {"cmd": "!voice", "desc": "голосовой профиль"},
                {"cmd": "!notify", "desc": "toggle tool narrations"},
                {"cmd": "!тишина", "desc": "режим тишины"},
                {"cmd": "!translator", "desc": "переводчик"},
                {"cmd": "!swarm", "desc": "multi-agent teams"},
                {"cmd": "!search", "desc": "веб-поиск"},
                {"cmd": "!inbox", "desc": "owner inbox"},
                {"cmd": "!watch", "desc": "proactive watch"},
                {"cmd": "!remember", "desc": "запомнить"},
                {"cmd": "!recall", "desc": "вспомнить"},
                {"cmd": "!help", "desc": "справка"},
            ]}

        @self.app.get("/api/model/status")
        async def model_status():
            """Текущий статус модели и маршрутизации."""
            from ..openclaw_client import openclaw_client as _oc
            from ..model_manager import model_manager as _mm
            route = _oc.get_last_runtime_route()
            return {
                "ok": True,
                "route": route,
                "provider": _mm.format_status() if hasattr(_mm, "format_status") else str(_mm),
                "active_model": str(getattr(_mm, "active_model_id", None) or route.get("model", "")),
            }

        @self.app.get("/api/endpoints")
        async def list_endpoints():
            """Список всех API endpoints."""
            routes = []
            for route in self.app.routes:
                if hasattr(route, "methods") and hasattr(route, "path"):
                    for method in route.methods:
                        if method in {"GET", "POST", "DELETE", "PUT"}:
                            routes.append({"method": method, "path": route.path})
            return {"ok": True, "count": len(routes), "endpoints": sorted(routes, key=lambda r: r["path"])}

        @self.app.get("/api/version")
        async def version_info():
            """Версия Краба и session info."""
            return {
                "ok": True,
                "version": "session5",
                "commits": 113,
                "tests": 2043,
                "api_endpoints": 184,
                "features": ["translator_mvp", "swarm_execution", "channel_parity", "finops", "hammerspoon_mcp"],
            }

        @self.app.get("/api/uptime")
        async def uptime():
            """Uptime Краба в секундах."""
            import time as _t
            boot = getattr(self, "_boot_ts", None)
            if not boot:
                self._boot_ts = _t.time()
                boot = self._boot_ts
            return {"ok": True, "uptime_sec": round(_t.time() - boot), "boot_ts": boot}

        @self.app.get("/api/system/info")
        async def system_info():
            """Системная информация о хосте."""
            import platform
            import psutil
            return {
                "ok": True,
                "hostname": platform.node(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": psutil.cpu_count(),
                "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
                "ram_used_pct": psutil.virtual_memory().percent,
                "disk_used_pct": psutil.disk_usage("/").percent,
            }

        @self.app.get("/api/translator/test")
        async def translator_test_api(text: str = Query(default=""), tgt: str = Query(default="")):
            """Тестовый перевод через API (GET для простоты)."""
            if not text:
                return {"ok": False, "error": "?text=Buenos+dias+amigo required"}
            try:
                from ..core.language_detect import detect_language, resolve_translation_pair
                from ..core.translator_engine import translate_text
                from ..openclaw_client import openclaw_client as _oc
                detected = detect_language(text)
                if not detected:
                    return {"ok": False, "error": "language not detected"}
                profile = self.kraab.get_translator_runtime_profile()
                src, tgt_lang = resolve_translation_pair(detected, profile.get("language_pair", "es-ru"))
                if tgt:
                    tgt_lang = tgt
                result = await translate_text(text, src, tgt_lang, openclaw_client=_oc)
                return {"ok": True, "src": src, "tgt": tgt_lang, "original": result.original,
                        "translated": result.translated, "latency_ms": result.latency_ms}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.post("/api/model/switch")
        async def model_switch(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Переключить модель через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..model_manager import model_manager as _mm
            model = str(payload.get("model") or "").strip()
            if not model:
                return {"ok": False, "error": "model required (e.g. 'auto', 'local', 'cloud', model_id)"}
            if model == "auto":
                _mm.set_provider("auto")
            elif model == "local":
                _mm.set_provider("local")
            elif model == "cloud":
                _mm.set_provider("cloud")
            else:
                _mm.set_model(model)
            return {"ok": True, "model": model, "active": str(getattr(_mm, "active_model_id", model))}

        @self.app.get("/api/notify/status")
        async def notify_status():
            """Статус tool narration toggle."""
            return {"ok": True, "enabled": bool(getattr(config, "TOOL_NARRATION_ENABLED", True))}

        @self.app.post("/api/notify/toggle")
        async def notify_toggle(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Toggle tool narration через API."""
            self._assert_write_access(x_krab_web_key, token)
            enabled = bool(payload.get("enabled", not getattr(config, "TOOL_NARRATION_ENABLED", True)))
            config.update_setting("TOOL_NARRATION_ENABLED", "1" if enabled else "0")
            return {"ok": True, "enabled": enabled}

        @self.app.get("/api/voice/profile")
        async def voice_profile():
            """Голосовой профиль runtime."""
            return {"ok": True, "profile": self.kraab.get_voice_runtime_profile()}

        @self.app.get("/api/swarm/task-board")
        async def swarm_task_board_status():
            """Сводка task board."""
            from ..core.swarm_task_board import swarm_task_board
            return {"ok": True, "summary": swarm_task_board.get_board_summary()}

        @self.app.get("/api/swarm/tasks")
        async def swarm_tasks_list(team: str = Query(default=""), limit: int = Query(default=20)):
            """Список задач task board."""
            from ..core.swarm_task_board import swarm_task_board
            tasks = swarm_task_board.list_tasks(team=team or None, limit=limit)
            return {"ok": True, "tasks": [
                {"task_id": t.task_id, "team": t.team, "title": t.title,
                 "status": t.status, "priority": t.priority, "created_at": t.created_at}
                for t in tasks
            ]}

        @self.app.get("/api/swarm/artifacts")
        async def swarm_artifacts_list(team: str = Query(default=""), limit: int = Query(default=10)):
            """Список swarm artifacts."""
            from ..core.swarm_artifact_store import swarm_artifact_store
            arts = swarm_artifact_store.list_artifacts(team=team or None, limit=limit)
            return {"ok": True, "artifacts": [
                {"team": a.get("team"), "topic": a.get("topic"), "timestamp_iso": a.get("timestamp_iso"),
                 "duration_sec": a.get("duration_sec"), "result_preview": (a.get("result") or "")[:200]}
                for a in arts
            ]}

        @self.app.get("/api/swarm/task/{task_id}")
        async def swarm_task_detail(task_id: str):
            """Детальная инфо о задаче."""
            from ..core.swarm_task_board import swarm_task_board
            all_tasks = swarm_task_board.list_tasks(limit=500)
            match = next((t for t in all_tasks if t.task_id.startswith(task_id)), None)
            if not match:
                return {"ok": False, "error": f"task '{task_id}' not found"}
            return {
                "ok": True,
                "task": {
                    "task_id": match.task_id, "team": match.team, "title": match.title,
                    "description": match.description, "status": match.status,
                    "priority": match.priority, "created_by": match.created_by,
                    "assigned_to": match.assigned_to, "created_at": match.created_at,
                    "updated_at": match.updated_at, "result": match.result,
                    "artifacts": match.artifacts, "parent_task_id": match.parent_task_id,
                },
            }

        @self.app.post("/api/swarm/tasks/create")
        async def swarm_task_create(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Создать task в swarm board через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_task_board import swarm_task_board
            team = str(payload.get("team") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not team or not title:
                return {"ok": False, "error": "team and title required"}
            task = swarm_task_board.create_task(
                team=team, title=title,
                description=str(payload.get("description") or ""),
                priority=str(payload.get("priority") or "medium"),
                created_by=str(payload.get("created_by") or "api"),
            )
            return {"ok": True, "task_id": task.task_id, "team": task.team, "title": task.title}

        @self.app.get("/api/swarm/team/{team_name}")
        async def swarm_team_info(team_name: str):
            """Детальная инфо о команде."""
            from ..core.swarm_artifact_store import swarm_artifact_store
            from ..core.swarm_bus import TEAM_REGISTRY, resolve_team_name
            from ..core.swarm_task_board import swarm_task_board
            resolved = resolve_team_name(team_name)
            if not resolved:
                return {"ok": False, "error": f"team '{team_name}' not found"}
            roles = TEAM_REGISTRY.get(resolved, [])
            tasks = swarm_task_board.list_tasks(team=resolved, limit=10)
            arts = swarm_artifact_store.list_artifacts(team=resolved, limit=5)
            return {
                "ok": True,
                "team": resolved,
                "roles": [{"name": r["name"], "title": r.get("title", ""), "emoji": r.get("emoji", "")} for r in roles],
                "tasks": [{"task_id": t.task_id, "title": t.title, "status": t.status} for t in tasks],
                "artifacts": [{"topic": a.get("topic"), "timestamp_iso": a.get("timestamp_iso")} for a in arts],
            }

        @self.app.get("/api/swarm/stats")
        async def swarm_stats():
            """Сводная статистика по всем командам."""
            from ..core.swarm_artifact_store import swarm_artifact_store
            from ..core.swarm_task_board import swarm_task_board
            from ..core.swarm_team_listener import is_listeners_enabled
            board = swarm_task_board.get_board_summary()
            arts = swarm_artifact_store.list_artifacts(limit=100)
            return {
                "ok": True,
                "board": board,
                "artifacts_count": len(arts),
                "listeners_enabled": is_listeners_enabled(),
            }

        @self.app.get("/api/swarm/reports")
        async def swarm_reports_list(limit: int = Query(default=10)):
            """Список markdown reports."""
            from pathlib import Path as _P
            report_dir = _P.home() / ".openclaw" / "krab_runtime_state" / "reports"
            if not report_dir.exists():
                return {"ok": True, "reports": []}
            files = sorted(report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
            return {"ok": True, "reports": [
                {"name": f.stem, "size_kb": round(f.stat().st_size / 1024, 1),
                 "modified": f.stat().st_mtime}
                for f in files
            ]}

        @self.app.post("/api/swarm/task/{task_id}/update")
        async def swarm_task_update(
            task_id: str,
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Обновить task status/result через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_task_board import swarm_task_board
            status = str(payload.get("status") or "").strip()
            result = str(payload.get("result") or "").strip()
            if status == "done" and result:
                swarm_task_board.complete_task(task_id, result=result)
            elif status == "failed":
                swarm_task_board.fail_task(task_id, reason=result or "via API")
            elif status:
                swarm_task_board.update_task(task_id, status=status)
            else:
                return {"ok": False, "error": "status required"}
            return {"ok": True, "task_id": task_id, "new_status": status}

        @self.app.delete("/api/swarm/task/{task_id}")
        async def swarm_task_delete(
            task_id: str,
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Удалить task из board."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_task_board import swarm_task_board
            # Помечаем как failed для FIFO cleanup
            try:
                swarm_task_board.fail_task(task_id, reason="deleted via API")
                return {"ok": True, "deleted": task_id}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        @self.app.post("/api/swarm/task/{task_id}/priority")
        async def swarm_task_priority(
            task_id: str,
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Change task priority via API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_task_board import swarm_task_board
            level = str(payload.get("priority") or "").strip().lower()
            if level not in {"low", "medium", "high", "critical"}:
                return {"ok": False, "error": "priority must be low/medium/high/critical"}
            swarm_task_board.update_task(task_id, priority=level)
            return {"ok": True, "task_id": task_id, "priority": level}

        @self.app.post("/api/swarm/listeners/toggle")
        async def swarm_listeners_toggle(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Toggle team listeners через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_team_listener import is_listeners_enabled, set_listeners_enabled
            enabled = bool(payload.get("enabled", not is_listeners_enabled()))
            set_listeners_enabled(enabled)
            return {"ok": True, "listeners_enabled": enabled}

        @self.app.post("/api/swarm/artifacts/cleanup")
        async def swarm_artifacts_cleanup(
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Очистка старых артефактов."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.swarm_artifact_store import swarm_artifact_store
            removed = swarm_artifact_store.cleanup_old(max_files=50)
            return {"ok": True, "removed": removed}

        @self.app.get("/api/swarm/listeners")
        async def swarm_listeners_status():
            """Статус team listeners."""
            from ..core.swarm_team_listener import is_listeners_enabled
            return {"ok": True, "listeners_enabled": is_listeners_enabled()}

        @self.app.get("/api/silence/status")
        async def silence_status():
            """Текущий статус тишины."""
            from ..core.silence_mode import silence_manager
            return {"ok": True, **silence_manager.status()}

        @self.app.post("/api/silence/toggle")
        async def silence_toggle(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """Toggle silence mode через API."""
            self._assert_write_access(x_krab_web_key, token)
            from ..core.silence_mode import silence_manager
            chat_id = str(payload.get("chat_id") or "").strip()
            minutes = int(payload.get("minutes") or 30)
            global_mode = bool(payload.get("global", False))
            if global_mode:
                if silence_manager.is_global_muted():
                    silence_manager.unmute_global()
                    return {"ok": True, "action": "unmuted_global"}
                silence_manager.mute_global(minutes=minutes)
                return {"ok": True, "action": "muted_global", "minutes": minutes}
            if not chat_id:
                return {"ok": False, "error": "chat_id required for per-chat silence"}
            if silence_manager.is_silenced(chat_id):
                silence_manager.unmute(chat_id)
                return {"ok": True, "action": "unmuted", "chat_id": chat_id}
            silence_manager.mute(chat_id, minutes=minutes)
            return {"ok": True, "action": "muted", "chat_id": chat_id, "minutes": minutes}

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
                repair_result = self._run_project_python_script(
                    self._project_root() / "scripts" / "openclaw_runtime_repair.py",
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
                sync_result = self._run_project_python_script(
                    self._project_root() / "scripts" / "sync_openclaw_models.py",
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

        @self.app.post("/api/runtime/chat-session/clear")
        async def runtime_chat_session_clear(
            payload: dict = Body(default_factory=dict),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
            token: str = Query(default=""),
        ):
            """
            Очищает runtime chat-session по chat_id через owner-only web endpoint.

            Зачем это нужно:
            - `!clear` в Telegram полезен, но требует ручного сообщения из owner-чата;
            - для recover/handoff/ops нам нужен тот же эффект из owner panel/CLI без похода в Telegram;
            - endpoint чистит и in-memory историю, и persisted `history_cache.db` через общий
              `openclaw_client.clear_session`, не дублируя логику в web-слое.
            """
            self._assert_write_access(x_krab_web_key, token)
            data = payload or {}
            chat_id = str(data.get("chat_id") or "").strip()
            if not chat_id:
                raise HTTPException(status_code=400, detail="chat_id_required")

            openclaw = self.deps.get("openclaw_client")
            if not openclaw or not hasattr(openclaw, "clear_session"):
                raise HTTPException(status_code=503, detail="chat_session_clear_not_supported")

            note = str(data.get("note") or "").strip()
            try:
                openclaw.clear_session(chat_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"chat_session_clear_failed: {exc}") from exc

            runtime_after = await self._collect_runtime_lite_snapshot()
            return {
                "ok": True,
                "action": "clear_chat_session",
                "chat_id": chat_id,
                "note": note,
                "runtime_after": runtime_after,
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
            script_path = "/Users/pablito/Antigravity_AGENTS/Краб/scripts/signal_ops_guard.py"

            try:
                # Запускаем с флагом --once для разовой проверки
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, script_path, "--once",
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

        @self.app.get("/api/ecosystem/capabilities")
        async def ecosystem_capabilities():
            """Возвращает capability-срез по control plane и внешним voice/audio сервисам."""
            return await self._ecosystem_capabilities_snapshot()

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
            operator_workflow = inbox_service.get_workflow_snapshot()
            workspace_state = build_workspace_state_snapshot()

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
                "operator_workflow": operator_workflow,
                "workspace_state": workspace_state,
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
            openclaw = self.deps.get("openclaw_client")
            last_runtime_route: dict[str, Any] = {}
            if openclaw and hasattr(openclaw, "get_last_runtime_route"):
                try:
                    last_runtime_route = dict(openclaw.get_last_runtime_route() or {})
                except Exception:
                    last_runtime_route = {}
            routing_status = self._overlay_live_route_on_openclaw_model_routing_status(
                routing=self._build_openclaw_model_routing_status(),
                last_runtime_route=last_runtime_route,
            )
            cloud_inventory = self._overlay_routing_provider_truth_on_cloud_inventory(
                cloud_inventory=cloud_inventory,
                routing_status=routing_status,
            )
            runtime_controls = self._build_openclaw_runtime_controls()
            auth_recovery = build_auth_recovery_readiness_snapshot(
                project_root=self._project_root(),
                status_payload=self._openclaw_models_status_snapshot().get("raw"),
                auth_profiles_payload=self._load_openclaw_auth_profiles(),
                runtime_models_payload=self._load_openclaw_runtime_models(),
                runtime_config_payload=self._load_openclaw_runtime_config(),
            )

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
                        "provider_auth_recovery": dict(item.get("provider_auth_recovery") or {}),
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

            payload = {
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
                "auth_recovery": auth_recovery,
                "catalog_guidance": {
                    "primary_flow": "Сначала выбери режим и пресет. Точный слот меняй только в advanced override.",
                    "openai_manual_only": False,
                },
            }
            self._store_model_catalog_cache(payload)
            return payload

        @self.app.get("/api/model/catalog")
        async def model_catalog(force_refresh: bool = Query(default=False)):
            """Каталог моделей/режимов для web-панели с кнопочным управлением."""
            router = self.deps["router"]
            if not force_refresh:
                cached_catalog = self._get_model_catalog_cache()
                if cached_catalog is not None:
                    return {"ok": True, "catalog": cached_catalog, "cached": True}
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
            routing = self._overlay_live_route_on_openclaw_model_routing_status(
                routing=routing,
                last_runtime_route=last_runtime_route,
            )

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
                    "owner_username": get_effective_owner_label(),
                    "owner_subjects": get_effective_owner_subjects(),
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
            post_apply_runtime_controls: dict[str, Any] | None = None
            post_apply_routing_status: dict[str, Any] | None = None

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
                execution_preset_raw = payload.get("execution_preset", "")
                main_max_concurrent_raw = payload.get("main_max_concurrent")
                subagent_max_concurrent_raw = payload.get("subagent_max_concurrent")
                slot_thinking_raw = payload.get("slot_thinking")
                try:
                    applied = self._apply_openclaw_runtime_controls(
                        primary_raw=primary_raw,
                        fallbacks_raw=list(fallbacks_raw),
                        context_tokens_raw=context_tokens_raw,
                        thinking_default_raw=thinking_default_raw,
                        execution_preset_raw=execution_preset_raw,
                        main_max_concurrent_raw=main_max_concurrent_raw,
                        subagent_max_concurrent_raw=subagent_max_concurrent_raw,
                        slot_thinking_raw=slot_thinking_raw if isinstance(slot_thinking_raw, dict) else {},
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

                self._runtime_lite_cache = None
                post_apply_routing_status = self._build_openclaw_model_routing_status()
                post_apply_runtime_controls = self._build_openclaw_runtime_controls()
                result_payload = {
                    "runtime": applied,
                    "routing_status": post_apply_routing_status,
                    "runtime_controls": post_apply_runtime_controls,
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

            catalog_refresh = {
                "degraded": False,
                "reason": "",
                "detail": "",
            }
            try:
                catalog_payload = await asyncio.wait_for(
                    _build_model_catalog(router),
                    timeout=self._model_apply_catalog_timeout_sec(),
                )
            except asyncio.TimeoutError:
                catalog_payload = self._build_model_catalog_fallback(
                    runtime_controls=post_apply_runtime_controls,
                    routing_status=post_apply_routing_status,
                    degraded_reason="catalog_refresh_timeout",
                )
                self._store_model_catalog_cache(catalog_payload)
                catalog_refresh = {
                    "degraded": True,
                    "reason": "catalog_refresh_timeout",
                    "detail": "Runtime уже записан, но полный refresh каталога занял слишком много времени; UI временно использует cache.",
                }
            except Exception as exc:  # noqa: BLE001
                catalog_payload = self._build_model_catalog_fallback(
                    runtime_controls=post_apply_runtime_controls,
                    routing_status=post_apply_routing_status,
                    degraded_reason="catalog_refresh_failed",
                )
                self._store_model_catalog_cache(catalog_payload)
                catalog_refresh = {
                    "degraded": True,
                    "reason": "catalog_refresh_failed",
                    "detail": f"Runtime уже записан, но post-apply refresh каталога завершился ошибкой: {exc}",
                }

            return {
                "ok": True,
                "action": action,
                "message": message_text,
                "result": result_payload,
                "catalog": catalog_payload,
                "catalog_refresh": catalog_refresh,
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
            return self._assistant_capabilities_snapshot()

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

        @self.app.post("/api/diagnostics/smoke")
        async def diagnostics_smoke():
            """
            Агрегированный owner-smoke для быстрой кнопки в панели.

            Нам нужен честный backend-контракт под кнопку `Run Smoke Trigger`, а не
            фронтовый placeholder. Endpoint собирает базовые browser/photo smoke
            и возвращает единый verdict, который потом можно расширять дальше.
            """
            browser_report, photo_payload = await asyncio.gather(
                self._collect_openclaw_browser_smoke_report("https://example.com"),
                self._collect_openclaw_photo_smoke_payload(),
            )

            browser_smoke = dict(browser_report.get("browser_smoke", {}) or {})
            photo_smoke = dict((photo_payload.get("report") or {}).get("photo_smoke", {}) or {})
            browser_ok = bool(browser_smoke.get("ok"))
            photo_available = bool(photo_payload.get("available"))
            photo_ok = bool(photo_smoke.get("ok")) if photo_available else False

            checks: list[dict[str, Any]] = [
                {
                    "name": "browser_smoke",
                    "ok": browser_ok,
                    "detail": str(browser_smoke.get("detail") or "browser smoke unavailable"),
                },
                {
                    "name": "photo_smoke",
                    "ok": photo_ok,
                    "detail": (
                        str(photo_smoke.get("detail") or "photo smoke unavailable")
                        if photo_available
                        else str(photo_payload.get("error") or "photo smoke unavailable")
                    ),
                },
            ]

            ok = all(bool(item.get("ok")) for item in checks)
            return {
                "ok": ok,
                "available": True,
                "checks": checks,
                "report": {
                    "browser": {
                        "available": True,
                        "report": browser_report,
                    },
                    "photo": photo_payload,
                },
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

            # После старта хотим вернуть тот же truthful payload, что и readiness-endpoint:
            # relay-smoke и owner Chrome probe независимы и могут собираться параллельно.
            smoke_report, owner_chrome = await asyncio.gather(
                self._collect_openclaw_browser_smoke_report("https://example.com"),
                self._probe_owner_chrome_devtools("https://example.com"),
            )
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
                    "owner_chrome": owner_chrome,
                },
            }

        @self.app.post("/api/openclaw/browser/open-owner-chrome")
        async def openclaw_browser_open_owner_chrome(
            token: str = Query(default=""),
            x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        ):
            """Открывает helper для relaunch обычного Chrome владельца с Remote Debugging."""
            self._assert_write_access(x_krab_web_key, token)
            return self._launch_owner_chrome_remote_debugging()

        @self.app.get("/api/openclaw/browser-mcp-readiness")
        async def openclaw_browser_mcp_readiness(url: str = "https://example.com"):
            """Агрегированный staged readiness для browser-контура владельца и managed MCP."""
            # Важный UX-момент: ordinary Chrome probe и relay-smoke не зависят друг от
            # друга напрямую, поэтому нет смысла ждать их строго последовательно.
            smoke_report, owner_chrome = await asyncio.gather(
                self._collect_openclaw_browser_smoke_report(url),
                self._probe_owner_chrome_devtools(url),
            )
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
            mcp = self._build_mcp_readiness_snapshot(browser, owner_chrome=owner_chrome)
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
                    "owner_chrome": owner_chrome,
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
            return await self._collect_openclaw_photo_smoke_payload()

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
            # После truthful runtime-check tier-state может измениться с stale `free`
            # на фактический `paid`, поэтому lightweight runtime snapshot нужно
            # пересобрать заново, а не держать старый TTL-cache.
            self._runtime_lite_cache = None
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
                self._runtime_lite_cache = None
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

            # Единый venv (Py 3.13) в приоритете; legacy .venv — фолбек.
            python_bin = project_root / "venv" / "bin" / "python"
            if not python_bin.exists():
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

            # Единый venv (Py 3.13) в приоритете; legacy .venv — фолбек.
            python_bin = project_root / "venv" / "bin" / "python"
            if not python_bin.exists():
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

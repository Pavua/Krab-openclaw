# -*- coding: utf-8 -*-
"""
OpenClaw Client - клиент взаимодействия с OpenClaw Gateway.

Ключевые задачи:
- Стриминг ответов и управление сессиями.
- Семантическая валидация ответов (защита от ложных 200 OK с текстом ошибки).
- Автоматический recovery policy: free -> paid -> openai -> local.
- Диагностика cloud runtime для web-панели.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .cache_manager import HISTORY_CACHE_TTL, history_cache
from .config import config
from .core.cloud_key_probe import (
    CloudProbeResult,
    default_openclaw_models_path,
    get_google_api_key_from_models,
    is_ai_studio_key,
    mask_secret,
    probe_gemini_key,
)
from .core.exceptions import ProviderAuthError, ProviderError
from .core.lm_studio_auth import build_lm_studio_auth_headers
from .core.lm_studio_health import is_lm_studio_available
from .core.logger import get_logger
from .core.observability import metrics
from .core.openclaw_runtime_models import (
    get_runtime_fallback_models,
    get_runtime_primary_model,
)
from .core.openclaw_secrets_runtime import (
    get_openclaw_cli_runtime_status,
    reload_openclaw_secrets,
)
from .core.routing_errors import RouterError, RouterQuotaError
from .core.sentry_perf import set_tag as _sentry_tag
from .core.sentry_perf import start_transaction as _sentry_txn

logger = get_logger(__name__)

AUTH_UNAUTHORIZED_CODE = "openclaw_auth_unauthorized"
LEGACY_AUTH_CODES = {AUTH_UNAUTHORIZED_CODE, "auth_invalid", "unsupported_key_type"}
MODEL_FALLBACK_LOG_RE = re.compile(
    r'^(?P<ts>\S+)\s+\[model-fallback\]\s+Model "(?P<requested>[^"]+)"[\s\S]*?Fell back to "(?P<fallback>[^"]+)"\.',
    re.IGNORECASE,
)
EMBEDDED_SESSION_LANE_ERROR_RE = re.compile(
    r'^(?P<ts>\S+)\s+\[diagnostic\]\s+lane task error:\s+lane=session:agent:main:openai:(?P<session>[a-z0-9-]+)\s+durationMs=\d+\s+error="(?P<error>.+)"$',
    re.IGNORECASE,
)

# Провайдеры CLI — не умеют обрабатывать binary/multimodal содержимое.
_CLI_PROVIDER_PREFIXES: tuple[str, ...] = (
    "codex-cli/",
    "gemini-cli/",
    "claude-cli/",
    "opencode/",
)

# Подстроки, однозначно указывающие на мультимодальную поддержку модели.
_VISION_CAPABLE_PATTERNS: list[str] = [
    "gemini-2.5",
    "gemini-3",
    "gpt-4-vision",
    "gpt-4o",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "qwen3.5-vl",
    "qwen2.5-vl",
    "-vl",
    "vision",
]


def _is_cli_provider(model: str) -> bool:
    """True если модель идёт через CLI-провайдер (text-only, multimodal не поддерживается)."""
    return bool(model) and model.startswith(_CLI_PROVIDER_PREFIXES)


def _supports_vision(model: str) -> bool:
    """True если модель поддерживает vision/multimodal запросы."""
    if not model or _is_cli_provider(model):
        return False
    model_lower = model.lower()
    return any(p in model_lower for p in _VISION_CAPABLE_PATTERNS)


class OpenClawClient:
    """Клиент OpenClaw Gateway API."""

    _think_block_pattern = re.compile(r"(?is)<think>.*?</think>")
    _final_block_pattern = re.compile(r"(?is)<final>(.*?)</final>")
    _think_final_tag_pattern = re.compile(r"(?i)</?(?:think|final)>")
    _plaintext_reasoning_intro_pattern = re.compile(
        r"(?i)^(?:think|thinking|thinking process|reasoning|analysis)\s*:?\s*$"
    )
    _plaintext_reasoning_step_pattern = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
    _plaintext_reasoning_meta_pattern = re.compile(
        r"(?i)^(?:step\s*\d+|thinking process|analysis|reasoning|analyze(?: the)? user(?:'s)? request|draft the response)\b"
    )
    _agentic_scratchpad_line_pattern = re.compile(
        r"(?ix)^("
        r"ready\.?"
        r"|yes\.?"
        r"|let'?s\s+(?:go|execute)\.?"
        r"|\.\.\."
        r"|wait[,.!]?\s+(?:(?:i|we)(?:'ll| will))\s+"
        r"(?:check|verify|inspect|look|use|open|run|try|confirm|explain|answer|draft|respond)\b.*"
        r"|(?:(?:i|we)(?:'ll| will))\s+"
        r"(?:check|verify|inspect|look|use|open|run|try|confirm|explain|answer|draft|respond)\b.*"
        r")$"
    )
    _agentic_scratchpad_command_pattern = re.compile(
        r"(?i)^(?:which|pwd|ls|rg|grep|find|git|python(?:3)?|pytest|ffmpeg|say|opencode|codex|claude|pi)\b.*$"
    )

    def __init__(self):
        self.base_url = config.OPENCLAW_URL.rstrip("/")
        self.token = config.OPENCLAW_TOKEN
        self._http_client = httpx.AsyncClient(
            # connect/write/pool — короткие, чтобы быстро падать на недоступный сервер.
            # read=None — без ограничения: OpenClaw сам управляет внутренней цепочкой
            # провайдеров и fallback-ретраями; любой read-timeout обрывал бы эту цепочку.
            timeout=httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                # OpenClaw v2026.3.28+: скоупы декларируются клиентом per-request,
                # а не берутся из токена. Нужен operator.write для chat completions.
                "x-openclaw-scopes": "operator.write,operator.read",
            },
        )
        self._sessions: Dict[str, list] = {}
        # Состояние нативного LM Studio chat-потока по `chat_id`.
        # Оно хранит `response_id`, чтобы продолжать локальный диалог через
        # `/api/v1/chat` без пересылки полного assistant-хвоста.
        self._lm_native_chat_state: Dict[str, dict[str, str]] = {}
        self._usage_stats = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        # Одноразовые флаги: чаты, для которых при следующем запросе история
        # не должна отправляться в LLM (memory-query режим).
        self._memory_query_flags: set[str] = set()

        # Source-of-truth по моделям/ключам OpenClaw (решение проекта: ~/.openclaw)
        self._models_path = default_openclaw_models_path()
        self._openclaw_runtime_config_path = Path.home() / ".openclaw" / "openclaw.json"

        # Сразу подтягиваем актуальный токен из runtime-конфига.
        # doctor --fix при каждом старте Краба может ротировать gateway token,
        # поэтому .env может устареть — runtime openclaw.json всегда актуальнее.
        self._sync_token_from_runtime_on_init()
        # W24/W26: авто-починка models.json — добавляем image в input для vision-моделей.
        # OpenClaw gateway стриппит image_url из payload если 'image' не заявлен в input[].
        # Вызывается при старте Краба; дополнительно — перед каждым photo-запросом
        # с async reload_openclaw_secrets (см. ниже), т.к. gateway может перезаписать
        # models.json при своём старте (race condition при одновременном запуске).
        self.ensure_vision_input_in_models_json()
        self._openclaw_sessions_index_path = (
            Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
        )
        self._gateway_log_path = Path(getattr(config, "BASE_DIR", Path.cwd())) / "openclaw.log"

        self.gemini_tiers = {
            "free": str(os.getenv("GEMINI_API_KEY_FREE", "") or "").strip(),
            "paid": str(os.getenv("GEMINI_API_KEY_PAID", "") or "").strip(),
        }
        self.active_tier = self._detect_initial_tier()

        self._cloud_tier_state: dict[str, Any] = {
            "active_tier": self.active_tier,
            "switches": 0,
            "last_switch_at": None,
            "last_error_code": None,
            "last_error_message": "",
            "last_provider_status": "unknown",
            "last_recovery_action": "none",
            "last_probe_at": None,
        }
        # Последний фактически использованный маршрут ответа (источник истины для web/UI).
        self._last_runtime_route: dict[str, Any] = {}
        # Трекинг активных tool calls для отображения в Telegram progress notices.
        self._active_tool_calls: list[dict[str, Any]] = []
        # Текущая in-flight LLM-задача (stream completion). Watchdog из llm_flow
        # (detect_stagnation) использует её для hard-cancel при зависании codex-cli.
        # None = нет активного запроса.
        self._current_request_task: Optional[asyncio.Task] = None

    def _sync_token_from_runtime_on_init(self) -> None:
        """При старте синхронизируем токен из ~/.openclaw/openclaw.json.

        doctor --fix запускается при каждом старте Краба и может ротировать
        gateway.auth.token. Читаем актуальный токен сразу, не дожидаясь auth-ошибки.
        """
        cfg_path = self._openclaw_runtime_config_path
        try:
            if not cfg_path.exists():
                return
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            gateway = payload.get("gateway", {}) if isinstance(payload, dict) else {}
            auth = gateway.get("auth", {}) if isinstance(gateway, dict) else {}
            runtime_token = (
                str(auth.get("token", "") or "").strip() if isinstance(auth, dict) else ""
            )
            if runtime_token and runtime_token != self.token:
                self.token = runtime_token
                self._http_client.headers["Authorization"] = f"Bearer {runtime_token}"
                logger.info("openclaw_token_synced_from_runtime_on_init", config_path=str(cfg_path))
        except (OSError, ValueError, TypeError):
            pass

    # Granular per-tool narration для Telegram progress notices.
    _TOOL_NARRATIONS: dict[str, str] = {
        "browser": "🌐 Открываю браузер...",
        "browse": "🌐 Открываю страницу...",
        "screenshot": "📸 Делаю скриншот...",
        "read_file": "📖 Читаю файл...",
        "read": "📖 Читаю данные...",
        "write_file": "✏️ Записываю файл...",
        "write": "✏️ Записываю...",
        "search": "🔍 Ищу информацию...",
        "web_search": "🔍 Ищу в интернете...",
        "bash": "⚙️ Выполняю команду...",
        "shell": "⚙️ Выполняю в терминале...",
        "python": "🐍 Запускаю Python...",
        "fetch": "📡 Загружаю данные...",
        "http": "📡 Отправляю HTTP-запрос...",
        "telegram": "📱 Работаю с Telegram...",
        "send_message": "📱 Отправляю сообщение...",
        "memory": "🧠 Обращаюсь к памяти...",
        "recall": "🧠 Вспоминаю...",
        "vision": "👁️ Анализирую изображение...",
        "code": "💻 Работаю с кодом...",
        "mercadona": "🛒 Проверяю Mercadona...",
        "shop": "🛒 Проверяю магазин...",
        "crypto": "📊 Проверяю криптовалюту...",
        "imessage": "💬 Работаю с iMessage...",
    }

    def _narrate_tool(self, name: str) -> str:
        """Возвращает human-readable narration для tool по имени."""
        narrations = self._TOOL_NARRATIONS
        # Точное совпадение
        if name in narrations:
            return narrations[name]
        # Совпадение по подстроке (browser_action → browser)
        name_lower = name.lower()
        for key, msg in narrations.items():
            if key in name_lower:
                return msg
        return f"🔧 Выполняю: {name}..."

    def get_active_tool_calls_summary(self) -> str:
        """Возвращает granular сводку активных/завершённых tool calls для Telegram notices."""
        if not self._active_tool_calls:
            return ""
        # Проверяем toggle — если выключен, не показываем narrations
        if not getattr(config, "TOOL_NARRATION_ENABLED", True):
            return ""
        running = [tc for tc in self._active_tool_calls if tc.get("status") == "running"]
        done = [tc for tc in self._active_tool_calls if tc.get("status") == "done"]
        parts: list[str] = []
        for tc in running:
            parts.append(self._narrate_tool(tc["name"]))
        if done:
            parts.append(f"✅ Готово: {', '.join(tc['name'] for tc in done)}")
        total = len(self._active_tool_calls)
        if total > 0:
            parts.append(f"Инструментов: {len(done)}/{total}")
        return "\n".join(parts)

    @staticmethod
    def _provider_from_model(model_id: str) -> str:
        """Возвращает имя провайдера по идентификатору модели."""
        raw = str(model_id or "").strip()
        if "/" in raw:
            return raw.split("/", 1)[0]
        return "unknown"

    def _resolve_buffered_read_timeout_sec(
        self,
        *,
        model_id: str,
        has_photo: bool = False,
    ) -> float | None:
        """
        Возвращает budget ожидания для buffered cloud-completion.

        Почему нужен отдельный budget:
        - `stream=False` даёт корректный контент, но скрывает стадию "модель уже думает";
        - часть cloud-провайдеров умеет держать HTTP-запрос очень долго и потом вернуть 200;
        - пока запрос не упал transport-ошибкой, fallback-цепочка не стартует.

        Здесь мы задаём не глобальный hard-timeout userbot, а именно потолок ожидания
        одного cloud-route до принудительного перехода к следующему кандидату.

        ВАЖНО: для CLI-провайдеров (codex-cli, google-gemini-cli, openai-codex) дефолт — None,
        т.к. OpenClaw Gateway сам управляет fallback-цепочкой и retry внутри timeoutSeconds.
        Двойной read-timeout (Gateway + Краб) вызывал ложные ошибки "Провайдер недоступен"
        при долгих, но корректных запросах (tool use, reasoning).
        """
        normalized_model = str(model_id or "").strip()
        provider = self._provider_from_model(normalized_model)

        timeout_sec: float | None = getattr(
            config,
            "OPENCLAW_BUFFERED_READ_TIMEOUT_SEC",
            None,
        )
        # CLI-провайдеры: дефолт None — OpenClaw Gateway сам управляет retry/fallback.
        # Пользователь может переопределить через .env, если нужен explicit budget.
        if provider == "codex-cli":
            env_val = getattr(
                config,
                "OPENCLAW_CODEX_CLI_BUFFERED_READ_TIMEOUT_SEC",
                None,
            )
            timeout_sec = float(env_val) if env_val else None
        elif provider == "google-gemini-cli":
            env_val = getattr(
                config,
                "OPENCLAW_GOOGLE_GEMINI_CLI_BUFFERED_READ_TIMEOUT_SEC",
                None,
            )
            timeout_sec = float(env_val) if env_val else None
        elif provider == "openai-codex":
            env_val = getattr(
                config,
                "OPENCLAW_OPENAI_CODEX_BUFFERED_READ_TIMEOUT_SEC",
                None,
            )
            timeout_sec = float(env_val) if env_val else None

        if timeout_sec is None:
            return None

        timeout_sec = float(timeout_sec)
        if has_photo:
            # Фото-маршруты по определению медленнее; не даём им умереть слишком рано.
            photo_soft_timeout_sec = float(
                getattr(config, "OPENCLAW_PHOTO_FIRST_CHUNK_TIMEOUT_SEC", 540.0) or 540.0
            )
            timeout_sec = max(timeout_sec, photo_soft_timeout_sec)

        return timeout_sec if timeout_sec > 0 else None

    @staticmethod
    def _local_recovery_enabled(*, force_cloud: bool, has_photo: bool = False) -> bool:
        """
        Разрешён ли аварийный fallback cloud -> local.

        Логика:
        - при force_cloud локальный recovery всегда выключен;
        - для фото при `LOCAL_PREFERRED_VISION_MODEL=auto` локальный recovery
          запрещён, чтобы cloud-ветка не пересаживала запрос на случайную
          маленькую vision-модель;
        - иначе управляется флагом LOCAL_FALLBACK_ENABLED.
        """
        if force_cloud:
            return False
        if has_photo:
            preferred_vision = (
                str(getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or "").strip().lower()
            )
            if preferred_vision in {"", "auto"}:
                return False
        return bool(getattr(config, "LOCAL_FALLBACK_ENABLED", True))

    def _set_last_runtime_route(
        self,
        *,
        channel: str,
        model: str,
        route_reason: str,
        route_detail: str = "",
        status: str = "ok",
        error_code: str | None = None,
        force_cloud: bool = False,
        attempt: int | None = None,
    ) -> None:
        """Фиксирует последний runtime-маршрут запроса без секретов."""
        from .core.operator_identity import current_account_id, current_operator_id  # noqa: PLC0415
        from .core.provider_failover import failover_policy  # noqa: PLC0415

        self._last_runtime_route = {
            "timestamp": int(time.time()),
            "channel": channel,
            "provider": self._provider_from_model(model),
            "model": str(model or ""),
            "active_tier": self.active_tier,
            "force_cloud": bool(force_cloud),
            "status": status,
            "error_code": error_code,
            "route_reason": route_reason,
            "route_detail": route_detail,
            # Phase 1: identity fields в каждом routing event
            "operator_id": current_operator_id(),
            "account_id": current_account_id(),
        }
        if attempt is not None and int(attempt) > 0:
            self._last_runtime_route["attempt"] = int(attempt)

        # Feed policy — success/failure каждого route попадает в health-таблицу
        # provider_failover. Сам auto-switch триггерится отдельным хуком
        # при достижении threshold (см. callbacks в userbot_bridge.start()).
        try:
            provider = self._last_runtime_route.get("provider", "") or ""
            if status == "ok" and provider:
                failover_policy.record_success(provider)
            elif status == "error" and error_code and provider:
                failover_policy.record_failure(provider, error_code)
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider_failover_feed_failed", error=str(exc))

    def get_last_runtime_route(self) -> dict[str, Any]:
        """Возвращает snapshot последнего фактического маршрута."""
        return dict(self._last_runtime_route)

    def register_current_request_task(self, task: Optional[asyncio.Task]) -> None:
        """
        Регистрирует текущий in-flight LLM task (обёртка над send_message_stream).

        Вызывается llm_flow при старте stream'а. При детекте стагнации watchdog
        вызывает cancel_current_request() — тогда .cancel() на этом task
        штатно прерывает await-цепочку async generator'а.

        Передай None чтобы снять регистрацию (после завершения).
        """
        self._current_request_task = task

    def cancel_current_request(self) -> bool:
        """
        Отменяет текущий in-flight LLM-call. Returns True если был активный task.

        Используется watchdog'ом (llm_flow.detect_stagnation): когда gateway
        task-poller видит, что OpenClaw runs.sqlite не обновлялся > threshold
        секунд — мы гарантированно hung и ждать дальше бессмысленно.

        Важно: task.cancel() прерывает await в send_message_stream и заставляет
        все async for/yield получить CancelledError — это штатный asyncio way.
        """
        task = self._current_request_task
        if task and not task.done():
            task.cancel()
            logger.warning("llm_request_cancelled_by_watchdog")
            return True
        return False

    def _sync_last_runtime_route_active_tier(self) -> None:
        """
        Подтягивает `active_tier` в последнем cloud-route к текущей truthful tier-state.

        Почему это нужно:
        - `last_runtime_route` часто фиксируется на warmup ещё до runtime-check/probe;
        - после truthful probe active tier может измениться с stale `free` на реальный `paid`;
        - owner UI и `health_lite` не должны спорить с `cloud_runtime` только из-за порядка вызовов.
        """
        if not self._last_runtime_route:
            return

        channel = str(self._last_runtime_route.get("channel") or "").strip().lower()
        provider = str(self._last_runtime_route.get("provider") or "").strip().lower()
        if channel != "openclaw_cloud":
            return
        if provider not in {"google", "google-gemini-cli"}:
            return

        self._last_runtime_route["active_tier"] = str(
            self._cloud_tier_state.get("active_tier", self.active_tier) or self.active_tier
        )

    def _refresh_gateway_token_from_runtime(self) -> bool:
        """
        Подтягивает gateway token из `~/.openclaw/openclaw.json` и обновляет HTTP headers.

        Зачем:
        - в non-bootstrap среде `.env` часто содержит устаревший `OPENCLAW_API_KEY`;
        - реальный gateway token живёт в runtime-конфиге OpenClaw;
        - при 401 делаем один auto-refresh, чтобы убрать ложные auth-падения.
        """
        cfg_path = self._openclaw_runtime_config_path
        try:
            if not cfg_path.exists():
                return False
            payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            gateway = payload.get("gateway", {}) if isinstance(payload, dict) else {}
            auth = gateway.get("auth", {}) if isinstance(gateway, dict) else {}
            runtime_token = ""
            if isinstance(auth, dict):
                runtime_token = str(auth.get("token", "") or "").strip()
            if not runtime_token and isinstance(gateway, dict):
                runtime_token = str(gateway.get("token", "") or "").strip()
            if not runtime_token or runtime_token == self.token:
                return False
            self.token = runtime_token
            self._http_client.headers["Authorization"] = f"Bearer {runtime_token}"
            logger.warning(
                "openclaw_gateway_token_refreshed_from_runtime",
                config_path=str(cfg_path),
            )
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "openclaw_gateway_token_refresh_failed",
                config_path=str(cfg_path),
                error=str(exc),
            )
            return False

    def _resolve_gateway_reported_model(
        self,
        requested_model: str,
        *,
        request_started_at: float,
    ) -> str:
        """
        Возвращает фактическую модель, если gateway тихо сделал внутренний fallback.

        Важный нюанс OpenClaw:
        - API `v1/chat/completions` часто возвращает requested model в JSON, даже
          если сам gateway внутри пересадил запрос на другой provider/model;
        - поэтому для truthful route-status приходится дополнительно смотреть
          свежую строку `[model-fallback]` в локальном gateway-логе.
        """
        normalized_requested = str(requested_model or "").strip()
        if not normalized_requested:
            return ""

        log_path = self._gateway_log_path
        if not log_path.exists():
            return normalized_requested

        try:
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
        except OSError as exc:
            logger.warning("openclaw_gateway_log_read_failed", path=str(log_path), error=str(exc))
            return normalized_requested

        for raw_line in reversed(lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            match = MODEL_FALLBACK_LOG_RE.match(line)
            if not match:
                continue
            requested = str(match.group("requested") or "").strip()
            if requested != normalized_requested:
                continue

            ts_raw = str(match.group("ts") or "").strip()
            try:
                event_ts = datetime.fromisoformat(ts_raw).timestamp()
            except ValueError:
                event_ts = 0.0
            if event_ts and event_ts < (float(request_started_at) - 2.0):
                continue

            fallback_model = str(match.group("fallback") or "").strip()
            if fallback_model:
                logger.info(
                    "openclaw_gateway_model_fallback_detected",
                    requested_model=normalized_requested,
                    fallback_model=fallback_model,
                    log_path=str(log_path),
                )
                return fallback_model

        embedded_fallback = self._resolve_gateway_session_model_from_log(
            log_lines=lines,
            requested_model=normalized_requested,
            request_started_at=request_started_at,
        )
        if embedded_fallback:
            return embedded_fallback

        return normalized_requested

    def _resolve_gateway_session_model_from_log(
        self,
        *,
        log_lines: list[str],
        requested_model: str,
        request_started_at: float,
    ) -> str:
        """
        Пытается восстановить фактическую модель через session-state embedded agent.

        Почему нужен второй источник истины:
        - OpenClaw не всегда пишет `[model-fallback]`, если primary падает auth/scopes-ошибкой;
        - при этом session `agent:main:openai:*` уже успевает обновиться на
          реальный fallback provider/model;
        - без этого owner UI видит requested model и врет о сработавшем primary.
        """
        sessions_path = self._openclaw_sessions_index_path
        if not sessions_path.exists():
            return ""

        try:
            sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "openclaw_sessions_index_read_failed",
                path=str(sessions_path),
                error=str(exc),
            )
            return ""
        if not isinstance(sessions_payload, dict):
            return ""

        normalized_requested = str(requested_model or "").strip()
        if not normalized_requested:
            return ""

        for raw_line in reversed(log_lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            match = EMBEDDED_SESSION_LANE_ERROR_RE.match(line)
            if not match:
                continue

            ts_raw = str(match.group("ts") or "").strip()
            try:
                event_ts = datetime.fromisoformat(ts_raw).timestamp()
            except ValueError:
                event_ts = 0.0
            if event_ts and event_ts < (float(request_started_at) - 2.0):
                continue

            session_key = f"agent:main:openai:{str(match.group('session') or '').strip()}"
            session_meta = sessions_payload.get(session_key)
            if not isinstance(session_meta, dict):
                continue

            resolved_model = self._compose_session_runtime_model(session_meta)
            if not resolved_model or resolved_model == normalized_requested:
                continue

            logger.info(
                "openclaw_gateway_session_fallback_detected",
                requested_model=normalized_requested,
                fallback_model=resolved_model,
                session_key=session_key,
                log_path=str(self._gateway_log_path),
            )
            return resolved_model

        return ""

    @staticmethod
    def _compose_session_runtime_model(session_meta: dict[str, Any]) -> str:
        """
        Собирает full model id из session-state OpenClaw.

        Session index часто хранит `modelProvider=google-gemini-cli` и
        `model=gemini-3.1-pro-preview` раздельно, поэтому для truthful route
        их надо склеивать обратно.
        """
        if not isinstance(session_meta, dict):
            return ""
        provider = str(session_meta.get("modelProvider") or "").strip()
        model = str(session_meta.get("model") or "").strip()
        if not model:
            return ""
        if "/" in model:
            return model
        if provider:
            return f"{provider}/{model}"
        return model

    @staticmethod
    def _messages_size(messages: List[Dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(part.get("text", ""))
        return total

    @classmethod
    def _split_plaintext_reasoning_and_answer(cls, text: str) -> tuple[str, str]:
        """
        Отделяет plain-text reasoning от финального ответа.

        Почему это нужно:
        - часть OpenClaw-совместимых маршрутов возвращает не только ответ, но и
          служебный reasoning в одном `content`;
        - такой текст нельзя сохранять в chat-history как нормальную assistant-реплику,
          иначе следующий запрос увидит цепочку мыслей вместо полезного контекста.
        """
        raw = str(text or "")
        if not raw.strip():
            return "", ""

        lines = raw.splitlines()
        non_empty_indexes = [idx for idx, line in enumerate(lines) if line.strip()]
        if not non_empty_indexes:
            return "", raw.strip()

        intro_hits = 0
        for idx in non_empty_indexes[:3]:
            stripped = lines[idx].strip()
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                intro_hits += 1
                continue
            if idx == non_empty_indexes[0] and stripped.lower().startswith("thinking process:"):
                intro_hits += 1
                continue
        if intro_hits == 0:
            return "", raw.strip()

        def _is_reasoning_line(candidate: str) -> bool:
            stripped = candidate.strip()
            if not stripped:
                return False
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_step_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_meta_pattern.match(stripped):
                return True
            return False

        last_content_idx: int | None = None
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip():
                last_content_idx = idx
                break
        if last_content_idx is None:
            return "", ""

        answer_end = last_content_idx
        answer_start: int | None = None
        for idx in range(last_content_idx, -1, -1):
            current = lines[idx]
            if not current.strip():
                if answer_start is not None:
                    break
                continue
            if _is_reasoning_line(current):
                if answer_start is not None:
                    break
                continue
            answer_start = idx

        if answer_start is None:
            return raw.strip(), ""

        reasoning = "\n".join(lines[:answer_start]).strip()
        extracted = "\n".join(lines[answer_start : answer_end + 1]).strip()
        if not reasoning:
            return "", raw.strip()
        return reasoning, extracted or raw.strip()

    @classmethod
    def _sanitize_assistant_response(cls, text: str) -> str:
        """
        Оставляет только пользовательски полезный финальный текст ответа.

        Зачем:
        - history cache должен хранить тот же смысловой ответ, который видит пользователь;
        - reasoning-блоки и `<think>/<final>` markup не должны попадать в будущий
          диалоговый контекст и ломать "память" модели.
        """
        raw = str(text or "").strip()
        if not raw:
            return ""

        final_match = cls._final_block_pattern.search(raw)
        if final_match:
            cleaned = str(final_match.group(1) or "")
        else:
            cleaned = cls._think_block_pattern.sub("", raw)

        cleaned = cls._think_final_tag_pattern.sub("", cleaned)
        _, answer = cls._split_plaintext_reasoning_and_answer(cleaned)
        normalized = answer or cleaned
        normalized = cls._strip_agentic_scratchpad(normalized)
        normalized = re.sub(r"[ \t]{2,}", " ", normalized)
        normalized = re.sub(r"(?mi)^\s*(assistant|user|system)\s*$", "", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    @classmethod
    def _strip_agentic_scratchpad(cls, text: str) -> str:
        """
        Убирает codex-style scratchpad до записи в историю и до отдачи userbot.

        Почему это делаем уже в OpenClawClient:
        - загрязнённый assistant-output иначе попадёт в history cache и будет
          воспроизводить тот же мусор на следующих ходах;
        - userbot тоже санирует ответ, но история должна очищаться раньше.
        """
        raw = str(text or "").strip()
        if not raw:
            return ""

        non_empty = [line.strip() for line in raw.splitlines() if line.strip()]
        if not non_empty:
            return raw

        probe_lines = non_empty[:12]
        scratch_hits = sum(
            1 for line in probe_lines if cls._agentic_scratchpad_line_pattern.match(line)
        )
        command_hits = sum(
            1 for line in probe_lines if cls._agentic_scratchpad_command_pattern.match(line)
        )
        if scratch_hits < 2 or (scratch_hits + command_hits) < 3:
            return raw

        kept_lines: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept_lines and kept_lines[-1] != "":
                    kept_lines.append("")
                continue
            if cls._agentic_scratchpad_line_pattern.match(stripped):
                continue
            if cls._agentic_scratchpad_command_pattern.match(stripped):
                continue
            kept_lines.append(line)

        cleaned = "\n".join(kept_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @classmethod
    def _sanitize_session_history(
        cls,
        messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], bool]:
        """
        Очищает уже сохранённую историю от reasoning-мусора.

        Почему это нужно:
        - баг с `<think>`/plain-text reasoning уже мог успеть попасть в
          `history_cache.db` до фикса;
        - если не санировать старые assistant-реплики, следующий запрос
          продолжит видеть "мысли модели" как обычный контекст и будет
          вести себя так, будто у него амнезия.
        """
        sanitized: list[dict[str, Any]] = []
        changed = False

        for message in messages:
            if not isinstance(message, dict):
                changed = True
                continue

            role = str(message.get("role") or "").strip()
            if not role:
                changed = True
                continue

            if role == "assistant" and isinstance(message.get("content"), str):
                cleaned_content = cls._sanitize_assistant_response(message.get("content") or "")
                if cleaned_content != str(message.get("content") or "").strip():
                    changed = True
                if not cleaned_content:
                    changed = True
                    continue
                if cleaned_content != message.get("content"):
                    updated_message = dict(message)
                    updated_message["content"] = cleaned_content
                    sanitized.append(updated_message)
                    continue

            sanitized.append(message)

        return sanitized, changed

    def _sanitize_session_and_cache(self, chat_id: str) -> None:
        """
        Приводит в порядок in-memory историю и её кэшированную копию.

        Это lazy-repair: как только чат снова оживает, мы переписываем его
        историю уже в очищенном виде, не требуя ручного `!clear`.
        """
        current_messages = self._sessions.get(chat_id)
        if not isinstance(current_messages, list) or not current_messages:
            return

        sanitized_messages, changed = self._sanitize_session_history(current_messages)
        if not changed:
            return

        self._sessions[chat_id] = sanitized_messages
        try:
            history_cache.set(
                f"chat_history:{chat_id}",
                json.dumps(sanitized_messages, ensure_ascii=False),
                ttl=HISTORY_CACHE_TTL,
            )
            logger.info(
                "history_cache_sanitized", chat_id=chat_id, messages=len(sanitized_messages)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("history_cache_sanitize_set_failed", chat_id=chat_id, error=str(exc))

    def _apply_sliding_window(
        self,
        chat_id: str,
        messages: List[Dict[str, Any]],
        *,
        max_msgs: int | None = None,
        max_chars: int | None = None,
        trim_reason: str = "history_window",
    ) -> List[Dict[str, Any]]:
        """
        Обрезает историю по числу сообщений и/или символов.

        Важно:
        - system prompt сохраняется, если он есть;
        - char-budget учитывает размер сохранённого system prompt, иначе можно
          формально "обрезать" хвост и всё равно отправить слишком большой пакет.
        """
        max_msgs = max(
            1,
            int(
                max_msgs if max_msgs is not None else getattr(config, "HISTORY_WINDOW_MESSAGES", 50)
            ),
        )
        if max_chars is None:
            max_chars = getattr(config, "HISTORY_WINDOW_MAX_CHARS", None)
        if len(messages) <= max_msgs and (
            max_chars is None or self._messages_size(messages) <= max_chars
        ):
            return messages

        out: list[dict[str, Any]] = []
        if messages and messages[0].get("role") == "system":
            out.append(messages[0])
            rest = messages[1:]
            slot_for_tail = max_msgs - 1
        else:
            rest = messages
            slot_for_tail = max_msgs

        if slot_for_tail == 0:
            tail = []
        elif len(rest) > slot_for_tail:
            tail = rest[-slot_for_tail:]
        else:
            tail = rest

        if max_chars is not None:
            reserved_chars = self._messages_size(out)
            available_chars = max(0, int(max_chars) - reserved_chars)
            current = 0
            new_tail = []
            for message in reversed(tail):
                size = self._messages_size([message])
                if available_chars <= 0 and new_tail:
                    break
                if available_chars > 0 and current + size > available_chars and new_tail:
                    break
                new_tail.append(message)
                current += size
            tail = list(reversed(new_tail))

        out.extend(tail)
        logger.info(
            "history_trimmed",
            reason=trim_reason,
            chat_id=chat_id,
            dropped_messages=len(messages) - len(out),
            before_count=len(messages),
            after_count=len(out),
            before_chars=self._messages_size(messages),
            after_chars=self._messages_size(out),
        )
        return out

    def _apply_local_route_history_budget(
        self,
        chat_id: str,
        messages: List[Dict[str, Any]],
        *,
        has_photo: bool,
        trim_reason: str,
    ) -> List[Dict[str, Any]]:
        """
        Отдельный budget для локального маршрута.

        Почему:
        - локальные модели в LM Studio заметно хуже переносят длинный диалоговый хвост;
        - нам важнее сохранить последние реплики и не раздувать prompt до десятков тысяч
          токенов, чем пытаться удержать весь исторический контекст любой ценой.
        """
        max_msgs = getattr(config, "LOCAL_HISTORY_WINDOW_MESSAGES", 18)
        max_chars = getattr(config, "LOCAL_HISTORY_WINDOW_MAX_CHARS", 12000)
        trimmed = self._apply_sliding_window(
            chat_id,
            messages,
            max_msgs=max_msgs,
            max_chars=max_chars,
            trim_reason=trim_reason,
        )
        if len(trimmed) != len(messages) or self._messages_size(trimmed) != self._messages_size(
            messages
        ):
            logger.info(
                "local_route_history_budget_applied",
                chat_id=chat_id,
                has_photo=has_photo,
                max_msgs=max_msgs,
                max_chars=max_chars,
                before_count=len(messages),
                after_count=len(trimmed),
                before_chars=self._messages_size(messages),
                after_chars=self._messages_size(trimmed),
            )
        return trimmed

    def _detect_initial_tier(self) -> str:
        """Определяет активный tier по ключу в OpenClaw models.json."""
        current_key = get_google_api_key_from_models(self._models_path)
        if current_key and current_key == self.gemini_tiers.get("paid"):
            return "paid"
        if current_key and current_key == self.gemini_tiers.get("free"):
            return "free"
        # Фолбэк по умолчанию — free
        return "free"

    def _runtime_google_key_state(self) -> dict[str, Any]:
        """
        Классифицирует фактический `providers.google.apiKey` в OpenClaw `models.json`.

        Зачем:
        - `active_tier` сам по себе не объясняет, почему runtime оказался в `free`;
        - в `models.json` может лежать не реальный AI Studio ключ, а placeholder
          вроде `GEMINI_API_KEY`, из-за чего tier по умолчанию выглядит как правда;
        - owner UI должен видеть drift между env-ключами и runtime key-path.
        """
        current_key = str(get_google_api_key_from_models(self._models_path) or "").strip()
        free_key = str(self.gemini_tiers.get("free") or "").strip()
        paid_key = str(self.gemini_tiers.get("paid") or "").strip()

        if not current_key:
            return {
                "state": "missing",
                "tier": "",
                "source": "models_json",
                "masked": "",
                "matches_free": False,
                "matches_paid": False,
            }
        if paid_key and current_key == paid_key:
            return {
                "state": "paid",
                "tier": "paid",
                "source": "models_json",
                "masked": mask_secret(current_key),
                "matches_free": False,
                "matches_paid": True,
            }
        if free_key and current_key == free_key:
            return {
                "state": "free",
                "tier": "free",
                "source": "models_json",
                "masked": mask_secret(current_key),
                "matches_free": True,
                "matches_paid": False,
            }
        if not is_ai_studio_key(current_key):
            return {
                "state": "placeholder",
                "tier": "",
                "source": "models_json",
                "masked": mask_secret(current_key),
                "matches_free": False,
                "matches_paid": False,
            }
        return {
            "state": "custom",
            "tier": "",
            "source": "models_json",
            "masked": mask_secret(current_key),
            "matches_free": False,
            "matches_paid": False,
        }

    def _effective_runtime_google_key_state(self) -> dict[str, Any]:
        """
        Возвращает effective state для runtime Google key с учётом env-placeholder.

        Почему это нужно:
        - OpenClaw может хранить в `models.json` не literal key, а ссылку вроде
          `GEMINI_API_KEY` или `GOOGLE_API_KEY`;
        - raw `placeholder` при этом не означает, что live runtime работает на
          free-tier или вообще без ключа;
        - owner UI должен видеть разницу между `raw_ref` и фактически
          разрешённым runtime-ключом.
        """
        raw_state = self._runtime_google_key_state()
        raw_value = str(get_google_api_key_from_models(self._models_path) or "").strip()
        if str(raw_state.get("state") or "") != "placeholder":
            return {
                **raw_state,
                "raw_state": str(raw_state.get("state") or ""),
                "raw_masked": str(raw_state.get("masked") or ""),
                "raw_reference": raw_value,
                "resolved_from_env": False,
            }

        env_name = raw_value.strip()
        resolved_value = str(os.getenv(env_name, "") or "").strip() if env_name else ""
        free_key = str(self.gemini_tiers.get("free") or "").strip()
        paid_key = str(self.gemini_tiers.get("paid") or "").strip()
        if not resolved_value:
            return {
                **raw_state,
                "raw_state": "placeholder",
                "raw_masked": str(raw_state.get("masked") or ""),
                "raw_reference": env_name,
                "resolved_from_env": False,
                "resolved_env_name": env_name,
            }
        if paid_key and resolved_value == paid_key:
            return {
                "state": "paid",
                "tier": "paid",
                "source": f"models_json_placeholder:{env_name}",
                "masked": mask_secret(resolved_value),
                "matches_free": False,
                "matches_paid": True,
                "raw_state": "placeholder",
                "raw_masked": str(raw_state.get("masked") or ""),
                "raw_reference": env_name,
                "resolved_from_env": True,
                "resolved_env_name": env_name,
            }
        if free_key and resolved_value == free_key:
            return {
                "state": "free",
                "tier": "free",
                "source": f"models_json_placeholder:{env_name}",
                "masked": mask_secret(resolved_value),
                "matches_free": True,
                "matches_paid": False,
                "raw_state": "placeholder",
                "raw_masked": str(raw_state.get("masked") or ""),
                "raw_reference": env_name,
                "resolved_from_env": True,
                "resolved_env_name": env_name,
            }
        if is_ai_studio_key(resolved_value):
            return {
                "state": "custom",
                "tier": "",
                "source": f"models_json_placeholder:{env_name}",
                "masked": mask_secret(resolved_value),
                "matches_free": False,
                "matches_paid": False,
                "raw_state": "placeholder",
                "raw_masked": str(raw_state.get("masked") or ""),
                "raw_reference": env_name,
                "resolved_from_env": True,
                "resolved_env_name": env_name,
            }
        return {
            **raw_state,
            "raw_state": "placeholder",
            "raw_masked": str(raw_state.get("masked") or ""),
            "raw_reference": env_name,
            "resolved_from_env": False,
            "resolved_env_name": env_name,
        }

    def _read_models_json(self) -> dict[str, Any]:
        if not self._models_path.exists():
            return {"providers": {}}
        try:
            return json.loads(self._models_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"providers": {}}

    def _write_models_json(self, payload: dict[str, Any]) -> bool:
        try:
            self._models_path.parent.mkdir(parents=True, exist_ok=True)
            self._models_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            logger.error(
                "openclaw_models_write_failed", error=str(exc), path=str(self._models_path)
            )
            return False

    def _set_google_key_in_models(self, key_value: str) -> bool:
        data = self._read_models_json()
        providers = data.setdefault("providers", {})
        google = providers.setdefault("google", {})
        google["apiKey"] = key_value
        return self._write_models_json(data)

    # Паттерны ID моделей, поддерживающих vision/multimodal.
    _VISION_MODEL_PATTERNS: tuple[str, ...] = (
        "gemini-1.5",
        "gemini-2",
        "gemini-3",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4-vision",
        "gpt-5",
        "claude-opus",
        "claude-sonnet",
        "claude-haiku",
    )

    def _is_model_declared_vision_in_config(self, model_id: str) -> bool:
        """Проверяет что models.json объявляет image в input для модели model_id.

        W24/W26: OpenClaw gateway читает input[] для маршрутизации multimodal запросов.
        Если image отсутствует — gateway стриппит image_url из payload перед
        передачей в Gemini/OpenAI, даже если bytes были переданы корректно.

        ВАЖНО: НЕ делаем early-return на первом матче — у одной модели могут быть
        записи в нескольких провайдерах (google/google-antigravity). Возвращаем True
        если хотя бы одна запись имеет image в input[].
        """
        data = self._read_models_json()
        norm = str(model_id or "").strip().lower()
        for pdata in data.get("providers", {}).values():
            for m in pdata.get("models", []):
                mid = str(m.get("id", "") or "").lower()
                if mid == norm or norm.endswith(f"/{mid}") or mid.endswith(f"/{norm}"):
                    inp = m.get("input", [])
                    if isinstance(inp, list) and "image" in inp:
                        return True
        return False

    def ensure_vision_input_in_models_json(self) -> int:
        """Добавляет 'image' в input[] для всех vision-capable моделей в models.json.

        W24/W26 fix: при старте Краба авто-починяет models.json если gateway стриппит image.
        Gateway может перезаписать models.json при собственном старте (race condition),
        поэтому дополнительно вызывается перед каждым photo-запросом
        (с последующим reload_openclaw_secrets).
        Возвращает количество исправленных моделей (0 = ничего не изменилось).
        """
        data = self._read_models_json()
        updated = 0
        for pdata in data.get("providers", {}).values():
            for m in pdata.get("models", []):
                mid = str(m.get("id", "") or "").lower()
                name = str(m.get("name", "") or "").lower()
                is_vision = any(p in mid or p in name for p in self._VISION_MODEL_PATTERNS)
                if not is_vision:
                    continue
                inp = m.get("input", [])
                if not isinstance(inp, list):
                    inp = ["text"]
                if "image" not in inp:
                    if "text" not in inp:
                        inp.append("text")
                    inp.append("image")
                    m["input"] = inp
                    updated += 1
        if updated:
            self._write_models_json(data)
            logger.info(
                "models_json_vision_input_patched",
                patched_count=updated,
            )
        return updated

    def _detect_semantic_error(self, text: str) -> dict[str, str] | None:
        """Детектор ложных успехов, когда backend вернул 200 с текстом ошибки."""
        payload = (text or "").strip()
        low = payload.lower()
        if not payload:
            return {"code": "lm_empty_stream", "message": "Пустой ответ от модели"}

        # Некоторые локальные модели могут вернуть служебный tool-трейс в контент вместо
        # нормального ответа (например `<tool_response>{"status":"error"}` + im-токены).
        # Такой ответ не должен уходить пользователю как есть.
        if "<tool_response>" in low and '"status": "error"' in low:
            return {
                "code": "lm_malformed_response",
                "message": "Локальная модель вернула служебный/битый ответ",
            }
        if "<|im_start|>" in low and "<|im_end|>" in low and '"status": "error"' in low:
            return {
                "code": "lm_malformed_response",
                "message": "Локальная модель вернула служебный/битый ответ",
            }

        semantic_patterns = [
            ("no models loaded", "model_not_loaded", "Локальная модель не загружена"),
            ("model unloaded", "model_not_loaded", "Локальная модель выгружена"),
            (
                "vision add-on is not loaded",
                "vision_addon_missing",
                "Локальная модель запущена без vision add-on",
            ),
            (
                "missing image config",
                "vision_addon_missing",
                "Локальная модель запущена без vision add-on",
            ),
            (
                "images were provided for processing",
                "vision_addon_missing",
                "Локальная модель запущена без vision add-on",
            ),
            ("<empty message>", "lm_empty_stream", "LM Studio вернула пустой поток"),
            ("empty message", "lm_empty_stream", "LM Studio вернула пустой поток"),
            ("stopiteration", "lm_empty_stream", "LM Studio вернула пустой поток"),
            (
                "model has crashed without additional information",
                "lm_model_crash",
                "Локальная модель LM Studio аварийно завершилась",
            ),
            (
                "the model has crashed without additional information",
                "lm_model_crash",
                "Локальная модель LM Studio аварийно завершилась",
            ),
            ("quota", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("429", "quota_exceeded", "Квота облачного ключа исчерпана"),
            ("api keys are not supported", "unsupported_key_type", "Неверный тип облачного ключа"),
            ("unauthenticated", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("invalid api key", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("forbidden", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("unauthorized", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("401", AUTH_UNAUTHORIZED_CODE, "Ошибка авторизации облачного ключа"),
            ("timeout", "provider_timeout", "Таймаут облачного провайдера"),
            (
                "an unknown error occurred",
                "gateway_unknown_error",
                "OpenClaw вернул неизвестную ошибку",
            ),
        ]
        for pattern, code, message in semantic_patterns:
            if pattern in low:
                return {"code": code, "message": message}
        return None

    @staticmethod
    def _semantic_from_provider_exception(exc: Exception) -> dict[str, str]:
        """
        Нормализует исключения провайдера в единый semantic error-контракт.

        Это нужно для консистентного fallback-поведения и корректной диагностики
        в `health/lite`/runtime badges даже когда OpenClaw отдал не текст ошибки,
        а HTTP-ошибку/исключение.
        """
        if isinstance(exc, ProviderAuthError):
            return {"code": AUTH_UNAUTHORIZED_CODE, "message": "Ошибка авторизации облачного ключа"}
        if isinstance(exc, ProviderError):
            low = str(exc).lower()
            if (
                "vision add-on is not loaded" in low
                or "missing image config" in low
                or "images were provided for processing" in low
            ):
                return {
                    "code": "vision_addon_missing",
                    "message": "Локальная модель запущена без vision add-on",
                }
            if "model unloaded" in low or "no models loaded" in low:
                return {"code": "model_not_loaded", "message": "Локальная модель выгружена"}
            code = "provider_timeout" if getattr(exc, "retryable", False) else "provider_error"
            return {"code": code, "message": str(exc) or "Ошибка провайдера"}
        return {"code": "transport_error", "message": str(exc) or "Ошибка транспорта"}

    def _build_retry_messages(self, messages_to_send: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Формирует компактный контекст для controlled retry.

        Почему:
        - при `EMPTY MESSAGE`/`model crashed` повтор с полным длинным контекстом
          часто воспроизводит ту же деградацию;
        - сжимаем историю до безопасного ядра: system + последние N сообщений.
        """
        if not messages_to_send:
            return []
        max_msgs = max(1, int(getattr(config, "RETRY_HISTORY_WINDOW_MESSAGES", 8) or 8))
        max_chars = max(400, int(getattr(config, "RETRY_HISTORY_WINDOW_MAX_CHARS", 4000) or 4000))
        per_message_max_chars = max(
            120, int(getattr(config, "RETRY_MESSAGE_MAX_CHARS", 1200) or 1200)
        )

        system_message = (
            messages_to_send[0]
            if messages_to_send and messages_to_send[0].get("role") == "system"
            else None
        )
        tail_source = messages_to_send[1:] if system_message else messages_to_send
        tail = tail_source[-max_msgs:]
        out: list[dict[str, Any]] = []
        if system_message:
            out.append(
                self._compact_message_for_retry(system_message, max_chars=per_message_max_chars)
            )
        out.extend(
            [
                self._compact_message_for_retry(message, max_chars=per_message_max_chars)
                for message in tail
            ]
        )
        compacted = self._apply_sliding_window(
            "semantic_retry",
            out,
            max_msgs=max(len(out), 1),
            max_chars=max_chars,
            trim_reason="semantic_retry_window",
        )
        if len(compacted) != len(messages_to_send) or self._messages_size(
            compacted
        ) != self._messages_size(messages_to_send):
            logger.warning(
                "retry_context_compacted",
                before_count=len(messages_to_send),
                after_count=len(compacted),
                before_chars=self._messages_size(messages_to_send),
                after_chars=self._messages_size(compacted),
                max_msgs=max_msgs,
                max_chars=max_chars,
                per_message_max_chars=per_message_max_chars,
            )
        return compacted

    @staticmethod
    def _truncate_middle_text(text: str, *, max_chars: int) -> str:
        """
        Сокращает длинный текст, сохраняя начало и конец.

        Это безопаснее для retry-контекста, чем слепо отрезать хвост:
        часто в конце реплики лежит самая свежая инструкция/ошибка.
        """
        payload = str(text or "")
        limit = max(1, int(max_chars))
        if len(payload) <= limit:
            return payload

        marker = "\n[...TRUNCATED MIDDLE...]\n"
        if limit <= len(marker) + 16:
            return payload[:limit]

        head_len = max(8, (limit - len(marker)) // 2)
        tail_len = max(8, limit - len(marker) - head_len)
        return f"{payload[:head_len]}{marker}{payload[-tail_len:]}"

    def _compact_message_for_retry(
        self, message: dict[str, Any], *, max_chars: int
    ) -> dict[str, Any]:
        """
        Поджимает отдельное сообщение для retry-бюджета.

        Нужен именно на уровне сообщения, потому что один огромный user-prompt
        может съесть весь retry budget даже при коротком списке сообщений.
        """
        if not isinstance(message, dict):
            return message

        cloned = dict(message)
        content = cloned.get("content")
        if isinstance(content, str):
            cloned["content"] = self._truncate_middle_text(content, max_chars=max_chars)
            return cloned
        if isinstance(content, list):
            compacted_parts: list[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    compacted_parts.append(part)
                    continue
                part_type = str(part.get("type", "") or "").strip().lower()
                if part_type == "text":
                    compacted_parts.append(
                        {
                            **part,
                            "text": self._truncate_middle_text(
                                str(part.get("text", "") or ""), max_chars=max_chars
                            ),
                        }
                    )
                    continue
                compacted_parts.append(part)
            cloned["content"] = compacted_parts
        return cloned

    @staticmethod
    def _strip_image_parts_for_text_route(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Удаляет image-блоки из истории для текстового маршрута.

        Почему:
        - после фото-сообщений в истории остаются multimodal parts;
        - на чисто текстовой модели это может вызывать ошибки вида
          "Vision add-on is not loaded..." даже при новом текстовом запросе.
        """
        sanitized: list[dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                sanitized.append(msg)
                continue

            text_chunks: list[str] = []
            had_image = False
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "") or "").strip().lower()
                if part_type == "text":
                    text_value = str(part.get("text", "") or "").strip()
                    if text_value:
                        text_chunks.append(text_value)
                    continue
                if part_type in {"image_url", "image_file", "input_image"}:
                    had_image = True

            replacement = "\n".join(text_chunks).strip()
            if had_image:
                replacement = (
                    replacement + "\n" if replacement else ""
                ) + "[Изображение в контексте пропущено для текстовой модели]"
            cloned = dict(msg)
            cloned["content"] = replacement
            sanitized.append(cloned)
        return sanitized

    async def _switch_cloud_tier(self, tier: str, *, reason: str) -> dict[str, Any]:
        """Переключает active tier ключа в OpenClaw models.json и делает secrets reload."""
        target_tier = "paid" if tier == "paid" else "free"
        key_value = self.gemini_tiers.get(target_tier, "")
        if not key_value:
            return {"ok": False, "error": f"missing_{target_tier}_key"}
        if not is_ai_studio_key(key_value):
            return {"ok": False, "error": f"invalid_{target_tier}_key_type"}

        if not self._set_google_key_in_models(key_value):
            return {"ok": False, "error": "models_json_write_failed"}

        reload_result = await reload_openclaw_secrets()
        if not reload_result.get("ok"):
            return {
                "ok": False,
                "error": "secrets_reload_failed",
                "reload": reload_result,
            }

        previous = self.active_tier
        self.active_tier = target_tier
        self._cloud_tier_state["active_tier"] = target_tier
        self._sync_last_runtime_route_active_tier()
        self._cloud_tier_state["switches"] = int(self._cloud_tier_state.get("switches", 0)) + 1
        self._cloud_tier_state["last_switch_at"] = int(time.time())
        self._cloud_tier_state["last_recovery_action"] = f"switch_to_{target_tier}"

        logger.info(
            "cloud_tier_switched",
            previous_tier=previous,
            new_tier=target_tier,
            reason=reason,
        )
        return {
            "ok": True,
            "previous_tier": previous,
            "new_tier": target_tier,
            "reload": reload_result,
        }

    def _resolve_provider_api_key(self, provider: str) -> tuple[str, str]:
        """Совместимый helper для модулей, которым нужен ключ провайдера."""
        provider_low = provider.strip().lower()
        if provider_low == "google":
            key = self.gemini_tiers.get(self.active_tier, "")
            src = f"env:GEMINI_API_KEY_{self.active_tier.upper()}"
            return key, src
        if provider_low == "openai":
            key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
            return key, "env:OPENAI_API_KEY"
        return "", "missing"

    @staticmethod
    def _normalize_usage_snapshot(usage: dict[str, Any] | None) -> dict[str, int] | None:
        """
        Нормализует usage payload к одному формату.

        Почему это нужно:
        - разные OpenAI-compatible прокси могут отдавать usage в SSE не на каждом чанке;
        - нам важно учитывать только осмысленный snapshot, а не пустые `{}`.
        """
        payload = usage or {}
        prompt_tokens = int(payload.get("prompt_tokens", payload.get("input_tokens", 0)) or 0)
        completion_tokens = int(
            payload.get("completion_tokens", payload.get("output_tokens", 0)) or 0
        )
        total_tokens = int(payload.get("total_tokens", 0) or 0) or (
            prompt_tokens + completion_tokens
        )
        if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _extract_text_from_message(message: dict[str, Any]) -> str:
        """
        Извлекает текстовую часть message для грубой оценки usage.

        Важно:
        - считаем только текстовые части;
        - image/audio payload в оценку не включаем, чтобы не завышать токены.
        """
        content = message.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        chunks: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "") or "").strip().lower()
            if part_type in {"text", "input_text"}:
                chunks.append(str(part.get("text", "") or ""))
        return "\n".join(chunk for chunk in chunks if chunk)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        """
        Грубая оценка числа токенов без внешнего tokenizer.

        Почему так:
        - OpenClaw/Gateway в stream-режиме может не вернуть usage вообще;
        - для ops/runtime-аналитики лучше иметь честную approximate-оценку,
          чем вечный `no_usage_yet`.
        """
        compact = " ".join(str(text or "").split())
        if not compact:
            return 0
        char_based = max(1, (len(compact) + 3) // 4)
        word_based = len(compact.split())
        return min(len(compact), max(char_based, word_based))

    def _estimate_usage_snapshot(
        self,
        messages: list[dict[str, Any]],
        response_text: str,
    ) -> dict[str, int] | None:
        """
        Строит approximate usage, если backend не прислал нативный usage.
        """
        prompt_text = "\n".join(
            fragment
            for fragment in (self._extract_text_from_message(message) for message in messages)
            if fragment
        )
        prompt_tokens = self._estimate_text_tokens(prompt_text)
        completion_tokens = self._estimate_text_tokens(response_text)
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens <= 0:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    @classmethod
    def _build_native_lm_input(
        cls,
        messages: list[dict[str, Any]],
        *,
        previous_response_id: str = "",
    ) -> str:
        """
        Собирает `input` для нативного LM Studio `/api/v1/chat`.

        Почему это отдельно:
        - нативный endpoint stateful и продолжает диалог через `previous_response_id`;
        - при cold-start нужно компактно вложить system prompt и уже накопленный
          текстовый контекст;
        - на follow-up с `previous_response_id` передаём только новый user turn.
        """
        normalized_prev = str(previous_response_id or "").strip()

        latest_user = ""
        system_prompt = ""
        dialogue_lines: list[str] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            text = cls._extract_text_from_message(item).strip()
            if not text:
                continue
            if role == "system" and not system_prompt:
                system_prompt = text
                continue
            if role == "user":
                latest_user = text
                dialogue_lines.append(f"Пользователь: {text}")
            elif role == "assistant":
                dialogue_lines.append(f"Ассистент: {text}")
            else:
                dialogue_lines.append(f"{role or 'Сообщение'}: {text}")

        if normalized_prev:
            return latest_user

        sections: list[str] = []
        if system_prompt:
            sections.append(f"Системная инструкция:\n{system_prompt}")
        if dialogue_lines:
            sections.append("Контекст диалога:\n" + "\n\n".join(dialogue_lines))
        return "\n\n".join(section for section in sections if section).strip()

    @staticmethod
    def _extract_native_lm_output_text(payload: dict[str, Any]) -> str:
        """
        Извлекает пользовательский текст из ответа `/api/v1/chat`.

        Важно:
        - reasoning-блоки намеренно игнорируем;
        - берём только финальные message/output_text фрагменты.
        """
        direct_output_text = str(payload.get("output_text", "") or "").strip()
        if direct_output_text:
            return direct_output_text

        output = payload.get("output")
        if not isinstance(output, list):
            return ""

        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            if item_type not in {"message", "output_text"}:
                continue
            content = item.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text:
                    chunks.append(text)
                continue
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = str(part.get("type", "") or "").strip().lower()
                    if part_type not in {"text", "output_text"}:
                        continue
                    text = str(part.get("text", "") or "").strip()
                    if text:
                        chunks.append(text)
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    @staticmethod
    def _extract_native_lm_stats(payload: dict[str, Any]) -> dict[str, int]:
        """
        Нормализует `stats` из LM Studio `/api/v1/chat`.

        Почему это важно:
        - в текущем API нет явного `finish_reason`, как в `/v1/chat/completions`;
        - зато есть `stats.total_output_tokens`, по которому видно, что ответ
          упёрся в установленный лимит.
        """
        raw_stats = payload.get("stats")
        if not isinstance(raw_stats, dict):
            return {}
        normalized: dict[str, int] = {}
        for key in ("input_tokens", "total_output_tokens", "reasoning_output_tokens"):
            try:
                normalized[key] = int(raw_stats.get(key) or 0)
            except (TypeError, ValueError):
                normalized[key] = 0
        return normalized

    @staticmethod
    def _merge_continuation_text(base_text: str, extra_text: str) -> str:
        """
        Склеивает основной текст и автопродолжение, убирая простой overlap.

        Почему не просто `base + "\\n\\n" + extra`:
        - модель иногда повторяет конец предыдущего блока или заголовок;
        - даже грубый overlap-search заметно улучшает читаемость Telegram-ответа.
        """
        base = str(base_text or "").rstrip()
        extra = str(extra_text or "").lstrip()
        if not base:
            return extra
        if not extra:
            return base
        max_overlap = min(len(base), len(extra), 240)
        for overlap in range(max_overlap, 24, -1):
            if base[-overlap:] == extra[:overlap]:
                return (base + extra[overlap:]).strip()
        return f"{base}\n\n{extra}".strip()

    @staticmethod
    def _native_lm_hits_output_cap(
        stats: dict[str, int],
        *,
        max_output_tokens: int | None,
        margin: int = 8,
    ) -> bool:
        """
        Определяет, что нативный ответ, вероятно, уткнулся в лимит вывода.

        Эвристика:
        - LM Studio `/api/v1/chat` не отдаёт `finish_reason`;
        - если `total_output_tokens` почти равен `max_output_tokens`, ответ почти
          наверняка обрезан по лимиту, а не завершён естественно.
        """
        if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            return False
        total_output_tokens = int((stats or {}).get("total_output_tokens") or 0)
        if total_output_tokens <= 0:
            return False
        safe_margin = max(0, int(margin))
        threshold = max(1, max_output_tokens - safe_margin)
        return total_output_tokens >= threshold

    async def _direct_lm_native_chat(
        self,
        *,
        client: httpx.AsyncClient,
        chat_id: str,
        messages_to_send: list[dict[str, Any]],
        model_hint: str,
        max_output_tokens: int | None = None,
    ) -> str | None:
        """
        Прямой нативный запрос в LM Studio `/api/v1/chat`.

        Возвращает готовый текст или `None`, если endpoint не дал финального
        assistant message. В таком случае верхний слой безопасно уходит в compat fallback.
        """
        state = self._lm_native_chat_state.get(chat_id) or {}
        previous_response_id = ""
        normalized_model = str(model_hint or "").strip()
        if str(state.get("model", "") or "").strip() == normalized_model:
            previous_response_id = str(state.get("response_id", "") or "").strip()
        elif state:
            self._lm_native_chat_state.pop(chat_id, None)

        async def _run_once(
            prev_response_id: str,
            *,
            input_override: str = "",
        ) -> tuple[str, str, dict[str, int]]:
            input_payload = str(input_override or "").strip()
            if not input_payload:
                input_payload = self._build_native_lm_input(
                    messages_to_send,
                    previous_response_id=prev_response_id,
                )
            if not input_payload:
                return "", "", {}
            payload: dict[str, Any] = {
                "model": normalized_model or "local",
                "input": input_payload,
            }
            if prev_response_id:
                payload["previous_response_id"] = prev_response_id
            if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                payload["max_output_tokens"] = max_output_tokens
            reasoning_mode = (
                str(getattr(config, "LM_STUDIO_NATIVE_REASONING_MODE", "off") or "").strip().lower()
            )
            if reasoning_mode:
                payload["reasoning"] = reasoning_mode
            response = await client.post("/api/v1/chat", json=payload)
            if response.status_code != 200:
                return "", "", {}
            data = response.json()
            return (
                self._extract_native_lm_output_text(data),
                str(data.get("response_id", "") or "").strip(),
                self._extract_native_lm_stats(data),
            )

        text, response_id, stats = await _run_once(previous_response_id)
        if not text and previous_response_id:
            # После рестарта LM Studio прежний `response_id` может стать недействительным.
            # Делаем один stateless retry, а не сохраняем сломанное состояние.
            self._lm_native_chat_state.pop(chat_id, None)
            text, response_id, stats = await _run_once("")

        if not text:
            return None

        merged_text = text
        current_response_id = response_id
        auto_continue_rounds = max(
            0,
            int(getattr(config, "LM_STUDIO_NATIVE_AUTO_CONTINUE_MAX_ROUNDS", 2) or 0),
        )
        output_cap_margin = max(
            0,
            int(getattr(config, "LM_STUDIO_NATIVE_OUTPUT_CAP_MARGIN", 8) or 0),
        )
        continuation_prompt = (
            "Продолжай ответ с того места, где остановился. "
            "Не повторяй уже написанное. Закончи мысль и список полностью."
        )

        for _ in range(auto_continue_rounds):
            if not current_response_id:
                break
            if not self._native_lm_hits_output_cap(
                stats,
                max_output_tokens=max_output_tokens,
                margin=output_cap_margin,
            ):
                break
            next_text, next_response_id, next_stats = await _run_once(
                current_response_id,
                input_override=continuation_prompt,
            )
            next_text = str(next_text or "").strip()
            if not next_text:
                break
            if self._detect_semantic_error(next_text):
                break
            merged_text = self._merge_continuation_text(merged_text, next_text)
            current_response_id = next_response_id or current_response_id
            stats = next_stats

        if current_response_id:
            self._lm_native_chat_state[chat_id] = {
                "response_id": current_response_id,
                "model": normalized_model,
            }
        return merged_text

    def _commit_usage_snapshot(
        self,
        usage: dict[str, Any] | None,
        *,
        model_id: str,
        tool_calls_count: int = 0,
        channel: str = "",
        is_fallback: bool = False,
        context_tokens: int = 0,
    ) -> None:
        """
        Коммитит usage один раз на completion и зеркалит его в Cost Analytics.

        Это восстанавливает связку, потерянную после рефакторинга:
        - `_usage_stats` остаётся совместимым источником для старых API;
        - `cost_analytics` начинает видеть реальные runtime-вызовы.
        """
        normalized = self._normalize_usage_snapshot(usage)
        if not normalized:
            return

        self._usage_stats["input_tokens"] += int(normalized["prompt_tokens"])
        self._usage_stats["output_tokens"] += int(normalized["completion_tokens"])
        self._usage_stats["total_tokens"] += int(normalized["total_tokens"])

        try:
            from .model_manager import model_manager  # lazy import

            analytics = getattr(model_manager, "cost_analytics", None)
            if analytics and hasattr(analytics, "record_usage"):
                analytics.record_usage(
                    normalized,
                    model_id=model_id,
                    tool_calls_count=tool_calls_count,
                    channel=channel,
                    is_fallback=is_fallback,
                    context_tokens=context_tokens,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cost_analytics_record_usage_failed",
                model=model_id,
                error=str(exc),
            )

    async def _openclaw_completion_once(
        self,
        *,
        model_id: str,
        messages_to_send: list[dict[str, Any]],
        max_output_tokens: int | None = None,
        has_photo: bool = False,
        allow_auth_retry: bool = True,
        disable_tools: bool = False,
    ) -> str:
        """Один запрос к OpenClaw (stream=false) с буферизацией ответа.

        КРИТИЧЕСКИЙ ФИХ: stream=True в google-antigravity Antigravity gateway возвращает
        только 'data: [DONE]' без контента — это приводит к lm_empty_stream на всех каналах.
        stream=False, напротив, работает корректно и возвращает полный JSON-ответ.
        """
        from .mcp_client import mcp_manager  # lazy import

        _dt = disable_tools or getattr(self, "_request_disable_tools", False)
        tools = [] if _dt else await mcp_manager.get_tool_manifest()

        # Per-team manifest filter: если активен swarm-контекст, оставляем
        # только разрешённые команде tools (см. core/swarm_tool_allowlist.py).
        if tools:
            try:
                from .core.swarm_tool_allowlist import (
                    filter_tools_for_team,
                    get_current_team,
                )

                _team = get_current_team()
                if _team:
                    tools = filter_tools_for_team(tools, _team)
            except Exception as _flt_exc:  # noqa: BLE001
                logger.warning("swarm_tool_filter_failed", error=str(_flt_exc))

        # ФИКС 2026.3.x: новый OpenClaw gateway требует "openclaw" или "openclaw/<agentId>"
        # вместо прямого имени провайдер/модель. Gateway сам маршрутизирует запрос
        # через агентскую конфигурацию (agents.defaults.model.primary + fallbacks).
        # model_id по-прежнему используется для логирования и трекинга fallback-цепи в Крабе.
        payload = {
            "messages": messages_to_send,
            "stream": False,  # ФИКС: streaming даёт пустой [DONE], JSON работает корректно
            "model": "openclaw",
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if isinstance(max_output_tokens, int) and max_output_tokens > 0:
            # Совместимый лимит длины ответа для OpenAI-совместимых /v1/chat/completions.
            payload["max_tokens"] = max_output_tokens

        full_response = ""
        usage_snapshot: dict[str, int] | None = None
        retry_after_token_refresh = False
        request_timeout: httpx.Timeout | None = None
        read_timeout_sec = self._resolve_buffered_read_timeout_sec(
            model_id=model_id,
            has_photo=has_photo,
        )
        if read_timeout_sec is not None:
            request_timeout = httpx.Timeout(
                connect=30.0,
                read=read_timeout_sec,
                write=30.0,
                pool=30.0,
            )
            logger.info(
                "openclaw_buffered_timeout_budget",
                model=model_id,
                read_timeout_sec=read_timeout_sec,
                has_photo=bool(has_photo),
            )

        # Используем обычный POST (не streaming), чтобы получить единый JSON-ответ
        _t0 = time.monotonic()
        # Correlation ID: пробрасываем request_id из structlog contextvars в
        # Gateway через X-Request-ID header — упрощает корреляцию логов
        # bridge↔Gateway. Если request_id нет (например, вызов вне message
        # handler), header не выставляется.
        _extra_headers: dict[str, str] | None = None
        try:
            from structlog.contextvars import get_contextvars as _get_ctxvars

            _rid = _get_ctxvars().get("request_id")
            if _rid:
                _extra_headers = {"X-Request-ID": str(_rid)}
        except Exception:  # noqa: BLE001
            pass
        try:
            response = await self._http_client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=request_timeout,
                headers=_extra_headers,
            )
        except httpx.TimeoutException as exc:
            # Пробрасываем как ProviderError(retryable=True), чтобы fallback-loop
            # (for attempt in range(4)) поймал его через except (ProviderAuthError, ProviderError)
            # и попробовал следующую модель в цепочке.
            metrics.inc("llm_error")
            raise ProviderError(
                message=f"timeout waiting for {model_id}: {exc}",
                user_message="Таймаут провайдера",
                retryable=True,
            )
        except (httpx.ConnectError, httpx.RequestError) as exc:
            # Gateway был down → ConnectError. Оборачиваем в ProviderError(retryable=True)
            # чтобы 4-attempt retry loop поймал его и попробовал local fallback / cloud retry.
            # До этого фикса ConnectError вылетал из loop напрямую — retry не происходил.
            metrics.inc("llm_error")
            logger.warning("openclaw_connect_error_retryable", model=model_id, error=str(exc))
            raise ProviderError(
                message=f"connect error for {model_id}: {exc}",
                user_message="Провайдер временно недоступен",
                retryable=True,
            )
        logger.info("openclaw_response_status", status=response.status_code, model=model_id)

        if response.status_code != 200:
            body_str = response.text
            logger.error("openclaw_api_error", status=response.status_code, body=body_str)
            metrics.inc("llm_error")
            if response.status_code in (401, 403):
                if allow_auth_retry and self._refresh_gateway_token_from_runtime():
                    retry_after_token_refresh = True
                else:
                    raise ProviderAuthError(
                        message=f"status={response.status_code} body={body_str[:500]}",
                        user_message="Ошибка авторизации API",
                    )
            elif response.status_code == 429:
                raise RouterQuotaError(
                    user_message="Квота исчерпана. Попробуй позже или переключись на локальную модель (!model local).",
                    details={"status": 429},
                )
            elif response.status_code >= 500:
                raise ProviderError(
                    message=f"status={response.status_code} body={body_str[:500]}",
                    user_message="Провайдер временно недоступен",
                    retryable=True,
                )
            else:
                raise ProviderError(
                    message=f"status={response.status_code} body={body_str[:500]}",
                    user_message=f"Ошибка API: {response.status_code}",
                    retryable=False,
                )

        if retry_after_token_refresh:
            logger.warning(
                "openclaw_retry_after_gateway_token_refresh",
                model=model_id,
            )
            return await self._openclaw_completion_once(
                model_id=model_id,
                messages_to_send=messages_to_send,
                max_output_tokens=max_output_tokens,
                has_photo=has_photo,
                allow_auth_retry=False,
            )

        # Читаем единый JSON-ответ (stream=False)
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = {}

        normalized_usage = self._normalize_usage_snapshot(data.get("usage"))
        if normalized_usage:
            usage_snapshot = normalized_usage

        choices = data.get("choices") or [{}]
        message_obj = choices[0].get("message") or {}
        full_response = message_obj.get("content", "") or ""
        tool_calls = message_obj.get("tool_calls")

        # Обработка tool_calls
        if tool_calls:
            logger.info("openclaw_tool_calls_detected", count=len(tool_calls))
            # Добавляем сообщение ассистента с tool_calls в историю для этого запроса
            messages_to_send.append(message_obj)

            for tc in tool_calls:
                tc_id = tc.get("id")
                func = tc.get("function") or {}
                func_name = func.get("name")
                import json

                try:
                    args = json.loads(func.get("arguments", "{}"))
                except Exception:
                    args = {}

                # Трекинг для Telegram progress notices
                tool_entry = {
                    "name": func_name,
                    "status": "running",
                    "started_at": time.monotonic(),
                }
                self._active_tool_calls.append(tool_entry)

                logger.info("executing_mcp_tool", name=func_name, args=args)
                tool_result = await mcp_manager.call_tool_unified(func_name, args)
                tool_entry["status"] = "done"

                # Добавляем результат выполнения инструмента
                messages_to_send.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": str(tool_result),
                    }
                )

            # Рекурсивный вызов для получения финального ответа после инструментов
            return await self._openclaw_completion_once(
                model_id=model_id,
                messages_to_send=messages_to_send,
                max_output_tokens=max_output_tokens,
                has_photo=has_photo,
                allow_auth_retry=allow_auth_retry,
            )

        if not usage_snapshot and full_response.strip():
            usage_snapshot = self._estimate_usage_snapshot(messages_to_send, full_response)
            if usage_snapshot:
                logger.info(
                    "openclaw_usage_estimated_from_text",
                    model=model_id,
                    prompt_tokens=usage_snapshot["prompt_tokens"],
                    completion_tokens=usage_snapshot["completion_tokens"],
                    total_tokens=usage_snapshot["total_tokens"],
                )
        self._commit_usage_snapshot(
            usage_snapshot,
            model_id=model_id,
            tool_calls_count=len(self._active_tool_calls),
            channel="telegram",  # можно расширить для других каналов
        )
        # Инструментирование: замер задержки и счётчик успешных вызовов
        _elapsed_ms = (time.monotonic() - _t0) * 1000
        metrics.add_latency(_elapsed_ms)
        metrics.inc("llm_success")
        return full_response.strip()

    async def _resolve_local_model_for_retry(
        self,
        model_manager: Any,
        preferred: str,
        *,
        has_photo: bool = False,
    ) -> str | None:
        """Подбирает локальную модель для аварийного retry."""
        if model_manager.is_local_model(preferred):
            return preferred
        preferred_local = await model_manager.resolve_preferred_local_model(has_photo=has_photo)
        if preferred_local:
            return preferred_local
        if hasattr(model_manager, "_local_candidates"):
            try:
                candidates = await model_manager._local_candidates(has_photo=has_photo)  # noqa: SLF001
            except Exception:
                candidates = []
            if candidates:
                return str(candidates[0][0])
        if not model_manager._models_cache:
            await model_manager.discover_models()
        local_candidates: list[tuple[str, Any]] = []
        for model_id, info in model_manager._models_cache.items():
            if not model_manager.is_local_model(model_id):
                continue
            if hasattr(model_manager, "_is_chat_capable_local_model"):
                try:
                    if not bool(model_manager._is_chat_capable_local_model(model_id, info)):  # noqa: SLF001
                        continue
                except Exception:
                    pass
            if has_photo and not bool(getattr(info, "supports_vision", False)):
                continue
            local_candidates.append((model_id, info))
        local_candidates.sort(key=lambda item: float(getattr(item[1], "size_gb", 0.0) or 0.0))
        if local_candidates:
            return str(local_candidates[0][0])
        return None

    def _is_cloud_candidate_usable(self, model_id: str, model_manager: Any) -> bool:
        """
        Проверяет, что облачный кандидат действительно пригоден для runtime retry.

        Ключевой кейс:
        - `openai/*` без OPENAI_API_KEY нельзя выбирать как recovery-кандидат,
          иначе получаем ложный цикл 401 и пропускаем рабочий local/cloud путь.
        """
        candidate = str(model_id or "").strip()
        if not candidate:
            return False
        if model_manager.is_local_model(candidate):
            return False

        provider = self._provider_from_model(candidate)
        if provider == "openai":
            return bool(str(os.getenv("OPENAI_API_KEY", "") or "").strip())
        return True

    async def _pick_cloud_retry_model(
        self,
        *,
        model_manager: Any,
        current_model: str,
        has_photo: bool,
    ) -> str:
        """Возвращает облачный retry-кандидат (или пустую строку, если кандидата нет).

        ФИКС W16: при has_photo=True кандидат ОБЯЗАН поддерживать vision.
        CLI-провайдеры (codex-cli, gemini-cli, opencode) не умеют multimodal —
        возвращать их при photo-запросе значит гарантированно потерять вложение.
        """
        runtime_chain: list[str] = []
        runtime_primary = str(get_runtime_primary_model() or "").strip()
        if runtime_primary:
            runtime_chain.append(runtime_primary)
        runtime_chain.extend(get_runtime_fallback_models())
        for candidate in runtime_chain:
            normalized = str(candidate or "").strip()
            if not normalized or normalized == str(current_model or "").strip():
                continue
            # Если запрос с фото — пропускаем модели без vision-поддержки.
            if has_photo and not _supports_vision(normalized):
                logger.debug(
                    "photo_retry_skip_non_vision_candidate",
                    candidate=normalized,
                    current_model=current_model,
                )
                continue
            if self._is_cloud_candidate_usable(normalized, model_manager):
                return normalized
        if not hasattr(model_manager, "get_best_cloud_model"):
            return ""
        candidate = str(await model_manager.get_best_cloud_model(has_photo=has_photo) or "").strip()
        if not candidate or candidate == str(current_model or "").strip():
            return ""
        # Финальная проверка: get_best_cloud_model тоже может вернуть non-vision кандидата.
        if has_photo and not _supports_vision(candidate):
            logger.warning(
                "photo_retry_best_cloud_candidate_not_vision_capable",
                candidate=candidate,
                current_model=current_model,
            )
            return ""
        if not self._is_cloud_candidate_usable(candidate, model_manager):
            return ""
        return candidate

    async def _pick_vision_cloud_model(
        self,
        *,
        model_manager: Any,
        current_model: str,
    ) -> str:
        """Ищет первый vision-capable cloud-кандидат в runtime chain.

        Используется при photo-запросе к CLI-провайдеру, который не умеет multimodal.
        Возвращает пустую строку, если подходящего кандидата нет.
        """
        runtime_chain: list[str] = []
        runtime_primary = str(get_runtime_primary_model() or "").strip()
        if runtime_primary:
            runtime_chain.append(runtime_primary)
        runtime_chain.extend(get_runtime_fallback_models())
        for candidate in runtime_chain:
            normalized = str(candidate or "").strip()
            if not normalized or normalized == str(current_model or "").strip():
                continue
            if _supports_vision(normalized) and self._is_cloud_candidate_usable(
                normalized, model_manager
            ):
                return normalized
        return ""

    @staticmethod
    def _allow_alt_local_vision_recovery() -> bool:
        """
        Разрешён ли авто-переход на альтернативную локальную vision-модель.

        Почему ограничиваем:
        - при `LOCAL_PREFERRED_VISION_MODEL=auto` пользователь ожидает, что фото
          уйдёт в cloud fallback, если text primary не умеет vision;
        - без этого recovery-path молча выгружает Nemotron и поднимает случайный
          маленький VL-кандидат, что даёт неожиданный язык/качество ответа.
        """
        preferred = str(getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or "").strip().lower()
        return preferred not in {"", "auto"}

    def _should_skip_local_photo_route(
        self,
        *,
        selected_model: str,
        model_manager: Any,
        has_photo: bool,
        force_cloud: bool,
    ) -> bool:
        """
        Нужно ли жёстко увести фото-запрос из локального маршрута в cloud.

        Почему это важно:
        - при `LOCAL_PREFERRED_VISION_MODEL=auto` пользователь ожидает, что фото
          не выгрузит текстовый primary-маршрут ради случайной маленькой vision-модели;
        - на практике это приводило к автоподгрузке `qwen2-vl` и ответам на
          английском вместо ожидаемого локального/облачного русского контура.
        """
        if force_cloud or not has_photo:
            return False
        if not str(selected_model or "").strip():
            return False
        if not model_manager.is_local_model(selected_model):
            return False
        return not self._allow_alt_local_vision_recovery()

    async def _direct_lm_fallback(
        self,
        *,
        chat_id: str,
        messages_to_send: list[dict[str, Any]],
        model_hint: str,
        has_photo: bool = False,
        max_output_tokens: int | None = None,
    ) -> str | None:
        """Прямой fallback в LM Studio (минуя OpenClaw)."""
        if not config.LM_STUDIO_URL:
            return None
        if not await is_lm_studio_available(config.LM_STUDIO_URL, timeout=5.0):
            return None

        messages_for_lm = self._apply_local_route_history_budget(
            chat_id,
            messages_to_send,
            has_photo=has_photo,
            trim_reason="local_direct_fallback",
        )

        try:
            async with httpx.AsyncClient(
                base_url=str(config.LM_STUDIO_URL or "").rstrip("/"),
                timeout=120,
                headers=build_lm_studio_auth_headers(
                    api_key=getattr(config, "LM_STUDIO_API_KEY", ""),
                )
                or None,
                verify=False,
                trust_env=False,
            ) as client:
                if not has_photo:
                    native_text = await self._direct_lm_native_chat(
                        client=client,
                        chat_id=chat_id,
                        messages_to_send=messages_for_lm,
                        model_hint=model_hint,
                        max_output_tokens=max_output_tokens,
                    )
                    if native_text:
                        return native_text

                payload = {
                    "messages": messages_for_lm,
                    "stream": False,
                    "model": model_hint if model_hint else "local",
                }
                if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                    payload["max_tokens"] = max_output_tokens
                resp = await client.post("/v1/chat/completions", json=payload)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                semantic = self._detect_semantic_error(content)
                if semantic:
                    return None
                return content
        except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError):
            return None

    def _finalize_chat_response(self, chat_id: str, final_response: str) -> None:
        """Сохраняет ответ ассистента в историю и кэш."""
        self._sessions[chat_id].append({"role": "assistant", "content": final_response})
        self._sessions[chat_id] = self._apply_sliding_window(chat_id, self._sessions[chat_id])
        try:
            history_cache.set(
                f"chat_history:{chat_id}",
                json.dumps(self._sessions[chat_id], ensure_ascii=False),
                ttl=HISTORY_CACHE_TTL,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("history_cache_set_failed", chat_id=chat_id, error=str(exc))

    async def health_check(self) -> bool:
        """Проверка доступности OpenClaw."""
        try:
            response = await self._http_client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.error("openclaw_health_check_failed", error=str(exc))
            return False

    async def wait_for_healthy(self, timeout: int = 90) -> bool:
        """Ожидает доступности OpenClaw (polling). Timeout configurable via OPENCLAW_HEALTH_WAIT_TIMEOUT_SEC."""
        started = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - started) < timeout:
            if await self.health_check():
                elapsed = asyncio.get_running_loop().time() - started
                if elapsed > 30:
                    logger.info("openclaw_slow_startup", elapsed_sec=round(elapsed, 1))
                logger.info("openclaw_healthy_verified")
                return True
            await asyncio.sleep(1.0)
        logger.warning("openclaw_wait_timeout", timeout=timeout)
        return False

    async def warmup_runtime_route(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Выполняет короткий runtime-probe, чтобы после рестарта появился живой route-truth.

        Почему это нужно:
        - `/api/health/lite` и userbot self-check раньше видели пустой
          `last_runtime_route` до первого реального пользовательского запроса;
        - из-за этого UI показывал stale `current_primary_broken`, хотя
          `openai-codex/gpt-5.4` уже отвечал через gateway;
        - probe идёт через тот же production routing-контур, но с коротким
          служебным prompt и отдельным временным chat_id, который потом очищается.
        """
        existing = self.get_last_runtime_route()
        existing_ts = int(existing.get("timestamp") or 0)
        existing_is_fresh = existing_ts > 0 and (int(time.time()) - existing_ts) <= 300
        if (
            not force_refresh
            and existing.get("status") == "ok"
            and existing_is_fresh
            and str(existing.get("channel") or "").strip()
        ):
            return {
                "ok": True,
                "skipped": True,
                "reason": "recent_runtime_route",
                "route": existing,
            }

        if not await self.health_check():
            return {
                "ok": False,
                "skipped": True,
                "reason": "gateway_unhealthy",
                "route": existing,
            }

        runtime_primary = str(get_runtime_primary_model() or "").strip()
        force_cloud_probe = not runtime_primary.lower().startswith("lmstudio/")
        probe_chat_id = "__runtime_route_warmup__"
        preview_parts: list[str] = []

        try:
            async for chunk in self.send_message_stream(
                message="Технический runtime warmup. Ответь только: OK.",
                chat_id=probe_chat_id,
                system_prompt="Служебный runtime warmup-probe. Ответь только одним словом: OK.",
                force_cloud=force_cloud_probe,
                max_output_tokens=8,
            ):
                piece = str(chunk or "").strip()
                if piece:
                    preview_parts.append(piece)
                if len(" ".join(preview_parts)) >= 80:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("openclaw_runtime_route_warmup_failed", error=str(exc))
            return {
                "ok": False,
                "skipped": False,
                "reason": "warmup_exception",
                "error": str(exc),
                "route": self.get_last_runtime_route(),
            }
        finally:
            self.clear_session(probe_chat_id)

        # После cold start warmup может первым зафиксировать cloud-route ещё до
        # truthful runtime-check. Для Gemini подтягиваем effective tier из
        # runtime-моделей сразу здесь, чтобы стартовый route/log не залипал на
        # stale `free`, если фактически уже используется paid key.
        if force_cloud_probe:
            current_key_state = self._effective_runtime_google_key_state()
            current_key_tier = str(current_key_state.get("tier") or "").strip().lower()
            if (
                current_key_tier in {"free", "paid"}
                and current_key_tier != str(self.active_tier or "").strip().lower()
            ):
                self.active_tier = current_key_tier
                self._cloud_tier_state["active_tier"] = current_key_tier
            self._sync_last_runtime_route_active_tier()

        route = self.get_last_runtime_route()
        return {
            "ok": str(route.get("status") or "").strip().lower() == "ok",
            "skipped": False,
            "reason": "warmup_completed",
            "response_preview": " ".join(preview_parts).strip()[:120],
            "route": route,
        }

    async def send_message_stream(
        self,
        message: str,
        chat_id: str,
        system_prompt: Optional[str] = None,
        images: Optional[List[str]] = None,
        force_cloud: bool = False,
        preferred_model: Optional[str] = None,
        max_output_tokens: int | None = None,
        disable_tools: bool = False,
    ) -> AsyncIterator[str]:
        """
        Отправляет сообщение в OpenClaw с recovery policy.

        Recovery policy:
        1) текущий маршрут,
        2) при quota free -> попытка switch paid,
        3) fallback по live runtime-цепочке OpenClaw,
        4) fallback на локальную модель,
        5) прямой LM Studio fallback (если force_cloud=False).
        """
        # Sentry Performance Monitoring: обёртка-транзакция для P95 latency
        # по gateway LLM call. Graceful — no-op если sentry_sdk не установлен
        # или init пропущен (dev env без SENTRY_DSN).
        _txn_name = f"openclaw_{preferred_model or 'auto'}"
        _txn_cm = _sentry_txn(op="llm.call", name=_txn_name)
        _txn_cm.__enter__()
        # LLM latency histogram: старт таймера; observe в finally ниже.
        _llm_call_start_perf = time.perf_counter()
        try:
            _sentry_tag("chat_id", str(chat_id))
            _sentry_tag("model", str(preferred_model or "auto"))
            _sentry_tag("force_cloud", "1" if force_cloud else "0")
            _sentry_tag("has_images", "1" if images else "0")
        except Exception:  # noqa: BLE001
            pass

        self._request_disable_tools = disable_tools
        self._active_tool_calls.clear()
        if chat_id not in self._sessions:
            cached = history_cache.get(f"chat_history:{chat_id}")
            if cached:
                try:
                    restored_messages = json.loads(cached)
                    sanitized_messages, changed = self._sanitize_session_history(restored_messages)
                    self._sessions[chat_id] = sanitized_messages
                    logger.info(
                        "history_restored_from_cache",
                        chat_id=chat_id,
                        messages=len(self._sessions[chat_id]),
                    )
                    if changed:
                        try:
                            history_cache.set(
                                f"chat_history:{chat_id}",
                                json.dumps(self._sessions[chat_id], ensure_ascii=False),
                                ttl=HISTORY_CACHE_TTL,
                            )
                            logger.info(
                                "history_cache_rewritten_after_restore",
                                chat_id=chat_id,
                                messages=len(self._sessions[chat_id]),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "history_cache_restore_rewrite_failed",
                                chat_id=chat_id,
                                error=str(exc),
                            )
                except (json.JSONDecodeError, TypeError):
                    self._sessions[chat_id] = []
            else:
                self._sessions[chat_id] = []

            # Добавляем Gemini prompt-cache nonce (если установлен через !reset),
            # чтобы инвалидировать cache без перезапуска рантайма.
            from .core.gemini_cache_nonce import clear_gemini_nonce, get_gemini_nonce

            effective_system_prompt = system_prompt
            _nonce = get_gemini_nonce(chat_id)
            if effective_system_prompt and _nonce:
                effective_system_prompt = (
                    f"{effective_system_prompt}\n\n<!-- cache_nonce: {_nonce} -->"
                )

            if effective_system_prompt and not self._sessions[chat_id]:
                self._sessions[chat_id].append(
                    {"role": "system", "content": effective_system_prompt}
                )
            elif effective_system_prompt and self._sessions[chat_id][0].get("role") != "system":
                self._sessions[chat_id].insert(
                    0, {"role": "system", "content": effective_system_prompt}
                )

            # Nonce consumed при первом применении к новой/пустой сессии.
            if _nonce:
                clear_gemini_nonce(chat_id)
        else:
            # Сессия уже загружена в памяти (например, !reset --layer=gemini не чистил её).
            # Проверяем, есть ли pending nonce: если да — обновляем content первого
            # system-message, чтобы на следующем request Gemini получил отличающийся
            # system_prompt и cache инвалидировался.
            from .core.gemini_cache_nonce import clear_gemini_nonce, get_gemini_nonce

            _nonce = get_gemini_nonce(chat_id)
            if _nonce and system_prompt and self._sessions[chat_id]:
                first_msg = self._sessions[chat_id][0]
                if isinstance(first_msg, dict) and first_msg.get("role") == "system":
                    first_msg["content"] = f"{system_prompt}\n\n<!-- cache_nonce: {_nonce} -->"
                else:
                    # Нет system-сообщения — вставим его с nonce.
                    self._sessions[chat_id].insert(
                        0,
                        {
                            "role": "system",
                            "content": (f"{system_prompt}\n\n<!-- cache_nonce: {_nonce} -->"),
                        },
                    )
                # Consume nonce после применения: чтобы не обновлять system при каждом запросе.
                clear_gemini_nonce(chat_id)

        self._sanitize_session_and_cache(chat_id)

        # Если запрос помечен как memory-query — очищаем накопленную session history
        # (кроме system prompt), чтобы старые stale-ответы не отравляли атрибуцию.
        # Флаг одноразовый: is_memory_query_flagged() сбрасывает его при чтении.
        if self.is_memory_query_flagged(chat_id):
            existing = self._sessions.get(chat_id, [])
            # Оставляем только system-сообщение (если есть).
            system_msgs = [m for m in existing if m.get("role") == "system"]
            self._sessions[chat_id] = system_msgs
            logger.info("memory_query_history_cleared", chat_id=chat_id)

        if images:
            content_parts = [{"type": "text", "text": message}]
            for img_b64 in images:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    }
                )
            self._sessions[chat_id].append({"role": "user", "content": content_parts})
        else:
            self._sessions[chat_id].append({"role": "user", "content": message})

        from .model_manager import model_manager  # lazy import

        request_marked = False
        if hasattr(model_manager, "mark_request_started"):
            try:
                model_manager.mark_request_started()
                request_marked = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("model_manager_mark_request_started_failed", error=str(exc))

        has_photo = bool(images)
        effective_force_cloud = bool(force_cloud)
        preferred_model_id = str(preferred_model or "").strip()
        selected_model = ""
        attempt_model = ""
        request_started_at = time.time()
        messages_to_send: list[dict[str, Any]] = []

        try:
            if preferred_model_id:
                # Явный выбор модели из UI/owner-пути должен иметь приоритет над
                # автоматическим роутингом. Иначе owner-панель показывает, что
                # модель выбрана, но фактически запрос уходит в default-slot.
                selected_model = preferred_model_id
                logger.info(
                    "openclaw_preferred_model_selected",
                    chat_id=chat_id,
                    preferred_model=preferred_model_id,
                    has_photo=has_photo,
                    force_cloud=effective_force_cloud,
                )
            else:
                selected_model = await model_manager.get_best_model(has_photo=has_photo)
            # В force_cloud режиме не позволяем оставаться на локальной модели,
            # иначе runtime-route показывает local и ломает "cloud truth".
            if effective_force_cloud and model_manager.is_local_model(selected_model):
                cloud_candidate = await self._pick_cloud_retry_model(
                    model_manager=model_manager,
                    current_model=selected_model,
                    has_photo=has_photo,
                )
                if cloud_candidate:
                    logger.warning(
                        "force_cloud_remapped_local_selection",
                        requested=selected_model,
                        remapped=cloud_candidate,
                    )
                    selected_model = cloud_candidate
                else:
                    logger.warning(
                        "force_cloud_no_cloud_candidate_available",
                        requested=selected_model,
                    )
            elif self._should_skip_local_photo_route(
                selected_model=selected_model,
                model_manager=model_manager,
                has_photo=has_photo,
                force_cloud=effective_force_cloud,
            ):
                # Фото в auto-vision режиме не должно откатываться в локальный recovery:
                # если локальная vision-модель не задана явно, считаем такой маршрут
                # фактически force-cloud даже если исходный запрос пришёл без force_cloud.
                effective_force_cloud = True
                cloud_candidate = await self._pick_cloud_retry_model(
                    model_manager=model_manager,
                    current_model=selected_model,
                    has_photo=True,
                )
                if cloud_candidate:
                    logger.info(
                        "photo_auto_mode_remapped_local_selection_to_cloud",
                        requested=selected_model,
                        remapped=cloud_candidate,
                    )
                    selected_model = cloud_candidate
                else:
                    logger.warning(
                        "photo_auto_mode_no_cloud_candidate_available",
                        requested=selected_model,
                    )
            elif has_photo and _is_cli_provider(selected_model):
                # CLI-провайдер не умеет multimodal — принудительно переключаемся
                # на первый vision-capable cloud-кандидат из runtime chain.
                vision_candidate = await self._pick_vision_cloud_model(
                    model_manager=model_manager,
                    current_model=selected_model,
                )
                if vision_candidate:
                    logger.info(
                        "photo_route_cli_provider_redirect",
                        primary=selected_model,
                        redirected_to=vision_candidate,
                    )
                    metrics.inc("photo_route_redirected")
                    selected_model = vision_candidate
                else:
                    logger.warning(
                        "photo_route_no_vision_fallback",
                        primary=selected_model,
                        fallback_chain=list(get_runtime_fallback_models()),
                    )
                    metrics.inc("photo_route_redirected_failed")
            if not effective_force_cloud and model_manager.is_local_model(selected_model):
                local_ready = await model_manager.ensure_model_loaded(
                    selected_model,
                    has_photo=has_photo,
                )
                if local_ready and hasattr(model_manager, "get_current_model"):
                    current_local = str(model_manager.get_current_model() or "").strip()
                    if current_local and current_local != selected_model:
                        logger.warning(
                            "local_model_remapped_after_autoload",
                            requested=selected_model,
                            remapped=current_local,
                        )
                        selected_model = current_local
                if not local_ready:
                    # Если локальный автозапуск не сработал, не отдаём пользователю silent-empty:
                    # заранее уходим в cloud-кандидат.
                    cloud_candidate = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=selected_model,
                        has_photo=has_photo,
                    )
                    if cloud_candidate:
                        logger.warning(
                            "local_autoload_failed_switching_to_cloud",
                            requested=selected_model,
                            cloud_candidate=cloud_candidate,
                        )
                        selected_model = cloud_candidate

            attempt_model = selected_model

            messages_to_send = self._apply_sliding_window(chat_id, self._sessions[chat_id])
            if not has_photo:
                messages_to_send = self._strip_image_parts_for_text_route(messages_to_send)
            if not effective_force_cloud and model_manager.is_local_model(selected_model):
                messages_to_send = self._apply_local_route_history_budget(
                    chat_id,
                    messages_to_send,
                    has_photo=has_photo,
                    trim_reason="local_primary_route",
                )

            # W24/W26: перед photo-запросом убеждаемся что models.json объявляет
            # image в input[] для выбранной модели. Gateway кэширует capability config
            # при старте и перезаписывает models.json — патч + reload решают race condition.
            if has_photo and not self._is_model_declared_vision_in_config(selected_model):
                patched = self.ensure_vision_input_in_models_json()
                logger.warning(
                    "photo_model_not_declared_vision_in_config",
                    model=selected_model,
                    patched_count=patched,
                )
                if patched:
                    # Нужен reload чтобы gateway подхватил обновлённый input[].
                    # Без этого gateway продолжает стрипать image_url в текущем запросе
                    # (использует in-memory capability cache, не перечитывает models.json).
                    try:
                        from .core.openclaw_secrets_runtime import reload_openclaw_secrets

                        reload_result = await reload_openclaw_secrets()
                        logger.info(
                            "photo_vision_patch_reload_done",
                            ok=reload_result.get("ok"),
                            model=selected_model,
                        )
                    except Exception as _reload_exc:  # noqa: BLE001
                        logger.warning(
                            "photo_vision_patch_reload_failed",
                            error=str(_reload_exc),
                        )

            logger.info(
                "openclaw_stream_start",
                chat_id=chat_id,
                model=selected_model,
                has_photo=has_photo,
                force_cloud=effective_force_cloud,
            )
            self._set_last_runtime_route(
                channel="planning",
                model=selected_model,
                route_reason="selected_model",
                route_detail="Определена целевая модель перед выполнением запроса",
                force_cloud=effective_force_cloud,
            )

            # Жесткий local-first: если выбран локальный маршрут, сначала бьем напрямую в LM Studio.
            # Это исключает ситуацию, когда OpenClaw runtime игнорирует модель и уходит в cloud.
            if not effective_force_cloud and model_manager.is_local_model(selected_model):
                lm_text = await self._direct_lm_fallback(
                    chat_id=chat_id,
                    messages_to_send=messages_to_send,
                    model_hint=selected_model,
                    has_photo=has_photo,
                    max_output_tokens=max_output_tokens,
                )
                if lm_text:
                    logger.info("local_direct_path_used", chat_id=chat_id, model=selected_model)
                    self._set_last_runtime_route(
                        channel="local_direct",
                        model=selected_model,
                        route_reason="local_direct_primary",
                        route_detail="Ответ получен напрямую из LM Studio",
                        force_cloud=effective_force_cloud,
                    )
                    self._finalize_chat_response(chat_id, lm_text)
                    yield lm_text
                    return
                logger.warning(
                    "local_direct_path_failed_fallback_openclaw",
                    chat_id=chat_id,
                    model=selected_model,
                )

            tried_paid = False
            tried_cloud_auth_recovery = False
            tried_cloud_quality_recovery = False
            tried_local = False
            tried_cloud_after_local = False
            tried_semantic_retry = False
            final_response = ""
            last_semantic: dict[str, str] | None = None

            for attempt in range(4):
                logger.info("openclaw_attempt", attempt=attempt + 1, model=attempt_model)
                route_channel = (
                    "openclaw_local"
                    if model_manager.is_local_model(attempt_model)
                    else "openclaw_cloud"
                )
                self._set_last_runtime_route(
                    channel=route_channel,
                    model=attempt_model,
                    route_reason="attempt_started",
                    route_detail="Запущена текущая попытка маршрута OpenClaw",
                    status="pending",
                    force_cloud=effective_force_cloud,
                    attempt=attempt + 1,
                )
                semantic: dict[str, str] | None = None
                try:
                    final_response = await self._openclaw_completion_once(
                        model_id=attempt_model,
                        messages_to_send=messages_to_send,
                        max_output_tokens=max_output_tokens,
                        has_photo=has_photo,
                    )
                    semantic = self._detect_semantic_error(final_response)
                except (ProviderAuthError, ProviderError) as exc:
                    semantic = self._semantic_from_provider_exception(exc)
                    final_response = ""

                if (
                    semantic
                    and semantic["code"] in {"lm_empty_stream", "lm_model_crash"}
                    and not tried_semantic_retry
                ):
                    tried_semantic_retry = True
                    retry_messages = self._build_retry_messages(messages_to_send)
                    logger.warning(
                        "openclaw_semantic_retry",
                        code=semantic["code"],
                        model=attempt_model,
                        messages_before=len(messages_to_send),
                        messages_after=len(retry_messages),
                    )
                    try:
                        final_response = await self._openclaw_completion_once(
                            model_id=attempt_model,
                            messages_to_send=retry_messages,
                            max_output_tokens=max_output_tokens,
                            has_photo=has_photo,
                        )
                        semantic = self._detect_semantic_error(final_response)
                        messages_to_send = retry_messages
                    except (ProviderAuthError, ProviderError) as retry_exc:
                        semantic = self._semantic_from_provider_exception(retry_exc)
                        final_response = ""

                if not semantic:
                    last_semantic = None
                    break

                last_semantic = semantic
                self._cloud_tier_state["last_error_code"] = semantic["code"]
                self._cloud_tier_state["last_error_message"] = semantic["message"]
                logger.warning(
                    "openclaw_semantic_error_detected",
                    code=semantic["code"],
                    message=semantic["message"],
                    model=attempt_model,
                )

                # 1) free quota -> paid
                if semantic["code"] == "quota_exceeded" and not tried_paid:
                    tried_paid = True
                    switch_result = await self._switch_cloud_tier("paid", reason="quota_exceeded")
                    if switch_result.get("ok"):
                        continue

                # 2) auth/key type/quota -> cloud retry без слепого openai fallback
                if (
                    semantic["code"] in (LEGACY_AUTH_CODES | {"quota_exceeded"})
                    and not tried_cloud_auth_recovery
                ):
                    tried_cloud_auth_recovery = True
                    cloud_retry = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=attempt_model,
                        has_photo=has_photo,
                    )
                    if cloud_retry:
                        attempt_model = cloud_retry
                        self._cloud_tier_state["last_recovery_action"] = "switch_to_cloud_retry"
                        continue

                # 2.25) Cloud quality recovery:
                # если облачный ответ пустой/битый/таймаутный — пробуем другой cloud-кандидат,
                # не переключаясь в local.
                if (
                    semantic["code"]
                    in {
                        "lm_empty_stream",
                        "lm_malformed_response",
                        "provider_timeout",
                        "provider_error",
                    }
                    and not tried_cloud_quality_recovery
                    and not model_manager.is_local_model(attempt_model)
                ):
                    tried_cloud_quality_recovery = True
                    cloud_retry = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=attempt_model,
                        has_photo=has_photo,
                    )
                    if cloud_retry:
                        attempt_model = cloud_retry
                        self._cloud_tier_state["last_recovery_action"] = (
                            "switch_to_cloud_quality_retry"
                        )
                        continue

                # 2.5) Фото пришло в локальную модель без vision add-on:
                # исключаем текущую локальную модель для фото и уходим на альтернативу.
                if semantic["code"] == "vision_addon_missing" and has_photo:
                    if (
                        not effective_force_cloud
                        and model_manager.is_local_model(attempt_model)
                        and hasattr(model_manager, "_exclude_local_model")
                    ):
                        try:
                            model_manager._exclude_local_model(  # noqa: SLF001
                                attempt_model,
                                reason="vision_addon_missing",
                                ttl_sec=1800.0,
                            )
                        except Exception:
                            pass

                    alt_local = ""
                    if (
                        not effective_force_cloud
                        and self._allow_alt_local_vision_recovery()
                        and hasattr(model_manager, "_local_candidates")
                    ):
                        try:
                            local_candidates = await model_manager._local_candidates(has_photo=True)  # noqa: SLF001
                        except Exception:
                            local_candidates = []
                        for candidate_id, _ in local_candidates:
                            if str(candidate_id or "").strip() != str(attempt_model or "").strip():
                                alt_local = str(candidate_id or "").strip()
                                break
                    if alt_local:
                        loaded = await model_manager.ensure_model_loaded(
                            alt_local,
                            has_photo=True,
                        )
                        if loaded:
                            attempt_model = alt_local
                            self._cloud_tier_state["last_recovery_action"] = (
                                "switch_to_alt_local_vision"
                            )
                            continue
                    elif not effective_force_cloud:
                        logger.info(
                            "vision_addon_missing_skips_alt_local_auto_mode",
                            current_model=attempt_model,
                            preferred_vision=str(
                                getattr(config, "LOCAL_PREFERRED_VISION_MODEL", "") or ""
                            ),
                        )

                    cloud_candidate = await self._pick_cloud_retry_model(
                        model_manager=model_manager,
                        current_model=attempt_model,
                        has_photo=True,
                    )
                    if cloud_candidate:
                        attempt_model = cloud_candidate
                        self._cloud_tier_state["last_recovery_action"] = (
                            "switch_to_cloud_on_vision_addon_missing"
                        )
                        continue

                # 3) критичные ошибки -> local autoload (если не force_cloud)
                local_recovery_codes = {
                    "model_not_loaded",
                    "vision_addon_missing",
                    "quota_exceeded",
                    "provider_timeout",
                    "provider_error",
                    "transport_error",
                    "lm_empty_stream",
                    "lm_model_crash",
                    "lm_malformed_response",
                } | LEGACY_AUTH_CODES
                if (
                    semantic["code"] in local_recovery_codes
                    and self._local_recovery_enabled(
                        force_cloud=effective_force_cloud, has_photo=has_photo
                    )
                    and not tried_local
                ):
                    tried_local = True
                    local_model = await self._resolve_local_model_for_retry(
                        model_manager,
                        attempt_model,
                        has_photo=has_photo,
                    )
                    if local_model:
                        loaded = await model_manager.ensure_model_loaded(
                            local_model,
                            has_photo=has_photo,
                        )
                        if loaded:
                            attempt_model = local_model
                            messages_to_send = self._apply_local_route_history_budget(
                                chat_id,
                                messages_to_send,
                                has_photo=has_photo,
                                trim_reason="local_recovery_route",
                            )
                            self._cloud_tier_state["last_recovery_action"] = "switch_to_local"
                            continue
                    if not tried_cloud_after_local:
                        tried_cloud_after_local = True
                        cloud_candidate = await self._pick_cloud_retry_model(
                            model_manager=model_manager,
                            current_model=attempt_model,
                            has_photo=has_photo,
                        )
                        if cloud_candidate:
                            attempt_model = cloud_candidate
                            self._cloud_tier_state["last_recovery_action"] = (
                                "switch_to_cloud_after_local_failure"
                            )
                            continue

                # Больше стратегий нет
                break

            if not final_response and last_semantic is not None:
                # Не перетираем реальную причину (например auth 401) синтетическим lm_empty_stream.
                semantic_after = dict(last_semantic)
            else:
                semantic_after = self._detect_semantic_error(final_response)

            if semantic_after:
                # Последняя защита: прямой LM fallback.
                # Для auth-ошибок fallback не применяем, чтобы не маскировать
                # реальную проблему "configured but unauthorized".
                if (
                    self._local_recovery_enabled(
                        force_cloud=effective_force_cloud, has_photo=has_photo
                    )
                    and semantic_after["code"] not in LEGACY_AUTH_CODES
                ):
                    lm_text = await self._direct_lm_fallback(
                        chat_id=chat_id,
                        messages_to_send=messages_to_send,
                        model_hint=attempt_model,
                        has_photo=has_photo,
                        max_output_tokens=max_output_tokens,
                    )
                    if lm_text:
                        final_response = lm_text
                        self._set_last_runtime_route(
                            channel="local_direct",
                            model=attempt_model,
                            route_reason="local_direct_recovery",
                            route_detail="Семантическая ошибка OpenClaw, восстановление через прямой LM Studio",
                            force_cloud=effective_force_cloud,
                        )
                        semantic_after = None

            if semantic_after:
                code = semantic_after["code"]
                self._set_last_runtime_route(
                    channel="error",
                    model=attempt_model,
                    route_reason="semantic_error",
                    route_detail=semantic_after["message"],
                    status="error",
                    error_code=code,
                    force_cloud=effective_force_cloud,
                )
                if code == "quota_exceeded":
                    user_text = "❌ Квота облачных ключей исчерпана. Переключись на локальную модель: !model local"
                elif code in LEGACY_AUTH_CODES:
                    user_text = "❌ Облачный ключ невалиден для текущего API. Проверь Gemini ключ формата AIza..."
                elif code == "model_not_loaded":
                    user_text = "❌ Локальная модель не загружена. Загрузи её в LM Studio или командой !model load <name>."
                elif code == "lm_empty_stream":
                    user_text = "❌ Модель вернула пустой поток. Повтори запрос или переключись на !model local."
                elif code == "lm_model_crash":
                    user_text = "❌ Локальная модель аварийно завершилась. Повтори запрос или переключись на !model cloud."
                elif code == "lm_malformed_response":
                    user_text = "❌ Локальная модель вернула служебный/повреждённый ответ. Повтори запрос или переключись на !model cloud."
                elif code == "vision_addon_missing":
                    user_text = "❌ Локальная модель не поддерживает обработку фото в текущей конфигурации. Переключи vision-модель или попробуй !model cloud."
                elif code == "gateway_unknown_error":
                    user_text = "⚠️ OpenClaw вернул неизвестную ошибку. Попробуй повторить запрос."
                else:
                    user_text = (
                        "❌ Облачный сервис временно недоступен. Попробуй позже или !model local."
                    )
                yield user_text
                return

            if not final_response:
                final_response = "❌ Модель не вернула ответ."

            # Сохраняем MEDIA:-строки до sanitize, потому что _sanitize_assistant_response
            # при наличии <final>...</final> оставляет ТОЛЬКО содержимое внутри тега —
            # всё снаружи (в т.ч. MEDIA: после </final>) вырезается.
            # Userbot читает итоговый yield и ищет MEDIA: через regex; если они пропали
            # из финального текста, голосовое сообщение никогда не будет отправлено.
            _pre_sanitize_media = re.findall(r"(?m)^MEDIA:\s*\S+\s*$", final_response)

            sanitized_response = self._sanitize_assistant_response(final_response)
            if sanitized_response:
                final_response = sanitized_response

            if (
                not self._last_runtime_route
                or self._last_runtime_route.get("status") != "ok"
                or self._last_runtime_route.get("channel") == "planning"
            ):
                resolved_model = self._resolve_gateway_reported_model(
                    attempt_model,
                    request_started_at=request_started_at,
                )
                route_channel = (
                    "openclaw_local"
                    if model_manager.is_local_model(resolved_model)
                    else "openclaw_cloud"
                )
                route_detail = "Ответ получен через OpenClaw API"
                if resolved_model and resolved_model != attempt_model:
                    route_detail = (
                        f"Ответ получен через OpenClaw API; gateway fallback -> {resolved_model}"
                    )
                self._set_last_runtime_route(
                    channel=route_channel,
                    model=resolved_model,
                    route_reason="openclaw_response_ok",
                    route_detail=route_detail,
                    force_cloud=effective_force_cloud,
                )

            self._finalize_chat_response(chat_id, final_response)

            # В историю пишем без MEDIA:-строк; для yield восстанавливаем их,
            # чтобы userbot мог распознать и отправить голосовое/файл.
            response_for_delivery = final_response
            if _pre_sanitize_media:
                for _ml in _pre_sanitize_media:
                    if _ml not in response_for_delivery:
                        response_for_delivery = response_for_delivery.rstrip() + "\n" + _ml
            yield response_for_delivery

        except RouterError:
            raise
        except (ProviderError, ProviderAuthError) as exc:
            semantic = self._semantic_from_provider_exception(exc)
            code = semantic["code"]
            self._cloud_tier_state["last_error_code"] = code
            self._cloud_tier_state["last_error_message"] = semantic["message"]
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="provider_exception",
                route_detail=semantic["message"],
                status="error",
                error_code=code,
                force_cloud=effective_force_cloud,
            )
            if code in LEGACY_AUTH_CODES:
                yield "❌ Облачный ключ не прошёл авторизацию. Проверь ключ/токен."
            else:
                yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except httpx.TimeoutException as exc:
            logger.error("openclaw_stream_timeout", error=str(exc))
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_timeout",
                route_detail=str(exc),
                status="error",
                error_code="provider_timeout",
                force_cloud=effective_force_cloud,
            )
            yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("openclaw_stream_connect_error", error=str(exc))
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_connect_error",
                route_detail=str(exc),
                status="error",
                error_code="transport_error",
                force_cloud=effective_force_cloud,
            )
            yield "❌ Провайдер временно недоступен. Попробуй позже или переключись на !model local."
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            logger.error("openclaw_stream_error", error=str(exc))
            if effective_force_cloud:
                yield "❌ Облачный сервис временно недоступен. Попробуй позже или переключись на !model local."
                return
            if self._local_recovery_enabled(force_cloud=effective_force_cloud, has_photo=has_photo):
                lm_text = await self._direct_lm_fallback(
                    chat_id=chat_id,
                    messages_to_send=messages_to_send,
                    model_hint=attempt_model or selected_model,
                    has_photo=has_photo,
                    max_output_tokens=max_output_tokens,
                )
                if lm_text:
                    self._set_last_runtime_route(
                        channel="local_direct",
                        model=attempt_model or selected_model,
                        route_reason="local_direct_exception_fallback",
                        route_detail="Ошибка OpenClaw транспорта, выполнен прямой fallback в LM Studio",
                        force_cloud=effective_force_cloud,
                    )
                    yield lm_text
                    return
            self._set_last_runtime_route(
                channel="error",
                model=attempt_model or selected_model,
                route_reason="transport_error",
                route_detail=str(exc),
                status="error",
                error_code="transport_error",
                force_cloud=effective_force_cloud,
            )
            yield "❌ Ошибка облака. Попробуй позже или переключись на локальную модель: !model local."
        finally:
            if request_marked and hasattr(model_manager, "mark_request_finished"):
                try:
                    model_manager.mark_request_finished()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("model_manager_mark_request_finished_failed", error=str(exc))
            # LLM latency histogram observe (provider+model из last_runtime_route).
            try:
                from .core.llm_latency_tracker import llm_latency_tracker

                _duration = time.perf_counter() - _llm_call_start_perf
                _route = self.get_last_runtime_route() or {}
                llm_latency_tracker.observe(
                    provider=str(_route.get("provider") or "unknown"),
                    model=str(_route.get("model") or preferred_model or "unknown"),
                    duration_s=float(_duration),
                )
            except Exception:  # noqa: BLE001
                pass
            # Закрываем Sentry-транзакцию (graceful no-op если SDK отсутствует).
            try:
                _txn_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    def clear_session(self, chat_id: str):
        """Очищает историю чата (in-memory + кэш + persistent session-файлы).

        Persistent cleanup: лучшее усилие по удалению
        ``~/.openclaw/agents/main/sessions/*.jsonl`` через content-lookup по
        маркеру ``"chat_id": <id>``. Т.к. явного mapping chat_id→session_id в
        Krab нет, ищем по содержимому файла: если встречается — удаляем.
        Ошибки IO не пропагируются — только WARN-лог.
        """
        if chat_id in self._sessions:
            del self._sessions[chat_id]
        self._lm_native_chat_state.pop(chat_id, None)
        history_cache.delete(f"chat_history:{chat_id}")

        # Persistent session-файлы OpenClaw: best-effort cleanup.
        # TODO(krab-session-mapping): если появится chat_id→session_id в Krab,
        # заменить pattern-lookup на прямой lookup из маппинга.
        try:
            removed = self._cleanup_openclaw_session_files(chat_id)
            if removed:
                logger.info(
                    "openclaw_session_files_removed",
                    chat_id=chat_id,
                    count=removed,
                )
        except OSError as exc:
            logger.warning(
                "openclaw_session_files_cleanup_failed",
                chat_id=chat_id,
                error=str(exc),
            )

        logger.info("session_cleared", chat_id=chat_id)

    def flag_memory_query(self, chat_id: str) -> None:
        """Помечает chat_id: следующий send_message_stream пропустит session history.

        Одноразовый флаг — сбрасывается автоматически в начале запроса.
        Используется memory_context_augmenter при детекции archive-запроса.
        """
        self._memory_query_flags.add(chat_id)
        logger.debug("memory_query_flagged", chat_id=chat_id)

    def is_memory_query_flagged(self, chat_id: str) -> bool:
        """Возвращает True если флаг установлен (и немедленно сбрасывает его)."""
        flagged = chat_id in self._memory_query_flags
        self._memory_query_flags.discard(chat_id)
        return flagged

    @staticmethod
    def _cleanup_openclaw_session_files(chat_id: str) -> int:
        """Удаляет session.jsonl файлы, в которых встречается chat_id.

        Возвращает число удалённых файлов. Безопасно: игнорирует отсутствие
        директории и любые IO-ошибки на отдельных файлах.
        """
        sessions_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
        if not sessions_dir.exists() or not sessions_dir.is_dir():
            return 0

        # Маркер ищем в разных формах (JSON может сериализовать по-разному).
        needle_quoted = f'"chat_id": "{chat_id}"'
        needle_unquoted = f'"chat_id":"{chat_id}"'
        needle_int = f'"chat_id": {chat_id}'
        needle_int_unquoted = f'"chat_id":{chat_id}'

        removed = 0
        try:
            candidates = list(sessions_dir.glob("*.jsonl"))
        except OSError:
            return 0

        for path in candidates:
            try:
                # Читаем как текст; big-files не ожидаются (session jsonl небольшие).
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if (
                needle_quoted in content
                or needle_unquoted in content
                or needle_int in content
                or needle_int_unquoted in content
            ):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    # Не валим весь reset из-за одного проблемного файла.
                    continue
        return removed

    def get_usage_stats(self) -> Dict[str, int]:
        """Возвращает статистику использования токенов."""
        return self._usage_stats

    def get_token_info(self) -> dict[str, Any]:
        """Маскированный отчет по ключам/tier (для UI)."""
        return {
            "active_tier": self.active_tier,
            "tiers": {
                "free": {
                    "is_configured": bool(self.gemini_tiers.get("free")),
                    "masked_key": mask_secret(self.gemini_tiers.get("free")),
                    "is_aistudio_key": is_ai_studio_key(self.gemini_tiers.get("free")),
                },
                "paid": {
                    "is_configured": bool(self.gemini_tiers.get("paid")),
                    "masked_key": mask_secret(self.gemini_tiers.get("paid")),
                    "is_aistudio_key": is_ai_studio_key(self.gemini_tiers.get("paid")),
                },
            },
            "current_google_key_masked": mask_secret(
                get_google_api_key_from_models(self._models_path)
            ),
            "last_error_code": self._cloud_tier_state.get("last_error_code"),
        }

    async def get_cloud_provider_diagnostics(
        self, providers: list[str] | None = None
    ) -> dict[str, Any]:
        """Диагностика cloud-провайдеров в безопасном формате."""
        providers_list = providers or ["google"]
        report: dict[str, Any] = {"ok": True, "providers": {}, "checked": providers_list}

        for provider in providers_list:
            provider_low = provider.lower().strip()
            if provider_low != "google":
                report["providers"][provider_low] = {
                    "ok": False,
                    "error_code": "provider_not_supported",
                    "summary": "Провайдер пока не поддерживается диагностикой",
                }
                report["ok"] = False
                continue

            tier = self.active_tier
            key, source = self._resolve_provider_api_key("google")
            probe: CloudProbeResult = await probe_gemini_key(
                key,
                key_source=source,
                key_tier=tier,
            )
            report["providers"]["google"] = {
                "ok": probe.provider_status == "ok",
                "provider_status": probe.provider_status,
                "error_code": probe.semantic_error_code,
                "summary": probe.detail[:220] if probe.detail else probe.provider_status,
                "key_source": probe.key_source,
                "key_tier": probe.key_tier,
                "recovery_action": probe.recovery_action,
                "http_status": probe.http_status,
            }
            if probe.provider_status != "ok":
                report["ok"] = False

            self._cloud_tier_state["last_provider_status"] = probe.provider_status
            self._cloud_tier_state["last_error_code"] = (
                probe.semantic_error_code if probe.provider_status != "ok" else None
            )
            self._cloud_tier_state["last_recovery_action"] = probe.recovery_action
            self._cloud_tier_state["last_probe_at"] = int(time.time())

        return report

    async def get_cloud_runtime_check(self) -> dict[str, Any]:
        """Расширенный runtime-check для web-панели."""
        current_key_state = self._effective_runtime_google_key_state()
        secrets_reload_runtime = get_openclaw_cli_runtime_status()
        current_key_tier = str(current_key_state.get("tier") or "").strip().lower()
        if (
            current_key_tier in {"free", "paid"}
            and current_key_tier != str(self.active_tier or "").strip().lower()
        ):
            # Синхронизируем active_tier с фактическим ключом из models.json,
            # чтобы runtime-check не застревал в stale default `free`.
            self.active_tier = current_key_tier
            self._cloud_tier_state["active_tier"] = current_key_tier
        self._sync_last_runtime_route_active_tier()

        free_probe = await probe_gemini_key(
            self.gemini_tiers.get("free"),
            key_source="env:GEMINI_API_KEY_FREE",
            key_tier="free",
        )
        paid_probe = await probe_gemini_key(
            self.gemini_tiers.get("paid"),
            key_source="env:GEMINI_API_KEY_PAID",
            key_tier="paid",
        )
        # Синхронизируем tier-state по фактическому runtime-check, чтобы health/lite
        # отражал правду сразу после cold start, а не только после реального запроса.
        probes: dict[str, CloudProbeResult] = {
            "free": free_probe,
            "paid": paid_probe,
        }
        selected_probe = probes.get(str(self.active_tier or "").strip().lower()) or free_probe
        if selected_probe.provider_status != "ok":
            if free_probe.provider_status == "ok":
                selected_probe = free_probe
            elif paid_probe.provider_status == "ok":
                selected_probe = paid_probe

        self._cloud_tier_state["last_provider_status"] = selected_probe.provider_status
        self._cloud_tier_state["last_error_code"] = (
            selected_probe.semantic_error_code if selected_probe.provider_status != "ok" else None
        )
        self._cloud_tier_state["last_error_message"] = (
            selected_probe.detail if selected_probe.provider_status != "ok" else ""
        )
        self._cloud_tier_state["last_recovery_action"] = selected_probe.recovery_action
        self._cloud_tier_state["last_probe_at"] = int(time.time())

        return {
            "ok": free_probe.provider_status == "ok" or paid_probe.provider_status == "ok",
            "active_tier": self.active_tier,
            "provider": "google",
            "free": free_probe.to_dict(),
            "paid": paid_probe.to_dict(),
            "current_google_key_masked": str(current_key_state.get("masked") or ""),
            "current_google_key_state": str(current_key_state.get("state") or "unknown"),
            "current_google_key_tier": str(current_key_state.get("tier") or ""),
            "current_google_key_raw_state": str(current_key_state.get("raw_state") or ""),
            "current_google_key_raw_masked": str(current_key_state.get("raw_masked") or ""),
            "current_google_key_reference": str(current_key_state.get("raw_reference") or ""),
            "current_google_key_resolved_from_env": bool(
                current_key_state.get("resolved_from_env")
            ),
            "current_google_key_resolved_env_name": str(
                current_key_state.get("resolved_env_name") or ""
            ),
            "secrets_reload_runtime": secrets_reload_runtime,
            "tier_state": self.get_tier_state_export(),
        }

    def get_tier_state_export(self) -> dict[str, Any]:
        """Экспорт внутреннего состояния cloud tier без секретов."""
        secrets_reload_runtime = get_openclaw_cli_runtime_status()
        return {
            "active_tier": self._cloud_tier_state.get("active_tier", self.active_tier),
            "switches": int(self._cloud_tier_state.get("switches", 0)),
            "last_switch_at": self._cloud_tier_state.get("last_switch_at"),
            "last_error_code": self._cloud_tier_state.get("last_error_code"),
            "last_error_message": self._cloud_tier_state.get("last_error_message", ""),
            "last_provider_status": self._cloud_tier_state.get("last_provider_status"),
            "last_recovery_action": self._cloud_tier_state.get("last_recovery_action"),
            "last_probe_at": self._cloud_tier_state.get("last_probe_at"),
            "tiers_configured": {
                "free": bool(self.gemini_tiers.get("free")),
                "paid": bool(self.gemini_tiers.get("paid")),
            },
            "secrets_reload_runtime": secrets_reload_runtime,
        }

    async def reset_cloud_tier(self) -> dict[str, Any]:
        """Ручной сброс active tier в free."""
        return await self._switch_cloud_tier("free", reason="manual_reset")

    async def switch_cloud_tier(self, tier: str) -> dict[str, Any]:
        """Публичный метод переключения tier (для web endpoint)."""
        return await self._switch_cloud_tier(tier, reason="manual_switch")

    async def get_health_report(self) -> dict[str, Any]:
        """Короткий health-отчет для web API."""
        return {
            "gateway_ok": await self.health_check(),
            "base_url": self.base_url,
            "tier_state": self.get_tier_state_export(),
            "usage": self.get_usage_stats(),
        }

    async def get_deep_health_report(self) -> dict[str, Any]:
        """Расширенный health-отчет c cloud runtime-check."""
        return {
            "health": await self.get_health_report(),
            "cloud_runtime": await self.get_cloud_runtime_check(),
        }

    async def get_remediation_plan(self) -> dict[str, Any]:
        """План восстановления на основе текущего состояния tier/ошибок."""
        state = self.get_tier_state_export()
        actions: list[str] = []
        if state.get("last_error_code") in LEGACY_AUTH_CODES:
            actions.append("Проверь и замени paid/free ключ на AI Studio API key формата AIza...")
            actions.append("Запусти sync_openclaw_models.command и затем check_cloud_chain.command")
        elif state.get("last_error_code") == "quota_exceeded":
            actions.append(
                "Переключи tier на paid и перезагрузи secrets (через web endpoint или CLI)"
            )
            actions.append("Если paid недоступен — включи local fallback (!model local)")
        elif state.get("last_error_code") == "model_not_loaded":
            actions.append("Загрузи локальную модель в LM Studio и повтори запрос")
        elif state.get("last_error_code") == "lm_empty_stream":
            actions.append(
                "Повтори запрос с сокращённым контекстом или переключись на другую локальную модель"
            )
            actions.append("Проверь, что у локальной модели нет аварий в логах LM Studio")
        elif state.get("last_error_code") == "lm_model_crash":
            actions.append("Перезапусти проблемную модель в LM Studio и повтори запрос")
            actions.append("Если сбой повторяется — временно переключись на cloud/local fallback")
        else:
            actions.append("Проверь доступность OpenClaw и LM Studio")
            actions.append("Запусти check_cloud_chain.command для автоматической диагностики")
        return {
            "state": state,
            "actions": actions,
        }


openclaw_client = OpenClawClient()

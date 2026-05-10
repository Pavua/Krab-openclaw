# -*- coding: utf-8 -*-
"""
RoutingPolicy — явная матрица маршрутизации между LM Studio (local) и cloud (Wave 60-A).

Принцип: local когда уместно (быстро, бесплатно, приватно), cloud когда нужно
(качество, vision, инструменты, owner).

Решения персистируются в ~/.openclaw/krab_runtime_state/routing_decisions.jsonl
(последние 200 записей) для observability и тюнинга.

TODO для user: проверить матрицу ROUTING_POLICY ниже и скорректировать task_type →
backend назначения под свой use-case (см. TODO-USER метки).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Типы
# ---------------------------------------------------------------------------

_VALID_BACKENDS = frozenset({"local", "cloud", "auto"})


class RouteDecision(NamedTuple):
    """Результат решения роутинга."""

    backend: str  # "local" | "cloud" | "auto"
    model_hint: Optional[str]  # подсказка модели (None = default backend)
    reason: str  # человекочитаемая причина для логов / !routing


# ---------------------------------------------------------------------------
# Матрица политик
# ---------------------------------------------------------------------------
# TODO-USER: просмотрите значения ниже и уточните, что должно быть local vs cloud.
# Особенно обратите внимание на:
#   - "translation_short": local уместно если модель достаточно хороша для вашего языка
#   - "swarm_internal_routing": local vs cloud зависит от качества delegation
#   - "default_chat": auto = пробуем local, fallback cloud — если LM Studio сильный,
#     можно сменить на "local"
#
# Также: privacy gate (sensitive content) помечен TODO-USER:PRIVACY ниже.
# По умолчанию: sensitive → local (данные не уходят в cloud).
# Если вы доверяете cloud-редактированию / bypass — смените на "cloud".

ROUTING_POLICY: dict[str, str] = {
    # --- Лёгкие задачи → LOCAL (быстро, бесплатно, приватно) ---
    "casual_chat_low_priority": "local",  # TODO-USER: random groups, low-engagement
    "command_classification": "local",  # NLU intent extraction (Wave 26 LLM classifier)
    "simple_lookup": "local",  # !status, !health, !uptime, !version, !quota
    "translation_short": "local",  # TODO-USER: !translate < 200 chars
    "swarm_internal_routing": "local",  # TODO-USER: AgentRoom delegation / routing step
    # --- Тяжёлые задачи → CLOUD (качество, контекст, инструменты) ---
    "owner_dm": "cloud",  # Всегда cloud для owner
    "agentic_tools": "cloud",  # Tool calls (codex agent loop)
    "code_generation": "cloud",  # !ask code questions, !codex
    "vision_analysis": "cloud",  # Фото/изображения
    "swarm_output": "cloud",  # TODO-USER: final responses команд свёрма
    "long_form": "cloud",  # Ожидаемый ответ > 500 символов
    # --- Адаптивные ---
    "default_chat": "auto",  # TODO-USER: "local" если LM Studio достаточно сильный
    "translation_long": "cloud",  # !translate >= 200 chars
    "memory_recall": "local",  # TODO-USER: поиск в памяти (vector query)
}


# ---------------------------------------------------------------------------
# Чувствительные ключевые слова
# ---------------------------------------------------------------------------
# TODO-USER:PRIVACY: по умолчанию sensitive → LOCAL (данные не уходят в cloud).
# Если хотите обратное (cloud с redaction) — смените _SENSITIVE_DEFAULT_BACKEND = "cloud".
_SENSITIVE_DEFAULT_BACKEND = "local"

_SENSITIVE_PATTERNS = re.compile(
    r"\b(password|passwd|token|api[_\s-]?key|secret|private[_\s-]?key"
    r"|bearer\s+\w{10,}|-----BEGIN|ssh-rsa"
    r"|credit[_\s-]?card|cvv|iban|swift)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Персистентный лог решений
# ---------------------------------------------------------------------------

_DECISIONS_LOG = Path.home() / ".openclaw" / "krab_runtime_state" / "routing_decisions.jsonl"
_DECISIONS_MAX = 200
_log_lock = threading.Lock()


def _append_decision(decision_entry: dict) -> None:
    """Дозаписывает запись в JSONL-лог, обрезая до последних _DECISIONS_MAX."""
    try:
        _DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock:
            lines: list[str] = []
            if _DECISIONS_LOG.exists():
                with _DECISIONS_LOG.open("r", encoding="utf-8") as f:
                    lines = f.readlines()
            lines.append(json.dumps(decision_entry, ensure_ascii=False) + "\n")
            # Обрезаем до последних N
            if len(lines) > _DECISIONS_MAX:
                lines = lines[-_DECISIONS_MAX:]
            with _DECISIONS_LOG.open("w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception as exc:  # noqa: BLE001
        logger.debug("routing_decision_log_failed", error=str(exc))


def read_recent_decisions(n: int = 20) -> list[dict]:
    """Читает последние n записей из лога решений."""
    try:
        if not _DECISIONS_LOG.exists():
            return []
        with _log_lock:
            with _DECISIONS_LOG.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        out = []
        for line in reversed(lines[-n:]):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return list(reversed(out))
    except Exception as exc:  # noqa: BLE001
        logger.debug("routing_decisions_read_failed", error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Runtime overrides (временные, !routing local/cloud <task>)
# ---------------------------------------------------------------------------

_overrides: dict[str, str] = {}
_overrides_lock = threading.Lock()


def set_task_override(task_type: str, backend: str) -> None:
    """Устанавливает временный override для task_type.  Сбрасывается при рестарте."""
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"Invalid backend: {backend!r}. Expected one of {_VALID_BACKENDS}")
    with _overrides_lock:
        _overrides[task_type] = backend
    logger.info("routing_override_set", task_type=task_type, backend=backend)


def clear_task_override(task_type: str) -> None:
    """Сбрасывает временный override для task_type."""
    with _overrides_lock:
        _overrides.pop(task_type, None)
    logger.info("routing_override_cleared", task_type=task_type)


def get_overrides() -> dict[str, str]:
    """Возвращает копию текущих overrides."""
    with _overrides_lock:
        return dict(_overrides)


# ---------------------------------------------------------------------------
# Health-aware LM Studio availability
# ---------------------------------------------------------------------------
# Простой in-process кэш: poll с backoff, чтобы не спамить проверками при
# каждом входящем сообщении.

_lm_available: Optional[bool] = None
_lm_last_check: float = 0.0
_LM_CHECK_INTERVAL = 30.0  # секунд между re-check
_LM_FAIL_BACKOFF = 120.0  # секунд backoff после недоступности


async def _probe_lm_studio(lm_studio_url: str) -> bool:
    """Проверяет LM Studio через is_lm_studio_available с кэшированием."""
    global _lm_available, _lm_last_check  # noqa: PLW0603

    now = time.monotonic()
    interval = _LM_FAIL_BACKOFF if _lm_available is False else _LM_CHECK_INTERVAL
    if _lm_available is not None and (now - _lm_last_check) < interval:
        return _lm_available

    try:
        from .local_health import is_lm_studio_available  # noqa: PLC0415

        result = await is_lm_studio_available(lm_studio_url, timeout=4.0)
        _lm_available = bool(result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("routing_policy_lm_probe_failed", error=str(exc))
        _lm_available = False
    _lm_last_check = now
    return bool(_lm_available)


def reset_lm_health_cache() -> None:
    """Сбрасывает кэш health (для тестов)."""
    global _lm_available, _lm_last_check  # noqa: PLW0603
    _lm_available = None
    _lm_last_check = 0.0


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------


class RoutingPolicy:
    """
    Явная матрица маршрутизации между LM Studio (local) и облачными моделями.

    Использование:
        policy = RoutingPolicy()
        decision = await policy.decide_route(
            task_type="owner_dm",
            message_text=text,
            chat_id=chat_id,
            has_photo=False,
            force_cloud_env=config.FORCE_CLOUD,
        )
    """

    def __init__(
        self,
        *,
        lm_studio_url: Optional[str] = None,
        owner_chat_ids: Optional[frozenset[int]] = None,
    ) -> None:
        # LM Studio URL берём из конфига если не передан
        if lm_studio_url is None:
            try:
                from ..config import config as _cfg  # noqa: PLC0415

                lm_studio_url = _cfg.LM_STUDIO_URL
            except Exception:  # noqa: BLE001
                lm_studio_url = os.getenv("LM_STUDIO_URL", "http://192.168.0.171:1234")
        self._lm_studio_url = lm_studio_url or ""

        # Owner chat IDs (для задачи owner_dm)
        if owner_chat_ids is None:
            try:
                from ..config import config as _cfg  # noqa: PLC0415

                raw = (
                    getattr(_cfg, "OWNER_IDS", None) or getattr(_cfg, "OWNER_CHAT_IDS", None) or []
                )
                owner_chat_ids = frozenset(int(x) for x in raw if x)
            except Exception:  # noqa: BLE001
                owner_chat_ids = frozenset()
        self._owner_chat_ids: frozenset[int] = owner_chat_ids

    async def decide_route(
        self,
        *,
        task_type: str,
        message_text: str = "",
        chat_id: int = 0,
        has_photo: bool = False,
        force_cloud_env: bool = False,
    ) -> RouteDecision:
        """
        Возвращает RouteDecision для заданного контекста запроса.

        Порядок приоритетов:
        1. force_cloud_env (глобальный FORCE_CLOUD из env) → всегда cloud
        2. has_photo → cloud (vision)
        3. owner_dm → cloud
        4. Временные overrides (!routing local/cloud <task>)
        5. Sensitive content gate (regex) → _SENSITIVE_DEFAULT_BACKEND
        6. Матрица ROUTING_POLICY по task_type
        7. Если backend=local: проверяем health LM Studio;
           если down → fallback cloud
        8. default: "auto"
        """
        # --- 1. Global force_cloud_env ---
        if force_cloud_env:
            decision = RouteDecision(
                backend="cloud",
                model_hint=None,
                reason="FORCE_CLOUD env override",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 2. Photo → cloud (vision) ---
        if has_photo:
            decision = RouteDecision(
                backend="cloud",
                model_hint=None,
                reason="vision_analysis: photo requires cloud model",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 3. Owner DM → cloud ---
        if chat_id and self._owner_chat_ids and chat_id in self._owner_chat_ids:
            decision = RouteDecision(
                backend="cloud",
                model_hint=None,
                reason="owner_dm: owner chat always cloud",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 4. Runtime overrides ---
        with _overrides_lock:
            override = _overrides.get(task_type)
        if override:
            decision = RouteDecision(
                backend=override,
                model_hint=None,
                reason=f"runtime_override: !routing {override} {task_type}",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 5. Sensitive content gate ---
        if message_text and _SENSITIVE_PATTERNS.search(message_text):
            # TODO-USER:PRIVACY: сейчас sensitive → local (данные не уходят).
            # Поменяйте _SENSITIVE_DEFAULT_BACKEND = "cloud" если хотите обратное.
            decision = RouteDecision(
                backend=_SENSITIVE_DEFAULT_BACKEND,
                model_hint=None,
                reason=f"sensitive_content: {_SENSITIVE_DEFAULT_BACKEND} preferred (privacy gate)",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 6. Матрица по task_type ---
        raw_backend = ROUTING_POLICY.get(task_type, ROUTING_POLICY.get("default_chat", "auto"))

        # --- 7. Health-aware: если local + LM Studio down → cloud ---
        if raw_backend == "local":
            lm_up = await self._check_lm_available()
            if not lm_up:
                decision = RouteDecision(
                    backend="cloud",
                    model_hint=None,
                    reason=f"lm_studio_unavailable: {task_type} fallback to cloud",
                )
                self._log_and_persist(task_type, chat_id, decision)
                return decision
            decision = RouteDecision(
                backend="local",
                model_hint=None,
                reason=f"policy_matrix: {task_type} → local",
            )
            self._log_and_persist(task_type, chat_id, decision)
            return decision

        # --- 8. Cloud или auto ---
        decision = RouteDecision(
            backend=raw_backend,
            model_hint=None,
            reason=f"policy_matrix: {task_type} → {raw_backend}",
        )
        self._log_and_persist(task_type, chat_id, decision)
        return decision

    async def _check_lm_available(self) -> bool:
        """Проверяет доступность LM Studio (с кэшированием)."""
        if not self._lm_studio_url:
            return False
        return await _probe_lm_studio(self._lm_studio_url)

    def _log_and_persist(self, task_type: str, chat_id: int, decision: RouteDecision) -> None:
        """Логирует решение в structlog и дозаписывает в JSONL."""
        logger.info(
            "routing_decision",
            task_type=task_type,
            chat_id=chat_id,
            backend=decision.backend,
            model_hint=decision.model_hint,
            reason=decision.reason,
        )
        entry = {
            "ts": time.time(),
            "task_type": task_type,
            "chat_id": chat_id,
            "backend": decision.backend,
            "model_hint": decision.model_hint,
            "reason": decision.reason,
        }
        _append_decision(entry)


# ---------------------------------------------------------------------------
# Классификатор task_type по контексту запроса
# ---------------------------------------------------------------------------

# Шаблоны для определения задачи генерации кода
_CODE_GEN_PATTERN = re.compile(
    r"\b(напиши\s+код|implement|fix\s+bug|debug|написать\s+код"
    r"|create\s+function|write\s+a\s+script|сделай\s+функцию"
    r"|реализу[а-я]+|исправ[а-я]+\s+баг)\b",
    re.IGNORECASE,
)

# Команды, соответствующие simple_lookup
_SIMPLE_COMMANDS = frozenset(
    {
        "status",
        "health",
        "uptime",
        "version",
        "quota",
        "ping",
        "info",
        "whoami",
    }
)


def classify_task_type(
    *,
    message_text: str,
    chat_id: int,
    is_owner_dm: bool,
    has_photo: bool,
    has_command_prefix: bool,
) -> str:
    """
    Возвращает task_type ключ из ROUTING_POLICY matrix.

    Heuristics (по убыванию приоритета):
    1. has_photo → vision_analysis
    2. is_owner_dm → owner_dm
    3. !<simple_cmd> → simple_lookup
    4. !translate → translation_short / translation_long
    5. !swarm → swarm_output
    6. !ask / !codex + код / длинный → code_generation / long_form
    7. Regex: код-генерация → code_generation
    8. Отрицательный chat_id (группа) → casual_chat_low_priority
    9. default_chat
    """
    # --- 1. Фото → cloud vision ---
    if has_photo:
        return "vision_analysis"

    # --- 2. Owner DM → cloud ---
    if is_owner_dm:
        return "owner_dm"

    text = (message_text or "").strip()
    text_len = len(text)

    # --- 3. Команды с префиксом ---
    if has_command_prefix and text.startswith("!"):
        # Извлекаем первое слово команды (без !)
        cmd_word = text[1:].split()[0].lower() if text[1:].split() else ""

        if cmd_word in _SIMPLE_COMMANDS:
            return "simple_lookup"

        if cmd_word in ("translate", "tr", "пер"):
            return "translation_long" if text_len >= 200 else "translation_short"

        if cmd_word == "swarm":
            return "swarm_output"

        if cmd_word in ("ask", "codex", "code"):
            # Длинный запрос или явно код → code_generation, иначе long_form
            if text_len > 100 or _CODE_GEN_PATTERN.search(text):
                return "code_generation"
            return "long_form"

    # --- 4. Regex: паттерны генерации кода ---
    if _CODE_GEN_PATTERN.search(text):
        return "code_generation"

    # --- 5. Группа с отрицательным chat_id → casual ---
    if chat_id < 0:
        return "casual_chat_low_priority"

    return "default_chat"


# ---------------------------------------------------------------------------
# Синглтон
# ---------------------------------------------------------------------------

_policy_instance: Optional[RoutingPolicy] = None
_policy_lock = threading.Lock()


def get_routing_policy() -> RoutingPolicy:
    """Возвращает синглтон RoutingPolicy."""
    global _policy_instance  # noqa: PLW0603
    if _policy_instance is None:
        with _policy_lock:
            if _policy_instance is None:
                _policy_instance = RoutingPolicy()
    return _policy_instance

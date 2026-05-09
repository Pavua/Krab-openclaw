"""Wave 44-V: Codex quota detection + transition state for owner notification.

Provides:
- CODEX_QUOTA_PATTERNS — regex set для надёжной детекции quota errors
  в stdout/stderr codex CLI subprocess.
- ``is_quota_error`` — проверяет stdout+stderr против паттернов.
- ``classify_quota`` — отличает weekly (7d) от transient (1h) limit.
- ``CodexQuotaExhaustedError`` — поднимается, когда ВСЕ codex accounts
  исчерпаны и нет смысла продолжать в codex chain.
- Transition tracking: ``mark_codex_disabled`` / ``mark_codex_recovered``
  идемпотентны и пишут в ``codex_quota_state.json`` чтобы owner alert
  отправлялся только при переходе IN/OUT (debounced).

Модуль чисто-функциональный (без зависимостей от Pyrogram и т.п.) — это
позволяет легко юнит-тестировать паттерны и transition state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)

# Состояние "Krab сейчас в OUT-of-codex режиме?" — debounced alert
STATE_FILE = Path.home() / ".openclaw/krab_runtime_state/codex_quota_state.json"

# Cooldowns
WEEKLY_COOLDOWN = timedelta(days=7)
TRANSIENT_COOLDOWN = timedelta(hours=1)

# Wave 44-V: regex-паттерны quota errors. Требуется AT LEAST одно явное
# совпадение — чтобы не false-positive trigger при обычных slow responses.
# Каждый паттерн осмысленный — не использует наивных substring matches.
CODEX_QUOTA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rate[\s_-]?limit[\s_-]?exceeded", re.I),
    re.compile(r"quota[\s_-]?(?:exhausted|exceeded|reached)", re.I),
    re.compile(r"\b429\b", re.I),
    re.compile(r"insufficient[\s_-]?quota", re.I),
    re.compile(r"weekly[\s_-]?(?:limit|quota)", re.I),
    re.compile(r"token[\s_-]?limit[\s_-]?exceeded[\s_-]?for[\s_-]?(?:week|day)", re.I),
    re.compile(r"RateLimitError", re.I),
    re.compile(r"refresh[\s_-]?token[\s_-]?reused", re.I),  # OAuth — also blocks
    re.compile(r"You exceeded your current quota", re.I),
    re.compile(r"\bplan[\s_-]?quota\b", re.I),
]

# Weekly indicators — отдельная подгруппа для cooldown classification
_WEEKLY_INDICATORS: list[re.Pattern[str]] = [
    re.compile(r"weekly", re.I),
    re.compile(r"week[\s_-]?(?:limit|quota|cap)", re.I),
    re.compile(r"7[\s_-]?day", re.I),
]


def is_quota_error(stderr: str = "", stdout: str = "") -> bool:
    """Проверяет, является ли вывод codex CLI quota error.

    Требуется явное совпадение хотя бы одного паттерна (не подстроки).
    """
    blob = f"{stderr or ''}\n{stdout or ''}"
    if not blob.strip():
        return False
    return any(p.search(blob) for p in CODEX_QUOTA_PATTERNS)


def classify_quota(stderr: str = "", stdout: str = "") -> str:
    """Возвращает 'weekly' если weekly indicators присутствуют, иначе 'transient'.

    Используется для выбора cooldown duration.
    """
    blob = f"{stderr or ''}\n{stdout or ''}"
    if any(p.search(blob) for p in _WEEKLY_INDICATORS):
        return "weekly"
    return "transient"


def cooldown_for_kind(kind: str) -> timedelta:
    """Возвращает duration для weekly/transient quota."""
    if kind == "weekly":
        return WEEKLY_COOLDOWN
    return TRANSIENT_COOLDOWN


class CodexQuotaExhaustedError(RuntimeError):
    """Все codex accounts исчерпали квоту — caller должен fall back на следующую модель."""

    def __init__(self, message: str = "All codex accounts exhausted", *, kind: str = "weekly"):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Transition state (debounced owner notification)
# ---------------------------------------------------------------------------


@dataclass
class _State:
    disabled: bool = False
    disabled_at: str | None = None
    recovered_at: str | None = None
    last_fallback_model: str | None = None
    kind: str | None = None


def _load_state() -> _State:
    if not STATE_FILE.exists():
        return _State()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("codex_quota_state_load_error", error=str(exc))
        return _State()
    return _State(
        disabled=bool(raw.get("disabled", False)),
        disabled_at=raw.get("disabled_at"),
        recovered_at=raw.get("recovered_at"),
        last_fallback_model=raw.get("last_fallback_model"),
        kind=raw.get("kind"),
    )


def _save_state(state: _State) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "disabled": state.disabled,
        "disabled_at": state.disabled_at,
        "recovered_at": state.recovered_at,
        "last_fallback_model": state.last_fallback_model,
        "kind": state.kind,
    }
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("codex_quota_state_save_error", error=str(exc))
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def is_codex_disabled() -> bool:
    """Возвращает True если все аккаунты сейчас в quota-exhausted state."""
    return _load_state().disabled


def mark_codex_disabled(*, fallback_model: str, kind: str = "weekly") -> bool:
    """Помечает codex как disabled. Идемпотентно.

    Returns:
        True если это transition (раньше был enabled) — caller должен
        отправить owner alert. False — уже было disabled, alert не нужен.
    """
    state = _load_state()
    if state.disabled:
        # Обновим last_fallback_model на свежее, но НЕ возвращаем transition
        if state.last_fallback_model != fallback_model:
            state.last_fallback_model = fallback_model
            _save_state(state)
        return False
    state.disabled = True
    state.disabled_at = datetime.now(timezone.utc).isoformat()
    state.recovered_at = None
    state.last_fallback_model = fallback_model
    state.kind = kind
    _save_state(state)
    logger.warning(
        "codex_disabled_transition",
        fallback_model=fallback_model,
        kind=kind,
    )
    # Wave 51-A: prometheus counter — отслеживаем transition events.
    try:
        from src.core.prometheus_metrics import record_codex_disabled_transition

        record_codex_disabled_transition(kind=kind)
    except Exception:  # noqa: BLE001
        pass
    return True


def mark_codex_recovered() -> bool:
    """Помечает codex как enabled после recovery. Идемпотентно.

    Returns:
        True если это transition (раньше был disabled) — caller должен
        отправить recovery alert. False — уже было enabled.
    """
    state = _load_state()
    if not state.disabled:
        return False
    state.disabled = False
    state.recovered_at = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    logger.info("codex_recovered_transition")
    return True


__all__ = [
    "CODEX_QUOTA_PATTERNS",
    "CodexQuotaExhaustedError",
    "WEEKLY_COOLDOWN",
    "TRANSIENT_COOLDOWN",
    "classify_quota",
    "cooldown_for_kind",
    "is_codex_disabled",
    "is_quota_error",
    "mark_codex_disabled",
    "mark_codex_recovered",
]

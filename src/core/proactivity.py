# -*- coding: utf-8 -*-
"""
Unified proactivity controller — единая точка управления уровнем активности Краба.

Читает KRAB_PROACTIVITY_LEVEL один раз при старте, диспетчеризует в sub-системы:
  - group_reply_decider (autonomy mode)
  - trigger_detector (implicit threshold)
  - auto_reactions (mode)
  - (future) unsolicited thoughts

Уровни:
  silent    (0) — только explicit @mention/reply; reactions off
  reactive  (1) — mention + reply-to-krab; contextual reactions; без implicit triggers
  attentive (2) — DEFAULT: + implicit triggers threshold 0.7, generic AI aliases; нормальный autonomy
  engaged   (3) — + follow-up windows 5 мин, threshold 0.5, chatty autonomy
  proactive (4) — + unsolicited thoughts, threshold 0.3

Состояние персистируется в ~/.openclaw/krab_runtime_state/proactivity.json.

Public API:
  get_level()              -> ProactivityLevel
  get_autonomy_mode()      -> str   ("strict" | "normal" | "chatty")
  get_trigger_threshold()  -> float
  get_reactions_mode()     -> str   ("off" | "contextual" | "aggressive")
  allows_unsolicited()     -> bool
  set_level(level: str)    -> None  # persist + reload
  should_reply(message_text, chat_id, ...) -> str  # "YES" | "NO" | "UNCLEAR"
"""

from __future__ import annotations

import json
import os
from enum import IntEnum
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Уровни
# ---------------------------------------------------------------------------


class ProactivityLevel(IntEnum):
    """Уровень проактивности Краба."""

    SILENT = 0
    REACTIVE = 1
    ATTENTIVE = 2  # DEFAULT
    ENGAGED = 3
    PROACTIVE = 4


# Карта уровень → настройки sub-систем
_LEVEL_SETTINGS: dict[ProactivityLevel, dict] = {
    ProactivityLevel.SILENT: {
        "autonomy_mode": "strict",
        "trigger_threshold": 9.9,  # никогда не сработает
        "reactions_mode": "off",
        "allows_unsolicited": False,
        "followup_window_sec": 0,
        "implicit_triggers": False,
    },
    ProactivityLevel.REACTIVE: {
        "autonomy_mode": "strict",
        "trigger_threshold": 9.9,  # нет implicit, только explicit
        "reactions_mode": "contextual",
        "allows_unsolicited": False,
        "followup_window_sec": 0,
        "implicit_triggers": False,
    },
    ProactivityLevel.ATTENTIVE: {
        "autonomy_mode": "normal",
        "trigger_threshold": 0.7,
        "reactions_mode": "contextual",
        "allows_unsolicited": False,
        "followup_window_sec": 300,  # 5 мин
        "implicit_triggers": True,
    },
    ProactivityLevel.ENGAGED: {
        "autonomy_mode": "chatty",
        "trigger_threshold": 0.5,
        "reactions_mode": "contextual",
        "allows_unsolicited": False,
        "followup_window_sec": 300,
        "implicit_triggers": True,
    },
    ProactivityLevel.PROACTIVE: {
        "autonomy_mode": "chatty",
        "trigger_threshold": 0.3,
        "reactions_mode": "aggressive",
        "allows_unsolicited": True,
        "followup_window_sec": 300,
        "implicit_triggers": True,
    },
}

# ---------------------------------------------------------------------------
# Персистентное хранилище
# ---------------------------------------------------------------------------

_PERSIST_DIR = Path(os.path.expanduser("~/.openclaw/krab_runtime_state"))
_PERSIST_FILE = _PERSIST_DIR / "proactivity.json"

_ENV_VAR = "KRAB_PROACTIVITY_LEVEL"
_DEFAULT_LEVEL = ProactivityLevel.ATTENTIVE


def _level_from_str(s: str) -> ProactivityLevel:
    """Парсит строку в ProactivityLevel. Понимает числа и имена."""
    s = s.strip().lower()
    by_name = {lv.name.lower(): lv for lv in ProactivityLevel}
    if s in by_name:
        return by_name[s]
    try:
        n = int(s)
        return ProactivityLevel(n)
    except (ValueError, KeyError):
        return _DEFAULT_LEVEL


def _load_persisted() -> Optional[ProactivityLevel]:
    """Читает уровень из JSON-файла. None если файла нет или ошибка."""
    try:
        if _PERSIST_FILE.exists():
            data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
            raw = str(data.get("level", ""))
            if raw:
                return _level_from_str(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactivity_load_failed", error=str(exc))
    return None


def _persist(level: ProactivityLevel) -> None:
    """Сохраняет уровень в JSON-файл."""
    try:
        _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _PERSIST_FILE.write_text(
            json.dumps({"level": level.name.lower()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactivity_persist_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Контроллер (singleton)
# ---------------------------------------------------------------------------


class ProactivityController:
    """
    Singleton-контроллер уровня проактивности.

    Приоритет источника:
      1. Persisted JSON (~/.openclaw/krab_runtime_state/proactivity.json)
      2. Env KRAB_PROACTIVITY_LEVEL
      3. Default: attentive
    """

    _instance: Optional["ProactivityController"] = None

    def __new__(cls) -> "ProactivityController":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        persisted = _load_persisted()
        if persisted is not None:
            self._level = persisted
        else:
            env_raw = os.environ.get(_ENV_VAR, "")
            self._level = _level_from_str(env_raw) if env_raw else _DEFAULT_LEVEL
        logger.info("proactivity_init", level=self._level.name)

    # ── Геттеры ──────────────────────────────────────────────────────────────

    def get_level(self) -> ProactivityLevel:
        return self._level

    def _settings(self) -> dict:
        return _LEVEL_SETTINGS[self._level]

    def get_autonomy_mode(self) -> str:
        return self._settings()["autonomy_mode"]

    def get_trigger_threshold(self) -> float:
        return float(self._settings()["trigger_threshold"])

    def get_reactions_mode(self) -> str:
        return self._settings()["reactions_mode"]

    def allows_unsolicited(self) -> bool:
        return bool(self._settings()["allows_unsolicited"])

    def allows_implicit_triggers(self) -> bool:
        return bool(self._settings()["implicit_triggers"])

    def get_followup_window_sec(self) -> int:
        return int(self._settings()["followup_window_sec"])

    # ── Сеттер ───────────────────────────────────────────────────────────────

    def set_level(self, level: str) -> None:
        """Переключить уровень в runtime + персистировать."""
        new = _level_from_str(level)
        old = self._level
        self._level = new
        _persist(new)
        logger.info("proactivity_changed", old=old.name, new=new.name)

    # ── Центральный gate ─────────────────────────────────────────────────────

    def should_reply(
        self,
        text: str,
        chat_id: str | int = "",
        *,
        is_explicit_mention: bool = False,
        is_reply_to_krab: bool = False,
        is_group: bool = True,
        is_reply_to_other: bool = False,
    ) -> str:
        """
        Центральный gate: стоит ли отвечать на это сообщение?

        Возвращает "YES" | "NO" | "UNCLEAR".
        - "YES"     — точно отвечать
        - "NO"      — игнорировать
        - "UNCLEAR" — нужен дополнительный LLM-классификатор

        Семантика уровней:
          silent    → только explicit
          reactive  → explicit + reply-to-krab
          attentive → + implicit triggers с threshold 0.7
          engaged   → + threshold 0.5
          proactive → + threshold 0.3 + unsolicited
        """
        # Explicit всегда YES (кроме silent — explicit всё равно YES)
        if is_explicit_mention or is_reply_to_krab:
            return "YES"

        lv = self._level

        # silent/reactive: только explicit
        if lv in (ProactivityLevel.SILENT, ProactivityLevel.REACTIVE):
            return "NO"

        # Уровни с implicit triggers
        if not is_group:
            # DM всегда YES (кроме silent — уже обработано выше)
            return "YES"

        if not self.allows_implicit_triggers():
            return "NO"

        # Проверяем implicit
        try:
            from .trigger_detector import TriggerType, detect_implicit_mention

            result = detect_implicit_mention(
                text,
                chat_id,
                is_reply_to_explicit_msg=is_reply_to_other,
                threshold=self.get_trigger_threshold(),
            )
            if result.trigger_type != TriggerType.NONE:
                return "YES"
        except Exception as exc:  # noqa: BLE001
            logger.warning("proactivity_trigger_check_error", error=str(exc))
            return "UNCLEAR"

        return "NO"


# ---------------------------------------------------------------------------
# Модуль-уровневые функции (удобный публичный API)
# ---------------------------------------------------------------------------

_ctrl: Optional[ProactivityController] = None


def _get_ctrl() -> ProactivityController:
    global _ctrl  # noqa: PLW0603
    if _ctrl is None:
        _ctrl = ProactivityController()
    return _ctrl


def get_level() -> ProactivityLevel:
    return _get_ctrl().get_level()


def get_autonomy_mode() -> str:
    return _get_ctrl().get_autonomy_mode()


def get_trigger_threshold() -> float:
    return _get_ctrl().get_trigger_threshold()


def get_reactions_mode() -> str:
    return _get_ctrl().get_reactions_mode()


def allows_unsolicited() -> bool:
    return _get_ctrl().allows_unsolicited()


def set_level(level: str) -> None:
    _get_ctrl().set_level(level)
    # Сбрасываем singleton чтобы следующий get_level() видел новое состояние
    # (не нужно — set_level уже обновляет _level in-place)


def should_reply(
    text: str,
    chat_id: str | int = "",
    *,
    is_explicit_mention: bool = False,
    is_reply_to_krab: bool = False,
    is_group: bool = True,
    is_reply_to_other: bool = False,
) -> str:
    """Центральный gate — обёртка над контроллером."""
    return _get_ctrl().should_reply(
        text,
        chat_id,
        is_explicit_mention=is_explicit_mention,
        is_reply_to_krab=is_reply_to_krab,
        is_group=is_group,
        is_reply_to_other=is_reply_to_other,
    )

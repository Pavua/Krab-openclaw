# -*- coding: utf-8 -*-
"""
Антиспам фильтр для групп (!spam).

Логика детекта:
  - flood: >5 сообщений за 10 сек от одного пользователя
  - ссылки: >3 ссылок/упоминаний в одном сообщении
  - forwarded + ссылки: пересланное + хотя бы одна ссылка

Действия при детекте: ban / mute / delete.
Конфиг хранится в ~/.openclaw/krab_runtime_state/spam_filter_config.json.
"""

from __future__ import annotations

import collections
import json
import re
import time
from pathlib import Path
from typing import Deque, Dict, Optional

# Путь к конфигу
_CONFIG_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "spam_filter_config.json"

# Порог flood: максимум сообщений за окно
FLOOD_MSG_LIMIT = 5
FLOOD_WINDOW_SEC = 10.0

# Порог ссылок в одном сообщении
LINK_LIMIT = 3

# Паттерн ссылок: http/https, t.me, @username
_URL_RE = re.compile(
    r"(https?://\S+|t\.me/\S+|@\w{4,})",
    re.IGNORECASE,
)

# Доступные действия
VALID_ACTIONS = frozenset({"ban", "mute", "delete"})
DEFAULT_ACTION = "delete"


# ---------------------------------------------------------------------------
# Конфиг (чтение/запись)
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Загружает конфиг из JSON. Возвращает {} если файл не существует."""
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_config(data: dict) -> None:
    """Сохраняет конфиг в JSON."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _chat_key(chat_id: int | str) -> str:
    return str(chat_id)


def is_enabled(chat_id: int | str) -> bool:
    """Проверяет, включён ли антиспам в чате."""
    cfg = _load_config()
    return bool(cfg.get(_chat_key(chat_id), {}).get("enabled", False))


def get_action(chat_id: int | str) -> str:
    """Возвращает действие при детекте (ban/mute/delete)."""
    cfg = _load_config()
    return str(cfg.get(_chat_key(chat_id), {}).get("action", DEFAULT_ACTION))


def get_status(chat_id: int | str) -> dict:
    """Возвращает полный статус антиспама для чата."""
    cfg = _load_config()
    entry = cfg.get(_chat_key(chat_id), {})
    return {
        "enabled": bool(entry.get("enabled", False)),
        "action": str(entry.get("action", DEFAULT_ACTION)),
        "chat_id": _chat_key(chat_id),
    }


def set_enabled(chat_id: int | str, enabled: bool) -> None:
    """Включает или выключает антиспам в чате."""
    cfg = _load_config()
    key = _chat_key(chat_id)
    if key not in cfg:
        cfg[key] = {"action": DEFAULT_ACTION}
    cfg[key]["enabled"] = enabled
    _save_config(cfg)


def set_action(chat_id: int | str, action: str) -> None:
    """Устанавливает действие при детекте."""
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Недопустимое действие: {action}. Доступны: {', '.join(sorted(VALID_ACTIONS))}"
        )
    cfg = _load_config()
    key = _chat_key(chat_id)
    if key not in cfg:
        cfg[key] = {"enabled": False}
    cfg[key]["action"] = action
    _save_config(cfg)


# ---------------------------------------------------------------------------
# Flood tracker (in-memory, per процесс)
# ---------------------------------------------------------------------------

# {chat_id: {user_id: deque[timestamp]}}
_flood_tracker: Dict[str, Dict[int, Deque[float]]] = collections.defaultdict(
    lambda: collections.defaultdict(lambda: collections.deque())
)


def _check_flood(chat_id: int | str, user_id: int) -> bool:
    """
    Регистрирует новое сообщение и возвращает True, если flood-лимит превышен.
    Flood: >FLOOD_MSG_LIMIT сообщений за FLOOD_WINDOW_SEC секунд.
    """
    key = _chat_key(chat_id)
    now = time.monotonic()
    dq = _flood_tracker[key][user_id]

    # Очищаем устаревшие метки
    while dq and now - dq[0] > FLOOD_WINDOW_SEC:
        dq.popleft()

    dq.append(now)
    return len(dq) > FLOOD_MSG_LIMIT


def _count_links(text: str) -> int:
    """Возвращает количество ссылок/упоминаний в тексте."""
    return len(_URL_RE.findall(text or ""))


def _is_forwarded_with_links(message: object) -> bool:
    """
    True, если сообщение является пересланным И содержит ссылки.
    Pyrogram: forward_origin или forward_from_chat/forward_from.
    """
    is_fwd = bool(
        getattr(message, "forward_origin", None)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_from_chat", None)
        or getattr(message, "forward_date", None)
    )
    if not is_fwd:
        return False
    text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
    return _count_links(text) >= 1


def classify_message(
    chat_id: int | str,
    user_id: int,
    message: object,
) -> Optional[str]:
    """
    Анализирует сообщение и возвращает причину детекта или None.

    Возвращает одно из: 'flood', 'links', 'fwd_links', или None.
    """
    text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")

    # Flood-детект
    if _check_flood(chat_id, user_id):
        return "flood"

    # Слишком много ссылок
    if _count_links(text) > LINK_LIMIT:
        return "links"

    # Пересланное + ссылки
    if _is_forwarded_with_links(message):
        return "fwd_links"

    return None

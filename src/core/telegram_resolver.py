"""
Telegram peer resolver — многостратегийный резолвер username/ссылок/ID.

Проблема: send_message(@dragonferociousness) → 400 chat not found,
потому что Telegram не знает peer до явного resolve.

Порядок стратегий:
  1. resolve_peer()       — Pyrogram кэш / MTProto ResolveUsername
  2. get_users()          — прямой lookup по @username
  3. iter_dialogs()       — поиск по совпадению имени/юзернейма в диалогах
  4. t.me/ ссылка        — извлекаем username и передаём обратно в стратегию 1-2

Использование:
    from src.core.telegram_resolver import resolve_peer

    result = await resolve_peer(client, "@dragonferociousness")
    if result["ok"]:
        await client.send_message(result["peer_id"], text)
    else:
        print(result["error_code"], result["tried_strategies"])
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .contact_cache import lookup as _cache_lookup
from .contact_cache import store as _cache_store
from .logger import get_logger

if TYPE_CHECKING:
    from pyrogram import Client

logger = get_logger(__name__)

# Regex для t.me/ и telegram.me/ ссылок
_TME_RE = re.compile(
    r"https?://(?:t(?:elegram)?\.me|telegram\.org)/([A-Za-z0-9_]{4,32})(?:/.*)?",
    re.IGNORECASE,
)

# Максимум диалогов для перебора в стратегии 3 (защита от зависания)
_DIALOG_SCAN_LIMIT = 300


def _is_username(target: str) -> bool:
    """Определяет, выглядит ли строка как @username или plain username.

    По правилам Telegram username: 5-32 символа, начинается с буквы,
    может содержать буквы/цифры/подчёркивания. Чисто числовые строки
    не являются username.
    """
    s = target.lstrip("@")
    # Должен начинаться с буквы (Telegram username rule)
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", s))


def _strip_at(target: str) -> str:
    return target.lstrip("@")


def _extract_tme_username(target: str) -> str | None:
    """Извлекает username из t.me/username ссылки, либо None."""
    m = _TME_RE.match(target.strip())
    return m.group(1) if m else None


async def _strategy_resolve_peer(client: "Client", target: str) -> dict[str, Any] | None:
    """
    Стратегия 1: client.resolve_peer().

    Pyrogram сначала смотрит в локальный кэш,
    затем делает MTProto ResolveUsername запрос к серверу.
    Возвращает None если не удалось.
    """
    try:
        peer = await client.resolve_peer(target)
        # peer — это InputPeer* объект; peer.user_id / peer.channel_id / peer.chat_id
        peer_id = (
            getattr(peer, "user_id", None)
            or getattr(peer, "channel_id", None)
            or getattr(peer, "chat_id", None)
        )
        logger.debug(
            "resolver_strategy1_ok", target=target, peer_id=peer_id, peer_type=type(peer).__name__
        )
        return {
            "ok": True,
            "peer_id": peer_id,
            "peer_obj": peer,
            "username": _strip_at(target) if _is_username(target) else None,
            "display_name": None,
            "strategy_used": "resolve_peer",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolver_strategy1_fail", target=target, error=str(exc))
        return None


async def _strategy_get_users(client: "Client", target: str) -> dict[str, Any] | None:
    """
    Стратегия 2: client.get_users().

    Работает только для пользователей (не групп/каналов).
    Возвращает None если не удалось.
    """
    if not _is_username(target):
        return None
    try:
        users = await client.get_users(target)
        # get_users может вернуть список или один объект
        user = users[0] if isinstance(users, list) else users
        if user is None:
            return None
        display_name = (
            " ".join(
                filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])
            ).strip()
            or None
        )
        logger.debug(
            "resolver_strategy2_ok", target=target, user_id=user.id, username=user.username
        )
        return {
            "ok": True,
            "peer_id": user.id,
            "peer_obj": user,
            "username": user.username,
            "display_name": display_name or user.username,
            "strategy_used": "get_users",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolver_strategy2_fail", target=target, error=str(exc))
        return None


async def _strategy_dialog_scan(client: "Client", target: str) -> dict[str, Any] | None:
    """
    Стратегия 3: iter_dialogs() — поиск по имени/юзернейму в открытых диалогах.

    Медленно, но работает для контактов которые уже переписывались с аккаунтом.
    Ограничено _DIALOG_SCAN_LIMIT диалогами.
    """
    needle = _strip_at(target).lower()
    scanned = 0
    try:
        async for dialog in client.get_dialogs(limit=_DIALOG_SCAN_LIMIT):
            scanned += 1
            chat = dialog.chat
            if chat is None:
                continue
            # Сравниваем username
            chat_username = getattr(chat, "username", None)
            if chat_username and chat_username.lower() == needle:
                display_name = (
                    getattr(chat, "title", None) or getattr(chat, "first_name", "") or chat_username
                )
                logger.debug(
                    "resolver_strategy3_username_match",
                    target=target,
                    chat_id=chat.id,
                    scanned=scanned,
                )
                return {
                    "ok": True,
                    "peer_id": chat.id,
                    "peer_obj": chat,
                    "username": chat_username,
                    "display_name": display_name,
                    "strategy_used": "dialog_scan",
                }
            # Сравниваем title/first_name (нечёткое, только exact lower)
            title = getattr(chat, "title", None) or ""
            first_name = getattr(chat, "first_name", None) or ""
            if needle in title.lower() or needle in first_name.lower():
                logger.debug(
                    "resolver_strategy3_name_match", target=target, chat_id=chat.id, scanned=scanned
                )
                display_name = title or first_name or needle
                return {
                    "ok": True,
                    "peer_id": chat.id,
                    "peer_obj": chat,
                    "username": chat_username,
                    "display_name": display_name,
                    "strategy_used": "dialog_scan",
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolver_strategy3_fail", target=target, error=str(exc), scanned=scanned)
    return None


def _cache_store_result(result: dict[str, Any], fallback_target: str) -> None:
    """Сохраняет успешный результат резолва в кэш контактов."""
    username = result.get("username") or _strip_at(fallback_target)
    peer_id = result.get("peer_id")
    display_name = result.get("display_name") or username
    # Числовые peer_id и "me" не кэшируем по username
    if not username or not peer_id or str(peer_id) == peer_id:
        return
    try:
        _cache_store(username, int(peer_id), display_name or username)
    except Exception as exc:  # noqa: BLE001
        logger.debug("resolver_cache_store_error", error=str(exc))


async def resolve_peer(client: "Client", target: str) -> dict[str, Any]:
    """
    Многостратегийный резолвер Telegram peer.

    Args:
        client: Pyrogram Client (активная сессия).
        target: Имя цели — @username, username без @, t.me/username,
                числовой chat_id (строка или int) или "me".

    Returns:
        dict с ключами:
          ok (bool)
          peer_id (int | None)       — числовой ID, готов для send_message
          username (str | None)      — @-имя без символа @
          display_name (str | None)  — человекочитаемое имя
          strategy_used (str | None) — какая стратегия сработала
          error_code (str)           — только при ok=False
          tried_strategies (list)    — список опробованных стратегий
          suggestions (list[str])    — подсказки при неудаче
    """
    target = str(target).strip()
    tried: list[str] = []

    logger.info("resolver_start", target=target)

    # --- Кэш контактов: проверяем до любых API-вызовов ---
    cached = _cache_lookup(target)
    if cached:
        logger.debug("resolver_cache_hit", target=target, peer_id=cached.get("peer_id"))
        return {
            "ok": True,
            "peer_id": cached["peer_id"],
            "username": cached.get("username"),
            "display_name": cached.get("display_name"),
            "strategy_used": "contact_cache",
        }

    # Числовой ID — сразу возвращаем без resolve
    if re.fullmatch(r"-?\d+", target):
        logger.debug("resolver_numeric_id", target=target)
        return {
            "ok": True,
            "peer_id": int(target),
            "username": None,
            "display_name": None,
            "strategy_used": "numeric_id",
        }

    # "me" — специальный токен Pyrogram
    if target.lower() == "me":
        return {
            "ok": True,
            "peer_id": "me",
            "username": None,
            "display_name": "Saved Messages",
            "strategy_used": "numeric_id",
        }

    # Стратегия 4 (препроцессинг): t.me/ ссылка → извлекаем username и переходим дальше
    tme_user = _extract_tme_username(target)
    if tme_user:
        logger.debug("resolver_tme_extracted", target=target, extracted=tme_user)
        target = tme_user  # дальше идём уже с plain username

    # Нормализуем: добавляем @ если похоже на username
    query = target
    if _is_username(target) and not target.startswith("@"):
        query = "@" + target

    # --- Стратегия 1: resolve_peer ---
    tried.append("resolve_peer")
    result = await _strategy_resolve_peer(client, query)
    if result:
        logger.info(
            "resolver_success",
            target=target,
            strategy=result["strategy_used"],
            peer_id=result["peer_id"],
        )
        _cache_store_result(result, target)
        return result

    # --- Стратегия 2: get_users (только для username-подобных строк) ---
    if _is_username(target):
        tried.append("get_users")
        result = await _strategy_get_users(client, query)
        if result:
            logger.info(
                "resolver_success",
                target=target,
                strategy=result["strategy_used"],
                peer_id=result["peer_id"],
            )
            _cache_store_result(result, target)
            return result

    # --- Стратегия 3: scan dialogs ---
    tried.append("dialog_scan")
    result = await _strategy_dialog_scan(client, target)
    if result:
        logger.info(
            "resolver_success",
            target=target,
            strategy=result["strategy_used"],
            peer_id=result["peer_id"],
        )
        _cache_store_result(result, target)
        return result

    # --- Все стратегии исчерпаны ---
    suggestions: list[str] = []
    if _is_username(target):
        suggestions.append(f"Убедитесь что @{_strip_at(target)} существует и не приватный аккаунт")
        suggestions.append(
            "Попробуйте найти пользователя через поиск в Telegram и написать ему сначала"
        )
        suggestions.append("Используйте числовой peer_id вместо username")
    else:
        suggestions.append("Используйте формат @username или числовой chat_id")
        suggestions.append("Для t.me ссылок используйте https://t.me/username")

    logger.warning(
        "resolver_failed",
        target=target,
        tried_strategies=tried,
        suggestions=suggestions,
    )
    return {
        "ok": False,
        "peer_id": None,
        "username": None,
        "display_name": None,
        "strategy_used": None,
        "error_code": "PEER_NOT_FOUND",
        "tried_strategies": tried,
        "suggestions": suggestions,
    }

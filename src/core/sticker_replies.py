# -*- coding: utf-8 -*-
"""
Sticker replies decision engine (Idea 2).

Pure decision-only сервис: получает сгенерированный текстовый ответ Краба и
решает, можно ли его заменить sticker'ом. Сам стикер НЕ отправляет — это
работа bridge/handler слоёв (см. backlog).

### Зачем

Часто LLM генерирует короткое подтверждающее сообщение типа «ок», «понял»,
«спасибо» или эмодзи-heavy реакцию вроде «🔥🔥🔥». В таких случаях стикер
читается живее текста и выглядит человечнее. Цель — не сэкономить токены
(они уже потрачены), а сделать общение менее «ботовским» в чатах, где
оператор включил режим стикеров.

### Что делает

1. Хранит маппинг `pattern → sticker_id` (regex или эмодзи).
2. Хранит per-chat opt-in (по-умолчанию выключено): не каждому собеседнику
   нужны стикеры от Краба.
3. На входе — текст ответа + chat_id; на выходе — sticker_id (str) или None.

### Что НЕ делает

- Не отправляет в Telegram.
- Не сохраняет историю стикерных реакций.
- Не управляет sticker pack'ом (file_id'ы передаются снаружи как configurable).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Дефолтный встроенный набор шаблонов. file_id — placeholder'ы, которые owner
# заменит через ENV/конфиг при wire-up. Ключи — стабильные имена слотов; их
# можно ремапить на реальные стикеры без правки кода.
DEFAULT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (slot_name, regex_pattern, description)
    (
        "ack_ok",
        r"^(ок|окей|ok|okay|понял|поняла|принято|good|пон)[\.\!\?]*$",
        "короткое подтверждение",
    ),
    ("ack_thanks", r"^(спасибо|спс|thanks|thx|благодарю|пасиб|сяп)[\.\!\?]*$", "благодарность"),
    ("emoji_fire", r"^[\s]*(🔥+)[\s]*$", "только огонь"),
    ("emoji_laugh", r"^[\s]*(😂+|🤣+|😁+)[\s]*$", "смех"),
    ("emoji_thumbs", r"^[\s]*(👍+|👌+)[\s]*$", "одобрение"),
    ("emoji_heart", r"^[\s]*(❤️+|💖+|♥+)[\s]*$", "сердце"),
    ("emoji_party", r"^[\s]*(🎉+|🥳+)[\s]*$", "праздник"),
    ("crab_greeting", r"^(привет|здаров|здарова|hi|hello|хай)[\.\!\?]*$", "приветствие → 🦀"),
)


# Дефолтный slot → sticker_id маппинг. Реальные file_id'ы owner подставит
# через `engine.set_sticker_id(slot, file_id)` или через JSON config.
# До тех пор используем плейсхолдер-маркеры, чтобы None никогда не возвращался
# случайно из-за «забыл заполнить».
_PLACEHOLDER_PREFIX = "PLACEHOLDER_STICKER:"


class StickerRepliesEngine:
    """Чистый decision engine для замены текстовых ответов стикерами.

    Singleton-паттерн как в `chat_ban_cache`: модульный экземпляр
    `sticker_replies_engine` создаётся ниже, путь к JSON-store настраивается
    через `configure_default_path()` из bootstrap.

    Состояние:
    - `_chats_enabled`: set[str] — chat_id'ы, в которых owner разрешил стикеры.
    - `_patterns`: список (slot, compiled_regex) — порядок важен (первый match
      побеждает; ставим более специфичные шаблоны раньше общих).
    - `_sticker_ids`: slot → file_id маппинг.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._chats_enabled: set[str] = set()
        # slot → (regex, description); порядок сохраняем через list of tuples.
        self._patterns: list[tuple[str, re.Pattern[str], str]] = []
        # slot → sticker file_id (или плейсхолдер).
        self._sticker_ids: dict[str, str] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._install_default_patterns()
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Привязывает JSON-store и подгружает opt-in список с диска."""
        with self._lock:
            self._storage_path = storage_path
            self._chats_enabled = set()
            self._load_from_disk()

    def _install_default_patterns(self) -> None:
        for slot, pattern, desc in DEFAULT_PATTERNS:
            try:
                compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
            except re.error as exc:
                # Битый дефолтный regex — баг в коде, не в данных.
                logger.warning(
                    "sticker_replies_default_pattern_invalid",
                    slot=slot,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            self._patterns.append((slot, compiled, desc))
            self._sticker_ids.setdefault(slot, f"{_PLACEHOLDER_PREFIX}{slot}")

    # ---- Public decision API --------------------------------------------

    def should_reply_with_sticker(
        self,
        text_response: str,
        *,
        chat_id: Any,
    ) -> str | None:
        """Возвращает sticker_id (str) если ответ можно заменить стикером, иначе None.

        Решение принимается так:
        1. Чат должен быть opt-in (`enable_for_chat`). Иначе сразу None.
        2. Текст должен быть коротким (<= 32 символов после strip) — длинные
           ответы стикером не заменишь без потери смысла.
        3. Один из шаблонов должен полностью матчить текст. Первый match
           побеждает — поэтому в DEFAULT_PATTERNS ставим более узкие выше.
        4. Слот должен иметь зарегистрированный sticker_id (не placeholder).
           Если только placeholder — возвращаем None и логируем, чтобы owner
           увидел что нужно сконфигурить.
        """
        target = self._normalize_chat(chat_id)
        if not target:
            return None
        text = (text_response or "").strip()
        if not text or len(text) > 32:
            return None
        with self._lock:
            if target not in self._chats_enabled:
                return None
            for slot, regex, _desc in self._patterns:
                if regex.fullmatch(text) is None:
                    continue
                sticker_id = self._sticker_ids.get(slot)
                if not sticker_id or sticker_id.startswith(_PLACEHOLDER_PREFIX):
                    logger.info(
                        "sticker_replies_slot_unconfigured",
                        slot=slot,
                        chat_id=target,
                    )
                    return None
                logger.info(
                    "sticker_replies_match",
                    slot=slot,
                    chat_id=target,
                    text_len=len(text),
                )
                return sticker_id
        return None

    # ---- Per-chat opt-in management ------------------------------------

    def enable_for_chat(self, chat_id: Any) -> bool:
        """Включает стикерные ответы для чата. True если состояние изменилось."""
        target = self._normalize_chat(chat_id)
        if not target:
            return False
        with self._lock:
            if target in self._chats_enabled:
                return False
            self._chats_enabled.add(target)
            self._persist_to_disk()
        logger.info("sticker_replies_chat_enabled", chat_id=target)
        return True

    def disable_for_chat(self, chat_id: Any) -> bool:
        """Выключает стикерные ответы для чата. True если состояние изменилось."""
        target = self._normalize_chat(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._chats_enabled:
                return False
            self._chats_enabled.discard(target)
            self._persist_to_disk()
        logger.info("sticker_replies_chat_disabled", chat_id=target)
        return True

    def is_enabled_for_chat(self, chat_id: Any) -> bool:
        target = self._normalize_chat(chat_id)
        if not target:
            return False
        with self._lock:
            return target in self._chats_enabled

    def list_enabled_chats(self) -> list[str]:
        """Снимок opt-in списка (копия, чтобы caller не мутировал внутреннее)."""
        with self._lock:
            return sorted(self._chats_enabled)

    # ---- Sticker mapping management -------------------------------------

    def set_sticker_id(self, slot: str, sticker_id: str) -> None:
        """Регистрирует/перезаписывает file_id для слота."""
        slot_norm = (slot or "").strip()
        sticker_norm = (sticker_id or "").strip()
        if not slot_norm or not sticker_norm:
            return
        with self._lock:
            self._sticker_ids[slot_norm] = sticker_norm
            self._persist_to_disk()
        logger.info("sticker_replies_slot_configured", slot=slot_norm)

    def list_slots(self) -> list[dict[str, Any]]:
        """Снимок всех слотов с описанием и текущим sticker_id (или placeholder)."""
        with self._lock:
            result: list[dict[str, Any]] = []
            for slot, regex, desc in self._patterns:
                sticker_id = self._sticker_ids.get(slot, "")
                configured = bool(sticker_id) and not sticker_id.startswith(_PLACEHOLDER_PREFIX)
                result.append(
                    {
                        "slot": slot,
                        "description": desc,
                        "pattern": regex.pattern,
                        "sticker_id": sticker_id,
                        "configured": configured,
                    }
                )
            return result

    def add_pattern(self, slot: str, pattern: str, description: str = "") -> bool:
        """Добавляет кастомный шаблон. True при успехе.

        Если slot уже существует — заменяет regex на новый. Sticker_id для
        слота ставится placeholder'ом, owner подставит через set_sticker_id.
        """
        slot_norm = (slot or "").strip()
        if not slot_norm or not pattern:
            return False
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
        except re.error as exc:
            logger.warning(
                "sticker_replies_pattern_invalid",
                slot=slot_norm,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        with self._lock:
            self._patterns = [(s, r, d) for (s, r, d) in self._patterns if s != slot_norm]
            self._patterns.append((slot_norm, compiled, description))
            self._sticker_ids.setdefault(slot_norm, f"{_PLACEHOLDER_PREFIX}{slot_norm}")
        logger.info("sticker_replies_pattern_added", slot=slot_norm)
        return True

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _normalize_chat(chat_id: Any) -> str:
        return str(chat_id or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "sticker_replies_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("sticker_replies_load_malformed", path=str(path))
            return
        chats = raw.get("chats_enabled")
        if isinstance(chats, list):
            self._chats_enabled = {str(c) for c in chats if c}
        sticker_ids = raw.get("sticker_ids")
        if isinstance(sticker_ids, dict):
            for slot, sticker_id in sticker_ids.items():
                if isinstance(slot, str) and isinstance(sticker_id, str) and sticker_id:
                    self._sticker_ids[slot] = sticker_id
        logger.info(
            "sticker_replies_loaded",
            chats=len(self._chats_enabled),
            sticker_ids=sum(
                1 for v in self._sticker_ids.values() if not v.startswith(_PLACEHOLDER_PREFIX)
            ),
        )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        payload: dict[str, Any] = {
            "chats_enabled": sorted(self._chats_enabled),
            "sticker_ids": {
                slot: sid
                for slot, sid in self._sticker_ids.items()
                if not sid.startswith(_PLACEHOLDER_PREFIX)
            },
            "updated_at": self._now_fn().isoformat(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "sticker_replies_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ---- Bulk helpers (для тестов / админ-команд) -----------------------

    def bulk_enable(self, chat_ids: Iterable[Any]) -> int:
        """Удобный bulk для тестов и owner-tooling. Возвращает кол-во добавленных."""
        added = 0
        for cid in chat_ids:
            if self.enable_for_chat(cid):
                added += 1
        return added


# Module-level singleton — паттерн как в chat_ban_cache / silence_mode.
sticker_replies_engine = StickerRepliesEngine()

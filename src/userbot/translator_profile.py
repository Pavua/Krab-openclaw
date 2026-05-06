"""
TranslatorProfileMixin — Wave 31-D.

Методы работы с persisted translator profile и session state.
Только файловая система + config, без Pyrogram dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.translator_runtime_profile import (
    load_translator_runtime_profile,
    normalize_translator_runtime_profile,
    save_translator_runtime_profile,
)
from ..core.translator_session_state import (
    apply_translator_session_update,
    default_translator_session_state,
    load_translator_session_state,
    save_translator_session_state,
)

if TYPE_CHECKING:
    pass


class TranslatorProfileMixin:
    """Wave 31-D: translator profile + session state helpers extracted from bridge."""

    @classmethod
    def _repo_root(cls) -> Path:
        """
        Возвращает корень текущего репозитория Краба.

        Нужен единый helper, чтобы userbot, web API и тесты ссылались на один и тот же
        repo-level persisted translator profile, а не расходились по рабочим каталогам.
        """
        del cls
        return Path(__file__).resolve().parent.parent.parent

    @classmethod
    def _translator_runtime_profile_path(cls) -> Path:
        """Возвращает repo-level путь persisted translator runtime profile."""
        return cls._repo_root() / "data" / "translator" / "runtime_profile.json"

    def get_translator_runtime_profile(self) -> dict[str, Any]:
        """
        Возвращает persisted translator runtime profile с короткой runtime truth-добавкой.

        Это не live session-state переводчика звонков. Здесь лежит именно product/runtime
        профиль owner-уровня, который используется командами, web-панелью и handoff.
        """
        profile = load_translator_runtime_profile(self._translator_runtime_profile_path())
        voice_profile = self.get_voice_runtime_profile()  # type: ignore[attr-defined]
        result = dict(profile)
        result["quick_phrase_count"] = len(profile.get("quick_phrases") or [])
        result["voice_foundation_ready"] = bool(voice_profile.get("live_voice_foundation"))
        result["voice_runtime_enabled"] = bool(voice_profile.get("enabled"))
        return result

    @classmethod
    def _translator_session_state_path(cls) -> Path:
        """Возвращает repo-level путь persisted translator session state."""
        return cls._repo_root() / "data" / "translator" / "session_state.json"

    def get_translator_session_state(self) -> dict[str, Any]:
        """
        Возвращает persisted translator session state с короткой runtime truth-добавкой.

        Это product-level control state, а не финальный source-of-truth live звонка.
        Но именно он нужен owner-командам и UI до подключения полноценного session feed.
        """
        state = load_translator_session_state(self._translator_session_state_path())
        profile = self.get_translator_runtime_profile()
        result = dict(state)
        result["language_pair"] = str(profile.get("language_pair") or "")
        result["target_device"] = str(profile.get("target_device") or "iphone_companion")
        return result

    def update_translator_runtime_profile(
        self,
        *,
        persist: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        """
        Обновляет persisted translator runtime profile и возвращает нормализованный срез.

        Почему логика здесь:
        - Telegram-команда `!translator` и owner web UI должны опираться на одну модель данных;
        - тесты и runtime-status не должны зависеть от разнородной ad-hoc сериализации.
        """
        path = self._translator_runtime_profile_path()
        current = load_translator_runtime_profile(path)
        normalized = normalize_translator_runtime_profile(changes, base=current)
        if persist:
            save_translator_runtime_profile(path, normalized)
        return self.get_translator_runtime_profile() if persist else normalized

    def update_translator_session_state(
        self,
        *,
        persist: bool = True,
        **changes: Any,
    ) -> dict[str, Any]:
        """
        Обновляет persisted translator session state и возвращает нормализованный срез.

        Почему логика здесь:
        - web UI, userbot-команды и handoff должны видеть один session-control слой;
        - до live feed Voice Gateway нам нужен честный persisted placeholder,
          а не ad-hoc state в памяти процесса.
        """
        path = self._translator_session_state_path()
        current = load_translator_session_state(path)
        normalized = apply_translator_session_update(changes, base=current)
        if persist:
            save_translator_session_state(path, normalized)
        return self.get_translator_session_state() if persist else normalized

    def reset_translator_session_state(self, *, persist: bool = True) -> dict[str, Any]:
        """Сбрасывает translator session state к каноническому idle-срезу."""
        state = default_translator_session_state()
        if persist:
            save_translator_session_state(self._translator_session_state_path(), state)
            return self.get_translator_session_state()
        return state

    def _is_translator_active_for_chat(self, chat_id: int | str) -> bool:
        """Проверяет, активна ли translator сессия для данного чата.

        Translator — строго opt-in: активируется только явным
        !translator on / !translator session start.
        active_chats должен содержать chat_id чтобы pipeline сработал —
        пустой список = inactive.
        """
        state = self.get_translator_session_state()
        if state.get("session_status") != "active":
            return False
        if state.get("translation_muted"):
            return False
        active_chats = state.get("active_chats") or []
        if not active_chats:
            # Пустой active_chats → сессия не привязана ни к одному чату (не активна).
            # Ранее здесь был fallback "активен для всех" — это нарушало opt-in семантику.
            return False
        return str(chat_id) in [str(c) for c in active_chats]

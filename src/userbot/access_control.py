# -*- coding: utf-8 -*-
"""
Access-control mixin для `KraabUserbot`.

Четвёртый шаг декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
Содержит ACL-проверки отправителей, trigger-детекцию, сборку системного промпта
в зависимости от уровня доступа, runtime-изоляцию chat scope и вспомогательные
утилиты (extraction аргументов команд, optional disclosure).

Замечания:
- `self.me`, `self.current_role`, `self._known_commands`,
  `self._disclosure_sent_for_chat_ids` — instance-атрибуты, инициализируются
  в `KraabUserbot.__init__`, доступны через MRO.
- Module-level singletons (`config`, ACL-функции, role prompts) импортируются
  лениво внутри тел методов, чтобы избежать циклических зависимостей при старте.

См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии разбиения.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrogram.types import Message

    from ..core.access_control import AccessLevel, AccessProfile


class AccessControlMixin:
    """
    ACL, trigger-детекция и system-prompt builder.

    Mixin для `KraabUserbot`: проверки доверия отправителя, blocklist,
    command access, runtime chat scope isolation, system prompt assembly,
    optional AI disclosure, extraction аргументов команд.
    """

    # ------------------------------------------------------------------
    # Trigger detection
    # ------------------------------------------------------------------

    def _is_trigger(self, text: str) -> bool:
        """Проверяет есть ли триггер в сообщении"""
        from ..config import config  # noqa: PLC0415

        if not text:
            return False
        text_lower = text.strip().lower()

        # Основные префиксы из конфига (!краб, @краб и т.д.)
        for prefix in config.TRIGGER_PREFIXES:
            if text_lower.startswith(prefix.lower()):
                return True

        # Просто упоминание имени в начале или конце (опционально)
        # Но по просьбе пользователя: "может и просто откликаться на Краб"
        if text_lower.startswith("краб"):
            return True

        return False

    # ------------------------------------------------------------------
    # Username / ACL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_username(value: str) -> str:
        """Нормализует username для сравнений ACL."""
        return str(value or "").strip().lstrip("@").lower()

    def _get_access_profile(self, user: object) -> "AccessProfile":
        """Возвращает ACL-профиль отправителя."""
        from ..core.access_control import (  # noqa: PLC0415
            AccessLevel,
            AccessProfile,
            resolve_access_profile,
        )

        if not user:
            return AccessProfile(level=AccessLevel.GUEST, source="missing_user", matched_subject="")
        return resolve_access_profile(
            user_id=getattr(user, "id", ""),
            username=getattr(user, "username", ""),
            self_user_id=getattr(self.me, "id", None),
        )

    def _is_allowed_sender(self, user: object) -> bool:
        """
        Проверяет, является ли отправитель доверенным участником owner/full контура.
        """
        return self._get_access_profile(user).is_trusted

    @staticmethod
    def _is_notification_sender(user: object) -> bool:
        """Определяет, является ли отправитель SMS/iMessage shortcode (≤ 5 цифр).

        Shortcode-номера (банки, аптеки, сервисы) используются для OTP и уведомлений.
        Отвечать им бессмысленно — они не принимают входящие.
        """
        username = str(getattr(user, "username", "") or "").strip().lstrip("@")
        phone = str(getattr(user, "phone", "") or "").strip().lstrip("+").replace(" ", "").replace("-", "")
        for candidate in (username, phone):
            if candidate and candidate.isdigit() and len(candidate) <= 5:
                return True
        return False

    def _is_manually_blocked(self, user: object) -> bool:
        """Проверяет наличие отправителя в MANUAL_BLOCKLIST (config или .env)."""
        from ..config import config  # noqa: PLC0415

        username = str(getattr(user, "username", "") or "").strip().lstrip("@").lower()
        user_id = str(getattr(user, "id", "") or "").strip()
        blocked: frozenset[str] = getattr(config, "MANUAL_BLOCKLIST", frozenset())
        return bool(blocked and (username in blocked or user_id in blocked))

    def _has_command_access(self, user: object, command_name: str) -> bool:
        """Проверяет доступ пользователя к конкретной Telegram-команде."""
        access_profile = self._get_access_profile(user)
        return access_profile.can_execute_command(command_name, self._known_commands)

    # ------------------------------------------------------------------
    # Runtime chat scope isolation
    # ------------------------------------------------------------------

    def _build_runtime_chat_scope_id(
        self,
        *,
        chat_id: str,
        user_id: int,
        is_allowed_sender: bool,
        access_level: str | "AccessLevel | None" = None,
    ) -> str:
        """
        Возвращает ключ сессии для LLM-контекста.

        Для неавторизованных пользователей включаем изоляцию, чтобы исключить
        смешивание истории с owner-контекстом и риск утечки персональных данных.
        """
        from ..config import config  # noqa: PLC0415
        from ..core.access_control import AccessLevel  # noqa: PLC0415

        resolved_level = str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "").strip().lower()
        if is_allowed_sender or not bool(getattr(config, "NON_OWNER_SAFE_MODE_ENABLED", True)):
            return str(chat_id)
        isolated_level = resolved_level or AccessLevel.GUEST.value
        return f"{isolated_level}:{chat_id}:{user_id}"

    # ------------------------------------------------------------------
    # System prompt assembly
    # ------------------------------------------------------------------

    def _build_system_prompt_for_sender(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str | "AccessLevel | None" = None,
    ) -> str:
        """
        Возвращает системный промпт в зависимости от доверия к отправителю.
        """
        from ..config import config  # noqa: PLC0415
        from ..core.access_control import AccessLevel  # noqa: PLC0415
        from ..core.openclaw_workspace import load_workspace_prompt_bundle  # noqa: PLC0415
        from ..employee_templates import get_role_prompt  # noqa: PLC0415

        resolved_level = str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "").strip().lower()
        if is_allowed_sender or not bool(getattr(config, "NON_OWNER_SAFE_MODE_ENABLED", True)):
            base_prompt = get_role_prompt(self.current_role)
            workspace_bundle = load_workspace_prompt_bundle()
            if workspace_bundle:
                base_prompt = (
                    f"{base_prompt}\n\n"
                    "Ниже канонический OpenClaw workspace для внешнего messaging-контура. "
                    "Это источник истины для Краба; придерживайся его, а не устаревших локальных копий.\n\n"
                    f"{workspace_bundle}"
                ).strip()
        elif resolved_level == AccessLevel.PARTIAL.value:
            partial_prompt = str(getattr(config, "PARTIAL_ACCESS_PROMPT", "") or "").strip()
            base_prompt = partial_prompt or str(getattr(config, "NON_OWNER_SAFE_PROMPT", "") or "").strip()
        else:
            safe_prompt = str(getattr(config, "NON_OWNER_SAFE_PROMPT", "") or "").strip()
            if safe_prompt:
                base_prompt = safe_prompt
            else:
                base_prompt = (
                    "Ты — нейтральный автоассистент. Не раскрывай персональные данные владельца "
                    "и внутренние рабочие сведения."
                )
        return self._append_runtime_constraints(base_prompt)

    @staticmethod
    def _append_runtime_constraints(prompt: str) -> str:
        """
        Добавляет runtime-ограничения, которые не должны теряться между ролями.
        """
        from ..config import config  # noqa: PLC0415

        base = str(prompt or "").strip()
        if not bool(getattr(config, "SCHEDULER_ENABLED", False)):
            guard = (
                "Важное ограничение runtime: фоновый scheduler/cron сейчас выключен. "
                "Не обещай, что что-то будет выполнено позже автоматически. "
                "Вместо этого честно предлагай выполнить действие сейчас или напомнить пользователю вручную при следующем сообщении."
            )
            if guard not in base:
                base = f"{base}\n\n{guard}".strip()
        return base

    # ------------------------------------------------------------------
    # Optional AI disclosure
    # ------------------------------------------------------------------

    def _apply_optional_disclosure(self, *, chat_id: str, text: str) -> str:
        """
        Опционально добавляет дисклеймер в первый ответ для конкретного чата.
        Это снижает риск «неожиданности» для новых собеседников и остается честным.
        """
        from ..config import config  # noqa: PLC0415

        if not bool(getattr(config, "AI_DISCLOSURE_ENABLED", False)):
            return text
        chat_key = str(chat_id or "").strip()
        if not chat_key:
            return text
        if chat_key in self._disclosure_sent_for_chat_ids:
            return text
        disclosure = str(getattr(config, "AI_DISCLOSURE_TEXT", "") or "").strip()
        if not disclosure:
            return text
        self._disclosure_sent_for_chat_ids.add(chat_key)
        body = str(text or "").strip()
        if not body:
            return disclosure
        return f"{disclosure}\n\n{body}"

    # ------------------------------------------------------------------
    # Command argument extraction
    # ------------------------------------------------------------------

    def _get_command_args(self, message: "Message") -> str:
        """Извлекает аргументы команды, убирая саму команду"""
        if not message.text:
            return ""

        # Если это не команда (нет префикса), возвращаем весь текст через clean_text
        # Но здесь мы знаем, что это хендлер команды
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            return parts[1].strip()
        return ""

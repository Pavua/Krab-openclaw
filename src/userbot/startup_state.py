# -*- coding: utf-8 -*-
"""
StartupState mixin для `KraabUserbot`.

Wave 31-C: извлечён из `src/userbot_bridge.py` (2026-05-05).
Содержит управление состоянием старта userbot:
- определение интерактивных ошибок логина
- установку startup_state + error_code
- маркировку relogin_required / transport_degraded
- восстановление running-состояния после probe

Зависимости через self.*:
- `self._startup_state` — str, инициализируется в KraabUserbot.__init__
- `self._startup_error_code` — str
- `self._startup_error` — str
"""

from __future__ import annotations

from ..config import config
from ..core.logger import get_logger

logger = get_logger(__name__)


class StartupStateMixin:
    """Wave 31-C: управление startup-состоянием и error-маркерами userbot."""

    @staticmethod
    def _is_interactive_login_required_error(exc: Exception) -> bool:
        """
        True, если ошибка указывает, что Pyrogram запросил интерактивный ввод
        (номер телефона/код), но консоль недоступна.
        """
        if isinstance(exc, EOFError):
            return True
        text = str(exc).lower()
        return (
            "eof when reading a line" in text
            or "phone number or bot token" in text
            or "enter phone number" in text
            or "please enter" in text
        )

    def _set_startup_state(self, *, state: str, error_code: str = "", error: str = "") -> None:
        """Обновляет внутреннее состояние старта userbot."""
        self._startup_state = str(state or "unknown")
        self._startup_error_code = str(error_code or "")
        self._startup_error = str(error or "")

    def _mark_manual_relogin_required(self, *, reason: str, error: str) -> None:
        """
        Переводит userbot в контролируемый режим `login_required` без падения процесса.
        """
        self._set_startup_state(
            state="login_required",
            error_code="telegram_session_login_required",
            error=error,
        )
        logger.warning(
            "telegram_manual_relogin_required",
            reason=reason,
            error=error,
            session_name=config.TELEGRAM_SESSION_NAME,
            next_action="run_telegram_relogin_command",
        )

    def _mark_transport_degraded(self, *, reason: str, error: str) -> None:
        """
        Помечает Telegram transport деградированным для health/lite и внешнего watchdog.

        Почему это нужно:
        - broken socket может долго жить с ложным `running`, если не обновить runtime-state;
        - внешний watchdog читает `/api/health/lite` и должен увидеть, что transport сломан;
        - `degraded` мягче, чем `login_required`, и не притворяется ручным relogin.
        """
        current_state = str(self._startup_state or "").strip().lower()
        if current_state in {"stopped", "stopping", "login_required"}:
            return
        self._set_startup_state(
            state="degraded",
            error_code="telegram_transport_degraded",
            error=error,
        )
        logger.warning("telegram_transport_marked_degraded", reason=reason, error=error)

    def _restore_running_state_after_probe(self) -> None:
        """
        Возвращает transport в `running`, если heartbeat снова healthy.
        """
        if str(self._startup_state or "").strip().lower() == "degraded":
            self._set_startup_state(state="running")
            logger.info("telegram_transport_probe_recovered")

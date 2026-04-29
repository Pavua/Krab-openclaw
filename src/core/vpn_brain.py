# -*- coding: utf-8 -*-
"""
VPN Brain — мозг для VPN-бота (@pablito_vpn_bot).

VPN-бот работает на отдельном Telegram-аккаунте и сам по себе "тупой":
принимает freeform от друзей и проксирует в Krab по HTTP. Этот модуль —
side Krab'а: формирует промпт с persona drift / memory hint, вызывает
LLM через injectable callable и возвращает ``VPNAnswer`` с метаданными.

Singleton + ``configure_llm_callable(...)`` — bootstrap wire-up из
userbot_bridge или web_app (LLM client инжектируется снаружи, чтобы
модуль оставался изолированным от конкретного провайдера).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

# Тип callable для LLM: получает prompt, возвращает текст ответа.
LLMCallable = Callable[[str], Awaitable[str]]

# Дефолтный таймаут на LLM-вызов (секунды).
DEFAULT_LLM_TIMEOUT_S: float = 30.0

# Generic fallback при таймауте/ошибке (fail-open).
_FALLBACK_TEXT = (
    "Извини, я сейчас не могу подключиться к VPN-помощнику. "
    "Попробуй ещё раз через минуту — если не пройдёт, напиши Pavua напрямую."
)


@dataclass
class VPNAnswer:
    """Ответ Krab'а на вопрос друга по VPN."""

    text: str
    confidence: float  # 0.0..1.0
    suggested_action: str | None
    latency_ms: int


def _build_prompt(
    *,
    friend_id: str,
    friend_name: str,
    question: str,
    context: Mapping[str, Any] | None,
) -> str:
    """Собирает internal prompt для LLM.

    Краткий контекст: persona Krab, информация о друге, история (если есть).
    Тон — дружелюбный, на русском.
    """
    history_lines: list[str] = []
    if context:
        # Поддерживаем оба формата: {"history": [...]} или произвольный dict.
        hist = context.get("history") if isinstance(context, Mapping) else None
        if isinstance(hist, list):
            for item in hist[-6:]:
                if isinstance(item, Mapping):
                    role = str(item.get("role") or "user")
                    text = str(item.get("text") or "").strip()
                    if text:
                        history_lines.append(f"{role}: {text}")
                elif isinstance(item, str) and item.strip():
                    history_lines.append(item.strip())
    history_block = "\n".join(history_lines) if history_lines else "(пусто)"

    return (
        "Ты Krab — личный AI-помощник Pavua. Сейчас ты помогаешь его другу "
        f"{friend_name} (id={friend_id}) с вопросом по VPN-сервису.\n"
        "Будь дружелюбным, кратким, говори на русском. Если вопрос не про VPN — "
        "вежливо верни разговор к VPN-теме. Если можешь предложить конкретное "
        "действие (например, перевыпустить ключ, проверить статус подписки, "
        "переключить локацию), укажи его одной строкой в начале как "
        "'ACTION: <slug>' (slug — короткий идентификатор без пробелов), "
        "иначе пропусти эту строку.\n\n"
        f"История переписки:\n{history_block}\n\n"
        f"Вопрос друга: {question}\n\n"
        "Ответ:"
    )


def _parse_action(text: str) -> tuple[str, str | None]:
    """Извлекает 'ACTION: <slug>' из начала ответа.

    Возвращает ``(cleaned_text, suggested_action)``.
    """
    if not text:
        return text, None
    stripped = text.lstrip()
    if not stripped.upper().startswith("ACTION:"):
        return text, None
    # Берём первую строку.
    nl = stripped.find("\n")
    if nl == -1:
        action_line = stripped
        rest = ""
    else:
        action_line = stripped[:nl]
        rest = stripped[nl + 1 :].lstrip()
    slug = action_line.split(":", 1)[1].strip().split()[0] if ":" in action_line else None
    if slug:
        slug = slug.strip().strip(".,;:").lower() or None
    return rest or stripped, slug


class VPNBrain:
    """Мозг VPN-бота. Singleton, см. ``vpn_brain`` ниже."""

    def __init__(
        self,
        *,
        llm_callable: LLMCallable | None = None,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_S,
    ) -> None:
        self._llm_callable: LLMCallable | None = llm_callable
        self._timeout_s: float = float(timeout_s)

    def configure_llm_callable(self, llm_callable: LLMCallable | None) -> None:
        """Bootstrap: инжектируем LLM callable из userbot/web_app."""
        self._llm_callable = llm_callable

    @property
    def has_llm(self) -> bool:
        return self._llm_callable is not None

    async def answer_friend_question(
        self,
        friend_id: str,
        friend_name: str,
        question: str,
        context: Mapping[str, Any] | None = None,
    ) -> VPNAnswer:
        """Главная точка входа.

        Возвращает ``VPNAnswer`` всегда (fail-open на ошибках LLM/таймауте).
        """
        started = time.monotonic()

        question_clean = (question or "").strip()
        if not question_clean:
            return VPNAnswer(
                text="Похоже, вопрос пустой. Напиши, что именно не работает с VPN.",
                confidence=0.0,
                suggested_action=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        if self._llm_callable is None:
            logger.warning("vpn_brain_llm_not_configured friend_id=%s", friend_id)
            return VPNAnswer(
                text=_FALLBACK_TEXT,
                confidence=0.0,
                suggested_action=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        prompt = _build_prompt(
            friend_id=friend_id,
            friend_name=friend_name,
            question=question_clean,
            context=context,
        )

        try:
            raw = await asyncio.wait_for(
                self._llm_callable(prompt),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "vpn_brain_llm_timeout friend_id=%s timeout_s=%.1f",
                friend_id,
                self._timeout_s,
            )
            return VPNAnswer(
                text=_FALLBACK_TEXT,
                confidence=0.0,
                suggested_action=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open контракт
            logger.error(
                "vpn_brain_llm_error friend_id=%s error_type=%s error=%s",
                friend_id,
                type(exc).__name__,
                str(exc),
            )
            return VPNAnswer(
                text=_FALLBACK_TEXT,
                confidence=0.0,
                suggested_action=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        text_raw = (raw or "").strip()
        if not text_raw:
            return VPNAnswer(
                text=_FALLBACK_TEXT,
                confidence=0.0,
                suggested_action=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        cleaned, action = _parse_action(text_raw)
        # Confidence-эвристика: длина ответа + наличие action — мягкий signal.
        length = len(cleaned)
        if length < 20:
            confidence = 0.4
        elif length < 80:
            confidence = 0.7
        else:
            confidence = 0.85
        if action:
            confidence = min(0.95, confidence + 0.05)

        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "vpn_brain_answer_ok friend_id=%s latency_ms=%d action=%s",
            friend_id,
            latency_ms,
            action or "-",
        )
        return VPNAnswer(
            text=cleaned,
            confidence=confidence,
            suggested_action=action,
            latency_ms=latency_ms,
        )


# Module-level singleton.
vpn_brain = VPNBrain()


def configure_llm_callable(llm_callable: LLMCallable | None) -> None:
    """Module-level alias для bootstrap wire-up."""
    vpn_brain.configure_llm_callable(llm_callable)

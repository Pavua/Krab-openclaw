# -*- coding: utf-8 -*-
"""
Утилиты безопасного извлечения текста из Telegram Message.

Зачем это нужно:
- в редких апдейтах Pyrogram может бросать UnicodeDecodeError уже при доступе к
  message.text/message.caption (до любых слайсов/преобразований);
- центральный helper позволяет единообразно обрабатывать такие случаи и
  не ронять обработчики, которые логируют входящие сообщения.
"""

from __future__ import annotations


def extract_message_text_safe(message, fallback_label: str = "Text") -> str:
    """
    Возвращает текст/caption сообщения с защитой от ошибок декодирования.

    Если text/caption недоступны или повреждены, возвращает fallback-маркер.
    """
    fallback = f"[{str(fallback_label or 'Text')}]"

    raw_text = None
    try:
        raw_text = getattr(message, "text", None)
    except Exception:
        raw_text = None

    if not raw_text:
        try:
            raw_text = getattr(message, "caption", None)
        except Exception:
            raw_text = None

    if raw_text is None:
        raw_text = fallback

    try:
        return str(raw_text)
    except Exception:
        return fallback


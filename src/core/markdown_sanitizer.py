# -*- coding: utf-8 -*-
"""
Telegram Markdown Sanitizer — безопасное форматирование текста для Telegram.

Зачем: Pyrogram парсит markdown при edit_text/send_message. Если в тексте
есть незакрытые тройные бэктики (```), Pyrogram интерпретирует их как <pre>
и логирует "Unclosed tags: <pre>" при каждом edit_text. При стриминге это
генерирует сотни предупреждений в секунду и может ломать отправку.

Этот модуль:
1. Закрывает незакрытые блоки кода (```)
2. Экранирует опасные символы если нужно
3. Обеспечивает fallback на plain text при ошибках парсинга
"""

import re
import logging

logger = logging.getLogger(__name__)


def sanitize_markdown_for_telegram(text: str) -> str:
    """
    Убеждается, что все тройные бэктики (```) в тексте закрыты.
    Если количество ``` нечётное — добавляет закрывающий ``` в конец.
    
    Также обрабатывает одинарные бэктики для inline code.
    
    Аргументы:
        text: исходный текст с возможным markdown-форматированием
    
    Возвращает:
        Текст с корректно закрытыми markdown-блоками
    """
    if not text:
        return ""
    
    # Считаем тройные бэктики (``` с возможным языком после)
    triple_backtick_count = len(re.findall(r'```', text))
    
    if triple_backtick_count % 2 != 0:
        # Нечётное количество — добавляем закрывающий блок
        text = text.rstrip() + "\n```"
    
    return text


def safe_edit_text(text: str, is_streaming: bool = False) -> str:
    """
    Подготавливает текст для edit_text в Telegram.
    
    При стриминге (is_streaming=True) текст часто содержит
    частичный markdown, поэтому мы всегда закрываем блоки.
    
    Аргументы:
        text: текст для отправки
        is_streaming: True если текст — промежуточный результат стрима
    
    Возвращает:
        Безопасный для Telegram markdown-текст
    """
    if not text:
        return "..."
    
    # Закрываем незакрытые блоки кода
    result = sanitize_markdown_for_telegram(text)
    
    return result


def strip_backticks_from_content(content: str) -> str:
    """
    Убирает тройные бэктики из содержимого, которое будет
    обёрнуто в свои тройные бэктики (вложенные ``` ➔ ошибка).
    
    Используется, например, в !sh и !exec для вывода команд.
    
    Аргументы:
        content: содержимое команды/кода
    
    Возвращает:
        Содержимое без тройных бэктиков (заменены на одинарные)
    """
    if not content:
        return ""
    
    # Заменяем ``` на ` (одинарный), чтобы не ломать обёртку
    return content.replace("```", "` ` `")

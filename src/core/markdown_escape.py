"""
Markdown escape helpers для безопасного вывода raw-текста в parse_mode=markdown.

Причина: Pyrogram parse_mode="markdown" интерпретирует `*`, `_`, `[`, `]`, `~`,
"`", `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!` как markdown-разметку.
Если в raw-тексте (например, имени файла или сообщении пользователя) встречается
`file_name.py`, Telegram бросает ParseError / BadRequest и ответ теряется.

Используй `escape_markdown()` при выводе user-input/raw-data в MD-mode.
"""

from __future__ import annotations

# Полный набор спецсимволов Telegram MarkdownV1-style (pyrogram).
_MD_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def escape_markdown(text: str | None) -> str | None:
    """
    Экранирует спецсимволы markdown в raw-тексте.

    Args:
        text: Исходная строка. Если None — возвращается None, если пустая — пустая.

    Returns:
        Строка с экранированными через `\\` markdown-спецсимволами.

    Examples:
        >>> escape_markdown("hello")
        'hello'
        >>> escape_markdown("**bold**")
        '\\\\*\\\\*bold\\\\*\\\\*'
        >>> escape_markdown("file_name.py")
        'file\\\\_name\\\\.py'
    """
    if text is None:
        return None
    if not text:
        return text
    result: list[str] = []
    for ch in text:
        if ch in _MD_SPECIAL:
            result.append("\\" + ch)
        else:
            result.append(ch)
    return "".join(result)


def looks_like_parse_error(exc: Exception) -> bool:
    """
    Определяет, связана ли ошибка Telegram с парсингом markdown entity.

    Pyrogram при невалидной разметке возвращает BAD_REQUEST с
    "can't parse entities" / "unclosed" / "unsupported start tag".
    """
    text = str(exc).lower()
    markers = (
        "can't parse",
        "cant parse",
        "unclosed",
        "unsupported start tag",
        "entity",
        "parse error",
        "message_parse",
        "markdown",
    )
    return any(m in text for m in markers)

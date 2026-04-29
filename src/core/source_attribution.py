# -*- coding: utf-8 -*-
"""
Source Attribution (Idea 16) — pure value-объекты для пометки происхождения фактов.

Зачем: когда Krab достаёт факт из памяти / веба / tool-output / прямого user input,
он должен мочь показать "откуда взято" в человекочитаемом виде. Сейчас retrieval
теряет провенанс — chunks приходят без metadata, и в ответе модель не может сказать
"из чата How2AI 24.04" или "из веб-поиска (yandex.ru)".

Этот модуль — pure data layer без хуков:
  - `SourceAttribution` — нормализованное описание источника
  - `format_attribution(src)` — human-readable строка для вставки в prompt/reply
  - `SourcedFact` — пара (text, source)
  - `attach_source_to_chunks(chunks, default_source)` — обогатить список чанков

Caller (memory_engine на момент retrieve, web search summariser, tool dispatcher)
сам решает, когда вызвать `attach_source_to_chunks` и как пробросить метаданные
дальше. Этот модуль никуда не хукается, ничего не персистит, не имеет singleton.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

# Допустимые origin'ы. Расширяем consciously — каждое значение должно иметь свой
# branch в format_attribution, иначе будет fallback на generic "из источника".
SourceOrigin = Literal["memory", "web", "tool", "user"]

_VALID_ORIGINS: frozenset[str] = frozenset({"memory", "web", "tool", "user"})


@dataclass(frozen=True, slots=True)
class SourceAttribution:
    """
    Нормализованное описание источника факта.

    Все поля кроме `origin` опциональны — caller заполняет то, что доступно.
    Frozen, чтобы можно было безопасно шарить между чанками одного retrieval-batch
    (см. `attach_source_to_chunks`).

    Поля:
        origin: тип источника (см. SourceOrigin)
        chat_id: Telegram chat_id если origin == "memory" и факт пришёл из чата
        chat_title: человекочитаемое имя чата ("How2AI", "Saved Messages")
        url: URL для origin == "web"
        domain: домен для origin == "web" (yandex.ru, wikipedia.org)
        tool_name: имя tool'а для origin == "tool" (web_search, peekaboo)
        timestamp: когда факт был записан/получен (UTC)
        confidence: уверенность 0.0..1.0 (для retrieval rerank scores)
        extra: произвольная metadata, не ломающая format_attribution
    """

    origin: SourceOrigin
    chat_id: int | None = None
    chat_title: str | None = None
    url: str | None = None
    domain: str | None = None
    tool_name: str | None = None
    timestamp: datetime | None = None
    confidence: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.origin not in _VALID_ORIGINS:
            raise ValueError(
                f"invalid_source_origin origin={self.origin!r} valid={sorted(_VALID_ORIGINS)}"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"invalid_source_confidence value={self.confidence} expected_range=0.0..1.0"
            )


@dataclass(frozen=True, slots=True)
class SourcedFact:
    """
    Факт + его атрибуция. Используется retrieval-слоями как обёртка над сырым
    chunk-текстом перед передачей в LLM-промпт.
    """

    text: str
    source: SourceAttribution


def _format_date(ts: datetime | None) -> str | None:
    """Форматирует timestamp в `DD.MM` (короткая форма для inline-вставки)."""
    if ts is None:
        return None
    # Если naive — считаем UTC (defensive, не должно случаться при правильном caller'е).
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.strftime("%d.%m")


def format_attribution(src: SourceAttribution) -> str:
    """
    Превращает SourceAttribution в человекочитаемую русскую строку.

    Примеры:
      memory + chat_title="How2AI" + ts → "из чата How2AI (24.04)"
      memory + chat_title=None + ts → "из памяти (24.04)"
      web + domain="yandex.ru" → "из веб-поиска (yandex.ru)"
      web + url="https://example.org/x" → "из веб-поиска (example.org)"
      tool + tool_name="web_search" → "из инструмента web_search"
      user → "от пользователя"

    Никогда не raise'ит — для невалидных комбинаций возвращает мягкий fallback.
    """
    date_str = _format_date(src.timestamp)

    if src.origin == "memory":
        if src.chat_title:
            base = f"из чата {src.chat_title}"
        elif src.chat_id is not None:
            base = f"из чата {src.chat_id}"
        else:
            base = "из памяти"
        return f"{base} ({date_str})" if date_str else base

    if src.origin == "web":
        domain = src.domain or _extract_domain(src.url)
        if domain:
            base = f"из веб-поиска ({domain})"
        else:
            base = "из веб-поиска"
        return f"{base} ({date_str})" if date_str else base

    if src.origin == "tool":
        base = f"из инструмента {src.tool_name}" if src.tool_name else "из инструмента"
        return f"{base} ({date_str})" if date_str else base

    if src.origin == "user":
        return f"от пользователя ({date_str})" if date_str else "от пользователя"

    # Защита от будущих расширений Literal без обновления функции.
    return "из источника"


def _extract_domain(url: str | None) -> str | None:
    """Минимальный domain-extract без зависимости от urllib (быстрее и без сюрпризов)."""
    if not url:
        return None
    s = url.strip()
    # Срезаем схему
    for prefix in ("https://", "http://", "//"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    # Срезаем path/query/fragment
    for sep in ("/", "?", "#"):
        idx = s.find(sep)
        if idx != -1:
            s = s[:idx]
    # Срезаем порт и user@
    if "@" in s:
        s = s.split("@", 1)[1]
    if ":" in s:
        s = s.split(":", 1)[0]
    return s or None


def attach_source_to_chunks(
    chunks: Iterable[Any],
    default_source: SourceAttribution,
) -> list[SourcedFact]:
    """
    Обогащает список чанков атрибуцией.

    Поведение:
      - str → SourcedFact(text=str, source=default_source)
      - SourcedFact → возвращается как есть (idempotent — атрибуция не перезаписывается)
      - dict с ключом "text" → SourcedFact(text=dict["text"], source=...);
        если в dict есть "source" типа SourceAttribution — она используется,
        иначе default_source
      - объект с атрибутом `text` → аналогично dict
      - всё остальное → str(item) с default_source

    Возвращает новый list — caller'у безопасно мутировать его.
    """
    result: list[SourcedFact] = []
    for item in chunks:
        if isinstance(item, SourcedFact):
            # Idempotent: повторный attach не перезаписывает существующую атрибуцию.
            result.append(item)
            continue
        if isinstance(item, str):
            result.append(SourcedFact(text=item, source=default_source))
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if text is None:
                # dict без text — кладём str-репрезентацию, чтобы caller увидел проблему.
                text = str(item)
            existing = item.get("source")
            src = existing if isinstance(existing, SourceAttribution) else default_source
            result.append(SourcedFact(text=str(text), source=src))
            continue
        # Generic объект с .text
        text_attr = getattr(item, "text", None)
        if text_attr is not None:
            existing = getattr(item, "source", None)
            src = existing if isinstance(existing, SourceAttribution) else default_source
            result.append(SourcedFact(text=str(text_attr), source=src))
            continue
        # Fallback: stringify
        result.append(SourcedFact(text=str(item), source=default_source))
    return result


def with_confidence(src: SourceAttribution, confidence: float) -> SourceAttribution:
    """Утилита: вернуть копию с новой confidence (для rerank-pipeline)."""
    return replace(src, confidence=confidence)


__all__ = [
    "SourceAttribution",
    "SourceOrigin",
    "SourcedFact",
    "attach_source_to_chunks",
    "format_attribution",
    "with_confidence",
]

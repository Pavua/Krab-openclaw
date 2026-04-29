# -*- coding: utf-8 -*-
"""
MemorySummarizer — Rolling Auto-Summarization (Idea 14, Session 28).

Periodically condense старые chunks чата в короткое summary, освобождая место
в context window. Pure builder: умеет посчитать пора ли суммировать, дёрнуть
LLM (LM Studio локально), сложить результат в JSON-стор и отдать обратно по
chat_id с дедупликацией по message_ids. Wire-up в `memory_engine` retrieval —
отдельная задача из backlog.

Дизайн:

- **Pure module.** Не дёргает userbot_bridge / memory_engine. Всё что трогает
  диск — через `storage_path` (опциональный). Тесты пишут в tmp_path.
- **Threshold-based.** `should_summarize(chat_id, message_count)` сравнивает
  общее количество сообщений в чате с количеством, покрытым последним
  summary. Если разница ≥ threshold (default 100) — пора.
- **LLM mockable.** Базовая реализация дёргает LM Studio через httpx
  POST. Тесты подменяют `summarize_window` через `llm_call` injection,
  не трогая сеть.
- **Dedup по covers_message_ids.** Повторный `record_summary` для того же
  набора message_ids — no-op (идемпотентно). Это защищает от двойной записи
  если memory_engine дёрнул summarize дважды на одном окне.
- **Persist per write.** Маленький JSON-стор (ожидается <1KB на чат, не более
  десятка чатов с активной summarization). На каждый record / clear файл
  переписывается. Reads — из in-memory dict.

Не решает:
- Не запускает summarize автоматически — caller должен вызвать
  `should_summarize` → собрать chunks → `summarize_window`.
- Не интегрировано в retrieval (memory_engine merge) — backlog.
- Не делает hierarchical summaries (summary of summaries) — будущий итератив.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .logger import get_logger

logger = get_logger(__name__)


# Defaults: подбирались эмпирически. 100 сообщений — типичное окно «активного
# дня» в одной группе. 500 chars — короткое summary, ~5-7 буллетов суть.
_DEFAULT_THRESHOLD: int = 100
_DEFAULT_TARGET_CHARS: int = 500
_LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
_LM_STUDIO_TIMEOUT = 8.0


@dataclass(frozen=True)
class RollingSummary:
    """Снимок старого окна сообщений в одну сжатую сводку."""

    chat_id: str
    summary_text: str
    covers_message_ids: list[int] = field(default_factory=list)
    generated_at: str = ""  # ISO8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RollingSummary:
        ids_raw = raw.get("covers_message_ids") or []
        ids: list[int] = []
        if isinstance(ids_raw, list):
            for item in ids_raw:
                try:
                    ids.append(int(item))
                except (TypeError, ValueError):
                    continue
        return cls(
            chat_id=str(raw.get("chat_id") or ""),
            summary_text=str(raw.get("summary_text") or ""),
            covers_message_ids=ids,
            generated_at=str(raw.get("generated_at") or ""),
        )


# Тип LLM-функции: получает prompt, возвращает summary text. Нужен для
# инъекции в тестах (mock без httpx) и потенциально для подмены провайдера.
LLMCall = Callable[[str], Awaitable[str]]


class MemorySummarizer:
    """Периодическая суммаризация старых сообщений + persist в JSON.

    Используется как module-level singleton (`memory_summarizer` ниже). Принимает
    `storage_path` в конструкторе ТОЛЬКО для unit-тестов; в рантайме singleton
    инициализируется через `configure_default_path()`.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        threshold: int = _DEFAULT_THRESHOLD,
        target_chars: int = _DEFAULT_TARGET_CHARS,
        lm_url: str = _LM_STUDIO_URL,
        lm_timeout: float = _LM_STUDIO_TIMEOUT,
        llm_call: LLMCall | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        # chat_id (str) → последняя записанная RollingSummary
        self._entries: dict[str, RollingSummary] = {}
        self._threshold = max(1, int(threshold))
        self._target_chars = max(100, int(target_chars))
        self._lm_url = lm_url
        self._lm_timeout = lm_timeout
        self._llm_call: LLMCall | None = llm_call
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает то что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    def configure_threshold(self, threshold: int) -> None:
        """Owner override порога (например, через `!config KRAB_ROLLING_SUMMARY_THRESHOLD`)."""
        with self._lock:
            self._threshold = max(1, int(threshold))

    # ---- Core API -------------------------------------------------------

    def should_summarize(self, chat_id: Any, message_count: int) -> bool:
        """True → пора запускать summarize_window для этого чата.

        Сравнивает общее число сообщений в чате с количеством message_ids,
        уже покрытых последним summary. Если разница ≥ threshold — да.
        """
        target = self._normalize(chat_id)
        if not target:
            return False
        try:
            count = int(message_count)
        except (TypeError, ValueError):
            return False
        if count <= 0:
            return False
        with self._lock:
            existing = self._entries.get(target)
            covered = len(existing.covers_message_ids) if existing else 0
            delta = count - covered
        return delta >= self._threshold

    async def summarize_window(
        self,
        chat_id: Any,
        messages: list[dict[str, Any]],
        *,
        target_chars: int | None = None,
        message_ids: list[int] | None = None,
        persist: bool = True,
    ) -> RollingSummary | None:
        """Сжимает окно сообщений в RollingSummary через LLM.

        `messages` — список dict вида ``{"sender": str, "text": str}``. Любые
        лишние ключи игнорируются. Если список пустой — возвращает None.
        `message_ids` — явные ID для дедупа; если None, выводятся из
        `messages[i]["id"]` если есть.
        """
        target = self._normalize(chat_id)
        if not target:
            return None
        if not messages:
            return None

        ids = self._extract_ids(messages, message_ids)
        # Дедуп: если этот ровно тот же набор IDs уже покрыт — возвращаем
        # существующее без повторного LLM-вызова.
        with self._lock:
            existing = self._entries.get(target)
            if existing and ids and set(ids) == set(existing.covers_message_ids):
                logger.info(
                    "rolling_summary_dedup_hit",
                    chat_id=target,
                    covered=len(ids),
                )
                return existing

        chars = max(100, int(target_chars or self._target_chars))
        prompt = self._build_prompt(messages, target_chars=chars)
        start = time.monotonic()
        try:
            raw_text = await self._invoke_llm(prompt)
        except Exception as exc:  # fail-open
            logger.warning(
                "rolling_summary_llm_failed",
                chat_id=target,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        summary_text = (raw_text or "").strip()
        if not summary_text:
            logger.warning("rolling_summary_empty_output", chat_id=target)
            return None
        if len(summary_text) > chars * 4:
            # Жёсткий cap: LLM иногда игнорирует target_chars. Обрезаем чтобы
            # не раздуть JSON-стор и context window.
            summary_text = summary_text[: chars * 4].rstrip() + "…"

        snapshot = RollingSummary(
            chat_id=target,
            summary_text=summary_text,
            covers_message_ids=list(ids),
            generated_at=self._now().isoformat(),
        )
        if persist:
            self.record_summary(snapshot)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "rolling_summary_generated",
            chat_id=target,
            covered=len(ids),
            chars=len(summary_text),
            latency_ms=latency_ms,
        )
        return snapshot

    def record_summary(self, summary: RollingSummary) -> bool:
        """Записать summary в стор. Идемпотентно по covers_message_ids.

        Возвращает True если запись действительно изменилась (новая или другие IDs),
        False если был exact-match дубликат.
        """
        if not summary.chat_id:
            return False
        with self._lock:
            existing = self._entries.get(summary.chat_id)
            if (
                existing
                and existing.summary_text == summary.summary_text
                and set(existing.covers_message_ids) == set(summary.covers_message_ids)
            ):
                return False
            self._entries[summary.chat_id] = summary
            self._persist_to_disk()
        return True

    def get_summary(self, chat_id: Any) -> RollingSummary | None:
        """Достать последний summary по chat_id. None если нет."""
        target = self._normalize(chat_id)
        if not target:
            return None
        with self._lock:
            return self._entries.get(target)

    def list_summaries(self) -> list[RollingSummary]:
        """Снимок всех записей (копии, не reference)."""
        with self._lock:
            return list(self._entries.values())

    def clear(self, chat_id: Any) -> bool:
        """Удалить summary для чата. True если запись была."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._entries:
                return False
            del self._entries[target]
            self._persist_to_disk()
        logger.info("rolling_summary_cleared", chat_id=target)
        return True

    # ---- Internal helpers -----------------------------------------------

    def _now(self) -> datetime:
        return self._now_fn()

    @staticmethod
    def _normalize(chat_id: Any) -> str:
        return str(chat_id or "").strip()

    @staticmethod
    def _extract_ids(messages: list[dict[str, Any]], message_ids: list[int] | None) -> list[int]:
        if message_ids is not None:
            out: list[int] = []
            for item in message_ids:
                try:
                    out.append(int(item))
                except (TypeError, ValueError):
                    continue
            return out
        ids: list[int] = []
        for msg in messages:
            raw = msg.get("id") if isinstance(msg, dict) else None
            if raw is None:
                continue
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids

    @staticmethod
    def _build_prompt(messages: list[dict[str, Any]], *, target_chars: int) -> str:
        lines: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = str(msg.get("sender") or "?")
            text = str(msg.get("text") or "").strip()
            if not text:
                continue
            # Ограничиваем длинные сообщения чтобы не съесть весь контекст
            if len(text) > 400:
                text = text[:400] + "…"
            lines.append(f"{sender}: {text}")
        block = "\n".join(lines) if lines else "(пусто)"
        return f"""Сожми диалог ниже в краткое связное summary на русском (≤{target_chars} символов).

ДИАЛОГ:
\"\"\"
{block}
\"\"\"

Правила:
- Сохрани ключевые факты, имена, числа, договорённости.
- Выкини small-talk и filler.
- Без преамбул («вот summary:») — сразу текст.
"""

    async def _invoke_llm(self, prompt: str) -> str:
        if self._llm_call is not None:
            return await self._llm_call(prompt)
        async with httpx.AsyncClient(timeout=self._lm_timeout) as client:
            resp = await client.post(
                self._lm_url,
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 600,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data["choices"][0]["message"]["content"] or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "rolling_summary_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("rolling_summary_load_malformed", path=str(path))
            return
        loaded = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                summary = RollingSummary.from_dict({**value, "chat_id": str(key)})
            except (TypeError, ValueError):
                continue
            if not summary.summary_text:
                continue
            self._entries[str(key)] = summary
            loaded += 1
        if loaded:
            logger.info("rolling_summary_loaded", count=loaded)

    async def load_async(self) -> None:
        """Async-обёртка над _load_from_disk для bootstrap из event loop."""
        await asyncio.to_thread(self._load_from_disk)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {key: value.to_dict() for key, value in self._entries.items()}
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "rolling_summary_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — паттерн совпадает с chat_ban_cache, silence_mode,
# inbox_service. В рантайме конфигурируется через
# `memory_summarizer.configure_default_path(...)` из bootstrap (когда фича
# будет включена через KRAB_ROLLING_SUMMARIZATION_ENABLED).
memory_summarizer = MemorySummarizer()

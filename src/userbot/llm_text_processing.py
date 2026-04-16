# -*- coding: utf-8 -*-
"""
LLM-text processing mixin для `KraabUserbot`.

Первый шаг декомпозиции `src/userbot_bridge.py` (session 5+, 2026-04-09).
Содержит чистые текстовые трансформации: strip reasoning/transport-markup,
batching, splitting, fallback normalization. Большинство методов stateless
либо работают только с `self.*` state (hidden reasoning trace dict,
batched followup ids). Зависимостей от Pyrogram client или background task
state у них нет — кроме `_coalesce_text_burst`, которому нужен
`self.client.get_chat_history` (он используется уже после `.start()`).

См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии разбиения.

Замечание про class-level regex-паттерны:
`_reply_to_tag_pattern`, `_think_block_pattern`, `_final_block_pattern`,
`_think_final_tag_pattern`, `_tool_response_block_pattern`,
`_llm_transport_tokens_pattern`, `_think_capture_pattern`,
`_plaintext_reasoning_intro_pattern`, `_plaintext_reasoning_step_pattern`,
`_plaintext_reasoning_meta_pattern`, `_agentic_scratchpad_line_pattern`,
`_agentic_scratchpad_command_pattern`, `_split_chunk_header_pattern`,
`_deferred_intent_pattern` — НЕ переносятся в этот mixin. Они остаются
class-level атрибутами `KraabUserbot`. Методы mixin'а обращаются к ним
через `cls.*` / `self.*`, а MRO резолвит их на уровне конкретного
подкласса (`KraabUserbot`), который и содержит эти паттерны.
"""

from __future__ import annotations

import asyncio
import re
import textwrap
import time
from datetime import datetime, timezone
from typing import Any

from pyrogram import enums
from pyrogram.types import Message

from ..config import config
from ..core.access_control import AccessLevel
from ..core.logger import get_logger
from ..openclaw_client import openclaw_client

logger = get_logger("userbot_bridge")


class LLMTextProcessingMixin:
    """
    Mixin `KraabUserbot` с методами обработки текста (reasoning strip,
    batching, splitting, fallback normalization).

    Class-level regex-паттерны остаются в `KraabUserbot` — mixin к ним
    обращается через `cls.*` / `self.*` MRO lookup.
    """

    @classmethod
    def _strip_transport_markup(cls, text: str) -> str:
        """
        Удаляет служебные транспортные теги из пользовательского текста.
        Примеры:
        - `[[reply_to:12345]]`
        - `[[reply_to_current]]`
        - `<|im_start|>...<|im_end|>`
        - `<tool_response>{...}</tool_response>`
        - `<think>...</think>` / `<final>...</final>`
        """
        raw = str(text or "")
        if not raw:
            return ""
        cleaned = cls._reply_to_tag_pattern.sub("", raw)
        cleaned = cls._think_block_pattern.sub("", cleaned)
        cleaned = cls._final_block_pattern.sub(lambda match: str(match.group(1) or ""), cleaned)
        cleaned = cls._think_final_tag_pattern.sub("", cleaned)
        cleaned = cls._tool_response_block_pattern.sub("", cleaned)
        cleaned = cls._llm_transport_tokens_pattern.sub("", cleaned)
        cleaned = cls._strip_plaintext_reasoning_prefix(cleaned)
        cleaned = cls._strip_agentic_scratchpad(cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"(?mi)^\s*(assistant|user|system)\s*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _split_plaintext_reasoning_and_answer(cls, text: str) -> tuple[str, str]:
        """
        Разделяет plain-text reasoning и итоговый ответ, если провайдер прислал
        мысли без `<think>`.

        Почему нужен отдельный guard:
        - часть маршрутов может вернуть reasoning в свободном тексте вида
          `think\\nThinking Process: ...`, не используя transport-теги;
        - основной пользовательский ответ не должен смешиваться с цепочкой мыслей;
        - reasoning позже можно вернуть owner-only режимом отдельно, но не внутри
          обычного ответа.
        """
        raw = str(text or "")
        if not raw.strip():
            return "", ""

        lines = raw.splitlines()
        non_empty_indexes = [idx for idx, line in enumerate(lines) if line.strip()]
        if not non_empty_indexes:
            return "", raw.strip()

        intro_hits = 0
        for idx in non_empty_indexes[:3]:
            stripped = lines[idx].strip()
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                intro_hits += 1
                continue
            if idx == non_empty_indexes[0] and stripped.lower().startswith("thinking process:"):
                intro_hits += 1
                continue
        if intro_hits == 0:
            return "", raw.strip()

        def _is_reasoning_line(candidate: str) -> bool:
            stripped = candidate.strip()
            if not stripped:
                return False
            if cls._plaintext_reasoning_intro_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_step_pattern.match(stripped):
                return True
            if cls._plaintext_reasoning_meta_pattern.match(stripped):
                return True
            return False

        last_content_idx: int | None = None
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip():
                last_content_idx = idx
                break
        if last_content_idx is None:
            return "", ""

        answer_end = last_content_idx
        answer_start: int | None = None
        for idx in range(last_content_idx, -1, -1):
            current = lines[idx]
            if not current.strip():
                if answer_start is not None:
                    break
                continue
            if _is_reasoning_line(current):
                if answer_start is not None:
                    break
                continue
            answer_start = idx

        if answer_start is None:
            return raw.strip(), ""

        reasoning = "\n".join(lines[:answer_start]).strip()
        extracted = "\n".join(lines[answer_start : answer_end + 1]).strip()
        if not reasoning:
            return "", raw.strip()
        return reasoning, extracted or raw.strip()

    @classmethod
    def _strip_plaintext_reasoning_prefix(cls, text: str) -> str:
        """
        Убирает plain-text reasoning, если провайдер прислал мысли без `<think>`.
        """
        _, answer = cls._split_plaintext_reasoning_and_answer(text)
        return answer

    @classmethod
    def _strip_agentic_scratchpad(cls, text: str) -> str:
        """
        Убирает codex-style scratchpad, если модель прислала self-talk вместо ответа.

        Почему нужен отдельный guard:
        - некоторые agentic-маршруты протекают в ответ строками вида
          `Wait, I'll check ...`, `Ready.`, `Let's go.` и shell-командами;
        - такие блоки не являются полезным ответом пользователю и забивают Telegram;
        - режем их только при уверенном scratchpad-профиле первых строк, чтобы не
          ломать обычные ответы, где команды действительно нужны пользователю.
        """
        raw = str(text or "").strip()
        if not raw:
            return ""

        non_empty = [line.strip() for line in raw.splitlines() if line.strip()]
        if not non_empty:
            return raw

        probe_lines = non_empty[:12]
        scratch_hits = sum(
            1 for line in probe_lines if cls._agentic_scratchpad_line_pattern.match(line)
        )
        command_hits = sum(
            1 for line in probe_lines if cls._agentic_scratchpad_command_pattern.match(line)
        )
        if scratch_hits < 2 or (scratch_hits + command_hits) < 3:
            return raw

        kept_lines: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept_lines and kept_lines[-1] != "":
                    kept_lines.append("")
                continue
            if cls._split_chunk_header_pattern.match(stripped):
                continue
            if cls._agentic_scratchpad_line_pattern.match(stripped):
                continue
            if cls._agentic_scratchpad_command_pattern.match(stripped):
                continue
            kept_lines.append(line)

        cleaned = "\n".join(kept_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @classmethod
    def _extract_reasoning_trace(cls, text: str) -> str:
        """
        Возвращает reasoning trace отдельно от основного ответа.

        Почему нужен отдельный helper:
        - пользователь попросил не смешивать мысли с финальным ответом;
        - owner/debug-контур иногда всё же хочет посмотреть reasoning отдельно;
        - часть провайдеров шлёт мысли внутри `<think>`, часть — plain-text префиксом.
        """
        raw = str(text or "")
        if not raw.strip():
            return ""

        fragments = [
            str(match.group(1) or "").strip() for match in cls._think_capture_pattern.finditer(raw)
        ]
        if not fragments and "<think>" in raw.lower():
            start = raw.lower().rfind("<think>")
            partial = raw[start + len("<think>") :]
            end = partial.lower().find("</think>")
            if end >= 0:
                partial = partial[:end]
            if partial.strip():
                fragments = [partial.strip()]

        if not fragments:
            reasoning_prefix, _ = cls._split_plaintext_reasoning_and_answer(raw)
            if reasoning_prefix.strip():
                fragments = [reasoning_prefix.strip()]

        if not fragments:
            return ""

        normalized_lines: list[str] = []
        for fragment in fragments:
            cleaned = cls._reply_to_tag_pattern.sub("", fragment)
            cleaned = cls._tool_response_block_pattern.sub("", cleaned)
            cleaned = cls._llm_transport_tokens_pattern.sub("", cleaned)
            cleaned = cls._think_final_tag_pattern.sub("", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
            for line in cleaned.splitlines():
                stripped = line.strip()
                if not stripped:
                    if normalized_lines and normalized_lines[-1] != "":
                        normalized_lines.append("")
                    continue
                if cls._plaintext_reasoning_intro_pattern.match(stripped):
                    continue
                if stripped.lower().startswith("thinking process:"):
                    stripped = stripped.split(":", 1)[1].strip()
                    if not stripped:
                        continue
                normalized_lines.append(stripped)

        reasoning = "\n".join(normalized_lines).strip()
        return reasoning

    def _remember_hidden_reasoning_trace(
        self,
        *,
        chat_id: str,
        query: str,
        raw_response: str,
        final_response: str,
        access_level: AccessLevel | str | None = None,
    ) -> None:
        """
        Сохраняет reasoning trace отдельно от пользовательского ответа.

        Почему in-memory:
        - trace нужен как owner-only debug-слой "на сейчас", а не как долговременная память;
        - не хочется писать потенциально чувствительные рассуждения в обычную память Краба;
        - при перезапуске runtime trace может честно пропасть без риска для source-of-truth.
        """
        level = (
            str(access_level.value if isinstance(access_level, AccessLevel) else access_level or "")
            .strip()
            .lower()
        )
        if level not in {AccessLevel.OWNER.value, AccessLevel.FULL.value}:
            return

        route_meta = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route_meta = openclaw_client.get_last_runtime_route() or {}
            except Exception:
                route_meta = {}

        trace_text = self._extract_reasoning_trace(raw_response)
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if traces is None:
            traces = {}
            self._hidden_reasoning_traces = traces
        traces[str(chat_id or "unknown")] = {
            "available": bool(trace_text),
            "query": str(query or "").strip(),
            "reasoning": trace_text,
            "answer_preview": textwrap.shorten(
                str(final_response or "").strip(), width=400, placeholder="..."
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "transport_mode": "buffered_edit_loop",
            "route_channel": str(route_meta.get("channel") or "").strip(),
            "route_model": str(route_meta.get("model") or "").strip(),
        }

    def get_hidden_reasoning_trace_snapshot(self, chat_id: str | int) -> dict[str, Any]:
        """Возвращает последний скрытый reasoning trace для конкретного чата."""
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if not isinstance(traces, dict):
            return {}
        trace = traces.get(str(chat_id or "unknown"))
        return dict(trace) if isinstance(trace, dict) else {}

    def clear_hidden_reasoning_trace_snapshot(self, chat_id: str | int) -> bool:
        """Очищает последний reasoning trace для конкретного чата."""
        traces = getattr(self, "_hidden_reasoning_traces", None)
        if not isinstance(traces, dict):
            return False
        return traces.pop(str(chat_id or "unknown"), None) is not None

    @classmethod
    def _extract_live_stream_text(cls, text: str, *, allow_reasoning: bool = False) -> str:
        """
        Возвращает лучший доступный текст для промежуточного live-stream отображения.

        Почему это отдельный helper:
        - часть провайдеров стримит ответ внутри `<final>` и закрывает тег только
          в самом конце; старое поведение из-за этого показывало почти пустой draft
          до финального чанка;
        - reasoning полезно держать отдельным опциональным режимом, а не мешать в
          обычный пользовательский текст.
        """
        raw = str(text or "")
        if not raw:
            return ""

        if "<final>" in raw.lower():
            lower_raw = raw.lower()
            start = lower_raw.rfind("<final>")
            if start >= 0:
                partial_final = raw[start + len("<final>") :]
                end = partial_final.lower().find("</final>")
                if end >= 0:
                    partial_final = partial_final[:end]
                partial_final = cls._reply_to_tag_pattern.sub("", partial_final)
                partial_final = cls._tool_response_block_pattern.sub("", partial_final)
                partial_final = cls._llm_transport_tokens_pattern.sub("", partial_final)
                partial_final = cls._think_final_tag_pattern.sub("", partial_final)
                partial_final = re.sub(r"[ \t]{2,}", " ", partial_final)
                partial_final = re.sub(r"\n{3,}", "\n\n", partial_final).strip()
                if partial_final:
                    return partial_final

        lower_raw = raw.lower()
        if allow_reasoning and "<think>" in lower_raw:
            start = lower_raw.rfind("<think>")
            partial_think = raw[start + len("<think>") :]
            end = partial_think.lower().find("</think>")
            if end >= 0:
                partial_think = partial_think[:end]
            partial_think = cls._reply_to_tag_pattern.sub("", partial_think)
            partial_think = cls._tool_response_block_pattern.sub("", partial_think)
            partial_think = cls._llm_transport_tokens_pattern.sub("", partial_think)
            partial_think = cls._think_final_tag_pattern.sub("", partial_think)
            partial_think = re.sub(r"[ \t]{2,}", " ", partial_think)
            partial_think = re.sub(r"\n{3,}", "\n\n", partial_think).strip()
            if partial_think:
                return f"🧠 {partial_think}"

        cleaned = cls._strip_transport_markup(raw)
        if cleaned:
            return cleaned

        return ""

    @classmethod
    def _apply_deferred_action_guard(cls, text: str) -> str:
        """
        Защищает от ложных обещаний "сделаю позже", когда scheduler выключен.
        """
        raw = str(text or "").strip()
        if not raw:
            return raw
        if bool(getattr(config, "SCHEDULER_ENABLED", False)):
            return raw
        if not bool(getattr(config, "DEFERRED_ACTION_GUARD_ENABLED", True)):
            return raw
        if not cls._deferred_intent_pattern.search(raw):
            return raw
        note = (
            "⚠️ Важно: фоновый cron/таймер сейчас не активен, "
            "поэтому отложенная задача автоматически не запустится."
        )
        if note in raw:
            return raw
        return f"{raw}\n\n{note}"

    def _get_clean_text(self, text: str) -> str:
        """Убирает триггер из текста"""
        if not text:
            return ""
        text_lower = text.lower()

        # Сначала проверяем длинные префиксы
        sorted_prefixes = sorted(config.TRIGGER_PREFIXES + ["краб"], key=len, reverse=True)
        for prefix in sorted_prefixes:
            if text_lower.startswith(prefix.lower()):
                clean = text[len(prefix) :].strip()
                # Убираем запятую если она была после имени (Краб, привет)
                if clean.startswith(","):
                    clean = clean[1:].strip()
                return clean
        return text.strip()

    def _split_message(self, text: str, limit: int = 4000) -> list[str]:
        """
        Разбивает длинный ответ на Telegram-friendly части.

        Почему не обычный `textwrap.wrap`:
        - длинный ответ в Telegram визуально выглядит «оборванным», если следующая
          часть приходит отдельным сообщением без явного маркера;
        - для списков и markdown-ответов важно по возможности сохранять границы строк;
        - нам нужен запас до лимита Telegram (4096), поэтому `limit=4000` сохраняем.
        """
        normalized = str(text or "")
        if len(normalized) <= limit:
            return [normalized]

        # Резерв под префикс вида `[Часть 2/3]`, чтобы не выйти за safe-limit.
        marker_reserve = 48
        body_limit = max(32, limit - marker_reserve)

        chunks: list[str] = []
        current = ""

        def _flush_current() -> None:
            nonlocal current
            if current:
                chunks.append(current)
                current = ""

        for line in normalized.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= body_limit:
                current = candidate
                continue

            _flush_current()
            if len(line) <= body_limit:
                current = line
                continue

            # Для сверхдлинной строки режем мягко, не схлопывая пробелы.
            wrapped = textwrap.wrap(
                line,
                width=body_limit,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            if not wrapped:
                continue
            chunks.extend(wrapped[:-1])
            current = wrapped[-1]

        _flush_current()

        if len(chunks) <= 1:
            return chunks or [normalized[:limit]]

        total = len(chunks)
        decorated: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            prefix = f"[Часть {index}/{total}]\n"
            payload = f"{prefix}{chunk}"
            if len(payload) > limit:
                payload = f"{prefix}{chunk[: max(0, limit - len(prefix))]}"
            decorated.append(payload)
        return decorated

    @staticmethod
    def escape_urls_for_restricted_groups(text: str) -> str:
        """
        Оборачивает «голые» URL в бэктики, чтобы обойти детектор ссылок
        admin-ботов в публичных группах (например, HOW2AI).

        Telegram отображает `https://...` в коде как моноширинный текст —
        ссылки не кликабельны, поэтому LinkRestrict-боты их не удаляют.

        Правила:
        - Только сегменты вне бэктиков — уже обёрнутые URL не трогаем.
        - Markdown-ссылки [текст](url) — не трогаем (ищем только «голые» URL).
        """
        if not text:
            return text

        # Паттерн для «голых» URL (не внутри markdown-ссылки и не в бэктиках)
        _url_re = re.compile(r'https?://[^\s\)\]`>]+', re.IGNORECASE)

        # Разбиваем по бэктикам: чётные индексы — вне кода, нечётные — внутри.
        parts = text.split("`")
        for i in range(0, len(parts), 2):
            segment = parts[i]
            # Не трогаем URL внутри markdown-ссылок вида [текст](url)
            def _maybe_wrap(m: re.Match) -> str:
                # Смотрим символ перед совпадением: если '(' — это markdown-ссылка
                start = m.start()
                if start > 0 and segment[start - 1] == "(":
                    return m.group(0)
                return f"`{m.group(0)}`"

            parts[i] = _url_re.sub(_maybe_wrap, segment)
        return "`".join(parts)

    @staticmethod
    def _normalize_user_visible_fallback_text(text: str) -> str:
        """
        Приводит сырые fallback-строки OpenClaw к понятному Telegram-тексту.

        Это не меняет содержательные ответы модели, а только перехватывает
        технические заглушки transport/runtime слоя.
        """
        normalized = str(text or "").strip()
        if not normalized:
            return normalized
        compact = re.sub(r"\s+", " ", normalized).strip().lower()
        fallback_map = {
            "no response from openclaw.": "❌ OpenClaw не вернул текстовый ответ. Попробуй повторить запрос.",
            "no response from openclaw": "❌ OpenClaw не вернул текстовый ответ. Попробуй повторить запрос.",
        }
        return fallback_map.get(compact, normalized)

    @classmethod
    def _looks_like_error_surface_text(cls, text: str) -> bool:
        """Определяет, что текст уже является пользовательской ошибкой/деградацией."""
        normalized = cls._normalize_user_visible_fallback_text(text)
        compact = re.sub(r"\s+", " ", str(normalized or "")).strip().lower()
        if not compact:
            return False
        if str(normalized).lstrip().startswith("❌"):
            return True
        return compact in {
            "no response from openclaw.",
            "no response from openclaw",
        }

    def _remember_batched_followup_message_ids(
        self,
        *,
        chat_id: str,
        message_ids: list[str],
    ) -> None:
        """
        Запоминает message-id, уже поглощённые более ранним batch-запросом.

        Это защищает от двойной обработки: follower handlers всё равно дойдут до
        per-chat lock, но после этого должны тихо завершиться.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        rows = getattr(self, "_batched_followup_message_ids", None)
        if rows is None:
            rows = {}
            self._batched_followup_message_ids = rows
        bucket = rows.setdefault(chat_key, {})
        now = time.monotonic()
        for message_id in message_ids:
            normalized = str(message_id or "").strip()
            if normalized:
                bucket[normalized] = now

    def _consume_batched_followup_message_id(self, *, chat_id: str, message_id: str) -> bool:
        """
        Возвращает True, если сообщение уже было включено в предыдущий batch.

        Храним id недолго: этого достаточно, чтобы отфильтровать уже стоящие в
        очереди handler-вызовы и не раздувать состояние бесконечно.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        normalized_id = str(message_id or "").strip()
        if not normalized_id:
            return False
        rows = getattr(self, "_batched_followup_message_ids", None) or {}
        bucket = rows.get(chat_key)
        if not bucket:
            return False
        now = time.monotonic()
        ttl_sec = 600.0
        expired = [
            mid for mid, saved_at in bucket.items() if now - float(saved_at or 0.0) > ttl_sec
        ]
        for expired_id in expired:
            bucket.pop(expired_id, None)
        if not bucket:
            rows.pop(chat_key, None)
            return False
        matched = normalized_id in bucket
        if matched:
            bucket.pop(normalized_id, None)
        if not bucket:
            rows.pop(chat_key, None)
        return matched

    def _is_text_batch_candidate(
        self,
        *,
        message: Message | Any,
        sender_id: int,
        is_private_chat: bool,
        self_user_id: int,
    ) -> bool:
        """
        Решает, можно ли включать сообщение в text-burst batch.

        Склеиваем только plain-text сообщения того же отправителя:
        команды, фото и аудио должны идти отдельным путём, иначе потеряем
        ожидаемую семантику и управляемость.

        В **приватном чате** достаточно совпадения sender_id — любое сообщение
        в личку по определению адресовано Крабу, и burst разрыв тогда читается
        как «одна мысль разбитая на несколько частей».

        В **группе** (B.5, 2026-04-09) эта эвристика опасна: разные участники
        могут писать параллельно, и merge «mid-conversation» text'ов чужих
        пользователей сделает странный combined prompt. Поэтому в группах
        дополнительно требуем чтобы absorbed сообщение **само по себе
        триггерило Краба** — либо через keyword trigger (`!краб`, `краб`,
        ...), либо через reply на сообщение самого Краба. Это оставляет
        полезный use case («owner пишет Крабу несколько раз подряд с
        трiггером в первом сообщении») покрытым, но не даёт batcher'у
        проглатывать unrelated сообщения соседей.
        """
        message_sender_id = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
        if sender_id and message_sender_id != sender_id:
            return False
        if getattr(message, "photo", None) or self._message_has_audio(message):
            return False
        text = self._get_clean_text(self._extract_message_text(message))
        if not text:
            return False
        if self._is_command_like_text(text):
            return False
        if is_private_chat:
            return True
        # Group / supergroup: absorbed candidate должен сам быть адресован Крабу.
        # Проверяем две самых дешёвых сигнала — trigger keyword и reply на Краба.
        if self._is_trigger(text):
            return True
        reply_target = getattr(message, "reply_to_message", None)
        reply_from = getattr(reply_target, "from_user", None) if reply_target else None
        if reply_from is not None and self_user_id:
            try:
                if int(getattr(reply_from, "id", 0) or 0) == int(self_user_id):
                    return True
            except (TypeError, ValueError):
                pass
        return False

    async def _coalesce_text_burst(
        self,
        *,
        message: Message,
        user: Any,
        query: str,
    ) -> tuple[Message, str]:
        """
        Склеивает короткую пачку text-сообщений одного отправителя в один query.

        Зачем это нужно:
        - после `!clear` пользователь часто заново передаёт контекст несколькими
          Telegram-сообщениями из-за лимита длины;
        - без склейки каждое сообщение уходит отдельным AI-запросом и вся очередь
          начинает жить своей жизнью;
        - выбираем последнюю user-message как anchor для ответа, чтобы в клиенте
          это выглядело естественно.

        B.5 (2026-04-09): работает и в private, и в group/supergroup. В группе
        дополнительно требуется что absorbed сообщение само триггерит Краба
        (`_is_text_batch_candidate` проверяет это через `_is_trigger` / reply-to-me).
        Это защищает от проглатывания unrelated сообщений других участников,
        но позволяет owner'у писать несколько triggered сообщений подряд и
        получать один combined ответ — что снижает LLM/API нагрузку и Telegram
        spam-detection риск (см. backlog B.5 от разговора с Chado, OG P Cod/id).
        """
        # Lazy import: `_message_unix_ts` остаётся module-level helper'ом
        # в `src.userbot_bridge`, поэтому импортируем его через local import
        # чтобы избежать циркулярной зависимости между mixin'ом и bridge'ем.
        from ..userbot_bridge import _message_unix_ts

        normalized_query = str(query or "").strip()
        if not normalized_query:
            return message, normalized_query
        chat_type = getattr(getattr(message, "chat", None), "type", None)
        is_private_chat = chat_type == enums.ChatType.PRIVATE
        # Forum-группы и каналы не batch'им — их семантика слишком разная,
        # а risk схлопнуть unrelated тему существенно выше ожидаемой пользы.
        is_batchable_group = chat_type in (
            enums.ChatType.GROUP,
            enums.ChatType.SUPERGROUP,
        )
        if not is_private_chat and not is_batchable_group:
            return message, normalized_query
        if self._is_command_like_text(normalized_query):
            return message, normalized_query
        history_reader = getattr(self.client, "get_chat_history", None)
        if not callable(history_reader):
            return message, normalized_query

        batch_window_sec = float(getattr(config, "TELEGRAM_MESSAGE_BATCH_WINDOW_SEC", 1.4) or 0.0)
        if batch_window_sec <= 0:
            return message, normalized_query
        await asyncio.sleep(max(0.0, batch_window_sec))

        max_messages = max(1, int(getattr(config, "TELEGRAM_MESSAGE_BATCH_MAX_MESSAGES", 6) or 6))
        max_chars = max(1, int(getattr(config, "TELEGRAM_MESSAGE_BATCH_MAX_CHARS", 12000) or 12000))
        history_limit = max(12, max_messages * 4)
        # В реальном Telegram self-sent burst может появляться в server-side history
        # не мгновенно: первый снимок истории иногда ещё не видит follower-сообщения,
        # хотя они уже отправлены буквально через 100-200 мс. Поэтому даём короткий
        # settle-poll и перечитываем историю несколько раз, пока список id не
        # стабилизируется или не истечёт небольшой дополнительный бюджет.
        settle_interval_sec = max(
            0.05,
            float(getattr(config, "TELEGRAM_MESSAGE_BATCH_SETTLE_INTERVAL_SEC", 0.18) or 0.18),
        )
        settle_max_extra_sec = max(
            0.0,
            float(getattr(config, "TELEGRAM_MESSAGE_BATCH_SETTLE_MAX_EXTRA_SEC", 0.72) or 0.72),
        )

        async def _read_recent_rows() -> list[Message]:
            rows: list[Message] = []
            async for row in history_reader(message.chat.id, limit=history_limit):
                rows.append(row)
            return rows

        history_rows = await _read_recent_rows()
        last_signature: tuple[int, ...] | None = None
        settle_deadline = time.monotonic() + settle_max_extra_sec
        while time.monotonic() < settle_deadline:
            current_signature = tuple(
                sorted(
                    int(getattr(row, "id", 0) or 0)
                    for row in history_rows
                    if int(getattr(row, "id", 0) or 0) >= int(getattr(message, "id", 0) or 0)
                )
            )
            if len(current_signature) > 1 and current_signature == last_signature:
                break
            last_signature = current_signature
            await asyncio.sleep(settle_interval_sec)
            refreshed_rows = await _read_recent_rows()
            refreshed_signature = tuple(
                sorted(
                    int(getattr(row, "id", 0) or 0)
                    for row in refreshed_rows
                    if int(getattr(row, "id", 0) or 0) >= int(getattr(message, "id", 0) or 0)
                )
            )
            history_rows = refreshed_rows
            if refreshed_signature == current_signature:
                break

        current_message_id = int(getattr(message, "id", 0) or 0)
        sender_id = int(getattr(user, "id", 0) or 0)
        if current_message_id <= 0 or sender_id <= 0:
            return message, normalized_query
        self_user_id = int(getattr(self.me, "id", 0) or 0)

        ordered_rows = sorted(
            (row for row in history_rows if int(getattr(row, "id", 0) or 0) >= current_message_id),
            key=lambda row: int(getattr(row, "id", 0) or 0),
        )
        if not ordered_rows:
            return message, normalized_query

        base_ts = _message_unix_ts(message)
        max_gap_sec = max(batch_window_sec + 1.0, 3.0)
        max_span_sec = max(batch_window_sec + 4.0, 6.0)
        combined_messages: list[Message] = []
        combined_parts: list[str] = []
        total_chars = 0
        current_found = False
        previous_ts = base_ts

        for row in ordered_rows:
            row_id = int(getattr(row, "id", 0) or 0)
            if not current_found:
                if row_id != current_message_id:
                    continue
                current_found = True
            elif len(combined_messages) >= max_messages:
                break
            elif not self._is_text_batch_candidate(
                message=row,
                sender_id=sender_id,
                is_private_chat=is_private_chat,
                self_user_id=self_user_id,
            ):
                break

            clean_text = (
                normalized_query
                if row_id == current_message_id
                else self._get_clean_text(self._extract_message_text(row))
            )
            if not clean_text:
                if row_id == current_message_id:
                    return message, normalized_query
                break

            row_ts = _message_unix_ts(row)
            if combined_messages:
                if (
                    row_ts is not None
                    and previous_ts is not None
                    and (row_ts - previous_ts) > max_gap_sec
                ):
                    break
                if row_ts is not None and base_ts is not None and (row_ts - base_ts) > max_span_sec:
                    break

            projected_chars = total_chars + len(clean_text) + (2 if combined_parts else 0)
            if projected_chars > max_chars:
                break

            combined_messages.append(row if row_id != current_message_id else message)
            combined_parts.append(clean_text)
            total_chars = projected_chars
            previous_ts = row_ts if row_ts is not None else previous_ts

        if len(combined_messages) <= 1:
            return message, normalized_query

        absorbed_ids = [
            str(getattr(row, "id", "") or "").strip()
            for row in combined_messages[1:]
            if str(getattr(row, "id", "") or "").strip()
        ]
        if absorbed_ids:
            self._remember_batched_followup_message_ids(
                chat_id=str(getattr(getattr(message, "chat", None), "id", "") or ""),
                message_ids=absorbed_ids,
            )

        # Ставим реакцию 👀 на absorbed сообщения чтобы пользователь видел,
        # что они включены в batch и не "висят" без ответа.
        chat_id_int = int(getattr(getattr(message, "chat", None), "id", 0) or 0)
        for absorbed_msg in combined_messages[1:]:
            try:
                await self.client.send_reaction(
                    chat_id=chat_id_int,
                    message_id=int(getattr(absorbed_msg, "id", 0) or 0),
                    emoji="👀",
                )
            except Exception:  # noqa: BLE001
                pass  # не все чаты поддерживают реакции

        combined_query = "\n\n".join(part for part in combined_parts if part).strip()
        anchor_message = combined_messages[-1]
        logger.info(
            "text_burst_coalesced",
            chat_id=str(getattr(getattr(message, "chat", None), "id", "") or ""),
            chat_type="private" if is_private_chat else "group",
            anchor_message_id=str(getattr(anchor_message, "id", "") or ""),
            absorbed_message_ids=absorbed_ids,
            messages_count=len(combined_messages),
            total_chars=len(combined_query),
        )
        return anchor_message, combined_query

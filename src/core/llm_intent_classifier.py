# -*- coding: utf-8 -*-
"""
LLMIntentClassifier — LLM-based intent classification для Smart Routing Phase 2.

Используется в Stage 4 smart routing pipeline когда regex score 0.2-0.6 (borderline).
Делает короткий call в LM Studio (local Qwen) для определения, должен ли Krab
ответить на сообщение, учитывая последние N сообщений контекста и per-chat policy.

Кэш: in-memory LRU (OrderedDict) с TTL 5 мин, max 500 записей.
Timeout: 2с. На любую ошибку — IntentResult(should_respond=False, error=...);
caller обязан fallback на regex score.

См. docs/SMART_ROUTING_DESIGN.md (Component 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx
import structlog

from .chat_response_policy import ChatMode, ChatResponsePolicy

logger = structlog.get_logger(__name__)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
LM_STUDIO_TIMEOUT = 2.0  # seconds
CACHE_MAX_SIZE = 500
CACHE_TTL_SEC = 300  # 5 min


@dataclass
class ChatMessage:
    """Subset of Telegram Message нужный для context building."""

    sender_name: str
    sender_id: int
    text: str
    timestamp: float
    is_krab: bool = False


@dataclass
class IntentResult:
    should_respond: bool
    confidence: float  # 0.0..1.0
    reasoning: str
    cached: bool = False
    latency_ms: float = 0.0
    error: str | None = None


class LLMIntentClassifier:
    """LLM-based intent classification для borderline cases."""

    def __init__(
        self,
        *,
        lm_url: str = LM_STUDIO_URL,
        timeout: float = LM_STUDIO_TIMEOUT,
        cache_max_size: int = CACHE_MAX_SIZE,
        cache_ttl_sec: float = CACHE_TTL_SEC,
    ):
        self._url = lm_url
        self._timeout = timeout
        self._cache_max_size = cache_max_size
        self._cache_ttl_sec = cache_ttl_sec
        self._cache: OrderedDict[str, tuple[float, IntentResult]] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _make_cache_key(
        text: str,
        context: list[ChatMessage],
        chat_id: str,
        mode: ChatMode,
    ) -> str:
        """SHA256 of normalized inputs (last 5 ctx msgs)."""
        ctx_repr = "|".join(f"{m.sender_id}:{m.text[:50]}" for m in context[-5:])
        raw = f"{text}|{ctx_repr}|{chat_id}|{mode.value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_prompt(
        text: str,
        context: list[ChatMessage],
        policy: ChatResponsePolicy,
    ) -> str:
        """Build LLM prompt — strict JSON output."""
        ctx_lines = []
        for i, msg in enumerate(context[-7:], 1):
            sender = "[Krab]" if msg.is_krab else msg.sender_name
            text_trunc = msg.text[:200] + "..." if len(msg.text) > 200 else msg.text
            ctx_lines.append(f"[{i}] {sender}: {text_trunc}")
        ctx_block = "\n".join(ctx_lines) if ctx_lines else "(пусто)"

        policy_hint = {
            ChatMode.SILENT: "Krab НИКОГДА не отвечает в этом чате (только hard gates).",
            ChatMode.CAUTIOUS: "Krab отвечает осторожно — только на явные обращения и followups. Threshold high.",
            ChatMode.NORMAL: "Krab отвечает на разумные вопросы и упоминания.",
            ChatMode.CHATTY: "Krab активный участник, отвечает шире на релевантные сообщения.",
        }[policy.mode]

        blocked = ", ".join(policy.blocked_topics) if policy.blocked_topics else "none"

        return f"""Ты — детектор обращений к Telegram userbot Krab. Определи, направлено ли последнее сообщение к Krab.

ПОЛИТИКА ЧАТА: mode={policy.mode.value}, threshold={policy.effective_threshold():.2f}
{policy_hint}
BLOCKED TOPICS (если совпадает — NO): {blocked}

КОНТЕКСТ ({len(context)} последних сообщений):
{ctx_block}

ПОСЛЕДНЕЕ СООБЩЕНИЕ: "{text}"

YES если:
- явное обращение / имя / "ты" к Krab
- followup на ответ Krab выше
- релевантный вопрос для AI-ассистента
- продолжение разговора между Krab и user

NO если:
- разговор между другими пользователями
- off-topic для AI / blocked topic
- слишком короткий мусор / эхо
- благодарность после уже данного ответа (не doubling)

Ответь СТРОГО в JSON (без markdown):
{{"should_respond": <true|false>, "confidence": <0.0-1.0>, "reasoning": "<≤80 chars>"}}"""

    async def classify_intent_for_krab(
        self,
        text: str,
        chat_context: list[ChatMessage],
        chat_id: str,
        policy: ChatResponsePolicy,
    ) -> IntentResult:
        """Main classification call.

        1. Check cache (TTL 5min)
        2. Build prompt + LLM call (timeout 2s)
        3. Parse JSON response
        4. Cache + return
        5. On error → IntentResult(should_respond=False, error=...) — caller fallback на regex.
        """
        if not text or not text.strip():
            return IntentResult(False, 0.0, "empty_text", error="empty")

        cache_key = self._make_cache_key(text, chat_context, chat_id, policy.mode)

        # Check cache
        async with self._lock:
            if cache_key in self._cache:
                ts, result = self._cache[cache_key]
                if time.time() - ts < self._cache_ttl_sec:
                    self._cache.move_to_end(cache_key)
                    return IntentResult(
                        should_respond=result.should_respond,
                        confidence=result.confidence,
                        reasoning=result.reasoning,
                        cached=True,
                        latency_ms=result.latency_ms,
                    )
                else:
                    del self._cache[cache_key]

        # LLM call
        prompt = self._build_prompt(text, chat_context, policy)
        start = time.time()
        try:
            result = await self._call_lm_studio(prompt)
        except Exception as exc:
            logger.warning(
                "llm_intent_classifier_error",
                error=str(exc),
                error_type=type(exc).__name__,
                chat_id=chat_id,
            )
            return IntentResult(
                should_respond=False,
                confidence=0.0,
                reasoning=f"llm_error: {type(exc).__name__}",
                error=str(exc),
                latency_ms=(time.time() - start) * 1000,
            )

        result.latency_ms = (time.time() - start) * 1000

        # Cache (LRU FIFO eviction)
        async with self._lock:
            self._cache[cache_key] = (time.time(), result)
            while len(self._cache) > self._cache_max_size:
                self._cache.popitem(last=False)

        return result

    async def _call_lm_studio(self, prompt: str) -> IntentResult:
        """HTTP POST to LM Studio + parse JSON."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._url,
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 150,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Strip potential markdown code blocks
            if content.startswith("```"):
                # ```json\n{...}\n```  → split returns ['', 'json\n{...}\n', '']
                parts = content.split("```")
                if len(parts) >= 2:
                    content = parts[1]
                    if content.startswith("json"):
                        content = content[4:]
                    content = content.strip()

            parsed = json.loads(content)
            return IntentResult(
                should_respond=bool(parsed.get("should_respond", False)),
                confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.0)))),
                reasoning=str(parsed.get("reasoning", ""))[:200],
            )

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()


# Singleton accessor для production
_default_classifier: LLMIntentClassifier | None = None


def get_classifier() -> LLMIntentClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = LLMIntentClassifier()
    return _default_classifier


def reset_classifier() -> None:
    """For tests."""
    global _default_classifier
    _default_classifier = None

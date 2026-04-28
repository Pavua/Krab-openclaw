# -*- coding: utf-8 -*-
"""
Тесты AudioSummarizer (Idea 35).

LM Studio HTTP вызов замокан через monkeypatch httpx.AsyncClient.post.
Покрытие: skip короткого, success-bullet, cache hit, language detect, fail-open.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.core.audio_summarizer import (
    AudioSummarizer,
    AudioSummary,
    _detect_lang,
    _looks_structured,
    get_summarizer,
    reset_summarizer,
)

# --- Helpers ---------------------------------------------------------------


def _mk_lm_response(
    bullets: list[str],
    topic: str = "обсуждение задачи",
    sentiment: str = "neutral",
    *,
    wrap_md: bool = False,
):
    body = json.dumps(
        {"bullets": bullets, "topic": topic, "sentiment": sentiment},
        ensure_ascii=False,
    )
    if wrap_md:
        body = "```json\n" + body + "\n```"
    payload = {"choices": [{"message": {"content": body}}]}
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _patch_post(monkeypatch, response_or_exc):
    async def fake_post(self, url, **kwargs):
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return response_or_exc

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _long_ru(n: int = 250) -> str:
    base = (
        "Привет, давай обсудим план релиза на следующую неделю и распределим задачи "
        "между разработчиками без перегрузки команды и с учётом отпусков "
    )
    out = base
    while len(out) < n:
        out += base
    return out[:n]


# --- Heuristics -----------------------------------------------------------


def test_detect_lang_russian():
    assert _detect_lang("привет, как дела сегодня") == "ru"


def test_detect_lang_english():
    assert _detect_lang("hello how are you doing today") == "en"


def test_looks_structured_bullets():
    text = "- первое\n- второе\n- третье\n- четвёртое"
    assert _looks_structured(text) is True


def test_looks_structured_plain_prose_false():
    assert _looks_structured("Просто длинный текст без списков и почти без цифр.") is False


# --- summarize: skip-кейсы ------------------------------------------------


def test_summarize_skips_short_transcript(monkeypatch):
    """Транскрипт < MIN_TRANSCRIPT_CHARS — None без LLM-вызова."""
    called: list[bool] = []

    async def fake_post(self, url, **kwargs):
        called.append(True)
        raise AssertionError("must not call LLM for short transcript")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    s = AudioSummarizer()
    result = asyncio.run(s.summarize("слишком коротко"))
    assert result is None
    assert not called


# --- summarize: success ----------------------------------------------------


def test_summarize_long_transcript_returns_bullets(monkeypatch):
    bullets = ["обсудили релиз", "распределили задачи", "учли отпуска"]
    _patch_post(monkeypatch, _mk_lm_response(bullets, topic="релиз", sentiment="positive"))

    s = AudioSummarizer()
    result = asyncio.run(s.summarize(_long_ru(300), max_bullets=5, language="ru"))

    assert result is not None
    assert isinstance(result, AudioSummary)
    assert result.bullets == bullets
    assert result.topic == "релиз"
    assert result.sentiment == "positive"
    assert result.length_chars >= 250
    assert result.cached is False


def test_summarize_handles_markdown_wrapped_response(monkeypatch):
    _patch_post(
        monkeypatch,
        _mk_lm_response(["пункт один", "пункт два"], wrap_md=True),
    )
    s = AudioSummarizer()
    result = asyncio.run(s.summarize(_long_ru(300)))
    assert result is not None
    assert result.bullets == ["пункт один", "пункт два"]


# --- summarize: cache -----------------------------------------------------


def test_summarize_cache_hit_skips_second_call(monkeypatch):
    call_count = {"n": 0}
    response = _mk_lm_response(["первое", "второе"])

    async def fake_post(self, url, **kwargs):
        call_count["n"] += 1
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    s = AudioSummarizer()
    transcript = _long_ru(280)
    first = asyncio.run(s.summarize(transcript))
    second = asyncio.run(s.summarize(transcript))

    assert first is not None and second is not None
    assert call_count["n"] == 1
    assert first.cached is False
    assert second.cached is True
    assert second.bullets == first.bullets


# --- summarize: language detect -------------------------------------------


def test_summarize_auto_lang_detects_russian(monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, **kwargs):
        captured["payload"] = kwargs.get("json") or {}
        return _mk_lm_response(["короткий итог"])

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    s = AudioSummarizer()
    # language='auto' — модуль должен сам решить ru/en
    result = asyncio.run(s.summarize(_long_ru(250), language="auto"))
    assert result is not None
    prompt_text = captured["payload"]["messages"][0]["content"]
    # Кириллический транскрипт → промпт упоминает "русском"
    assert "русском" in prompt_text


# --- summarize: fail-open --------------------------------------------------


def test_summarize_fails_open_on_http_error(monkeypatch):
    _patch_post(monkeypatch, httpx.ConnectError("LM Studio offline"))
    s = AudioSummarizer()
    result = asyncio.run(s.summarize(_long_ru(300)))
    assert result is None


def test_summarize_fails_open_on_bad_json(monkeypatch):
    payload = {"choices": [{"message": {"content": "not a json {bullets:"}}]}
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    _patch_post(monkeypatch, resp)
    s = AudioSummarizer()
    result = asyncio.run(s.summarize(_long_ru(300)))
    assert result is None


def test_summarize_fails_open_on_empty_bullets(monkeypatch):
    _patch_post(monkeypatch, _mk_lm_response(bullets=[]))
    s = AudioSummarizer()
    result = asyncio.run(s.summarize(_long_ru(300)))
    assert result is None


# --- singleton -------------------------------------------------------------


def test_singleton_lifecycle():
    reset_summarizer()
    a = get_summarizer()
    b = get_summarizer()
    assert a is b
    reset_summarizer()
    c = get_summarizer()
    assert c is not a


# --- skip when already structured -----------------------------------------


def test_summarize_skips_structured_text(monkeypatch):
    called: list[bool] = []

    async def fake_post(self, url, **kwargs):
        called.append(True)
        return _mk_lm_response(["x"])

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    s = AudioSummarizer()
    structured = (
        "1. купить молоко\n"
        "2. забрать детей\n"
        "3. позвонить врачу\n"
        "4. оплатить счёт за свет\n"
        "5. забронировать гостиницу"
    )
    # Длиннее MIN, но _looks_structured → True
    result = asyncio.run(s.summarize(structured + " " * 50))
    assert result is None
    assert not called

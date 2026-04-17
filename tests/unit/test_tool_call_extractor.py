# -*- coding: utf-8 -*-
"""
Тесты для extract_tool_calls_from_progress() в src/core/openclaw_task_poller.py.

Buffered mode tool indicator (session 9): Gateway пишет progress_summary в runs.sqlite
в разных форматах (emoji, RU/EN narration, JSON, markdown). Тесты покрывают
все паттерны + edge cases (пустая строка, длинные args, длинная очередь).
"""

from __future__ import annotations

from src.core.openclaw_task_poller import extract_tool_calls_from_progress


def test_empty() -> None:
    """Пустая строка → пустой tuple."""
    assert extract_tool_calls_from_progress("") == ("", [])


def test_tool_emoji() -> None:
    """Pattern 1: emoji 🔧 с tool(args)."""
    active, queued = extract_tool_calls_from_progress("🔧 web_search(query='hello')")
    assert "web_search" in active
    assert queued == []


def test_executing_pattern() -> None:
    """Pattern 2: "Executing <tool>"."""
    active, _ = extract_tool_calls_from_progress("Executing read_file now")
    assert active == "read_file"


def test_russian_vyzov() -> None:
    """Pattern 2: RU narration 'Вызов <tool>'."""
    active, _ = extract_tool_calls_from_progress("Вызов grep_in_repo")
    assert active == "grep_in_repo"


def test_json_tool() -> None:
    """Pattern 3: JSON-like '"tool": "name"'."""
    active, _ = extract_tool_calls_from_progress('{"tool": "curl_fetch", "args": {}}')
    assert active == "curl_fetch"


def test_markdown_code() -> None:
    """Pattern 4: markdown backtick `tool(`."""
    active, _ = extract_tool_calls_from_progress("Running `search_web(q='...')`")
    assert "search_web" in active


def test_queued() -> None:
    """Очередь запланированных tools с EN keyword."""
    text = "Running read_file. Queued: parse, grep, summarize"
    active, queued = extract_tool_calls_from_progress(text)
    assert active == "read_file"
    assert "parse" in queued and "grep" in queued and "summarize" in queued


def test_russian_ocheredi() -> None:
    """RU narration + 'В очереди:' → активный + queue."""
    text = "🔧 Активно: web_search\nВ очереди: fetch, parse"
    active, queued = extract_tool_calls_from_progress(text)
    assert "web_search" in active
    assert "fetch" in queued


def test_args_truncated() -> None:
    """Длинные args должны обрезаться до ~40 символов (+ "...")."""
    long_args = "query='" + "x" * 100 + "'"
    active, _ = extract_tool_calls_from_progress(f"🔧 web_search({long_args})")
    assert len(active) < 60  # truncated to ~50 chars


def test_max_queue() -> None:
    """Очередь ограничена 5 элементами."""
    text = "Running tool_a. Queued: b, c, d, e, f, g, h, i"
    _, queued = extract_tool_calls_from_progress(text)
    assert len(queued) <= 5

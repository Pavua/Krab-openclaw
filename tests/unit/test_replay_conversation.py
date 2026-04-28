"""Unit-тесты для scripts/replay_conversation.py."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Динамический импорт скрипта (он лежит в scripts/, не в src/)
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "replay_conversation.py"
_spec = importlib.util.spec_from_file_location("replay_conversation", _SCRIPT_PATH)
assert _spec and _spec.loader
replay_conversation = importlib.util.module_from_spec(_spec)
sys.modules["replay_conversation"] = replay_conversation
_spec.loader.exec_module(replay_conversation)


def _make_archive_db(path: Path, rows: list[tuple]) -> None:
    """Создаёт минимальный archive.db с таблицей messages."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE messages ("
            "message_id TEXT NOT NULL, "
            "chat_id TEXT NOT NULL, "
            "sender_id TEXT, "
            "timestamp TEXT NOT NULL, "
            "text_redacted TEXT NOT NULL, "
            "reply_to_id TEXT, "
            "PRIMARY KEY (chat_id, message_id))"
        )
        conn.executemany(
            "INSERT INTO messages (message_id, chat_id, sender_id, timestamp, "
            "text_redacted, reply_to_id) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_dry_run_outputs_jsonl(tmp_path: Path) -> None:
    """--dry-run проходит без LLM, создаёт валидный JSONL по сообщениям."""
    db = tmp_path / "archive.db"
    _make_archive_db(
        db,
        [
            ("1", "100", "u1", "2026-04-20T10:00:00", "Привет", None),
            ("2", "100", "u2", "2026-04-20T10:05:00", "Как дела?", "1"),
            ("3", "100", "u1", "2026-04-22T10:00:00", "вне окна", None),
        ],
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("ALT SYSTEM PROMPT", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    rc = replay_conversation.main(
        [
            "--chat-id", "100",
            "--from", "2026-04-20",
            "--to", "2026-04-21",
            "--system-prompt-file", str(prompt_file),
            "--db", str(db),
            "--out", str(out),
            "--dry-run",
            "--yes",
        ]
    )
    assert rc == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # третье сообщение вне окна
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["message_id"] == "1"
    assert parsed[0]["original_text"] == "Привет"
    assert parsed[0]["new_response"] == ""  # dry-run
    assert "diff_score" in parsed[1]


def test_invalid_chat_id_returns_empty_gracefully(tmp_path: Path) -> None:
    """Несуществующий chat_id не падает, возвращает rc=0 и пустой output."""
    db = tmp_path / "archive.db"
    _make_archive_db(db, [("1", "100", "u1", "2026-04-20T10:00:00", "X", None)])
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("S", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    rc = replay_conversation.main(
        [
            "--chat-id", "99999",
            "--from", "2026-04-20",
            "--to", "2026-04-21",
            "--system-prompt-file", str(prompt_file),
            "--db", str(db),
            "--out", str(out),
            "--dry-run",
            "--yes",
        ]
    )
    assert rc == 0
    assert out.read_text(encoding="utf-8") == ""


def test_prompt_file_missing_returns_error(tmp_path: Path) -> None:
    """Отсутствующий system-prompt-file → rc=2."""
    db = tmp_path / "archive.db"
    _make_archive_db(db, [("1", "100", "u1", "2026-04-20T10:00:00", "X", None)])
    rc = replay_conversation.main(
        [
            "--chat-id", "100",
            "--from", "2026-04-20",
            "--to", "2026-04-21",
            "--system-prompt-file", str(tmp_path / "missing.txt"),
            "--db", str(db),
            "--dry-run",
            "--yes",
        ]
    )
    assert rc == 2


def test_output_jsonl_with_injected_llm(tmp_path: Path) -> None:
    """Каждая JSONL-строка парсится; injected llm даёт детерминированный ответ."""
    db = tmp_path / "archive.db"
    _make_archive_db(
        db,
        [
            ("1", "100", "u1", "2026-04-20T10:00:00", "hello", None),
            ("2", "100", "u2", "2026-04-20T10:05:00", "world", None),
        ],
    )
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("PROMPT", encoding="utf-8")
    out = tmp_path / "out.jsonl"

    captured: list[tuple[str, str, str]] = []

    def fake_llm(system_prompt: str, user_text: str, model: str) -> str:
        captured.append((system_prompt, user_text, model))
        return f"echo:{user_text}"

    rc = replay_conversation.main(
        [
            "--chat-id", "100",
            "--from", "2026-04-20",
            "--to", "2026-04-21",
            "--system-prompt-file", str(prompt_file),
            "--model", "test-model",
            "--db", str(db),
            "--out", str(out),
            "--yes",
        ],
        llm=fake_llm,
    )
    assert rc == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["new_response"] == "echo:hello"
    assert parsed[1]["new_response"] == "echo:world"
    # diff_score должен быть рассчитан и быть float в [0,1]
    for row in parsed:
        assert isinstance(row["diff_score"], (int, float))
        assert 0.0 <= float(row["diff_score"]) <= 1.0
    # llm был вызван дважды с тем же system prompt и моделью
    assert len(captured) == 2
    assert all(c[0] == "PROMPT" and c[2] == "test-model" for c in captured)


def test_diff_score_identical_is_zero() -> None:
    assert replay_conversation.diff_score("abc", "abc") == 0.0


def test_diff_score_disjoint_is_high() -> None:
    score = replay_conversation.diff_score("aaaa", "bbbb")
    assert score > 0.9


def test_cost_confirm_threshold_skipped_when_yes() -> None:
    assert replay_conversation._confirm_cost(1000, assume_yes=True) is True


def test_cost_confirm_under_threshold_passes() -> None:
    assert replay_conversation._confirm_cost(5, assume_yes=False) is True


def test_cost_confirm_prompts_when_over_threshold() -> None:
    answers = {"called": 0}

    def prompt(_: str) -> str:
        answers["called"] += 1
        return "n"

    assert (
        replay_conversation._confirm_cost(500, assume_yes=False, prompt_fn=prompt)
        is False
    )
    assert answers["called"] == 1

#!/usr/bin/env python3
"""Conversation Replay — dev tool для прогонки исторической переписки через LLM
с альтернативным system prompt.

Read-only по archive.db. Делает outbound HTTP к OpenClaw Gateway (или к подменяемому
callable из тестов). Не трогает production runtime.

Пример использования:

    venv/bin/python scripts/replay_conversation.py \
        --chat-id -1001234567890 \
        --from 2026-04-20 \
        --to 2026-04-21 \
        --limit 20 \
        --system-prompt-file prompts/new_system.txt \
        --model google/gemini-3-pro-preview \
        --out replay_run_001.jsonl

Output (JSONL, одна строка на сообщение):
    {"message_id": "...", "timestamp": "...", "sender_id": "...",
     "original_text": "...", "new_response": "...", "diff_score": 0.42}
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, TextIO

# Дефолтный путь к архиву памяти Краба
DEFAULT_ARCHIVE_DB = Path("~/.openclaw/krab_memory/archive.db").expanduser()
# Дефолтный gateway (OpenClaw)
DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"
# Порог числа сообщений выше которого спрашиваем подтверждение (cost-aware)
COST_CONFIRM_THRESHOLD = 100


@dataclass
class ReplayMessage:
    """Одно сообщение из archive.db, попавшее в replay-окно."""

    message_id: str
    chat_id: str
    sender_id: str | None
    timestamp: str
    text: str
    reply_to_id: str | None = None


@dataclass
class ReplayResult:
    """Результат проигрывания одного сообщения."""

    message_id: str
    timestamp: str
    sender_id: str | None
    original_text: str
    new_response: str
    diff_score: float
    error: str | None = None
    # Дополнительные поля при необходимости расширения
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        payload = {
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "sender_id": self.sender_id,
            "original_text": self.original_text,
            "new_response": self.new_response,
            "diff_score": self.diff_score,
        }
        if self.error:
            payload["error"] = self.error
        if self.extra:
            payload["extra"] = self.extra
        return json.dumps(payload, ensure_ascii=False)


# Тип LLM-callable: (system_prompt, user_text, model) -> response_text
LLMCallable = Callable[[str, str, str], str]


def load_messages(
    db_path: Path,
    chat_id: str,
    date_from: str,
    date_to: str,
    limit: int | None = None,
) -> list[ReplayMessage]:
    """Читает сообщения чата за период [date_from, date_to] из archive.db (read-only).

    Даты в формате ISO (YYYY-MM-DD или полный timestamp). Сравнение строковое
    благодаря ISO-формату хранения.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"archive_db_missing path={db_path}")

    # Открываем строго read-only через URI
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT message_id, chat_id, sender_id, timestamp, "
            "text_redacted, reply_to_id "
            "FROM messages "
            "WHERE chat_id = ? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC"
        )
        params: list[Any] = [str(chat_id), date_from, date_to]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        ReplayMessage(
            message_id=str(r["message_id"]),
            chat_id=str(r["chat_id"]),
            sender_id=(str(r["sender_id"]) if r["sender_id"] is not None else None),
            timestamp=str(r["timestamp"]),
            text=str(r["text_redacted"] or ""),
            reply_to_id=(str(r["reply_to_id"]) if r["reply_to_id"] else None),
        )
        for r in rows
    ]


def diff_score(a: str, b: str) -> float:
    """Простая мера расхождения [0..1] между двумя строками. 0 = идентичны."""
    if not a and not b:
        return 0.0
    ratio = SequenceMatcher(None, a or "", b or "").ratio()
    return round(1.0 - ratio, 4)


def default_gateway_llm(gateway_url: str = DEFAULT_GATEWAY_URL) -> LLMCallable:
    """Создаёт LLMCallable, бьющий в OpenClaw Gateway по HTTP.

    Импорт httpx ленивый — чтобы тесты не требовали сетевого стека.
    """

    def _call(system_prompt: str, user_text: str, model: str) -> str:
        import httpx  # локальный импорт — не нужен в unit-тестах

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        }
        url = gateway_url.rstrip("/") + "/v1/chat/completions"
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        # Совместимый OpenAI-формат
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"gateway_unexpected_response error={exc}") from exc

    return _call


def replay(
    messages: Iterable[ReplayMessage],
    system_prompt: str,
    model: str,
    llm: LLMCallable,
    *,
    out_stream: TextIO,
) -> list[ReplayResult]:
    """Прогоняет сообщения через llm(system_prompt, user_text, model)."""
    results: list[ReplayResult] = []
    for msg in messages:
        try:
            new_response = llm(system_prompt, msg.text, model)
            err: str | None = None
        except Exception as exc:  # dev-tool: ловим всё, помечаем error
            new_response = ""
            err = f"{type(exc).__name__}: {exc}"

        score = diff_score(msg.text, new_response) if not err else 1.0
        result = ReplayResult(
            message_id=msg.message_id,
            timestamp=msg.timestamp,
            sender_id=msg.sender_id,
            original_text=msg.text,
            new_response=new_response,
            diff_score=score,
            error=err,
        )
        results.append(result)
        out_stream.write(result.to_jsonl() + "\n")
        out_stream.flush()
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay archive.db conversation through alternate system prompt"
    )
    p.add_argument("--chat-id", required=True, help="Telegram chat_id (как строка)")
    p.add_argument("--from", dest="date_from", required=True, help="ISO дата/время начала")
    p.add_argument("--to", dest="date_to", required=True, help="ISO дата/время конца")
    p.add_argument("--limit", type=int, default=None, help="Максимум сообщений")
    p.add_argument(
        "--system-prompt-file",
        type=Path,
        required=True,
        help="Файл с альтернативным system prompt",
    )
    p.add_argument(
        "--model",
        default="google/gemini-3-pro-preview",
        help="Модель для replay",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Путь к JSONL output (по умолчанию stdout)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_ARCHIVE_DB,
        help=f"archive.db путь (default: {DEFAULT_ARCHIVE_DB})",
    )
    p.add_argument(
        "--gateway-url",
        default=DEFAULT_GATEWAY_URL,
        help=f"OpenClaw Gateway URL (default: {DEFAULT_GATEWAY_URL})",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Не спрашивать подтверждение при >100 сообщениях",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Не звать LLM — только выгрузить сообщения с пустым new_response",
    )
    return p.parse_args(argv)


def _confirm_cost(n: int, *, assume_yes: bool, prompt_fn: Callable[[str], str] = input) -> bool:
    """Спросить подтверждение если сообщений > порога."""
    if n <= COST_CONFIRM_THRESHOLD or assume_yes:
        return True
    try:
        ans = prompt_fn(
            f"Будет вызвано {n} LLM-запросов (порог {COST_CONFIRM_THRESHOLD}). Продолжить? [y/N] "
        )
    except EOFError:
        return False
    return ans.strip().lower() in {"y", "yes", "д", "да"}


def _make_dry_run_llm() -> LLMCallable:
    def _stub(system_prompt: str, user_text: str, model: str) -> str:
        return ""

    return _stub


def main(argv: list[str] | None = None, *, llm: LLMCallable | None = None) -> int:
    args = parse_args(argv)

    # Чтение system prompt
    if not args.system_prompt_file.exists():
        print(
            f"system_prompt_file_missing path={args.system_prompt_file}",
            file=sys.stderr,
        )
        return 2
    system_prompt = args.system_prompt_file.read_text(encoding="utf-8")

    # Загрузка сообщений
    try:
        messages = load_messages(
            args.db,
            chat_id=args.chat_id,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"archive_db_error error_type={type(exc).__name__} error={exc}", file=sys.stderr)
        return 2

    if not messages:
        print("no_messages_in_window chat_id=%s" % args.chat_id, file=sys.stderr)
        # Всё же создаём пустой output для воспроизводимости
        if args.out:
            args.out.write_text("", encoding="utf-8")
        return 0

    # Cost-confirm
    if not args.dry_run and not _confirm_cost(len(messages), assume_yes=args.yes):
        print("aborted_by_user", file=sys.stderr)
        return 1

    # LLM resolver
    if args.dry_run:
        llm_fn: LLMCallable = _make_dry_run_llm()
    elif llm is not None:
        llm_fn = llm
    else:
        llm_fn = default_gateway_llm(args.gateway_url)

    # Output stream
    out_stream: TextIO
    close_out = False
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        out_stream = args.out.open("w", encoding="utf-8")
        close_out = True
    else:
        out_stream = sys.stdout

    try:
        replay(
            messages,
            system_prompt=system_prompt,
            model=args.model,
            llm=llm_fn,
            out_stream=out_stream,
        )
    finally:
        if close_out:
            out_stream.close()

    print(
        f"replay_done count={len(messages)} model={args.model} "
        f"out={args.out if args.out else '<stdout>'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

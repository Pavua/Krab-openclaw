#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Живой E2E round-trip для reserve Telegram Bot.

Что делает:
1) Проверяет truthful runtime-срез reserve-канала через web endpoint;
2) Безопасно открывает временную копию owner Telegram session;
3) Отправляет deterministic probe в private chat reserve-боту;
4) Ждёт ответ от бота и сохраняет машиночитаемый отчёт.

Зачем это нужно:
- в roadmap долго оставался незакрытый пробел: post-restart delivery была подтверждена,
  а полный inbound `owner -> reserve bot -> reply` ещё не был автоматизирован;
- этот скрипт закрывает именно live transport-доказательство, не подменяя его unit-тестами.

Связь с проектом:
- использует тот же `kraab.session`, что и основной owner userbot, но работает только
  через временную копию session-файла, чтобы не конфликтовать с живым runtime;
- использует `:8080/api/channels/capabilities` как truthful preflight reserve-safe статуса.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config


DEFAULT_PROMPT = "Где ты сейчас работаешь?"
DEFAULT_EXPECTED_MARKERS = (
    "reserve telegram bot",
    "owner-канал",
    "python userbot",
)


def _now_iso() -> str:
    """Возвращает UTC timestamp в компактном ISO-формате."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_base_url() -> str:
    """Берёт базовый URL owner web runtime из env с безопасным fallback."""
    override = str(os.getenv("KRAB_SMOKE_BASE_URL", "") or "").strip()
    if override:
        return override.rstrip("/")
    host = str(os.getenv("WEB_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = str(os.getenv("WEB_PORT", "8080") or "8080").strip()
    return f"http://{host}:{port}"


def _fetch_json(url: str, timeout_sec: float = 10.0) -> tuple[dict[str, Any], str | None]:
    """Безопасно читает локальный JSON endpoint."""
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - локальный owner endpoint.
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310 - локальный owner endpoint.
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), None
    except error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            raw = str(exc)
        return {}, f"http_error:{exc.code}; body={raw[:240]}"
    except (error.URLError, TimeoutError, ValueError) as exc:
        return {}, str(exc)


def _normalize_bot_username(raw_value: str) -> str:
    """Приводит username бота к виду без `@` и лишних пробелов."""
    return str(raw_value or "").strip().lstrip("@")


def _resolve_bot_username(openclaw_path: Path) -> str:
    """
    Ищет username reserve Telegram Bot сначала в env, затем в runtime-конфиге.

    Почему env first:
    - на временной учётке username уже известен и быстро доступен из `.env`;
    - runtime-конфиг не всегда хранит его как отдельное поле.
    """
    env_candidates = (
        os.getenv("OPENCLAW_TELEGRAM_BOT_USERNAME", ""),
        os.getenv("TELEGRAM_BOT_USERNAME", ""),
        os.getenv("OPENCLAW_ALERT_TARGET", ""),
    )
    for candidate in env_candidates:
        normalized = _normalize_bot_username(candidate)
        if normalized and not normalized.isdigit():
            return normalized

    try:
        payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""

    telegram = ((payload.get("channels") or {}).get("telegram") or {})
    for key in ("botUsername", "username", "bot_username"):
        normalized = _normalize_bot_username(str(telegram.get(key) or ""))
        if normalized:
            return normalized
    return ""


def _session_source(project_root: Path) -> Path:
    """Возвращает канонический session-файл owner userbot."""
    session_name = str(Config.TELEGRAM_SESSION_NAME or "kraab").strip() or "kraab"
    return project_root / "data" / "sessions" / f"{session_name}.session"


def _copy_session_bundle(session_src: Path, tmp_dir: Path) -> tuple[str, Path]:
    """
    Копирует session-файл во временную директорию для отдельного MTProto-клиента.

    Так мы не лезем в живой SQLite-файл, который уже может держать runtime.
    """
    session_name = session_src.stem
    copy_name = f"{session_name}_reserve_e2e"
    session_dst = tmp_dir / f"{copy_name}.session"
    shutil.copy2(session_src, session_dst)
    for suffix in (".session-shm", ".session-wal", ".session-journal"):
        sidecar = session_src.with_name(f"{session_name}{suffix}")
        if sidecar.exists():
            try:
                shutil.copy2(sidecar, tmp_dir / f"{copy_name}{suffix}")
            except OSError:
                continue
    return copy_name, session_dst


def _message_text(message: Any) -> str:
    """Извлекает текстовую часть сообщения для отчёта и match-проверки."""
    for attr in ("text", "caption"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _message_author_id(message: Any) -> int | None:
    """Возвращает user id отправителя, если он присутствует."""
    from_user = getattr(message, "from_user", None)
    if from_user is None:
        return None
    author_id = getattr(from_user, "id", None)
    try:
        return int(author_id) if author_id is not None else None
    except (TypeError, ValueError):
        return None


def _reply_contains_expected_marker(text: str, markers: Iterable[str]) -> bool:
    """Проверяет, что ответ похож на наш deterministic reserve-ответ."""
    normalized = str(text or "").strip().lower()
    return any(str(marker or "").strip().lower() in normalized for marker in markers)


def _find_bot_reply(messages: Iterable[Any], *, sent_message_id: int, bot_user_id: int) -> dict[str, Any] | None:
    """
    Ищет первое текстовое сообщение бота после нашего probe-сообщения.

    Ключевой критерий — не content match, а сам факт живого ответа reserve-бота.
    Match по expected marker сохраняем отдельно как более строгий diagnostic сигнал.
    """
    for message in messages:
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id <= sent_message_id:
            continue
        if _message_author_id(message) != bot_user_id:
            continue
        text = _message_text(message)
        if not text:
            continue
        return {
            "message_id": message_id,
            "text": text,
            "date": str(getattr(message, "date", "") or ""),
        }
    return None


def _poll_for_reply(
    *,
    app: Any,
    chat_id: int,
    bot_user_id: int,
    sent_message_id: int,
    timeout_sec: float,
    poll_interval_sec: float,
) -> tuple[dict[str, Any] | None, str | None, float]:
    """Ждёт reply в chat history и возвращает найденное сообщение."""
    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_sec))
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            history = list(app.get_chat_history(chat_id, limit=12))
        except Exception as exc:  # noqa: BLE001 - live E2E должен сохранить первичный текст ошибки.
            last_error = str(exc)
            time.sleep(max(0.25, float(poll_interval_sec)))
            continue

        found = _find_bot_reply(history, sent_message_id=sent_message_id, bot_user_id=bot_user_id)
        if found is not None:
            return found, None, round(time.monotonic() - started, 2)
        time.sleep(max(0.25, float(poll_interval_sec)))
    return None, last_error or "reply_timeout", round(time.monotonic() - started, 2)


def _preflight_channel_snapshot(base_url: str) -> dict[str, Any]:
    """Снимает truthful preflight reserve-состояния через owner web runtime."""
    payload, err = _fetch_json(f"{base_url}/api/channels/capabilities")
    if err is not None:
        return {
            "ok": False,
            "error": err,
            "reserve_safe": None,
            "reserve_transport": "",
        }

    channel_capabilities = (
        (payload.get("channel_capabilities") or {})
        if isinstance(payload, dict)
        else {}
    )
    summary = (
        (channel_capabilities.get("summary") or {})
        if isinstance(channel_capabilities, dict)
        else {}
    )
    return {
        "ok": True,
        "error": None,
        "reserve_safe": bool(summary.get("reserve_safe")),
        "reserve_transport": str(summary.get("reserve_transport") or ""),
        "payload": payload,
    }


def _default_output_path() -> Path:
    """Готовит путь для JSON-отчёта живого E2E."""
    out_dir = PROJECT_ROOT / "artifacts" / "live_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return out_dir / f"reserve_telegram_roundtrip_{stamp}.json"


def run_roundtrip(
    *,
    prompt: str,
    expected_markers: tuple[str, ...],
    timeout_sec: float,
    poll_interval_sec: float,
    base_url: str,
) -> dict[str, Any]:
    """Выполняет полный reserve Telegram round-trip и возвращает отчёт."""
    report: dict[str, Any] = {
        "generated_at_utc": _now_iso(),
        "ok": False,
        "prompt": prompt,
        "expected_markers": list(expected_markers),
        "base_url": base_url,
    }

    preflight = _preflight_channel_snapshot(base_url)
    report["preflight"] = preflight

    openclaw_path = Path.home() / ".openclaw" / "openclaw.json"
    bot_username = _resolve_bot_username(openclaw_path)
    report["bot_username"] = f"@{bot_username}" if bot_username else ""
    if not bot_username:
        report["error"] = "telegram_bot_username_missing"
        return report

    session_src = _session_source(PROJECT_ROOT)
    report["session_source"] = str(session_src)
    if not session_src.exists():
        report["error"] = "telegram_session_missing"
        return report

    if not Config.TELEGRAM_API_ID or not Config.TELEGRAM_API_HASH:
        report["error"] = "telegram_api_credentials_missing"
        return report

    try:
        from pyrogram import Client
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"pyrogram_import_failed: {exc}"
        return report

    with tempfile.TemporaryDirectory(prefix="krab_reserve_roundtrip_") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        try:
            copy_name, session_copy = _copy_session_bundle(session_src, tmp_dir)
        except OSError as exc:
            report["error"] = f"session_copy_failed: {exc}"
            return report

        report["session_copy"] = str(session_copy)
        try:
            with Client(
                copy_name,
                api_id=Config.TELEGRAM_API_ID,
                api_hash=Config.TELEGRAM_API_HASH,
                workdir=str(tmp_dir),
            ) as app:
                bot_user = app.get_users(bot_username)
                bot_user_id = int(getattr(bot_user, "id", 0) or 0)
                if bot_user_id <= 0:
                    report["error"] = "telegram_bot_resolve_failed"
                    return report

                sent_message = app.send_message(bot_user_id, prompt)
                sent_message_id = int(getattr(sent_message, "id", 0) or 0)
                report["sent_message"] = {
                    "chat_id": bot_user_id,
                    "message_id": sent_message_id,
                }

                reply, reply_error, latency_sec = _poll_for_reply(
                    app=app,
                    chat_id=bot_user_id,
                    bot_user_id=bot_user_id,
                    sent_message_id=sent_message_id,
                    timeout_sec=timeout_sec,
                    poll_interval_sec=poll_interval_sec,
                )
                report["latency_sec"] = latency_sec
                report["reply"] = reply or {}
                report["reply_error"] = reply_error
        except sqlite3.Error as exc:
            report["error"] = f"sqlite_error: {exc}"
            return report
        except Exception as exc:  # noqa: BLE001
            report["error"] = f"telegram_roundtrip_failed: {exc}"
            return report

    reply_text = str((report.get("reply") or {}).get("text") or "")
    reply_received = bool(reply_text)
    content_match = _reply_contains_expected_marker(reply_text, expected_markers)
    report["reply_received"] = reply_received
    report["content_match"] = content_match

    # Успех round-trip считаем только когда reserve-safe preflight честный и бот реально ответил.
    report["ok"] = bool(preflight.get("reserve_safe")) and reply_received
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Живой reserve Telegram Bot round-trip E2E")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Сообщение, которое будет отправлено reserve-боту.")
    parser.add_argument(
        "--expect-marker",
        action="append",
        default=[],
        help="Дополнительный substring, который считаем ожидаемым в ответе бота.",
    )
    parser.add_argument("--timeout-sec", type=float, default=75.0, help="Максимальное ожидание ответа reserve-бота.")
    parser.add_argument("--poll-interval-sec", type=float, default=2.0, help="Интервал опроса chat history.")
    parser.add_argument("--output", type=Path, default=_default_output_path(), help="Куда сохранить JSON-отчёт.")
    args = parser.parse_args()

    expected_markers = tuple(dict.fromkeys([*DEFAULT_EXPECTED_MARKERS, *args.expect_marker]))
    report = run_roundtrip(
        prompt=str(args.prompt or DEFAULT_PROMPT),
        expected_markers=expected_markers,
        timeout_sec=float(args.timeout_sec),
        poll_interval_sec=float(args.poll_interval_sec),
        base_url=_resolve_base_url(),
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nОтчёт: {output_path}")
    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

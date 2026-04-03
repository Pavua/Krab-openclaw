# -*- coding: utf-8 -*-
"""
probe_telegram_file_access_smoke.py — живой smoke для deterministic `!probe`.

Зачем нужен:
- после hardening Краба нужен простой live-check, который доказывает, что
  owner может отправить `!probe <absolute_path>` и получить не фантазию, а
  конкретный factual verdict;
- результат должен сохраняться в `artifacts/ops`, чтобы следующий агент видел
  тот же smoke-report, а не пересказывал его по памяти;
- reuse существующего TelegramBridge keeps it simple: мы проверяем именно
  рабочий Telegram-контур, а не новый одноразовый клиент.

Как связан с системой:
- использует `mcp-servers/telegram/telegram_bridge.py` и owner session
  `p0lrd_cc`, уже применяемую в других Telegram smoke-проверках;
- отправляет команды в указанный owner chat и ждёт ответов от живого Краба;
- пишет `latest` + timestamp JSON в `artifacts/ops`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"
BRIDGE_DIR = ROOT / "mcp-servers" / "telegram"

if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from telegram_bridge import TelegramBridge  # noqa: E402


DEFAULT_OWNER_SESSION = "p0lrd_cc"
DEFAULT_CHAT = "yung_nagato"
VERDICT_MARKERS: tuple[str, ...] = (
    "file_read_confirmed",
    "file_read_not_confirmed",
    "directory_access_confirmed",
    "directory_access_not_confirmed",
    "not_found",
)
DEFAULT_PATHS: tuple[str, ...] = (
    str((Path.home() / "Downloads").resolve()),
    str((Path.home() / ".openclaw" / "workspace-main-messaging").resolve()),
    str(ROOT.resolve()),
)


@dataclass(slots=True)
class ProbeMatch:
    """Компактное описание найденного probe-ответа."""

    verdict: str
    message: dict[str, Any]


def parse_args() -> argparse.Namespace:
    """Разбирает CLI-параметры smoke-проверки."""
    parser = argparse.ArgumentParser(
        description="Живой smoke deterministic команды !probe через Telegram owner session.",
    )
    parser.add_argument(
        "--chat",
        default="",
        help="Чат для отправки !probe (default: OWNER_USERNAME из .env, затем OPENCLAW_ALERT_TARGET, затем yung_nagato)",
    )
    parser.add_argument(
        "--owner-session",
        default=DEFAULT_OWNER_SESSION,
        help="Telegram session name для owner smoke (default: p0lrd_cc)",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=45.0,
        help="Сколько секунд ждать ответ на каждый !probe (default: 45)",
    )
    parser.add_argument(
        "--poll-sec",
        type=float,
        default=2.0,
        help="Интервал reread истории при ожидании ответа (default: 2)",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=12,
        help="Сколько сообщений перечитывать из истории при polling (default: 12)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Опциональный путь для JSON-артефакта. Иначе пишем в artifacts/ops.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Абсолютные пути для проверки. Если не переданы, используются канонические smoke-paths.",
    )
    return parser.parse_args()


def _normalize_paths(cli_paths: list[str]) -> list[str]:
    """Нормализует и дедуплицирует probe-paths."""
    source = cli_paths or list(DEFAULT_PATHS)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in source:
        value = str(raw or "").strip()
        if not value:
            continue
        expanded = str(Path(value).expanduser())
        if not expanded.startswith("/"):
            raise ValueError(f"Путь должен быть абсолютным: {raw}")
        if expanded in seen:
            continue
        seen.add(expanded)
        normalized.append(expanded)
    if not normalized:
        raise ValueError("Нужен хотя бы один абсолютный путь для !probe")
    return normalized


def _extract_verdict(text: str) -> str:
    """Достаёт deterministic verdict из текста ответа."""
    lowered = str(text or "")
    for marker in VERDICT_MARKERS:
        if marker in lowered:
            return marker
    return ""


def _select_probe_reply(history: list[dict[str, Any]], *, sent_id: int) -> ProbeMatch | None:
    """
    Находит лучший ответ на конкретный `!probe`.

    Приоритет:
    1. Явный reply на исходное сообщение.
    2. Первый более новый текст с известным deterministic verdict.
    """
    ordered = sorted(
        (item for item in history if isinstance(item, dict)),
        key=lambda item: int(item.get("id") or 0),
    )

    fallback: ProbeMatch | None = None
    for item in ordered:
        msg_id = int(item.get("id") or 0)
        if msg_id <= sent_id:
            continue
        verdict = _extract_verdict(str(item.get("text") or ""))
        if not verdict:
            continue
        if int(item.get("reply_to_message_id") or 0) == sent_id:
            return ProbeMatch(verdict=verdict, message=item)
        if fallback is None:
            fallback = ProbeMatch(verdict=verdict, message=item)
    return fallback


async def _wait_for_probe_reply(
    bridge: TelegramBridge,
    *,
    chat: str,
    sent_message: dict[str, Any],
    timeout_sec: float,
    poll_sec: float,
    history_limit: int,
) -> ProbeMatch | None:
    """Polling-ом ждёт ответ Краба на конкретную `!probe` команду."""
    sent_id = int(sent_message.get("id") or 0)
    deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_sec))
    while True:
        history = await bridge.get_chat_history(chat, limit=history_limit)
        match = _select_probe_reply(history, sent_id=sent_id)
        if match is not None:
            return match
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(max(0.2, float(poll_sec)))


def _build_probe_record(
    *,
    target_path: str,
    command_text: str,
    sent_message: dict[str, Any],
    reply: ProbeMatch | None,
    timeout_sec: float,
) -> dict[str, Any]:
    """Собирает machine-readable запись по одной `!probe` команде."""
    payload: dict[str, Any] = {
        "path": target_path,
        "command": command_text,
        "sent_message": sent_message,
        "timeout_sec": timeout_sec,
        "ok": False,
        "verdict": "timeout",
        "reply_message": None,
    }
    if reply is None:
        payload["error"] = "reply_timeout"
        return payload
    payload["ok"] = True
    payload["verdict"] = reply.verdict
    payload["reply_message"] = reply.message
    payload["error"] = ""
    return payload


def _format_probe_command(target_path: str) -> str:
    """Собирает `!probe` команду безопасно для путей с пробелами."""
    normalized = str(target_path or "")
    if " " in normalized:
        escaped = normalized.replace('"', '\\"')
        return f'!probe "{escaped}"'
    return f"!probe {normalized}"


def _write_artifact(report: dict[str, Any], output_path: Path | None = None) -> list[str]:
    """Пишет JSON-артефакт в explicit output или в `artifacts/ops` latest+timestamp."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return [str(output_path)]

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = ARTIFACTS_DIR / "probe_telegram_file_access_smoke_latest.json"
    versioned_path = ARTIFACTS_DIR / f"probe_telegram_file_access_smoke_{timestamp}.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    latest_path.write_text(payload, encoding="utf-8")
    versioned_path.write_text(payload, encoding="utf-8")
    return [str(latest_path), str(versioned_path)]


async def amain(args: argparse.Namespace) -> int:
    """Запускает live-smoke и печатает итоговый JSON-отчёт."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    paths = _normalize_paths(list(args.paths or []))
    owner_chat = os.getenv("OWNER_USERNAME", "").strip()
    env_chat = os.getenv("OPENCLAW_ALERT_TARGET", "").strip()
    chat = str(args.chat or "").strip() or owner_chat or env_chat or DEFAULT_CHAT
    if not chat:
        raise ValueError("Нужен chat target для live-smoke")

    os.environ["TELEGRAM_SESSION_NAME"] = str(args.owner_session or DEFAULT_OWNER_SESSION).strip() or DEFAULT_OWNER_SESSION
    bridge = TelegramBridge()
    report: dict[str, Any] = {
        "ok": False,
        "kind": "probe_telegram_file_access_smoke",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chat": chat,
        "owner_session": os.environ["TELEGRAM_SESSION_NAME"],
        "paths": paths,
        "results": [],
    }

    await bridge.start()
    try:
        for target_path in paths:
            command_text = _format_probe_command(target_path)
            sent = await bridge.send_message(chat, command_text)
            reply = await _wait_for_probe_reply(
                bridge,
                chat=chat,
                sent_message=sent,
                timeout_sec=float(args.timeout_sec),
                poll_sec=float(args.poll_sec),
                history_limit=int(args.history_limit),
            )
            report["results"].append(
                _build_probe_record(
                    target_path=target_path,
                    command_text=command_text,
                    sent_message=sent,
                    reply=reply,
                    timeout_sec=float(args.timeout_sec),
                )
            )
    finally:
        await bridge.stop()

    report["ok"] = bool(report["results"]) and all(bool(item.get("ok")) for item in report["results"])
    output_arg = str(args.output or "").strip()
    report["artifact_paths"] = []
    written_paths = _write_artifact(report, Path(output_arg).expanduser() if output_arg else None)
    report["artifact_paths"] = written_paths
    _write_artifact(report, Path(output_arg).expanduser() if output_arg else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main() -> int:
    """CLI entrypoint."""
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

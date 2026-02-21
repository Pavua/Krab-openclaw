# -*- coding: utf-8 -*-
"""
Signal Ops Guard: фоновый мониторинг состояния Signal-канала OpenClaw.

Зачем:
1) Не пропускать критические сбои Signal (SSE stream error, not registered, rate limit).
2) Фиксировать инциденты в артефактах с антидублем.
3) Опционально пересылать алерты в выбранный канал (например, Telegram).

Как связано с проектом:
- Использует OpenClaw CLI (`openclaw channels status --probe`, `openclaw channels logs --json`).
- Не внедряется в backend-логику Krab, работает как внешний ops-guard процесс.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"
STATE_FILE = ARTIFACTS_DIR / "signal_guard_state.json"
ALERTS_FILE = ARTIFACTS_DIR / "signal_guard_alerts.jsonl"

DEFAULT_LINES = 120
DEFAULT_COOLDOWN_SEC = 15 * 60
DEFAULT_INTERVAL_SEC = 60

# Паттерны, которые считаем критичными для Signal-канала.
CRITICAL_PATTERNS = (
    r"Signal SSE stream error",
    r"probe failed",
    r"not registered",
    r"Rate Limited",
    r"\b429\b",
    r"connection lost",
    r"fetch failed",
)


@dataclass
class GuardIssue:
    code: str
    severity: str
    message: str
    details: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "last_alert_at": {},
            "last_status": "unknown",
            "updated_at": None,
        }
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "last_alert_at": {},
            "last_status": "unknown",
            "updated_at": None,
        }


def _save_state(state: dict[str, Any]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_alert(entry: dict[str, Any]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    with ALERTS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _parse_signal_status_line(status_output: str) -> str:
    for line in status_output.splitlines():
        if "Signal default:" in line:
            lowered = line.lower()
            if "works" in lowered:
                return "works"
            if "probe failed" in lowered:
                return "probe_failed"
            if "not configured" in lowered:
                return "not_configured"
            return "unknown"
    return "missing"


def _collect_signal_logs(lines: int) -> list[dict[str, Any]]:
    proc = _run(
        [
            "openclaw",
            "channels",
            "logs",
            "--channel",
            "signal",
            "--lines",
            str(lines),
            "--json",
        ]
    )
    if proc.returncode != 0:
        return [
            {
                "time": _now_iso(),
                "level": "error",
                "message": f"channels logs command failed: rc={proc.returncode}; stderr={proc.stderr.strip()}",
            }
        ]
    try:
        payload = json.loads(proc.stdout)
        if isinstance(payload, dict) and isinstance(payload.get("lines"), list):
            return payload["lines"]
    except Exception:
        pass
    return []


def _count_pattern_hits(logs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pattern in CRITICAL_PATTERNS:
        regex = re.compile(pattern, flags=re.IGNORECASE)
        matched = 0
        for item in logs:
            msg = str(item.get("message", ""))
            if regex.search(msg):
                matched += 1
        counts[pattern] = matched
    return counts


def _detect_issue(signal_status: str, logs: list[dict[str, Any]]) -> GuardIssue | None:
    pattern_hits = _count_pattern_hits(logs)

    # Приоритет: статус канала.
    if signal_status == "probe_failed":
        return GuardIssue(
            code="signal_probe_failed",
            severity="high",
            message="Signal канал в статусе probe_failed.",
            details={"status": signal_status, "pattern_hits": pattern_hits},
        )
    if signal_status in {"missing", "not_configured"}:
        return GuardIssue(
            code="signal_missing_or_not_configured",
            severity="critical",
            message="Signal канал отсутствует или не сконфигурирован.",
            details={"status": signal_status, "pattern_hits": pattern_hits},
        )

    # Если статус works, но лог уже шумит постоянными ошибками — всё равно алертим.
    # Порог: >= 3 критичных ошибок fetch/SSE на последних N строках.
    sse_errors = pattern_hits.get(r"Signal SSE stream error", 0)
    fetch_failed = pattern_hits.get(r"fetch failed", 0)
    rate_limited = pattern_hits.get(r"Rate Limited", 0) + pattern_hits.get(r"\b429\b", 0)
    not_registered = pattern_hits.get(r"not registered", 0)

    if not_registered > 0:
        return GuardIssue(
            code="signal_not_registered",
            severity="critical",
            message="Signal сообщает not registered в логах.",
            details={"status": signal_status, "pattern_hits": pattern_hits},
        )

    if rate_limited > 0:
        return GuardIssue(
            code="signal_rate_limited",
            severity="high",
            message="Signal упёрся в Rate Limit (429).",
            details={"status": signal_status, "pattern_hits": pattern_hits},
        )

    if sse_errors >= 3 or fetch_failed >= 3:
        return GuardIssue(
            code="signal_sse_instability",
            severity="high",
            message="Signal SSE нестабилен (повторяющиеся stream/fetch ошибки).",
            details={"status": signal_status, "pattern_hits": pattern_hits},
        )

    return None


def _send_channel_alert(text: str) -> tuple[bool, str]:
    channel = os.getenv("OPENCLAW_ALERT_CHANNEL", "").strip()
    target = os.getenv("OPENCLAW_ALERT_TARGET", "").strip()

    # Fallback по умолчанию: если есть Telegram-бот и OWNER_USERNAME,
    # пробуем отправку в Telegram владельцу.
    if not channel:
        if os.getenv("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip():
            channel = "telegram"
    if not target:
        target = (
            os.getenv("OPENCLAW_TELEGRAM_CHAT_ID", "").strip()
            or os.getenv("OWNER_TELEGRAM_ID", "").strip()
            or os.getenv("OWNER_USERNAME", "").strip()
        )

    if not channel or not target:
        return False, "OPENCLAW_ALERT_CHANNEL/OPENCLAW_ALERT_TARGET не заданы"

    send_cmd = lambda tgt: _run(
        [
            "openclaw",
            "message",
            "send",
            "--channel",
            channel,
            "--target",
            tgt,
            "--message",
            text,
        ]
    )

    proc = send_cmd(target)
    if proc.returncode == 0:
        return True, "sent"

    stderr = proc.stderr.strip() or proc.stdout.strip() or f"rc={proc.returncode}"
    is_tg_chat_not_found = (
        channel == "telegram"
        and "chat not found" in stderr.lower()
        and target.startswith("@")
    )
    if not is_tg_chat_not_found:
        return False, stderr

    # Если username в Telegram не сработал, пробуем chat_id fallback.
    fallback_targets = [
        os.getenv("OPENCLAW_TELEGRAM_CHAT_ID", "").strip(),
        os.getenv("OWNER_TELEGRAM_ID", "").strip(),
    ]
    for candidate in fallback_targets:
        if not candidate:
            continue
        fallback_proc = send_cmd(candidate)
        if fallback_proc.returncode == 0:
            return True, f"sent_via_fallback:{candidate}"
        stderr = (
            fallback_proc.stderr.strip()
            or fallback_proc.stdout.strip()
            or f"rc={fallback_proc.returncode}"
        )

    hint = (
        "Telegram chat not found. Нужен chat_id: открой диалог с ботом, отправь /start, "
        "затем выполни ./scripts/resolve_telegram_alert_target.command"
    )
    return False, f"{stderr} | {hint}"


def _send_macos_notification(title: str, subtitle: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    script = (
        'display notification '
        + shlex.quote(body)
        + ' with title '
        + shlex.quote(title)
        + ' subtitle '
        + shlex.quote(subtitle)
    )
    _run(["osascript", "-e", script])


def _alert_allowed(state: dict[str, Any], issue_code: str, cooldown_sec: int) -> bool:
    ts = state.get("last_alert_at", {}).get(issue_code)
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
    except Exception:
        return True
    return (datetime.now(timezone.utc) - last).total_seconds() >= cooldown_sec


def _record_alert_time(state: dict[str, Any], issue_code: str) -> None:
    state.setdefault("last_alert_at", {})[issue_code] = _now_iso()


def run_once(lines: int, cooldown_sec: int, verbose: bool = False) -> int:
    status_proc = _run(["openclaw", "channels", "status", "--probe"])
    status_text = (status_proc.stdout or "") + "\n" + (status_proc.stderr or "")
    signal_status = _parse_signal_status_line(status_text)

    logs = _collect_signal_logs(lines)
    issue = _detect_issue(signal_status, logs)

    state = _load_state()
    previous_status = state.get("last_status", "unknown")
    state["last_status"] = signal_status

    if verbose:
        print(f"Signal status: {signal_status}")
        print(f"Previous status: {previous_status}")
        print(f"Log lines analyzed: {len(logs)}")

    if issue is None:
        # Отдельный positive-сигнал при восстановлении.
        if previous_status != "works" and signal_status == "works":
            recovery_text = (
                "✅ [Signal Guard] Канал восстановлен.\n"
                f"Статус: {signal_status}\n"
                f"Время: {_now_iso()}"
            )
            sent, reason = _send_channel_alert(recovery_text)
            if verbose:
                print(f"Recovery alert: sent={sent}, reason={reason}")
        _save_state(state)
        print("✅ Signal guard: критичных проблем не найдено.")
        return 0

    if not _alert_allowed(state, issue.code, cooldown_sec):
        _save_state(state)
        print(f"⚠️ Signal guard: проблема обнаружена ({issue.code}), но алерт в cooldown.")
        return 1

    alert_text = (
        "🚨 [Signal Guard] Обнаружен инцидент\n"
        f"Код: {issue.code}\n"
        f"Severity: {issue.severity}\n"
        f"Сообщение: {issue.message}\n"
        f"Время: {_now_iso()}"
    )

    sent, send_reason = _send_channel_alert(alert_text)
    _send_macos_notification("Krab Signal Guard", issue.code, issue.message)

    entry = {
        "time": _now_iso(),
        "issue": {
            "code": issue.code,
            "severity": issue.severity,
            "message": issue.message,
            "details": issue.details,
        },
        "channel_alert_sent": sent,
        "channel_alert_result": send_reason,
    }
    _append_alert(entry)

    _record_alert_time(state, issue.code)
    _save_state(state)

    print("🚨 Signal guard: инцидент зафиксирован.")
    print(f"- issue: {issue.code}")
    print(f"- channel alert sent: {sent}")
    if not sent:
        print(f"- reason: {send_reason}")
    print(f"- alerts file: {ALERTS_FILE}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal Ops Guard")
    parser.add_argument("--once", action="store_true", help="Один цикл проверки и выход")
    parser.add_argument("--interval-sec", type=int, default=DEFAULT_INTERVAL_SEC)
    parser.add_argument("--lines", type=int, default=DEFAULT_LINES)
    parser.add_argument("--cooldown-sec", type=int, default=DEFAULT_COOLDOWN_SEC)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.once:
        return run_once(lines=args.lines, cooldown_sec=args.cooldown_sec, verbose=args.verbose)

    print("🛰️ Signal guard daemon mode started")
    print(f"- interval-sec: {args.interval_sec}")
    print(f"- lines: {args.lines}")
    print(f"- cooldown-sec: {args.cooldown_sec}")

    while True:
        try:
            run_once(lines=args.lines, cooldown_sec=args.cooldown_sec, verbose=args.verbose)
        except Exception as exc:
            # Не падаем насмерть, чтобы guard жил постоянно.
            print(f"❌ Signal guard daemon error: {exc}")
        time.sleep(max(args.interval_sec, 5))


if __name__ == "__main__":
    raise SystemExit(main())

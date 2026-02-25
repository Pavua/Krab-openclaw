#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live smoke-проверка каналов и утечек служебного текста в Krab/OpenClaw.

Что проверяет:
1) `openclaw channels status --probe` (доступность gateway/channels).
2) Последние строки ключевых логов на запрещённые паттерны:
   - tool-маркеры (`<|begin_of_box|>`, `<|end_of_box|>`)
   - служебные фразы оркестрации (`The user is asking`, `I will now call ...`)
   - типичные runtime-сбои (`The model has crashed`, `400 No models loaded`)

Зачем:
- Быстрый one-click smoke после релиза/фикса без ручного просмотра логов.
- Репорт в artifacts/ops для handover и incident triage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"
OPENCLAW_LOGS_DIR = Path.home() / ".openclaw" / "logs"
PROJECT_LOGS = [
    ROOT / "logs" / "krab_manual_bg.log",
    ROOT / "logs" / "krab.log",
    ROOT / "logs" / "ai_decisions.log",
]
OPENCLAW_LOGS = [
    OPENCLAW_LOGS_DIR / "gateway.log",
    OPENCLAW_LOGS_DIR / "gateway.err.log",
]
DEFAULT_LOGS = PROJECT_LOGS + OPENCLAW_LOGS

PATTERN_SPECS: list[tuple[str, str, str]] = [
    ("tool_box_marker_begin", r"<\|begin_of_box\|>", "error"),
    ("tool_box_marker_end", r"<\|end_of_box\|>", "error"),
    ("orchestration_leak_user_asking", r"\bthe user is asking\b", "error"),
    ("orchestration_leak_call_function", r"\bi will now call\b", "error"),
    ("model_crash", r"\bthe model has crashed\b", "error"),
    ("no_models_loaded", r"\b400\s+no models loaded\b", "error"),
    (
        "sanitizer_plugin_config_invalid",
        r"plugins\.entries\.krab-output-sanitizer\.config:\s*invalid config",
        "error",
    ),
    (
        "sanitizer_plugin_untracked_provenance",
        r"krab-output-sanitizer:\s*loaded without install/load-path provenance",
        "warn",
    ),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    if max_lines <= 0 or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return lines[-max_lines:]


def _scan_patterns(
    path: Path,
    lines: list[str],
    patterns: list[tuple[str, re.Pattern[str], str]],
    *,
    max_age_sec: int = 1800,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    now_utc = datetime.now(timezone.utc)
    openclaw_log_path = str(path).startswith(str(OPENCLAW_LOGS_DIR))

    def _line_ts(raw: str) -> datetime | None:
        # Форматы:
        # - 2026-02-25T20:16:39.459+01:00 ...
        # - 2026-02-25T19:16:39.093Z ...
        m = re.match(r"^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:\d{2}))", raw)
        if not m:
            return None
        token = m.group(1).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(token)
        except Exception:
            return None

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if openclaw_log_path and max_age_sec > 0:
            ts = _line_ts(raw_line)
            if ts is not None:
                age = (now_utc - ts.astimezone(timezone.utc)).total_seconds()
                if age > float(max_age_sec):
                    continue
        lowered = line.lower()
        for code, rx, severity in patterns:
            if rx.search(lowered):
                # Внешний gateway.log может содержать внутренние tool-маркеры модели
                # как часть диагностического следа OpenClaw. Это не равно утечке
                # в пользовательский канал Krab, поэтому не блокируем smoke.
                if str(path).endswith("/.openclaw/logs/gateway.log") and code in {
                    "tool_box_marker_begin",
                    "tool_box_marker_end",
                    "orchestration_leak_user_asking",
                    "orchestration_leak_call_function",
                }:
                    continue
                effective_severity = severity
                findings.append(
                    {
                        "file": str(path),
                        "line_tail_index": idx,
                        "code": code,
                        "severity": effective_severity,
                        "excerpt": line[:220],
                    }
                )
    return findings


def _run_channels_probe(timeout_sec: int) -> dict[str, Any]:
    cmd = ["openclaw", "channels", "status", "--probe"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(3, int(timeout_sec)),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "exit_code": 127,
            "summary": "openclaw_cli_not_found",
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "summary": f"probe_failed: {str(exc) or exc.__class__.__name__}",
            "stdout_tail": "",
            "stderr_tail": "",
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined = f"{stdout}\n{stderr}".lower()
    ok = proc.returncode == 0 and "gateway reachable" in combined
    summary = "ok" if ok else "probe_failed"
    return {
        "ok": bool(ok),
        "exit_code": int(proc.returncode),
        "summary": summary,
        "stdout_tail": "\n".join(stdout.splitlines()[-24:]),
        "stderr_tail": "\n".join(stderr.splitlines()[-24:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke каналов и sanitizer-паттернов.")
    parser.add_argument("--tail-lines", type=int, default=120, help="Сколько последних строк сканировать в каждом логе.")
    parser.add_argument("--probe-timeout", type=int, default=20, help="Таймаут openclaw channels probe (сек).")
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=1800,
        help="Макс. возраст событий в openclaw-логах для учета (сек). 0 = без фильтра.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Считать warning-паттерны ошибкой smoke-прогона.",
    )
    parser.add_argument(
        "--no-openclaw-logs",
        action="store_true",
        help="Не сканировать ~/.openclaw/logs/*.log (только логи проекта).",
    )
    args = parser.parse_args()

    compiled_patterns = [
        (code, re.compile(rx, re.IGNORECASE), severity)
        for code, rx, severity in PATTERN_SPECS
    ]
    probe = _run_channels_probe(timeout_sec=args.probe_timeout)

    findings: list[dict[str, Any]] = []
    scanned_logs: list[dict[str, Any]] = []
    logs_to_scan = list(PROJECT_LOGS)
    if not args.no_openclaw_logs:
        logs_to_scan.extend(OPENCLAW_LOGS)

    for log_path in logs_to_scan:
        tail = _tail_lines(log_path, args.tail_lines)
        scanned_logs.append(
            {
                "file": str(log_path),
                "exists": log_path.exists(),
                "tail_lines_scanned": len(tail),
            }
        )
        if tail:
            findings.extend(
                _scan_patterns(
                    log_path,
                    tail,
                    compiled_patterns,
                    max_age_sec=int(max(0, args.max_age_seconds)),
                )
            )

    error_findings = [x for x in findings if str(x.get("severity")) == "error"]
    warn_findings = [x for x in findings if str(x.get("severity")) == "warn"]
    has_blocking_findings = bool(error_findings) or (bool(warn_findings) and args.strict_warnings)
    ok = bool(probe.get("ok")) and not has_blocking_findings
    report = {
        "ok": ok,
        "generated_at": _now_iso(),
        "tail_lines": int(args.tail_lines),
        "max_age_seconds": int(max(0, args.max_age_seconds)),
        "channels_probe": probe,
        "logs": scanned_logs,
        "findings_count": len(findings),
        "error_findings_count": len(error_findings),
        "warn_findings_count": len(warn_findings),
        "findings": findings,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    report_path = ARTIFACTS_DIR / f"live_channel_smoke_{stamp}.json"
    latest_path = ARTIFACTS_DIR / "live_channel_smoke_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    print("🧪 Live Channel Smoke")
    print(f"- channels_probe: {'ok' if probe.get('ok') else 'fail'}")
    print(f"- findings(total/error/warn): {len(findings)}/{len(error_findings)}/{len(warn_findings)}")
    print(f"- report: {report_path}")

    if findings:
        print("\n⚠️ Обнаружены запрещённые паттерны:")
        for item in findings[:12]:
            print(
                f"  - [{item['severity']}] {item['code']} :: {item['file']} :: {item['excerpt']}"
            )

    if not probe.get("ok"):
        print("\n❌ channels probe не пройден.")
        tail = str(probe.get("stdout_tail", "") or probe.get("stderr_tail", "")).strip()
        if tail:
            print(tail)

    if ok:
        print("\n✅ Smoke пройден.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

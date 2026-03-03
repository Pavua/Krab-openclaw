#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live smoke-проверка каналов и критических паттернов стабильности Krab/OpenClaw.

Зачем нужен скрипт:
1) дать быстрый acceptance-срез E1→E3 без ручного разбора логов;
2) проверить фактический статус активных каналов через web endpoint;
3) поймать критичные сигнатуры (`No models loaded`, `model crashed`, cloud 401 и т.п.) в хвостах логов.

Связь с системой:
- использует `GET /api/openclaw/channels/status` как runtime truth по каналам;
- использует `GET /api/health/lite` как liveness/auth truth;
- предназначен для запуска из репозитория Krab и может быть обёрнут в `.command`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

DEFAULT_CHANNELS_URL = "http://127.0.0.1:8080/api/openclaw/channels/status"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8080/api/health/lite"
DEFAULT_LOG_FILES = (
    Path("openclaw.log"),
    Path("krab.log"),
)

# Контракт кодов E1→E3 (error/warn), плюс guard по sanitizer provenance/config.
PATTERN_SPECS: list[tuple[str, re.Pattern[str], str]] = [
    ("telegram_session_io_error", re.compile(r"(disk i/o error|auth key not found|database is locked)", re.IGNORECASE), "error"),
    ("no_models_loaded", re.compile(r"no models loaded", re.IGNORECASE), "error"),
    ("lm_empty_stream", re.compile(r"(empty message|stopiteration.*empty)", re.IGNORECASE), "error"),
    ("lm_model_crash", re.compile(r"model has crashed without additional information", re.IGNORECASE), "error"),
    (
        "openclaw_auth_unauthorized",
        re.compile(r"(unauthorized|unauthenticated|invalid api key|forbidden|status=401| 401 )", re.IGNORECASE),
        "error",
    ),
    (
        "sanitizer_plugin_config_invalid",
        re.compile(r"krab-output-sanitizer.*(config invalid|invalid config|schema error)", re.IGNORECASE),
        "error",
    ),
    (
        "sanitizer_plugin_untracked_provenance",
        re.compile(r"without install/load-path provenance", re.IGNORECASE),
        "warn",
    ),
]


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    """Возвращает хвост файла как список строк; для отсутствующего файла — пустой список."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = content.splitlines()
    if max_lines <= 0:
        return lines
    return lines[-max_lines:]


def _scan_patterns(
    source_path: Path,
    lines: Iterable[str],
    pattern_specs: Iterable[tuple[str, re.Pattern[str], str]],
) -> list[dict[str, Any]]:
    """Ищет известные паттерны в линиях и возвращает findings в машиночитаемом виде."""
    findings: list[dict[str, Any]] = []
    patterns = list(pattern_specs)
    for idx, line in enumerate(lines, start=1):
        raw = str(line or "")
        for code, pattern, severity in patterns:
            if pattern.search(raw):
                findings.append(
                    {
                        "source": str(source_path),
                        "line": idx,
                        "code": code,
                        "severity": severity,
                        "text": raw.strip(),
                    }
                )
    return findings


def _parse_line_timestamp_utc(line: str) -> datetime | None:
    """
    Пытается распарсить UTC timestamp из начала строки лога.

    Поддержка:
    - `YYYY-MM-DDTHH:MM:SS.sssZ ...`
    - `YYYY-MM-DDTHH:MM:SSZ ...`
    """
    raw = str(line or "").strip()
    if len(raw) < 20:
        return None
    prefix = raw.split(" ", 1)[0]
    candidate = prefix.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_recent_lines(lines: list[str], max_age_minutes: float) -> list[str]:
    """
    Оставляет только свежие линии по timestamp в пределах окна.

    Если timestamp в строке не распознан — строка остаётся (fail-open),
    чтобы не терять важные сообщения из нестандартного формата логов.
    """
    if max_age_minutes <= 0:
        return lines
    now_utc = datetime.now(timezone.utc)
    max_age_sec = max_age_minutes * 60.0
    out: list[str] = []
    for line in lines:
        ts = _parse_line_timestamp_utc(line)
        if ts is None:
            out.append(line)
            continue
        age = (now_utc - ts).total_seconds()
        if age <= max_age_sec:
            out.append(line)
    return out


def _fetch_json(url: str, timeout_sec: float = 8.0) -> tuple[dict[str, Any], str | None]:
    """Безопасно получает JSON по HTTP URL."""
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - локальный URL/CLI контракт.
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310 - локальный URL/CLI контракт.
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), None
    except (error.URLError, error.HTTPError, TimeoutError, ValueError) as exc:
        return {}, str(exc)


def _classify_channels(channels_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Классифицирует каналы в passed/failed/skipped.

    Правила:
    - `OK` -> passed;
    - `FAIL`/`WARN` с `not configured` -> skipped (не считаем красным acceptance-блокером);
    - любой иной не-OK -> failed.
    """
    channels = channels_payload.get("channels") if isinstance(channels_payload, dict) else []
    if not isinstance(channels, list):
        channels = []

    passed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in channels:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unknown")
        status = str(item.get("status") or "WARN").upper()
        meta = str(item.get("meta") or "")
        entry = {"name": name, "status": status, "meta": meta}
        meta_low = meta.lower()

        if status == "OK":
            passed.append(entry)
            continue
        if "not configured" in meta_low:
            skipped.append(entry)
            continue
        failed.append(entry)

    required_total = len(passed) + len(failed)
    success_rate = 100.0 if required_total == 0 else round((len(passed) / required_total) * 100.0, 2)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "required_total": required_total,
        "success_rate": success_rate,
        "gateway_reachable": bool(channels_payload.get("gateway_reachable")),
    }


def build_report(
    *,
    channels_url: str,
    health_url: str,
    log_files: list[Path],
    tail_size: int,
    max_age_minutes: float,
) -> dict[str, Any]:
    """Собирает единый smoke-отчет E1→E3."""
    channels_payload, channels_error = _fetch_json(channels_url)
    health_payload, health_error = _fetch_json(health_url)

    findings: list[dict[str, Any]] = []
    scanned_logs: list[dict[str, Any]] = []
    for log_path in log_files:
        lines = _tail_lines(log_path, tail_size)
        recent_lines = _filter_recent_lines(lines, max_age_minutes)
        scanned_logs.append(
            {
                "path": str(log_path),
                "lines_total_tail": len(lines),
                "lines_recent_window": len(recent_lines),
            }
        )
        findings.extend(_scan_patterns(log_path, recent_lines, PATTERN_SPECS))

    channel_stats = _classify_channels(channels_payload)
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warn"]

    smoke_ok = (
        channels_error is None
        and health_error is None
        and len(channel_stats["failed"]) == 0
        and len(errors) == 0
        and channel_stats["success_rate"] >= 95.0
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": smoke_ok,
        "inputs": {
            "channels_url": channels_url,
            "health_url": health_url,
            "tail_size": tail_size,
            "max_age_minutes": max_age_minutes,
            "logs": [str(p) for p in log_files],
        },
        "health_lite": {
            "error": health_error,
            "openclaw_auth_state": health_payload.get("openclaw_auth_state"),
            "telegram_userbot_state": health_payload.get("telegram_userbot_state"),
            "lmstudio_model_state": health_payload.get("lmstudio_model_state"),
        },
        "channels": {
            "error": channels_error,
            "gateway_reachable": channel_stats["gateway_reachable"],
            "success_rate": channel_stats["success_rate"],
            "required_total": channel_stats["required_total"],
            "passed": channel_stats["passed"],
            "failed": channel_stats["failed"],
            "skipped": channel_stats["skipped"],
        },
        "logs": {
            "scanned": scanned_logs,
            "errors": errors,
            "warnings": warnings,
            "all_findings": findings,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live smoke-проверка каналов и стабильности Krab/OpenClaw",
    )
    parser.add_argument("--channels-url", default=DEFAULT_CHANNELS_URL, help="URL channels status endpoint")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL, help="URL health/lite endpoint")
    parser.add_argument(
        "--log-file",
        action="append",
        dest="log_files",
        default=[],
        help="Путь к логу (можно повторять аргумент несколько раз)",
    )
    parser.add_argument("--tail-lines", type=int, default=500, help="Сколько последних строк читать из каждого лога")
    parser.add_argument(
        "--max-age-minutes",
        type=float,
        default=15.0,
        help="Окно свежести логов для паттернов (минуты). 0 = без фильтра.",
    )
    parser.add_argument("--output", default="", help="Куда сохранить JSON отчет (опционально)")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    log_files = [Path(p).expanduser() for p in (args.log_files or [])]
    if not log_files:
        log_files = list(DEFAULT_LOG_FILES)

    report = build_report(
        channels_url=args.channels_url,
        health_url=args.health_url,
        log_files=log_files,
        tail_size=max(0, int(args.tail_lines)),
        max_age_minutes=max(0.0, float(args.max_age_minutes)),
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")

    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

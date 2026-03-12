#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acceptance-проверка E1→E3 для Krab/OpenClaw с KPI-отчётом.

Зачем нужен:
1) за один запуск собрать объективный JSON по критическим багам стабильности;
2) проверить runtime truth (`/api/health/lite`, `/api/openclaw/channels/status`);
3) опционально прогнать restart-циклы и зафиксировать KPI manual relogin;
4) проверить свежие логи на сигнатуры `No models loaded`, `EMPTY MESSAGE`, `model crash`, `cloud 401`.

Связь с проектом:
- дополняет `scripts/live_channel_smoke.py` (не заменяет его);
- используется как acceptance-слой после итераций E1/E2/E3.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_HEALTH_URL = "http://127.0.0.1:8080/api/health/lite"
DEFAULT_CHANNELS_URL = "http://127.0.0.1:8080/api/openclaw/channels/status"
DEFAULT_LOG_FILES = [
    Path("openclaw.log"),
    Path("krab.log"),
]

KPI_PATTERNS = {
    "no_models_loaded": re.compile(r"no models loaded", re.IGNORECASE),
    "empty_message_user_visible": re.compile(
        r"(❌\s*модель\s*вернула\s*пустой\s*поток|<empty message>|\bempty message\b)",
        re.IGNORECASE,
    ),
    "lm_model_crash": re.compile(
        r"(model has crashed without additional information|lm_model_crash)",
        re.IGNORECASE,
    ),
    "manual_relogin_required": re.compile(
        r"(telegram_manual_relogin_required|telegram_session_login_required|startup_state.?login_required)",
        re.IGNORECASE,
    ),
    "cloud_unauthorized": re.compile(
        r"(openclaw_auth_unauthorized|\bunauthorized\b|\bunauthenticated\b|invalid api key|\bforbidden\b|status=401|\b401\b)",
        re.IGNORECASE,
    ),
}

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
TIMESTAMP_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)


def _launcher_state_files(root: Path) -> list[Path]:
    """
    Возвращает launcher-state файлы для текущей учётки вместе с legacy repo-level хвостами.

    Это нужно для restart-acceptance после multi-account миграции: новые launcher'ы
    пишут state в `~/.openclaw/krab_runtime_state`, но старые запускались из корня repo.
    """
    runtime_state_dir = Path.home() / ".openclaw" / "krab_runtime_state"
    return [
        runtime_state_dir / "launcher.lock",
        runtime_state_dir / "openclaw.owner",
        runtime_state_dir / "openclaw.pid",
        runtime_state_dir / "stop_krab",
        root / ".krab_launcher.lock",
        root / ".openclaw.owner",
        root / ".openclaw.pid",
        root / ".stop_krab",
    ]


def _fetch_json(url: str, timeout_sec: float = 8.0) -> tuple[dict[str, Any], str | None]:
    req = request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body), None
    except (error.URLError, error.HTTPError, TimeoutError, ValueError) as exc:
        return {}, str(exc)


def _fetch_json_with_retries(
    url: str,
    *,
    timeout_sec: float = 8.0,
    attempts: int = 1,
) -> tuple[dict[str, Any], str | None]:
    """Повторяет HTTP-запрос endpoint'а для устойчивости к кратковременным сбоям."""
    safe_attempts = max(1, int(attempts))
    last_payload: dict[str, Any] = {}
    last_error: str | None = None
    for _ in range(safe_attempts):
        payload, err = _fetch_json(url, timeout_sec=timeout_sec)
        if err is None:
            return payload, None
        last_payload = payload
        last_error = err
    return last_payload, last_error


def _parse_line_timestamp_utc(line: str) -> datetime | None:
    raw = ANSI_RE.sub("", str(line or "")).strip()
    match = TIMESTAMP_RE.search(raw)
    if not match:
        return None
    candidate = match.group("ts").replace(" ", "T")
    candidate = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tail_lines(path: Path, max_lines: int) -> list[str]:
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


def _filter_recent_lines(lines: list[str], max_age_minutes: float) -> list[str]:
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


def _scan_kpi_patterns(log_files: list[Path], *, tail_lines: int, max_age_minutes: float) -> dict[str, Any]:
    counters: dict[str, int] = {key: 0 for key in KPI_PATTERNS}
    matches: dict[str, list[dict[str, Any]]] = {key: [] for key in KPI_PATTERNS}
    scanned: list[dict[str, Any]] = []

    for log_path in log_files:
        tail = _tail_lines(log_path, tail_lines)
        recent = _filter_recent_lines(tail, max_age_minutes)
        scanned.append(
            {
                "path": str(log_path),
                "tail_lines": len(tail),
                "recent_lines": len(recent),
            }
        )
        for idx, line in enumerate(recent, start=1):
            raw = str(line or "")
            for code, pattern in KPI_PATTERNS.items():
                if pattern.search(raw):
                    counters[code] += 1
                    if len(matches[code]) < 20:
                        matches[code].append(
                            {
                                "source": str(log_path),
                                "line": idx,
                                "text": raw.strip(),
                            }
                        )

    return {"counters": counters, "matches": matches, "scanned": scanned}


def _wait_for_state(health_url: str, *, expected: str, timeout_sec: int) -> tuple[bool, dict[str, Any], str | None]:
    started = time.time()
    last_payload: dict[str, Any] = {}
    last_error: str | None = None

    while (time.time() - started) < timeout_sec:
        payload, err = _fetch_json(health_url)
        if err is None and payload:
            last_payload = payload
            if str(payload.get("telegram_userbot_state") or "") == expected:
                return True, payload, None
        else:
            last_error = err
        time.sleep(1.0)

    return False, last_payload, last_error


def _run_cmd(cmd: str, *, cwd: Path, timeout_sec: int = 180) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return int(proc.returncode), str(proc.stdout or "")
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc)


def _run_restart_cycles(
    *,
    cycles: int,
    root: Path,
    health_url: str,
    stop_cmd: str,
    start_cmd: str,
    per_cycle_timeout_sec: int,
) -> dict[str, Any]:
    """
    Выполняет restart-циклы launcher'а и фиксирует состояние после каждого старта.

    Важно: скрипт целенаправленно не трогает чужие репозитории; только Krab runtime.
    """
    attempts: list[dict[str, Any]] = []
    if cycles <= 0:
        return {"enabled": False, "cycles": 0, "attempts": attempts, "all_running": True}

    # На всякий случай убираем stale lock-файлы, если stop не дочистил их.
    lock_files = _launcher_state_files(root)

    for cycle in range(1, cycles + 1):
        cycle_result: dict[str, Any] = {
            "cycle": cycle,
            "stop": {},
            "start": {},
            "health_after_start": {},
            "running": False,
        }

        stop_rc, stop_out = _run_cmd(stop_cmd, cwd=root)
        cycle_result["stop"] = {
            "rc": stop_rc,
            "output_tail": "\n".join((stop_out or "").splitlines()[-20:]),
        }

        for lock in lock_files:
            try:
                if lock.exists():
                    lock.unlink()
            except OSError:
                pass

        # Запускаем launcher в detached-сессии, чтобы он продолжал жить после выхода shell.
        launcher_log = root / "logs" / f"acceptance_restart_cycle_{cycle}.log"
        launcher_log.parent.mkdir(parents=True, exist_ok=True)
        start_detached = (
            f"nohup {start_cmd} </dev/null > {launcher_log} 2>&1 & "
            "echo $!"
        )
        start_rc, start_out = _run_cmd(start_detached, cwd=root)
        launcher_pid = ""
        if start_out.strip():
            launcher_pid = start_out.strip().splitlines()[-1].strip()

        cycle_result["start"] = {
            "rc": start_rc,
            "launcher_pid": launcher_pid,
            "log": str(launcher_log),
        }

        ok, payload, wait_err = _wait_for_state(
            health_url,
            expected="running",
            timeout_sec=per_cycle_timeout_sec,
        )
        cycle_result["running"] = bool(ok)
        cycle_result["health_after_start"] = payload
        cycle_result["health_wait_error"] = wait_err
        attempts.append(cycle_result)

    all_running = all(bool(item.get("running")) for item in attempts)
    return {
        "enabled": True,
        "cycles": cycles,
        "attempts": attempts,
        "all_running": all_running,
    }


def _classify_channels(channels_payload: dict[str, Any]) -> dict[str, Any]:
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
        if status == "OK":
            passed.append(entry)
            continue
        if "not configured" in meta.lower():
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


def _is_transient_disconnect_failure(entry: dict[str, Any]) -> bool:
    """Признак краткого flapping окна у канала во время auto-reconnect."""
    meta = str(entry.get("meta") or "").lower()
    if "not configured" in meta:
        return False
    return "disconnected" in meta


def _fetch_stable_channels_payload(url: str) -> tuple[dict[str, Any], str | None]:
    """
    Берёт channels/status и делает short-settle retry для transient disconnected.

    Это уменьшает ложные красные acceptance-падения на моменте health-monitor restart.
    """
    payload, err = _fetch_json_with_retries(url, timeout_sec=10.0, attempts=3)
    if err is not None:
        return payload, err

    current_payload = payload
    current_summary = _classify_channels(current_payload)
    for _ in range(2):
        failed = current_summary.get("failed") or []
        if not failed:
            return current_payload, None
        if not all(_is_transient_disconnect_failure(item) for item in failed):
            return current_payload, None
        time.sleep(1.2)
        next_payload, next_err = _fetch_json_with_retries(url, timeout_sec=10.0, attempts=1)
        if next_err is not None:
            return current_payload, None
        current_payload = next_payload
        current_summary = _classify_channels(current_payload)

    return current_payload, None


def _normalize_probe_channels_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Нормализует JSON от `openclaw channels status --probe --json`
    к формату `channels/status`.
    """
    raw_channels = payload.get("channels") if isinstance(payload, dict) else {}
    if not isinstance(raw_channels, dict):
        return {}

    normalized_channels: list[dict[str, Any]] = []
    for channel_id, info in raw_channels.items():
        if not isinstance(info, dict):
            continue

        configured = bool(info.get("configured"))
        running = bool(info.get("running"))
        probe = info.get("probe") if isinstance(info.get("probe"), dict) else {}
        probe_ok = bool(probe.get("ok")) if isinstance(probe, dict) else False

        meta_parts: list[str] = []
        if not configured:
            status = "WARN"
            meta_parts.append("not configured")
        elif not running:
            status = "FAIL"
            meta_parts.append("disconnected")
        elif probe_ok:
            status = "OK"
            meta_parts.append("works")
        else:
            status = "FAIL"
            probe_error = (
                str(probe.get("error") or probe.get("status") or "").strip()
                if isinstance(probe, dict)
                else ""
            )
            meta_parts.append(f"probe failed: {probe_error or 'unknown'}")

        normalized_channels.append(
            {
                "name": str(channel_id),
                "status": status,
                "meta": ", ".join(meta_parts),
            }
        )

    return {
        "gateway_reachable": True,
        "channels": normalized_channels,
    }


def _fetch_probe_channels_payload() -> tuple[dict[str, Any], str | None]:
    """
    Берёт состояние каналов через `openclaw channels status --probe --json`.
    """
    try:
        proc = subprocess.run(
            ["openclaw", "channels", "status", "--probe", "--json"],
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc) or exc.__class__.__name__

    stdout = str(proc.stdout or "").strip()
    stderr = str(proc.stderr or "").strip()
    combined = "\n".join(item for item in (stdout, stderr) if item).strip()
    if proc.returncode != 0:
        tail = "\n".join(combined.splitlines()[-6:]).strip()
        return {}, f"probe_rc={proc.returncode} {tail}".strip()

    json_start = stdout.find("{")
    if json_start < 0:
        tail = "\n".join(combined.splitlines()[-6:]).strip()
        detail = f": {tail}" if tail else ""
        return {}, f"probe_json_not_found{detail}"

    try:
        payload = json.loads(stdout[json_start:])
    except ValueError as exc:
        return {}, f"probe_json_decode_error: {exc}"

    normalized = _normalize_probe_channels_payload(payload)
    channels = normalized.get("channels")
    if not isinstance(channels, list) or len(channels) == 0:
        return {}, "probe_channels_empty"
    return normalized, None


def _fetch_channels_with_fallback(channels_url: str) -> tuple[dict[str, Any], str | None, str, str | None]:
    """
    Возвращает channels payload с fallback на CLI probe.

    Возвращает:
    - payload
    - критическую ошибку (None если итоговый источник доступен)
    - источник truth (`web_endpoint` | `gateway_probe` | `unavailable`)
    - web_error (для диагностики even when fallback succeeded)
    """
    channels_payload, channels_error = _fetch_stable_channels_payload(channels_url)
    if channels_error is None:
        return channels_payload, None, "web_endpoint", None

    probe_payload, probe_error = _fetch_probe_channels_payload()
    if probe_error is None:
        return probe_payload, None, "gateway_probe", channels_error

    combined_error = f"web_error={channels_error}; probe_error={probe_error}"
    return channels_payload, combined_error, "unavailable", channels_error


def build_acceptance_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    log_files = [Path(p).expanduser() for p in args.log_files] or DEFAULT_LOG_FILES

    restart = _run_restart_cycles(
        cycles=int(args.restart_cycles),
        root=root,
        health_url=args.health_url,
        stop_cmd=args.stop_cmd,
        start_cmd=args.start_cmd,
        per_cycle_timeout_sec=int(args.restart_timeout_sec),
    )

    health_payload, health_error = _fetch_json_with_retries(args.health_url, timeout_sec=8.0, attempts=2)
    channels_payload, channels_error, channels_source, channels_web_error = _fetch_channels_with_fallback(
        args.channels_url
    )
    channels = _classify_channels(channels_payload)

    kpi_scan = _scan_kpi_patterns(
        log_files,
        tail_lines=int(args.tail_lines),
        max_age_minutes=float(args.max_age_minutes),
    )
    c = kpi_scan["counters"]

    kpi = {
        "no_models_loaded_outside_userbot": c["no_models_loaded"],
        "empty_message_user_visible": c["empty_message_user_visible"],
        "manual_relogin_required_events": c["manual_relogin_required"],
        "cloud_unauthorized_events": c["cloud_unauthorized"],
        "channel_smoke_success_rate": channels["success_rate"],
        "channel_smoke_required_total": channels["required_total"],
        "channel_smoke_failed_count": len(channels["failed"]),
        "restart_cycles_requested": int(args.restart_cycles),
        "restart_cycles_all_running": bool(restart.get("all_running", False)),
    }

    # Критерии выхода E1→E3 согласно sprint KPI.
    exit_checks = {
        "kpi_no_models_loaded_zero": kpi["no_models_loaded_outside_userbot"] == 0,
        "kpi_empty_message_zero": kpi["empty_message_user_visible"] == 0,
        "kpi_manual_relogin_zero": (
            kpi["manual_relogin_required_events"] == 0 if int(args.restart_cycles) > 0 else True
        ),
        "kpi_cloud_false_ok_zero": not (
            str(health_payload.get("openclaw_auth_state") or "") == "ok"
            and kpi["cloud_unauthorized_events"] > 0
        ),
        "kpi_channel_success_ge_95": channels["success_rate"] >= 95.0,
        "health_endpoint_available": health_error is None,
        "channels_endpoint_available": channels_error is None,
        "restart_cycles_running": bool(restart.get("all_running", True)),
    }

    ok = all(bool(v) for v in exit_checks.values())

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "inputs": {
            "root": str(root),
            "health_url": args.health_url,
            "channels_url": args.channels_url,
            "log_files": [str(p) for p in log_files],
            "tail_lines": int(args.tail_lines),
            "max_age_minutes": float(args.max_age_minutes),
            "restart_cycles": int(args.restart_cycles),
            "restart_timeout_sec": int(args.restart_timeout_sec),
        },
        "health_lite": {
            "error": health_error,
            "payload": health_payload,
        },
        "channels": {
            "error": channels_error,
            "source": channels_source,
            "web_error": channels_web_error,
            "payload": channels_payload,
            "summary": channels,
        },
        "restart": restart,
        "kpi": kpi,
        "kpi_matches": kpi_scan["matches"],
        "logs_scanned": kpi_scan["scanned"],
        "exit_checks": exit_checks,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acceptance E1→E3 + KPI report")
    parser.add_argument("--root", default=".", help="Корень репозитория Krab")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--channels-url", default=DEFAULT_CHANNELS_URL)
    parser.add_argument("--log-file", action="append", dest="log_files", default=[])
    parser.add_argument("--tail-lines", type=int, default=1200)
    parser.add_argument("--max-age-minutes", type=float, default=30.0)
    parser.add_argument("--restart-cycles", type=int, default=0)
    parser.add_argument("--restart-timeout-sec", type=int, default=90)
    parser.add_argument("--start-cmd", default="'./new start_krab.command'")
    parser.add_argument("--stop-cmd", default="'./new Stop Krab.command'")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_acceptance_report(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)

    if args.output:
        path = Path(args.output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")

    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

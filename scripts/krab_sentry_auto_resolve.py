#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 158: еженедельный auto-resolver для устаревших unresolved Sentry issues.

Цель: agents/me забываем закрывать fixed issues — накапливается шум в Sentry.
Cron weekly: находит unresolved issues, где lastSeen старше 7 дней
И нет новых events в последние 7 дней (flat count) → mark resolved.

Отличие от Wave 42-A (sentry_stale_resolver.py):
- Берём org-scope endpoint (а не per-project) — все проекты сразу.
- Проверяем не только lastSeen-возраст, но и stats[7d] плоский.
- Output формат — структурированный JSON в stdout (cron-friendly).
- Защита: dry-run default; --apply нужен явно для PUT.

Usage:
    # Dry-run (default, безопасно):
    venv/bin/python scripts/krab_sentry_auto_resolve.py

    # Реальный resolve:
    venv/bin/python scripts/krab_sentry_auto_resolve.py --apply

    # Через LaunchAgent (ai.krab.sentry-auto-resolve, weekly Fri 09:00):
    launchctl start ai.krab.sentry-auto-resolve

Output (stdout JSON):
    {
      "timestamp": "2026-05-12T09:00:00+00:00",
      "mode": "dry-run" | "apply",
      "total_unresolved": 42,
      "stale_count": 5,
      "resolved": [{"id": "...", "shortId": "KRAB-1", "title": "...", "lastSeen": "...", "age_days": 9}, ...],
      "errors": [...]
    }

Env (из .env):
- SENTRY_AUTH_TOKEN — required;
- SENTRY_ORG_SLUG (default po-zm);
- KRAB_SENTRY_AUTO_RESOLVE_STALE_DAYS (default 7);
- KRAB_SENTRY_AUTO_RESOLVE_LIMIT (default 100, page size).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


def _load_dotenv() -> None:
    """Читает .env в os.environ для LaunchAgent-сценария."""
    env_file = os.environ.get(
        "ENV_FILE",
        str(Path(__file__).parent.parent / ".env"),
    )
    path = Path(env_file)
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


# ─── Конфигурация ────────────────────────────────────────────────────────────

SENTRY_TOKEN: str | None = os.environ.get("SENTRY_AUTH_TOKEN")
SENTRY_ORG: str = os.environ.get("SENTRY_ORG_SLUG", "po-zm")

STALE_DAYS: int = int(os.environ.get("KRAB_SENTRY_AUTO_RESOLVE_STALE_DAYS", "7"))
PAGE_LIMIT: int = int(os.environ.get("KRAB_SENTRY_AUTO_RESOLVE_LIMIT", "100"))
TIMEOUT_SEC: float = float(os.environ.get("KRAB_SENTRY_AUTO_RESOLVE_TIMEOUT_SEC", "30"))
MAX_RETRIES: int = int(os.environ.get("KRAB_SENTRY_AUTO_RESOLVE_MAX_RETRIES", "3"))

DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
STATE_DIR: Path = Path(
    os.environ.get("KRAB_SENTRY_AUTO_RESOLVE_STATE_DIR", str(DEFAULT_STATE_DIR))
)
LOG_FILE: Path = STATE_DIR / "sentry_auto_resolve.log"

USER_AGENT = "krab-sentry-auto-resolve/wave-158 (+https://github.com/po-zm/krab)"


# ─── Логирование ─────────────────────────────────────────────────────────────


def _log_to(file: Path, msg: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with file.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def log_info(msg: str) -> None:
    _log_to(LOG_FILE, msg)


def log_err(msg: str) -> None:
    _log_to(LOG_FILE, f"ERR {msg}")
    print(f"ERR {msg}", file=sys.stderr, flush=True)


# ─── Sentry API ──────────────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SENTRY_TOKEN}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response | None:
    """HTTP с retry + exponential backoff. Возвращает Response или None при провале."""
    for attempt in range(max_retries):
        try:
            resp = client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=_auth_headers(),
            )
        except httpx.RequestError as exc:
            if attempt + 1 < max_retries:
                sleep_sec = 2.0 * (2**attempt)
                log_err(
                    f"{method} attempt={attempt + 1}/{max_retries} url={url} "
                    f"error_type={type(exc).__name__} error={exc} retry_in={sleep_sec}s"
                )
                time.sleep(sleep_sec)
                continue
            log_err(
                f"{method} exhausted url={url} error_type={type(exc).__name__} error={exc}"
            )
            return None

        if resp.status_code == 429:
            retry_after_str = resp.headers.get("Retry-After", "5")
            try:
                retry_after = float(retry_after_str)
            except ValueError:
                retry_after = 5.0
            if attempt + 1 < max_retries:
                log_err(
                    f"{method} 429 retry_after={retry_after}s attempt={attempt + 1}"
                )
                time.sleep(retry_after)
                continue
            log_err(f"{method} 429 exhausted")
            return None

        if 500 <= resp.status_code < 600:
            if attempt + 1 < max_retries:
                sleep_sec = 2.0 * (2**attempt)
                log_err(
                    f"{method} 5xx={resp.status_code} attempt={attempt + 1} "
                    f"retry_in={sleep_sec}s"
                )
                time.sleep(sleep_sec)
                continue
            log_err(f"{method} 5xx={resp.status_code} exhausted")
            return None

        return resp
    return None


def fetch_unresolved_issues(
    client: httpx.Client, *, limit: int = PAGE_LIMIT, stats_period: str = "30d"
) -> list[dict[str, Any]]:
    """Получает unresolved issues org-scope за statsPeriod.

    Запрашиваем 30d по умолчанию (надо «не виден 7d+» — поэтому 30d даёт хороший
    запас, чтобы lastSeen ≥ 7d попадал в выдачу).
    """
    url = f"https://sentry.io/api/0/organizations/{SENTRY_ORG}/issues/"
    params = {
        "query": "is:unresolved",
        "statsPeriod": stats_period,
        "limit": str(limit),
    }
    resp = _request_with_retry(client, "GET", url, params=params)
    if resp is None or resp.status_code != 200:
        log_err(
            f"fetch_unresolved status={resp.status_code if resp else 'none'} "
            f"body_first120={resp.text[:120] if resp else ''}"
        )
        return []
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log_err(f"fetch_unresolved invalid_json error_type={type(exc).__name__} error={exc}")
        return []
    if not isinstance(data, list):
        log_err(f"fetch_unresolved unexpected_type={type(data).__name__}")
        return []
    return data


def resolve_issue(
    client: httpx.Client, issue_id: str, *, reason: str = "auto_resolve_stale_wave158"
) -> bool:
    """Помечает issue как resolved через PUT org-scope endpoint."""
    url = f"https://sentry.io/api/0/organizations/{SENTRY_ORG}/issues/{issue_id}/"
    resp = _request_with_retry(
        client,
        "PUT",
        url,
        json_body={"status": "resolved", "statusDetails": {}},
    )
    if resp is None or resp.status_code not in (200, 202):
        log_err(
            f"resolve_issue issue_id={issue_id} status="
            f"{resp.status_code if resp else 'none'} reason={reason}"
        )
        return False
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        # 200 без JSON всё равно OK
        log_info(f"resolved issue_id={issue_id} reason={reason} no_json_body")
        return True
    if data.get("status") == "resolved":
        log_info(f"resolved issue_id={issue_id} reason={reason}")
        return True
    log_err(
        f"resolve_issue issue_id={issue_id} unexpected_status="
        f"{data.get('status')} reason={reason}"
    )
    return False


# ─── Stale-определение ───────────────────────────────────────────────────────


def _parse_last_seen(value: str | None) -> datetime | None:
    """Парсит ISO-8601 timestamp Sentry (с Z или +00:00). None при ошибке."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _stats_recent_events(issue: dict[str, Any], *, window_days: int) -> int:
    """Сумма events в последних window_days по stats["24h"] / stats["30d"].

    Sentry stats format: {"24h": [[ts, count], ...], "30d": [[ts, count], ...]}.
    Если stats отсутствует — возвращаем 0 (consider flat).
    """
    stats = issue.get("stats") or {}
    if not isinstance(stats, dict):
        return 0
    # Предпочитаем 30d (или какой есть) для широкого окна
    candidates = stats.get("30d") or stats.get("24h") or []
    if not isinstance(candidates, list):
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    cutoff_ts = cutoff.timestamp()
    total = 0
    for entry in candidates:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        ts, cnt = entry[0], entry[1]
        try:
            ts_val = float(ts)
            cnt_val = int(cnt)
        except (TypeError, ValueError):
            continue
        if ts_val >= cutoff_ts:
            total += cnt_val
    return total


def is_stale(
    issue: dict[str, Any],
    *,
    stale_days: int = STALE_DAYS,
    now_fn: Any = None,
) -> tuple[bool, str]:
    """Решает, считается ли issue устаревшим (stale).

    Критерии (ОБА должны выполниться):
    1. lastSeen старше stale_days дней.
    2. В последние stale_days дней нет событий (flat по stats).

    Args:
        issue: dict как от Sentry API.
        stale_days: порог возраста.
        now_fn: callable для подмены текущего времени (для тестов).

    Returns:
        (True, reason) если stale; (False, reason) если активен.
    """
    last_seen = _parse_last_seen(issue.get("lastSeen"))
    if last_seen is None:
        return False, "no_last_seen"

    now = now_fn() if now_fn else datetime.now(timezone.utc)
    age = now - last_seen
    age_days = age.total_seconds() / 86400.0

    if age_days < stale_days:
        return False, f"recent_age_days={age_days:.1f}"

    recent_events = _stats_recent_events(issue, window_days=stale_days)
    if recent_events > 0:
        return (
            False,
            f"events_in_window={recent_events} age_days={age_days:.1f}",
        )

    return True, f"stale age_days={age_days:.1f} no_events_in_{stale_days}d"


# ─── Main pipeline ───────────────────────────────────────────────────────────


def _summary_entry(issue: dict[str, Any], age_days: float) -> dict[str, Any]:
    """Минимальный JSON-snapshot issue для output."""
    return {
        "id": str(issue.get("id") or ""),
        "shortId": issue.get("shortId") or "",
        "title": (issue.get("title") or "")[:200],
        "lastSeen": issue.get("lastSeen") or "",
        "count": int(issue.get("count") or 0),
        "age_days": round(age_days, 1),
        "project": ((issue.get("project") or {}).get("slug")) or "",
    }


def run_auto_resolve(
    *,
    client: httpx.Client | None = None,
    apply: bool = False,
    stale_days: int = STALE_DAYS,
    now_fn: Any = None,
) -> dict[str, Any]:
    """Основной пайплайн: list → filter stale → (optional) resolve."""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=TIMEOUT_SEC)
    try:
        issues = fetch_unresolved_issues(client)
        now = now_fn() if now_fn else datetime.now(timezone.utc)

        stale_candidates: list[tuple[dict[str, Any], float]] = []
        for issue in issues:
            stale, _reason = is_stale(issue, stale_days=stale_days, now_fn=now_fn)
            if not stale:
                continue
            last_seen = _parse_last_seen(issue.get("lastSeen"))
            age_days = (now - last_seen).total_seconds() / 86400.0 if last_seen else 0.0
            stale_candidates.append((issue, age_days))

        resolved: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for issue, age_days in stale_candidates:
            entry = _summary_entry(issue, age_days)
            if not apply:
                resolved.append(entry)
                log_info(
                    f"[DRY] stale id={entry['id']} short={entry['shortId']} "
                    f"age_days={entry['age_days']}"
                )
                continue
            ok = resolve_issue(client, entry["id"])
            if ok:
                resolved.append(entry)
            else:
                errors.append(entry)

        snapshot = {
            "timestamp": now.isoformat(),
            "mode": "apply" if apply else "dry-run",
            "stale_days": stale_days,
            "total_unresolved": len(issues),
            "stale_count": len(stale_candidates),
            "resolved": resolved,
            "errors": errors,
        }
        log_info(
            f"run_done mode={snapshot['mode']} total_unresolved={len(issues)} "
            f"stale={len(stale_candidates)} resolved={len(resolved)} "
            f"errors={len(errors)}"
        )
        return snapshot
    finally:
        if owns_client and client is not None:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sentry auto-resolver for stale unresolved issues (Wave 158).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mark stale issues as resolved (default dry-run).",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=STALE_DAYS,
        help=f"Age threshold in days (default {STALE_DAYS}).",
    )
    args = parser.parse_args(argv)

    if not SENTRY_TOKEN:
        log_err("SENTRY_AUTH_TOKEN not set — exit")
        return 2

    snapshot = run_auto_resolve(apply=args.apply, stale_days=args.stale_days)
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

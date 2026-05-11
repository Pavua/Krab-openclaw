#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 65-H: Sentry direct API poller (replaces sentry_poll_alerts.sh).

Логика:
1. GET https://sentry.io/api/0/projects/{org}/{proj}/issues/?query=is:unresolved&statsPeriod={WINDOW}
2. Для каждого issue.id, которого нет в STATE_FILE → format + Telegram send
3. Записать новые id в STATE_FILE (rolling window 1000)
4. Записать lastSeen cursor для incremental polling

Отличия от bash-версии (Session 23):
- httpx с retry + exponential backoff (3 попытки)
- 30s timeout (вместо 15s — bash often timeout на flaky edge)
- 429 (rate-limit) handling с Retry-After
- Persistent cursor (`last_seen_cursor`) для инкрементального polling
- Логи структурированы: poll.log (info) + poll.err.log (errors)

Env (из .env):
- SENTRY_AUTH_TOKEN, SENTRY_ORG_SLUG (default po-zm),
- SENTRY_PROJECTS (default "python-fastapi krab-ear-agent krab-ear-backend"),
- OPENCLAW_TELEGRAM_BOT_TOKEN, OWNER_NOTIFY_CHAT_ID (или OWNER_USER_IDS),
- SENTRY_POLL_WINDOW (default 24h), SENTRY_POLL_LEVELS (default "error fatal"),
- KRAB_SENTRY_POLL_DIRECT_API (default 1, выставить =0 для возврата к bash-варианту),
- SENTRY_POLL_TIMEOUT_SEC (default 30), SENTRY_POLL_MAX_RETRIES (default 3).
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
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
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
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
SENTRY_PROJECTS: list[str] = os.environ.get(
    "SENTRY_PROJECTS",
    "python-fastapi krab-ear-agent krab-ear-backend",
).split()
WINDOW: str = os.environ.get("SENTRY_POLL_WINDOW", "24h")
LEVELS: list[str] = os.environ.get("SENTRY_POLL_LEVELS", "error fatal").split()

TG_TOKEN: str | None = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN")
_OWNER_LIST: str = os.environ.get("OWNER_USER_IDS", "")
TG_OWNER: str = (
    _OWNER_LIST.split(",")[0].strip() if _OWNER_LIST else os.environ.get("OWNER_NOTIFY_CHAT_ID", "")
)

TIMEOUT_SEC: float = float(os.environ.get("SENTRY_POLL_TIMEOUT_SEC", "30"))
MAX_RETRIES: int = int(os.environ.get("SENTRY_POLL_MAX_RETRIES", "3"))

STATE_DIR: Path = Path(os.environ.get("KRAB_SENTRY_POLL_STATE_DIR", "/tmp/krab_sentry_poll"))
STATE_FILE: Path = STATE_DIR / "seen_ids"
CURSOR_FILE: Path = STATE_DIR / "last_seen_cursor.json"
LOG_FILE: Path = STATE_DIR / "poll.log"
ERR_LOG: Path = STATE_DIR / "poll.err.log"

USER_AGENT = "krab-sentry-poll/wave-65-h (+https://github.com/po-zm/krab)"


# ─── Логирование ─────────────────────────────────────────────────────────────


def _log(file: Path, msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with file.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def log(msg: str) -> None:
    _log(LOG_FILE, msg)


def log_err(msg: str) -> None:
    _log(ERR_LOG, f"ERR {msg}")
    print(f"ERR {msg}", file=sys.stderr, flush=True)


# ─── Cursor / state ──────────────────────────────────────────────────────────


def _load_seen_ids() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    return {line.strip() for line in STATE_FILE.read_text().splitlines() if line.strip()}


def _append_seen_id(issue_id: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("a") as fh:
        fh.write(f"{issue_id}\n")


def _trim_seen_ids(max_entries: int = 1000) -> None:
    """Trim FIFO к last max_entries записям."""
    if not STATE_FILE.exists():
        return
    lines = STATE_FILE.read_text().splitlines()
    if len(lines) > max_entries:
        STATE_FILE.write_text("\n".join(lines[-max_entries:]) + "\n")
        log(f"state_trimmed to {max_entries}")


def _load_cursor() -> dict[str, str]:
    """Returns {project: last_seen_iso} mapping для инкрементального polling."""
    if not CURSOR_FILE.exists():
        return {}
    try:
        return json.loads(CURSOR_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cursor(cursor: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))


# ─── Sentry API ──────────────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SENTRY_TOKEN}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def _build_query(levels: list[str]) -> str:
    """Build Sentry query string из levels list."""
    if not levels:
        return "is:unresolved"
    level_query = "level:[" + ",".join(levels) + "]"
    return f"is:unresolved {level_query}"


def fetch_issues_with_retry(
    project: str,
    *,
    client: httpx.Client | None = None,
    max_retries: int = MAX_RETRIES,
    timeout: float = TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Получает unresolved issues с retry + exponential backoff.

    Возвращает [] при exhausted retries или non-list response.
    Учитывает Retry-After header при 429.
    """
    url = f"https://sentry.io/api/0/projects/{SENTRY_ORG}/{project}/issues/"
    params = {
        "statsPeriod": WINDOW,
        "query": _build_query(LEVELS),
        "limit": "20",
    }
    headers = _auth_headers()

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)

    try:
        for attempt in range(max_retries):
            try:
                resp = client.get(url, params=params, headers=headers)
            except httpx.RequestError as exc:
                # ConnectTimeout, ReadTimeout, NetworkError — retry
                if attempt + 1 < max_retries:
                    sleep_sec = 2.0 * (2**attempt)  # 2, 4, 8s
                    log_err(
                        f"fetch_issues attempt={attempt + 1}/{max_retries} "
                        f"project={project} err={type(exc).__name__}: {exc} "
                        f"retry_in={sleep_sec}s"
                    )
                    time.sleep(sleep_sec)
                    continue
                log_err(
                    f"fetch_issues exhausted retries project={project} "
                    f"err={type(exc).__name__}: {exc}"
                )
                return []

            # Rate-limit (429) — respect Retry-After
            if resp.status_code == 429:
                retry_after_str = resp.headers.get("Retry-After", "5")
                try:
                    retry_after = float(retry_after_str)
                except ValueError:
                    retry_after = 5.0
                if attempt + 1 < max_retries:
                    log_err(
                        f"fetch_issues 429 project={project} "
                        f"retry_after={retry_after}s attempt={attempt + 1}"
                    )
                    time.sleep(retry_after)
                    continue
                log_err(f"fetch_issues 429 exhausted project={project}")
                return []

            # 5xx — retry с exp backoff
            if 500 <= resp.status_code < 600:
                if attempt + 1 < max_retries:
                    sleep_sec = 2.0 * (2**attempt)
                    log_err(
                        f"fetch_issues 5xx={resp.status_code} project={project} "
                        f"attempt={attempt + 1} retry_in={sleep_sec}s"
                    )
                    time.sleep(sleep_sec)
                    continue
                log_err(f"fetch_issues 5xx={resp.status_code} exhausted project={project}")
                return []

            # 4xx (кроме 429) — fail fast
            if resp.status_code >= 400:
                log_err(
                    f"fetch_issues {resp.status_code} project={project} "
                    f"body_first120={resp.text[:120]}"
                )
                return []

            # 200 — parse and validate
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                log_err(
                    f"fetch_issues invalid_json project={project} "
                    f"err={exc} body_first120={resp.text[:120]}"
                )
                return []

            if not isinstance(data, list):
                log_err(
                    f"fetch_issues non_list_response project={project} "
                    f"body_first120={resp.text[:120]}"
                )
                return []

            return data

        return []
    finally:
        if owns_client and client is not None:
            client.close()


# ─── Форматирование и отправка ───────────────────────────────────────────────


def format_alert_text(issue: dict[str, Any]) -> str:
    emoji_map = {"fatal": "🔥", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
    level = (issue.get("level") or "error").lower()
    emoji = emoji_map.get(level, "⚡")
    title = html.escape((issue.get("title") or "Unknown")[:200])
    culprit = html.escape((issue.get("culprit") or "")[:120])
    proj = html.escape((((issue.get("project") or {}).get("slug")) or "")[:50])
    short_id = html.escape(issue.get("shortId") or "")
    count = issue.get("count") or 0
    users = issue.get("userCount") or 0
    last_seen = (issue.get("lastSeen") or "")[:19].replace("T", " ")
    lines = [
        f"{emoji} <b>Sentry alert</b> — {level.upper()}",
        f"[{proj}] {title}",
    ]
    if culprit:
        lines.append(f"<i>culprit:</i> {culprit}")
    lines.append(f"<i>events:</i> {count} • <i>users:</i> {users} • <i>last:</i> {last_seen}")
    if short_id:
        lines.append(f"<i>id:</i> <code>{short_id}</code>")
    return "\n".join(lines)


def send_telegram(
    text: str,
    issue_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> bool:
    """Отправляет alert в Telegram. Возвращает True при успехе."""
    if not TG_TOKEN or not TG_OWNER:
        log_err("send_telegram missing creds")
        return False

    payload = {
        "chat_id": TG_OWNER,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🔗 Open in Sentry", "url": issue_url}],
            ],
        },
    }

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        resp = client.post(url, json=payload)
        if resp.status_code != 200:
            log_err(f"telegram_send_failed status={resp.status_code}")
            return False
        return True
    except httpx.RequestError as exc:
        log_err(f"telegram_send_error: {type(exc).__name__}: {exc}")
        return False
    finally:
        if owns_client and client is not None:
            client.close()


# ─── Точка входа ─────────────────────────────────────────────────────────────


def _parse_last_seen(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def main() -> int:
    if not SENTRY_TOKEN:
        log_err("SENTRY_AUTH_TOKEN not set — exit")
        return 2
    if not TG_TOKEN or not TG_OWNER:
        log_err(
            f"Telegram creds missing (token={'SET' if TG_TOKEN else 'EMPTY'} "
            f"owner={'SET' if TG_OWNER else 'EMPTY'}) — exit"
        )
        return 3

    seen_ids = _load_seen_ids()
    cursor = _load_cursor()

    total_new = 0
    total_sent = 0

    # Shared client для всех вызовов (connection pool reuse)
    with httpx.Client(timeout=TIMEOUT_SEC) as client:
        for project in SENTRY_PROJECTS:
            log(f"polling project={project}")
            issues = fetch_issues_with_retry(project, client=client)

            # cursor для инкрементального polling — last_seen самого свежего issue
            project_cursor_iso = cursor.get(project, "")
            project_cursor_dt = _parse_last_seen(project_cursor_iso)
            newest_last_seen = project_cursor_dt

            for issue in issues:
                issue_id = issue.get("id")
                if not issue_id:
                    continue

                # Track newest lastSeen для cursor update
                last_seen_dt = _parse_last_seen(issue.get("lastSeen") or "")
                if last_seen_dt and (newest_last_seen is None or last_seen_dt > newest_last_seen):
                    newest_last_seen = last_seen_dt

                if issue_id in seen_ids:
                    continue  # already alerted

                # Cursor-based skip: если issue lastSeen <= project_cursor, оно
                # уже было видно в прошлом poll. Защищает от повторных alerts
                # при reset seen_ids state.
                if (
                    project_cursor_dt is not None
                    and last_seen_dt is not None
                    and last_seen_dt <= project_cursor_dt
                ):
                    continue

                total_new += 1
                text = format_alert_text(issue)
                permalink = issue.get("permalink") or ""

                if send_telegram(text, permalink, client=client):
                    _append_seen_id(issue_id)
                    seen_ids.add(issue_id)
                    total_sent += 1
                    log(f"alert_sent project={project} issue_id={issue_id}")

            # Update cursor для project
            if newest_last_seen is not None:
                cursor[project] = newest_last_seen.astimezone(timezone.utc).isoformat()

    _save_cursor(cursor)
    _trim_seen_ids()

    log(f"poll_done new={total_new} sent={total_sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

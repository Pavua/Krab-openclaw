#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 42-A: автоматический resolver устаревших Sentry issues.

Логика:
1. GET /api/0/projects/{org}/{proj}/issues/?query=is:unresolved&statsPeriod=14d
2. Для каждого issue:
   - title соответствует known_fixed_pattern → "known_fixed:wave-X" → resolve
   - last_seen > 7 дней назад → "stale_no_recurrence_Nd" → resolve
   - count <= 2 AND last_seen > 3d → "one_off_Nd" → resolve
3. PUT /api/0/issues/{id}/ {"status": "resolved"}
4. Запись результата в ~/.openclaw/krab_runtime_state/sentry_resolver.log

По умолчанию работает в dry-run режиме — безопасно.
Для реального resolve: передать --no-dry-run явно.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _load_dotenv() -> None:
    """Читает .env из ENV_FILE (или дефолтного пути) в os.environ.

    Поддерживает LaunchAgent-сценарий, где переменные не наследуются от shell.
    Простой парсер: KEY=VALUE, # комментарии, кавычки игнорируются.
    """
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
        # Не перезаписываем уже выставленные переменные окружения
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

# Порог «устаревший без рецидива»: last_seen > N дней
STALE_DAYS: int = int(os.environ.get("SENTRY_STALE_DAYS", "7"))
# Порог «одиночный»: count <= 2 AND last_seen > N дней
ONE_OFF_DAYS: int = int(os.environ.get("SENTRY_ONE_OFF_DAYS", "3"))

LOG_FILE: Path = Path.home() / ".openclaw" / "krab_runtime_state" / "sentry_resolver.log"

# Известные исправленные паттерны: (строка для поиска в title/metadata, wave-ссылка)
KNOWN_FIXED_PATTERNS: list[tuple[str, str]] = [
    ('Invalid parse mode "markdown"', "wave-25-d-fix"),
    ("disk image is malformed", "wave-24-d"),
    ("storage marked corrupt", "wave-16-f-handled"),
    ("network_silence_reconnect_failed", "wave-33-a"),
    ("db_corruption_detected_runtime", "wave-24-d"),
    ("cannot_send_request_client_closed", "wave-33-noise"),
]


# ─── Логирование ─────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    """Дописывает строку в лог-файл и выводит в stdout."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"[{ts}] {msg}"
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


# ─── Sentry API ──────────────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SENTRY_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_issues(project: str) -> list[dict]:
    """Получает unresolved issues проекта за последние 14 дней."""
    url = (
        f"https://sentry.io/api/0/projects/{SENTRY_ORG}/{project}/issues/"
        f"?query=is%3Aunresolved&statsPeriod=14d&limit=100"
    )
    req = urllib.request.Request(url, headers=_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        log(f"fetch_issues HTTP {exc.code} project={project}: {exc.reason}")
        return []
    except Exception as exc:  # noqa: BLE001
        log(f"fetch_issues error project={project}: {exc}")
        return []


def resolve_issue(issue_id: str, reason: str) -> bool:
    """Отправляет PUT для перевода issue в status=resolved."""
    # Wave 42-A-fix: Sentry требует org-prefixed URL для PUT (без org → 404)
    url = f"https://sentry.io/api/0/organizations/{SENTRY_ORG}/issues/{issue_id}/"
    body = json.dumps({"status": "resolved", "statusDetails": {}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers=_auth_headers(),
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            # Верифицируем что статус действительно сменился
            if data.get("status") == "resolved":
                log(f"resolved issue_id={issue_id} reason={reason}")
                return True
            log(f"resolve unexpected status issue_id={issue_id}: {data.get('status')}")
            return False
    except urllib.error.HTTPError as exc:
        log(f"resolve HTTP {exc.code} issue_id={issue_id}: {exc.reason}")
        return False
    except Exception as exc:  # noqa: BLE001
        log(f"resolve error issue_id={issue_id}: {exc}")
        return False


# ─── Категоризация ────────────────────────────────────────────────────────────


def categorize_issue(issue: dict) -> tuple[bool, str]:
    """Возвращает (should_resolve, reason).

    Приоритет:
    1. Известный исправленный паттерн → resolve немедленно.
    2. last_seen > STALE_DAYS → stale_no_recurrence.
    3. count <= 2 AND last_seen > ONE_OFF_DAYS → one_off.
    4. Иначе → оставить активным.
    """
    title: str = issue.get("title") or ""
    metadata: str = str(issue.get("metadata") or "")
    count: int = int(issue.get("count") or 0)
    last_seen_str: str = issue.get("lastSeen") or ""

    # 1. Известные исправленные паттерны
    for pattern, wave in KNOWN_FIXED_PATTERNS:
        if pattern in title or pattern in metadata:
            return True, f"known_fixed:{wave}"

    # 2. Парсим last_seen
    if last_seen_str:
        try:
            last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - last_seen).days

            if age_days > STALE_DAYS:
                return True, f"stale_no_recurrence_{age_days}d"

            if count <= 2 and age_days > ONE_OFF_DAYS:
                return True, f"one_off_{age_days}d"
        except (ValueError, TypeError):
            pass

    return False, "active"


# ─── Точка входа ─────────────────────────────────────────────────────────────


def main(dry_run: bool = True) -> int:
    """Основная логика резолвера.

    Args:
        dry_run: Если True — только логирует candidates, не вызывает API resolve.

    Returns:
        0 при успехе, 2 если SENTRY_AUTH_TOKEN не задан.
    """
    if not SENTRY_TOKEN:
        log("SENTRY_AUTH_TOKEN не задан — выход")
        return 2

    mode = "DRY-RUN" if dry_run else "LIVE"
    log(f"=== sentry_stale_resolver START mode={mode} projects={SENTRY_PROJECTS} ===")

    total_resolved = 0
    total_kept = 0
    total_errors = 0

    for project in SENTRY_PROJECTS:
        issues = fetch_issues(project)
        log(f"project={project} fetched={len(issues)} issues")

        for issue in issues:
            issue_id = issue.get("id", "?")
            title_short = (issue.get("title") or "")[:70]
            should_resolve, reason = categorize_issue(issue)

            if should_resolve:
                if dry_run:
                    log(f'[DRY] would resolve id={issue_id} title="{title_short}" reason={reason}')
                    total_resolved += 1
                else:
                    ok = resolve_issue(issue_id, reason)
                    if ok:
                        total_resolved += 1
                    else:
                        total_errors += 1
            else:
                total_kept += 1

    log(
        f"=== DONE mode={mode} resolved={total_resolved} "
        f"kept={total_kept} errors={total_errors} ==="
    )
    return 0


if __name__ == "__main__":
    # По умолчанию — dry-run (безопасно).
    # Для реального resolve передать --no-dry-run явно.
    dry = "--no-dry-run" not in sys.argv
    if dry and "--no-dry-run" not in sys.argv:
        print("INFO: режим dry-run (без изменений). Для resolve: --no-dry-run", flush=True)
    sys.exit(main(dry_run=dry))

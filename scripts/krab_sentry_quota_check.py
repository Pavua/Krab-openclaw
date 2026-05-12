#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 71: еженедельная проверка Sentry quota usage с baseline comparison.

Цель: автоматизированная проверка эффективности Sentry hygiene-фиксов Session 45
(Wave 62-E/F/G, 65-D/E/H и др.) — мониторим events/week и алёртим при regression.

Логика:
1. GET https://sentry.io/api/0/organizations/{org}/stats_v2/?statsPeriod=7d&field=sum(quantity)&category=error
   → total events за последние 7 дней.
2. GET .../issues/?query=is:unresolved&statsPeriod=7d&sort=freq&limit=5
   → top-5 issues по частоте.
3. Baseline: ~/.openclaw/krab_runtime_state/sentry_quota_baseline.json.
   - Если файла нет → инициализируем текущим значением, regression=False.
   - Иначе сравниваем: regression если current > baseline * (1 + threshold).
4. Output: JSON snapshot в stdout + log.
5. При regression — write alert log + (опционально) Telegram DM owner.

Usage:
    # Ручной запуск (первый раз инициализирует baseline, далее compare):
    venv/bin/python scripts/krab_sentry_quota_check.py

    # Принудительный reset baseline (после крупного hygiene-фикса):
    rm ~/.openclaw/krab_runtime_state/sentry_quota_baseline.json
    venv/bin/python scripts/krab_sentry_quota_check.py

    # Через LaunchAgent (ai.krab.sentry-quota-check, weekly Mon 10:00):
    launchctl start ai.krab.sentry-quota-check

Baseline format (sentry_quota_baseline.json):
    {
      "initialized_at": "2026-05-12T03:26:57.064073+00:00",  # ISO-8601 UTC
      "total_events": 497                                     # int, sum(quantity) за 7d
    }

Env (из .env):
- SENTRY_AUTH_TOKEN, SENTRY_ORG_SLUG (default po-zm),
- KRAB_SENTRY_QUOTA_REGRESSION_THRESHOLD (default 0.20 = 20%),
- KRAB_SENTRY_QUOTA_ALERT_TELEGRAM (default 0 — отключено),
- OPENCLAW_TELEGRAM_BOT_TOKEN, OWNER_USER_IDS (для Telegram).
"""

from __future__ import annotations

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
REGRESSION_THRESHOLD: float = float(
    os.environ.get("KRAB_SENTRY_QUOTA_REGRESSION_THRESHOLD", "0.20")
)
ALERT_TELEGRAM: bool = os.environ.get("KRAB_SENTRY_QUOTA_ALERT_TELEGRAM", "0") == "1"

TG_TOKEN: str | None = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN")
_OWNER_LIST: str = os.environ.get("OWNER_USER_IDS", "")
TG_OWNER: str = (
    _OWNER_LIST.split(",")[0].strip()
    if _OWNER_LIST
    else os.environ.get("OWNER_NOTIFY_CHAT_ID", "")
)

TIMEOUT_SEC: float = float(os.environ.get("SENTRY_QUOTA_TIMEOUT_SEC", "30"))
MAX_RETRIES: int = int(os.environ.get("SENTRY_QUOTA_MAX_RETRIES", "3"))

# Runtime state — следуем project convention (~/.openclaw/krab_runtime_state/)
DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
STATE_DIR: Path = Path(
    os.environ.get("KRAB_SENTRY_QUOTA_STATE_DIR", str(DEFAULT_STATE_DIR))
)
BASELINE_FILE: Path = STATE_DIR / "sentry_quota_baseline.json"
LOG_FILE: Path = STATE_DIR / "sentry_quota_check.log"
ALERT_LOG: Path = STATE_DIR / "sentry_quota_check.alert.log"

USER_AGENT = "krab-sentry-quota/wave-71 (+https://github.com/po-zm/krab)"


# ─── Логирование ─────────────────────────────────────────────────────────────


def _log_to(file: Path, msg: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with file.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def log_info(msg: str) -> None:
    _log_to(LOG_FILE, msg)


def log_alert(msg: str) -> None:
    _log_to(ALERT_LOG, f"ALERT {msg}")
    print(f"ALERT {msg}", file=sys.stderr, flush=True)


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


def _get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response | None:
    """GET с retry + exponential backoff (как sentry_poll_direct.py)."""
    for attempt in range(max_retries):
        try:
            resp = client.get(url, params=params, headers=_auth_headers())
        except httpx.RequestError as exc:
            if attempt + 1 < max_retries:
                sleep_sec = 2.0 * (2**attempt)
                log_err(
                    f"get attempt={attempt + 1}/{max_retries} url={url} "
                    f"error_type={type(exc).__name__} error={exc} "
                    f"retry_in={sleep_sec}s"
                )
                time.sleep(sleep_sec)
                continue
            log_err(
                f"get exhausted url={url} error_type={type(exc).__name__} error={exc}"
            )
            return None

        if resp.status_code == 429:
            retry_after_str = resp.headers.get("Retry-After", "5")
            try:
                retry_after = float(retry_after_str)
            except ValueError:
                retry_after = 5.0
            if attempt + 1 < max_retries:
                log_err(f"get 429 retry_after={retry_after}s attempt={attempt + 1}")
                time.sleep(retry_after)
                continue
            log_err("get 429 exhausted")
            return None

        if 500 <= resp.status_code < 600:
            if attempt + 1 < max_retries:
                sleep_sec = 2.0 * (2**attempt)
                log_err(
                    f"get 5xx={resp.status_code} attempt={attempt + 1} "
                    f"retry_in={sleep_sec}s"
                )
                time.sleep(sleep_sec)
                continue
            log_err(f"get 5xx={resp.status_code} exhausted")
            return None

        return resp
    return None


def fetch_weekly_event_count(client: httpx.Client) -> int | None:
    """Получает суммарный count events за 7d через stats_v2 endpoint.

    Возвращает int total или None при ошибке/невалидном response.
    """
    url = f"https://sentry.io/api/0/organizations/{SENTRY_ORG}/stats_v2/"
    params = {
        "statsPeriod": "7d",
        "interval": "1d",
        "field": "sum(quantity)",
        "category": "error",
    }
    resp = _get_with_retry(client, url, params)
    if resp is None:
        return None
    if resp.status_code != 200:
        log_err(
            f"stats_v2 status={resp.status_code} body_first120={resp.text[:120]}"
        )
        return None
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log_err(f"stats_v2 invalid_json error_type={type(exc).__name__} error={exc}")
        return None

    # stats_v2 формат: {"groups": [{"totals": {"sum(quantity)": N}, ...}], ...}
    try:
        groups = data.get("groups", [])
        if not groups:
            return 0
        total = 0
        for grp in groups:
            totals = grp.get("totals", {}) or {}
            val = totals.get("sum(quantity)", 0)
            if isinstance(val, (int, float)):
                total += int(val)
        return total
    except (AttributeError, TypeError) as exc:
        log_err(f"stats_v2 parse error_type={type(exc).__name__} error={exc}")
        return None


def fetch_top_issues(client: httpx.Client, limit: int = 5) -> list[dict[str, Any]]:
    """Top-N unresolved issues по частоте за 7d (org-scope)."""
    url = f"https://sentry.io/api/0/organizations/{SENTRY_ORG}/issues/"
    params = {
        "statsPeriod": "7d",
        "query": "is:unresolved",
        "sort": "freq",
        "limit": str(limit),
    }
    resp = _get_with_retry(client, url, params)
    if resp is None or resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for issue in data[:limit]:
        out.append(
            {
                "shortId": issue.get("shortId") or "",
                "title": (issue.get("title") or "")[:200],
                "count": int(issue.get("count") or 0),
                "level": issue.get("level") or "",
                "project": ((issue.get("project") or {}).get("slug")) or "",
            }
        )
    return out


# ─── Baseline ────────────────────────────────────────────────────────────────


def load_baseline(path: Path = BASELINE_FILE) -> dict[str, Any] | None:
    """Читает baseline или None если файл отсутствует/повреждён."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log_err(f"baseline_load error_type={type(exc).__name__} error={exc}")
        return None


def save_baseline(payload: dict[str, Any], path: Path = BASELINE_FILE) -> None:
    """Записывает baseline атомарно."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def detect_regression(
    current: int,
    baseline: int,
    *,
    threshold: float = REGRESSION_THRESHOLD,
) -> bool:
    """True если current > baseline * (1 + threshold).

    Защита от false-positive: baseline <= 0 → всегда False (нет точки сравнения).
    """
    if baseline <= 0:
        return False
    return current > baseline * (1.0 + threshold)


# ─── Telegram alert (опциональный) ───────────────────────────────────────────


def send_telegram_alert(
    text: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> bool:
    if not TG_TOKEN or not TG_OWNER:
        log_err("telegram_alert missing_creds")
        return False
    payload = {
        "chat_id": TG_OWNER,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=payload
        )
        if resp.status_code != 200:
            log_err(f"telegram_alert status={resp.status_code}")
            return False
        return True
    except httpx.RequestError as exc:
        log_err(f"telegram_alert error_type={type(exc).__name__} error={exc}")
        return False
    finally:
        if owns_client and client is not None:
            client.close()


# ─── Main flow ───────────────────────────────────────────────────────────────


def build_snapshot(
    current_total: int,
    top_issues: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
    *,
    threshold: float = REGRESSION_THRESHOLD,
) -> dict[str, Any]:
    """Формирует JSON snapshot для stdout + alert decision."""
    baseline_total = int(baseline.get("total_events", 0)) if baseline else 0
    regression = detect_regression(current_total, baseline_total, threshold=threshold)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_events": current_total,
        "top_5_issues": top_issues,
        "regression": regression,
        "baseline_total": baseline_total,
        "threshold": threshold,
        "baseline_initialized": baseline is not None,
    }


def run_check(
    *,
    client: httpx.Client | None = None,
    baseline_path: Path = BASELINE_FILE,
    threshold: float = REGRESSION_THRESHOLD,
    alert_telegram: bool = ALERT_TELEGRAM,
) -> dict[str, Any]:
    """Полный цикл: fetch → compare → snapshot → persist → alert."""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=TIMEOUT_SEC)
    try:
        current_total = fetch_weekly_event_count(client)
        if current_total is None:
            log_err("run_check fetch_failed")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_events": None,
                "top_5_issues": [],
                "regression": False,
                "baseline_total": 0,
                "error": "fetch_failed",
            }

        top_issues = fetch_top_issues(client)
        baseline = load_baseline(baseline_path)
        snapshot = build_snapshot(
            current_total, top_issues, baseline, threshold=threshold
        )

        # Первый запуск — инициализируем baseline
        if baseline is None:
            save_baseline(
                {
                    "initialized_at": snapshot["timestamp"],
                    "total_events": current_total,
                },
                path=baseline_path,
            )
            log_info(f"baseline_initialized total_events={current_total}")
        else:
            log_info(
                f"check_done current={current_total} "
                f"baseline={snapshot['baseline_total']} "
                f"regression={snapshot['regression']}"
            )

        if snapshot["regression"]:
            log_alert(
                f"regression detected current={current_total} "
                f"baseline={snapshot['baseline_total']} threshold={threshold}"
            )
            if alert_telegram:
                txt = (
                    f"⚠️ <b>Sentry quota regression</b>\n"
                    f"current: {current_total} events/week\n"
                    f"baseline: {snapshot['baseline_total']}\n"
                    f"threshold: +{int(threshold * 100)}%"
                )
                send_telegram_alert(txt, client=client)

        return snapshot
    finally:
        if owns_client and client is not None:
            client.close()


def main() -> int:
    if not SENTRY_TOKEN:
        log_err("SENTRY_AUTH_TOKEN not set — exit")
        return 2

    snapshot = run_check()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

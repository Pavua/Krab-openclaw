"""Wave 24-A: Multi-account codex CLI rotation для умножения квоты.

Поддерживает 2-3+ ChatGPT Plus аккаунтов через изоляцию CODEX_HOME.
Каждый аккаунт хранит auth в отдельном каталоге ~/.codex_accounts/<name>/.

Selection logic:
- Round-robin: выбирается аккаунт с наиболее ранним last_used
- При получении quota_exceeded от account N → mark unavailable на QUOTA_RESET_HOURS
- Tracking в ~/.openclaw/krab_runtime_state/codex_accounts.json:
  {
    "primary": {"calls_today": 89, "last_used": "...", "quota_exhausted_until": null},
    "account2": {"calls_today": 12, "quota_exhausted_until": "2026-05-12T07:00:00Z"},
  }

API:
- get_next_codex_home() → str|None — возвращает path для следующего CODEX_HOME
- record_call(account_name, success, error) — обновляет state
- record_quota_exhaustion(account_name, reset_at) — помечает unavailable
- list_accounts() → list[dict] — для panel/diagnostic
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)

ACCOUNTS_DIR = Path.home() / ".codex_accounts"
STATE_FILE = Path.home() / ".openclaw/krab_runtime_state/codex_accounts.json"
# ChatGPT Plus quota — сбрасывается weekly, используем 24h backoff по умолчанию
QUOTA_RESET_HOURS = 24


def _load_state() -> dict[str, Any]:
    """Загружает state из JSON файла."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("codex_rotator_state_load_error", error=str(exc))
        return {}


def _save_state(state: dict[str, Any]) -> None:
    """Сохраняет state в JSON файл (атомарная запись через temp)."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("codex_rotator_state_save_error", error=str(exc))
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _is_available(account_state: dict[str, Any]) -> bool:
    """Проверяет, доступен ли аккаунт (quota не исчерпана)."""
    until = account_state.get("quota_exhausted_until")
    if not until:
        return True
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= until_dt
    except Exception:  # noqa: BLE001
        return True


def list_accounts() -> list[dict[str, Any]]:
    """Возвращает список аккаунтов с состоянием.

    Включает только аккаунты с auth.json (залогиненные).
    """
    if not ACCOUNTS_DIR.exists():
        return []
    state = _load_state()
    accounts: list[dict[str, Any]] = []
    for d in sorted(ACCOUNTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        auth_file = d / "auth.json"
        if not auth_file.exists():
            # Каталог существует, но login ещё не выполнен
            accounts.append(
                {
                    "name": d.name,
                    "path": str(d),
                    "calls_today": 0,
                    "last_used": None,
                    "quota_exhausted_until": None,
                    "available": False,
                    "logged_in": False,
                }
            )
            continue
        s = state.get(d.name, {})
        accounts.append(
            {
                "name": d.name,
                "path": str(d),
                "calls_today": s.get("calls_today", 0),
                "last_used": s.get("last_used"),
                "quota_exhausted_until": s.get("quota_exhausted_until"),
                "available": _is_available(s),
                "logged_in": True,
            }
        )
    return accounts


def get_next_codex_home() -> str | None:
    """Round-robin selection среди available залогиненных аккаунтов.

    Выбирает аккаунт с наиболее ранним last_used (LRU).

    Returns:
        Path строка CODEX_HOME или None если все аккаунты exhausted / не залогинены.
    """
    accounts = list_accounts()
    # Только залогиненные и доступные
    available = [a for a in accounts if a["available"] and a["logged_in"]]
    if not available:
        logger.warning("codex_rotator_no_available_accounts", total=len(accounts))
        return None
    # LRU: least recently used первым (None last_used → всегда первый)
    available.sort(key=lambda a: a.get("last_used") or "")
    chosen = available[0]
    logger.debug(
        "codex_rotator_selected",
        account=chosen["name"],
        calls_today=chosen["calls_today"],
        last_used=chosen["last_used"],
    )
    return chosen["path"]


def record_call(
    account_name: str,
    *,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Обновляет state после вызова codex CLI.

    При quota/rate-limit ошибке автоматически вызывает record_quota_exhaustion.
    """
    state = _load_state()
    s = state.setdefault(account_name, {})

    # Daily counter с reset на полночь UTC
    today = datetime.now(timezone.utc).date().isoformat()
    if s.get("counter_date") != today:
        s["calls_today"] = 0
        s["counter_date"] = today
    s["calls_today"] = s.get("calls_today", 0) + 1
    s["last_used"] = datetime.now(timezone.utc).isoformat()

    if (
        not success
        and error
        and any(k in error.lower() for k in ("quota", "rate limit", "429", "exceeded"))
    ):
        # Не сохраняем основной state — запись будет в record_quota_exhaustion
        record_quota_exhaustion(
            account_name,
            datetime.now(timezone.utc) + timedelta(hours=QUOTA_RESET_HOURS),
        )
        return

    _save_state(state)
    logger.debug(
        "codex_rotator_call_recorded",
        account=account_name,
        success=success,
        calls_today=s["calls_today"],
    )


def record_quota_exhaustion(account_name: str, reset_at: datetime) -> None:
    """Помечает аккаунт как недоступный до reset_at.

    Args:
        account_name: имя каталога в ~/.codex_accounts/
        reset_at: datetime (timezone-aware), когда аккаунт снова доступен
    """
    state = _load_state()
    s = state.setdefault(account_name, {})
    s["quota_exhausted_until"] = reset_at.isoformat()
    _save_state(state)
    logger.warning(
        "codex_account_quota_exhausted",
        account=account_name,
        reset_at=reset_at.isoformat(),
    )


def get_account_name_from_home(codex_home: str) -> str:
    """Извлекает имя аккаунта из пути CODEX_HOME.

    Например: '/home/user/.codex_accounts/account2' → 'account2'
    """
    return Path(codex_home).name

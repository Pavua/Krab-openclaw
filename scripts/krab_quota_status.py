"""Wave 53-G: krab_quota_status — CLI для просмотра codex quota state + probe stats.

Использование:
  python scripts/krab_quota_status.py             — стандартный вывод quota state
  python scripts/krab_quota_status.py --probe-state — добавляет per-account probe stats
  python scripts/krab_quota_status.py --json       — вывод в JSON

Читает:
- codex_quota_state.json    — disabled/recovered флаг (Wave 44-V)
- codex_accounts.json       — per-account rotator state (Wave 24-A)
- codex_quota_probe_state.json — per-account probe backoff stats (Wave 53-G)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_RUNTIME = Path.home() / ".openclaw/krab_runtime_state"
_QUOTA_STATE = _RUNTIME / "codex_quota_state.json"
_ACCOUNTS_STATE = _RUNTIME / "codex_accounts.json"
_PROBE_STATE = _RUNTIME / "codex_quota_probe_state.json"


def _read_json(path: Path) -> dict:
    """Читает JSON файл. Graceful при ошибке."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _fmt_ts(ts: str | None) -> str:
    """Форматирует ISO timestamp в читаемый вид."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            future = abs(secs)
            if future >= 3600:
                return f"через {future // 3600}h {(future % 3600) // 60}m"
            return f"через {future // 60}m {future % 60}s"
        if secs < 60:
            return f"{secs}s назад"
        if secs < 3600:
            return f"{secs // 60}m назад"
        return f"{secs // 3600}h {(secs % 3600) // 60}m назад"
    except Exception:  # noqa: BLE001
        return ts[:19] if ts else "—"


def print_quota_state() -> None:
    """Выводит основной quota state."""
    state = _read_json(_QUOTA_STATE)
    disabled = state.get("disabled", False)
    status = "ОТКЛЮЧЁН (quota exhausted)" if disabled else "АКТИВЕН"
    print(f"\n=== Codex Quota State ===")
    print(f"  Статус:           {status}")
    if disabled:
        print(f"  Отключён:         {_fmt_ts(state.get('disabled_at'))}")
        print(f"  Тип квоты:        {state.get('kind', '?')}")
        print(f"  Fallback модель:  {state.get('last_fallback_model', '?')}")
    if state.get("recovered_at"):
        print(f"  Восстановлен:     {_fmt_ts(state.get('recovered_at'))}")


def print_accounts_state() -> None:
    """Выводит per-account rotator state."""
    state = _read_json(_ACCOUNTS_STATE)
    if not state:
        print("\n=== Codex Accounts ===")
        print("  (нет данных)")
        return
    print(f"\n=== Codex Accounts ({len(state)} аккаунтов) ===")
    for name, acct in state.items():
        available = acct.get("available", True)
        exhausted_until = acct.get("quota_exhausted_until")
        status = "✅" if available or not exhausted_until else "❌"
        calls = acct.get("calls_today", 0)
        print(f"  {status} {name}: calls_today={calls}, last_used={_fmt_ts(acct.get('last_used'))}")
        if exhausted_until:
            print(f"       exhausted_until={_fmt_ts(exhausted_until)}")


def print_probe_state() -> None:
    """Wave 53-G: выводит per-account probe backoff stats."""
    state = _read_json(_PROBE_STATE)
    if not state:
        print("\n=== Probe State (Wave 53-G) ===")
        print("  (нет данных — probe ещё не запускался)")
        return

    stats = state.get("global_stats", {})
    total = stats.get("total_probes", 0)
    successes = stats.get("successes", 0)
    failures = stats.get("failures", 0)
    rate = f"{successes / total * 100:.1f}%" if total > 0 else "—"

    print(f"\n=== Probe State (Wave 53-G) ===")
    print(f"  Всего проб:       {total}")
    print(f"  Успешных:         {successes} ({rate})")
    print(f"  Неудачных:        {failures}")

    accounts = state.get("accounts", {})
    if accounts:
        print(f"\n  Per-account backoff ({len(accounts)} аккаунтов):")
        for acct_name, acct in accounts.items():
            f_count = acct.get("failures", 0)
            last = _fmt_ts(acct.get("last_probe_ts"))
            nxt = _fmt_ts(acct.get("next_probe_ts"))
            backoff_level = "нет" if f_count == 0 else f"×{2 ** f_count} (failures={f_count})"
            print(f"    [{acct_name}]")
            print(f"      failures: {f_count}  backoff: {backoff_level}")
            print(f"      last_probe: {last}")
            print(f"      next_probe: {nxt}")


def build_json_output(*, include_probe: bool) -> dict:
    """Собирает полный JSON вывод."""
    result = {
        "quota_state": _read_json(_QUOTA_STATE),
        "accounts": _read_json(_ACCOUNTS_STATE),
    }
    if include_probe:
        result["probe_state"] = _read_json(_PROBE_STATE)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Krab quota status CLI (Wave 53-G)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--probe-state",
        action="store_true",
        help="Показать per-account probe backoff stats (Wave 53-G)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Вывод в JSON формате",
    )
    args = parser.parse_args()

    if args.as_json:
        data = build_json_output(include_probe=args.probe_state)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    # Human-readable вывод
    print_quota_state()
    print_accounts_state()
    if args.probe_state:
        print_probe_state()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

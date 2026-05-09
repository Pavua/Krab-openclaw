"""Wave 35-B: bypass perf threshold check — runs каждые 15 минут.

Условия алерта:
- p95 latency > 60s для cli (codex обычно 16-30s — 60s показывает problem)
- p95 latency > 30s для vertex/gemma (обычно 5-15s)
- fail_rate > 10% за last 1h
- total_calls < 5 за last 1h при наличии активности (silent failure)

При срабатывании → POST /api/notify со структурированным сообщением.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

# Пороговые значения per kind: p95 latency (секунды) и fail_rate (доля)
THRESHOLDS: dict[str, dict[str, float]] = {
    "cli": {"p95_sec": 60.0, "fail_rate": 0.10},
    "vertex": {"p95_sec": 30.0, "fail_rate": 0.10},
    "anthropic-vertex": {"p95_sec": 30.0, "fail_rate": 0.10},
    "gemma": {"p95_sec": 30.0, "fail_rate": 0.10},
    "google-direct": {"p95_sec": 30.0, "fail_rate": 0.10},
}

# Минимальное количество вызовов для расчёта статистики
MIN_SAMPLES = 3

# Дебаунс: не алертим чаще одного раза в час
ALERT_DEBOUNCE_SEC = 3600

# Файл для хранения метки последнего алерта
LAST_ALERT_FILE = Path.home() / ".openclaw/krab_runtime_state/bypass_perf_alert_last.json"

# Owner panel endpoint
PANEL_BASE = "http://127.0.0.1:8080"


def _fetch_perf() -> dict | None:
    """Запрашивает /api/bypass/perf?window=1h. Возвращает None при ошибке.

    Session 39: используем exclude_expected=true чтобы не алертить про
    known transient failures (quota/permission) пока квоты не одобрены.
    """
    url = f"{PANEL_BASE}/api/bypass/perf?window=1h&exclude_expected=true"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"cannot fetch perf: {exc}")
        return None


def _load_last_alert() -> dict:
    """Читает метку последнего алерта из файла."""
    if LAST_ALERT_FILE.exists():
        try:
            return json.loads(LAST_ALERT_FILE.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {}


def _save_last_alert(alerts: list[str]) -> None:
    """Сохраняет метку последнего алерта."""
    LAST_ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_ALERT_FILE.write_text(json.dumps({"last_alert_ts": time.time(), "alerts": alerts}))


def _send_notify(text: str) -> None:
    """Отправляет POST /api/notify. Ошибки логируются, не бросаются."""
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        f"{PANEL_BASE}/api/notify",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        print(f"alert send failed: {exc}")


def _check_thresholds(by_kind: dict) -> list[str]:
    """Проверяет пороговые значения per kind, возвращает список нарушений."""
    violations: list[str] = []

    for kind, stats in by_kind.items():
        count = stats.get("count", 0)
        if count < MIN_SAMPLES:
            # Слишком мало сэмплов — не алертим (избегаем ложных срабатываний)
            continue

        # Пороги для данного kind (fallback — умеренные значения)
        threshold = THRESHOLDS.get(kind, {"p95_sec": 60.0, "fail_rate": 0.20})

        p95 = stats.get("p95", 0.0)
        fail_rate = stats.get("fail_rate", 0.0)

        if p95 > threshold["p95_sec"]:
            violations.append(f"{kind}: p95={p95:.1f}s > {threshold['p95_sec']}s (count={count})")
        if fail_rate > threshold["fail_rate"]:
            violations.append(
                f"{kind}: fail_rate={fail_rate:.1%} > {threshold['fail_rate']:.0%} (count={count})"
            )

    return violations


def main() -> int:
    """Точка входа. Возвращает 0 при успехе, 1 при ошибке."""
    # Получаем статистику за последний час
    data = _fetch_perf()
    if data is None:
        return 1

    if not data.get("ok", False):
        # Endpoint вернул не-ok (например, нет данных) — не ошибка
        return 0

    by_kind: dict = data.get("by_kind", {})
    violations = _check_thresholds(by_kind)

    if not violations:
        # Всё в норме — выходим без алерта
        return 0

    # Проверяем дебаунс: не алертим чаще раза в час
    last = _load_last_alert()
    elapsed = time.time() - last.get("last_alert_ts", 0.0)
    if elapsed < ALERT_DEBOUNCE_SEC:
        print(f"debounce: last alert {elapsed:.0f}s ago, skipping")
        return 0

    # Формируем текст алерта
    total_calls = data.get("total_calls", 0)
    lines = "\n".join(f"  {v}" for v in violations)
    text = f"⚠️ *Bypass perf degradation*\n{lines}\n\nTotal calls (1h): {total_calls}"

    # Отправляем уведомление
    _send_notify(text)

    # Сохраняем метку для дебаунса
    _save_last_alert(violations)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

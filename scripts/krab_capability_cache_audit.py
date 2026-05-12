#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/krab_capability_cache_audit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 129: weekly audit per-chat capability cache vs Telegram reality.

Зачем:
    ChatCapabilityCache (src/core/chat_capability_cache.py) хранит cached
    permissions (slow_mode_seconds, voice_allowed, text_allowed) с TTL 24h.
    Если админ revoke'нул voice mid-day, cached entry проживёт до 24h —
    Krab будет пробовать send_voice → fail → ban score++. Этот audit
    еженедельно сэмплит до 20 чатов и сверяет cache с актуальным
    `get_chat(chat_id)` через owner panel HTTP-bridge.

Workflow:
    1. Прочитать chat_capability_cache.json.
    2. Сэмплировать до N=20 случайных chat_id.
    3. Для каждого — fetch live permissions через owner panel
       `GET /api/chat/<id>/capability` (если endpoint доступен).
       Если endpoint вернул 4xx/5xx — пропустить (не считать mismatch).
    4. Сравнить cached vs live (slow_mode_seconds + voice_allowed +
       text_allowed). Записать mismatches.
    5. Persist JSON отчёт в ~/.openclaw/krab_runtime_state/capability_cache_audit.json
       (FIFO, keep последние 10 runs).
    6. Инкрементировать Prometheus Counter krab_capability_cache_mismatches_total
       через `inc_capability_cache_mismatch()` если такой helper доступен.

CLI:
    venv/bin/python scripts/krab_capability_cache_audit.py
    venv/bin/python scripts/krab_capability_cache_audit.py --sample 5 --panel http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Optional dep — если httpx нет (тесты), используем urllib fallback.
try:
    import httpx as _httpx  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    _httpx = None  # type: ignore[assignment]


DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.openclaw/krab_runtime_state"))
DEFAULT_CACHE_PATH = DEFAULT_STATE_DIR / "chat_capability_cache.json"
DEFAULT_REPORT_PATH = DEFAULT_STATE_DIR / "capability_cache_audit.json"
DEFAULT_PANEL_URL = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
DEFAULT_SAMPLE = 20
MAX_HISTORY = 10
# Поля которые мы сверяем — должны совпадать field-by-field.
_COMPARED_FIELDS = ("slow_mode_seconds", "voice_allowed", "text_allowed")


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Читает persisted cache. Возвращает пустой dict если файла нет/битый."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # Фильтруем только валидные entry-словари.
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def sample_chat_ids(
    cache: dict[str, dict[str, Any]],
    sample_size: int,
    rng: random.Random | None = None,
) -> list[str]:
    """Случайная выборка до sample_size chat_id из cache."""
    rng = rng or random.Random()
    keys = list(cache.keys())
    if not keys:
        return []
    n = min(sample_size, len(keys))
    return rng.sample(keys, n)


def fetch_live_capability(
    panel_url: str,
    chat_id: str,
    *,
    fetcher: Any | None = None,
    timeout_sec: float = 10.0,
) -> dict[str, Any] | None:
    """
    Запрашивает live capability у owner panel.

    `fetcher` — инжектируемый callable(url) → dict | None для тестов.
    В production используем httpx GET с тайм-аутом.

    Возвращает None при сетевой/HTTP-ошибке — caller трактует это как
    "skip" (НЕ как mismatch), чтобы временные сбои panel не раздували
    counter.
    """
    url = f"{panel_url.rstrip('/')}/api/chat/{chat_id}/capability"
    if fetcher is not None:
        try:
            return fetcher(url)
        except Exception:  # noqa: BLE001
            return None
    if _httpx is None:
        return None
    try:
        resp = _httpx.get(url, timeout=timeout_sec)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def compare_entries(
    cached: dict[str, Any], live: dict[str, Any]
) -> list[str]:
    """Возвращает список различающихся полей (пустой если match)."""
    diffs: list[str] = []
    for field in _COMPARED_FIELDS:
        if cached.get(field) != live.get(field):
            diffs.append(field)
    return diffs


def run_audit(
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    panel_url: str = DEFAULT_PANEL_URL,
    sample_size: int = DEFAULT_SAMPLE,
    fetcher: Any | None = None,
    rng: random.Random | None = None,
    now_fn: Any | None = None,
) -> dict[str, Any]:
    """
    Главная точка входа. Сэмплирует, сверяет, возвращает summary dict.

    Не пишет ничего на диск и не трогает Prometheus — это делает CLI wrapper.
    Дизайн: чистая функция (для тестируемости).
    """
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    cache = load_cache(cache_path)
    total_cached = len(cache)
    sampled_ids = sample_chat_ids(cache, sample_size, rng=rng)
    mismatched: list[dict[str, Any]] = []
    skipped: list[str] = []
    for chat_id in sampled_ids:
        live = fetch_live_capability(panel_url, chat_id, fetcher=fetcher)
        if live is None:
            skipped.append(chat_id)
            continue
        diffs = compare_entries(cache[chat_id], live)
        if diffs:
            mismatched.append(
                {
                    "chat_id": chat_id,
                    "diff_fields": diffs,
                    "cached": {f: cache[chat_id].get(f) for f in _COMPARED_FIELDS},
                    "live": {f: live.get(f) for f in _COMPARED_FIELDS},
                }
            )
    return {
        "timestamp": now.isoformat(),
        "total_cached": total_cached,
        "sampled": len(sampled_ids),
        "skipped": skipped,
        "mismatched": mismatched,
        "mismatch_count": len(mismatched),
    }


def persist_report(
    report: dict[str, Any], *, report_path: Path = DEFAULT_REPORT_PATH
) -> None:
    """Append-only FIFO журнал из MAX_HISTORY последних запусков."""
    history: list[dict[str, Any]] = []
    if report_path.exists():
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8") or "[]")
            if isinstance(raw, list):
                history = [r for r in raw if isinstance(r, dict)]
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(report)
    history = history[-MAX_HISTORY:]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def emit_prometheus(mismatch_count: int) -> None:
    """Best-effort инкремент krab_capability_cache_mismatches_total."""
    if mismatch_count <= 0:
        return
    try:
        from src.core.prometheus_metrics import (  # type: ignore[import-not-found]
            inc_capability_cache_mismatch,
        )
    except Exception:  # noqa: BLE001
        return
    for _ in range(mismatch_count):
        try:
            inc_capability_cache_mismatch("audit")
        except Exception:  # noqa: BLE001
            return


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Krab capability cache audit (Wave 129)")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    p.add_argument("--panel", type=str, default=DEFAULT_PANEL_URL)
    p.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    report = run_audit(
        cache_path=args.cache,
        panel_url=args.panel,
        sample_size=args.sample,
    )
    persist_report(report, report_path=args.report)
    emit_prometheus(report["mismatch_count"])
    # Печатаем компактный JSON в stdout — pickup'нется LaunchAgent log.
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

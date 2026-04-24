#!/usr/bin/env python3
"""
Анализ `memory_phase2_shadow_compare` событий из лога launchd.

Собирает за последние N часов:
  * сколько запросов всего,
  * сколько из них поменяли бы top-5 при включении Hybrid режима,
  * сколько раз vector search нашёл дополнительные hits (recall events),
  * P50/P99 latency cost (hybrid vs fts-only),
  * средний recall delta.

Использование:
    python scripts/analyze_shadow_logs.py --hours 24
    python scripts/analyze_shadow_logs.py --hours 48 --log /path/to/krab_launchd.out.log

Вывод — текстовая summary-таблица в stdout. Exit 0 даже если событий нет.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_LOG = Path(os.path.expanduser("~/Antigravity_AGENTS/Краб/logs/krab_launchd.out.log"))

# structlog обычно пишет `key=value` пары; здесь нам нужен просто факт наличия
# метки + захват числовых полей. Поддерживаем оба формата: JSON-строка (если
# настроен JSONRenderer) и key=value (KeyValueRenderer).
EVENT_MARK = "memory_phase2_shadow_compare"


@dataclass
class ShadowEvent:
    ts: datetime | None
    fts_hits: int = 0
    vec_hits: int = 0
    shadow_merged: int = 0
    would_change_top5: bool = False
    latency_fts_ms: float = 0.0
    latency_vec_ms: float = 0.0
    raw: str = ""


@dataclass
class Summary:
    total: int = 0
    changed_top5: int = 0
    recall_events: int = 0  # vec_hits > 0 И вектор добавил что-то новое
    fts_latencies: list[float] = field(default_factory=list)
    vec_latencies: list[float] = field(default_factory=list)
    hit_deltas: list[int] = field(default_factory=list)  # merged - fts


KV_PATTERN = re.compile(r"(\w+)=((?:\"[^\"]*\")|(?:'[^']*')|\S+)")
# Наивный timestamp-парсер: ловим ISO-8601 в начале строки (`2026-04-24T12:34:56`).
TS_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def _parse_value(v: str):
    v = v.strip()
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    if v in ("True", "true"):
        return True
    if v in ("False", "false"):
        return False
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def _parse_line(line: str) -> ShadowEvent | None:
    if EVENT_MARK not in line:
        return None

    # Попытка JSON-строкой.
    data: dict = {}
    try:
        # JSON-событие часто включает поле "event" = "memory_phase2_shadow_compare".
        # Ищем first { ... last }.
        start = line.find("{")
        end = line.rfind("}")
        if start != -1 and end > start:
            maybe_json = json.loads(line[start : end + 1])
            if isinstance(maybe_json, dict) and maybe_json.get("event") == EVENT_MARK:
                data = maybe_json
    except (json.JSONDecodeError, ValueError):
        data = {}

    if not data:
        # Парсим key=value pairs.
        for m in KV_PATTERN.finditer(line):
            data[m.group(1)] = _parse_value(m.group(2))

    ts: datetime | None = None
    ts_raw = data.get("timestamp") or data.get("ts")
    if isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    if ts is None:
        tsm = TS_PATTERN.search(line)
        if tsm:
            try:
                ts = datetime.fromisoformat(tsm.group(1).replace("Z", "+00:00"))
            except ValueError:
                ts = None
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return ShadowEvent(
        ts=ts,
        fts_hits=int(data.get("fts_hits", 0) or 0),
        vec_hits=int(data.get("vec_hits", 0) or 0),
        shadow_merged=int(data.get("shadow_merged", 0) or 0),
        would_change_top5=bool(data.get("would_change_top5", False)),
        latency_fts_ms=float(data.get("latency_fts_ms", 0.0) or 0.0),
        latency_vec_ms=float(data.get("latency_vec_ms", 0.0) or 0.0),
        raw=line.rstrip(),
    )


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return s[idx]


def analyze(events: Iterable[ShadowEvent]) -> Summary:
    s = Summary()
    for ev in events:
        s.total += 1
        if ev.would_change_top5:
            s.changed_top5 += 1
        if ev.vec_hits > 0 and ev.shadow_merged > ev.fts_hits:
            s.recall_events += 1
        s.fts_latencies.append(ev.latency_fts_ms)
        s.vec_latencies.append(ev.latency_vec_ms)
        s.hit_deltas.append(ev.shadow_merged - ev.fts_hits)
    return s


def format_summary(s: Summary, hours: int) -> str:
    if s.total == 0:
        return f"[shadow-analyze] За последние {hours}h не найдено событий '{EVENT_MARK}'.\n"

    pct_changed = 100.0 * s.changed_top5 / s.total if s.total else 0.0
    pct_recall = 100.0 * s.recall_events / s.total if s.total else 0.0
    avg_delta = sum(s.hit_deltas) / len(s.hit_deltas) if s.hit_deltas else 0.0
    fts_p50 = _percentile(s.fts_latencies, 50)
    fts_p99 = _percentile(s.fts_latencies, 99)
    vec_p50 = _percentile(s.vec_latencies, 50)
    vec_p99 = _percentile(s.vec_latencies, 99)
    avg_cost = sum(s.vec_latencies) / len(s.vec_latencies) if s.vec_latencies else 0.0

    lines = [
        f"=== Memory Phase 2 shadow-reads summary ({hours}h window) ===",
        f"Queries total:            {s.total}",
        f"Would change top-5:       {s.changed_top5} ({pct_changed:.1f}%)",
        f"Recall events (vec adds): {s.recall_events} ({pct_recall:.1f}%)",
        f"Avg hit delta (hyb-fts):  {avg_delta:+.2f}",
        "",
        "Latency (ms):",
        f"  FTS  P50={fts_p50:6.1f}   P99={fts_p99:6.1f}",
        f"  VEC  P50={vec_p50:6.1f}   P99={vec_p99:6.1f}",
        f"  Avg hybrid cost:        +{avg_cost:.1f}ms per query",
        "",
        "Interpretation:",
        f"  За {hours}h из {s.total} запросов: "
        f"{s.total - s.changed_top5} без изменений, "
        f"{s.changed_top5} changed top-5 "
        f"(+{pct_recall:.0f}% recall events), "
        f"latency cost avg +{avg_cost:.0f}ms.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="Окно анализа в часах")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Путь к log-файлу")
    args = parser.parse_args()

    if not args.log.exists():
        print(f"[shadow-analyze] Log file not found: {args.log}", file=sys.stderr)
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    events: list[ShadowEvent] = []
    with args.log.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ev = _parse_line(line)
            if ev is None:
                continue
            if ev.ts is not None and ev.ts < cutoff:
                continue
            events.append(ev)

    summary = analyze(events)
    print(format_summary(summary, args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

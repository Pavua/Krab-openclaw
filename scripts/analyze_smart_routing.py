"""
Анализатор Smart Routing — читает krab_main.log + chat_response_policies.json
и выдаёт actionable summary по routing-решениям за последние N часов.

Использование:
    python scripts/analyze_smart_routing.py [--hours N] [--json] [--chat-id ID]

Default: --hours 24. --json — machine-readable JSON вместо текста.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# ── Пути по умолчанию ──────────────────────────────────────────────────────
_RUNTIME_STATE = Path.home() / ".openclaw" / "krab_runtime_state"
_LOG_PATH = _RUNTIME_STATE / "krab_main.log"
_POLICY_PATH = _RUNTIME_STATE / "chat_response_policies.json"

# ── Парсинг structlog-строк ─────────────────────────────────────────────────
# Формат: "2026-04-03 16:02:40 [info     ] event_name   key=value key2='v v'"
_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[(\w+)\s*\]\s+(\S+)\s*(.*)")
_KV_RE = re.compile(r"(\w+)=(?:'([^']*)'|\"([^\"]*)\"|([\S]*))")


def _parse_timestamp(ts_str: str) -> float:
    """Парсит 'YYYY-MM-DD HH:MM:SS' → unix timestamp (local time)."""
    import datetime

    try:
        dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.timestamp()
    except ValueError:
        return 0.0


def _parse_kv(kv_str: str) -> dict[str, str]:
    """Парсит `key=value key2='val val'` в dict."""
    result: dict[str, str] = {}
    for m in _KV_RE.finditer(kv_str):
        key = m.group(1)
        val = m.group(2) or m.group(3) or m.group(4) or ""
        result[key] = val
    return result


def _parse_line(line: str) -> dict[str, Any] | None:
    """Парсит одну log-строку → dict или None если формат не совпал."""
    m = _LINE_RE.match(line.rstrip())
    if not m:
        return None
    ts_str, level, event, kv_str = m.group(1), m.group(2), m.group(3), m.group(4)
    entry: dict[str, Any] = {
        "ts": _parse_timestamp(ts_str),
        "ts_str": ts_str,
        "level": level,
        "event": event,
    }
    entry.update(_parse_kv(kv_str))
    return entry


# ── Чтение лога ────────────────────────────────────────────────────────────

_INTERESTING_EVENTS = frozenset(
    {
        "smart_trigger_decision",
        "smart_trigger_failed",
        "feedback_negative_delete",
        "feedback_negative_reaction",
        "feedback_positive_reaction",
        "chat_response_policy_auto_downshift",
        "chat_response_policy_auto_upshift",
    }
)


def load_events(
    log_path: Path,
    since_ts: float,
    chat_id_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Читает log_path и возвращает события Smart Routing за период [since_ts, now]."""
    events: list[dict[str, Any]] = []
    if not log_path.exists():
        return events

    try:
        with log_path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                entry = _parse_line(raw)
                if entry is None:
                    continue
                if entry["event"] not in _INTERESTING_EVENTS:
                    continue
                if entry["ts"] < since_ts:
                    continue
                if chat_id_filter is not None:
                    if str(entry.get("chat_id", "")) != str(chat_id_filter):
                        continue
                events.append(entry)
    except OSError:
        pass

    return events


# ── Чтение policy store ─────────────────────────────────────────────────────


def load_policies(policy_path: Path) -> dict[str, dict[str, Any]]:
    """Загружает chat_response_policies.json → {chat_id: policy_dict}."""
    if not policy_path.exists():
        return {}
    try:
        raw = policy_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Файл может быть списком или dict {chat_id: {...}}
        if isinstance(data, list):
            return {str(p["chat_id"]): p for p in data if "chat_id" in p}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except (json.JSONDecodeError, OSError, KeyError):
        pass
    return {}


# ── Агрегация ───────────────────────────────────────────────────────────────


def _bin_confidence(conf: float) -> str:
    """Возвращает bin label для confidence: '0.0-0.1', '0.1-0.2', ..."""
    idx = min(int(conf * 10), 9)
    lo = idx * 0.1
    hi = lo + 0.1
    return f"{lo:.1f}-{hi:.1f}"


class SmartRoutingReport:
    """Агрегированный отчёт Smart Routing за период."""

    def __init__(self) -> None:
        # decision_path → count
        self.decision_path_counts: dict[str, int] = defaultdict(int)
        # confidence bin → count
        self.confidence_hist: dict[str, int] = defaultdict(int)
        # chat_id → {total, responded, failed}
        self.per_chat: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "responded": 0, "failed": 0}
        )
        # chat_id → {negative, positive}
        self.feedback: dict[str, dict[str, int]] = defaultdict(
            lambda: {"negative": 0, "positive": 0}
        )
        # auto-adjust events list
        self.auto_adjustments: list[dict[str, Any]] = []

        self.total_decisions = 0
        self.total_failed = 0

    def process(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            event = ev["event"]
            chat_id = str(ev.get("chat_id", "unknown"))

            if event == "smart_trigger_decision":
                self.total_decisions += 1
                path = str(ev.get("decision_path", "unknown"))
                self.decision_path_counts[path] += 1
                conf_raw = ev.get("confidence", "0")
                try:
                    conf = float(conf_raw)
                except (ValueError, TypeError):
                    conf = 0.0
                self.confidence_hist[_bin_confidence(conf)] += 1
                self.per_chat[chat_id]["total"] += 1
                should = str(ev.get("should_respond", "false")).lower()
                if should in {"true", "1", "yes"}:
                    self.per_chat[chat_id]["responded"] += 1

            elif event == "smart_trigger_failed":
                self.total_failed += 1
                self.per_chat[chat_id]["failed"] += 1

            elif event in ("feedback_negative_delete", "feedback_negative_reaction"):
                self.feedback[chat_id]["negative"] += 1

            elif event == "feedback_positive_reaction":
                self.feedback[chat_id]["positive"] += 1

            elif event in (
                "chat_response_policy_auto_downshift",
                "chat_response_policy_auto_upshift",
            ):
                direction = "upshift" if "upshift" in event else "downshift"
                self.auto_adjustments.append(
                    {
                        "chat_id": chat_id,
                        "direction": direction,
                        "from_mode": ev.get("from_mode", "?"),
                        "to_mode": ev.get("to_mode", "?"),
                        "ts_str": ev.get("ts_str", ""),
                    }
                )

    # ── Детекция аномалий ─────────────────────────────────────────────────

    def detect_anomalies(self) -> list[dict[str, str]]:
        """Возвращает список аномалий с полями: severity, code, message."""
        anomalies: list[dict[str, str]] = []
        total = self.total_decisions

        # 1. llm_yes rate > 50%
        llm_yes = self.decision_path_counts.get("llm_yes", 0)
        if total > 0 and llm_yes / total > 0.5:
            anomalies.append(
                {
                    "severity": "warn",
                    "code": "HIGH_LLM_YES_RATE",
                    "message": (
                        f"llm_yes составляет {llm_yes}/{total} ({llm_yes / total:.0%}) решений — "
                        "модель слишком активно отвечает, рекомендуется поднять threshold."
                    ),
                }
            )

        # 2. smart_trigger_failed > 5%
        total_calls = total + self.total_failed
        if total_calls > 0 and self.total_failed / total_calls > 0.05:
            rate = self.total_failed / total_calls
            anomalies.append(
                {
                    "severity": "error",
                    "code": "HIGH_FAILURE_RATE",
                    "message": (
                        f"smart_trigger_failed: {self.total_failed}/{total_calls} ({rate:.0%}) — "
                        "LM Studio, вероятно, недоступен или модель перегружена."
                    ),
                }
            )

        # 3. Чаты с negative > 5 без positive → рекомендуем downshift
        for chat_id, fb in self.feedback.items():
            if fb["negative"] > 5 and fb["positive"] == 0:
                anomalies.append(
                    {
                        "severity": "warn",
                        "code": "DOWNSHIFT_CANDIDATE",
                        "message": (
                            f"Чат {chat_id}: {fb['negative']} негативных сигналов "
                            "без позитивных — рекомендуется снизить режим (downshift)."
                        ),
                    }
                )

        return anomalies

    # ── Сборка результата ─────────────────────────────────────────────────

    def to_dict(
        self,
        policies: dict[str, dict[str, Any]],
        hours: int,
    ) -> dict[str, Any]:
        anomalies = self.detect_anomalies()
        total = self.total_decisions

        # Сортируем confidence bins
        conf_hist_sorted = dict(sorted(self.confidence_hist.items()))

        # Per-chat с policy cross-reference
        per_chat_out: dict[str, dict[str, Any]] = {}
        all_chats = set(self.per_chat.keys()) | set(self.feedback.keys())
        for cid in sorted(all_chats):
            pc = self.per_chat.get(cid, {"total": 0, "responded": 0, "failed": 0})
            fb = self.feedback.get(cid, {"negative": 0, "positive": 0})
            pol = policies.get(cid, {})
            per_chat_out[cid] = {
                "total_decisions": pc["total"],
                "responded": pc["responded"],
                "failed": pc["failed"],
                "response_rate": (
                    round(pc["responded"] / pc["total"], 3) if pc["total"] > 0 else None
                ),
                "feedback_negative": fb["negative"],
                "feedback_positive": fb["positive"],
                "policy_mode": pol.get("mode"),
                "policy_threshold_override": pol.get("threshold_override"),
                "policy_last_auto_adjust_ts": pol.get("last_auto_adjust_ts"),
                "policy_auto_adjust_enabled": pol.get("auto_adjust_enabled"),
            }

        return {
            "period_hours": hours,
            "total_decisions": total,
            "total_failed": self.total_failed,
            "decision_path_distribution": dict(self.decision_path_counts),
            "confidence_histogram": conf_hist_sorted,
            "per_chat": per_chat_out,
            "auto_adjustments": self.auto_adjustments,
            "anomalies": anomalies,
        }


# ── Форматирование вывода ───────────────────────────────────────────────────

_SEVERITY_PREFIX = {
    "error": "🔴",
    "warn": "🟡",
    "info": "🟢",
}

_PATH_LABELS: dict[str, str] = {
    "hard_gate": "Hard gate (запрет)",
    "policy_silent": "Policy silent",
    "regex_high": "Regex HIGH",
    "regex_low": "Regex low",
    "llm_yes": "LLM → ответить",
    "llm_no": "LLM → промолчать",
    "unknown": "Неизвестный путь",
}


def format_text_report(data: dict[str, Any]) -> str:
    lines: list[str] = []
    hours = data["period_hours"]
    total = data["total_decisions"]
    failed = data["total_failed"]
    total_calls = total + failed

    lines.append(f"=== Smart Routing — анализ за {hours}ч ===")
    lines.append("")

    # Общая статистика
    lines.append(f"Всего решений:  {total}")
    lines.append(
        f"Сбоев (failed): {failed}" + (f" ({failed / total_calls:.0%})" if total_calls else "")
    )
    if total == 0 and failed == 0:
        lines.append("")
        lines.append(
            "  (нет событий в лог-файле за указанный период — Krab только запустился или Smart Routing не активен)"
        )

    lines.append("")

    # Decision path distribution
    paths = data["decision_path_distribution"]
    if paths:
        lines.append("Распределение по decision_path:")
        for path, cnt in sorted(paths.items(), key=lambda x: -x[1]):
            label = _PATH_LABELS.get(path, path)
            pct = f"{cnt / total:.0%}" if total > 0 else "?%"
            lines.append(f"  {label:<30} {cnt:>5}  ({pct})")
    else:
        lines.append("Распределение по decision_path: нет данных")

    lines.append("")

    # Confidence histogram
    hist = data["confidence_histogram"]
    if hist:
        lines.append("Распределение уверенности (confidence):")
        for bin_label, cnt in sorted(hist.items()):
            bar = "█" * min(cnt, 40)
            lines.append(f"  [{bin_label}]  {bar}  {cnt}")
    else:
        lines.append("Распределение confidence: нет данных")

    lines.append("")

    # Per-chat stats (только чаты с activity)
    per_chat = data["per_chat"]
    if per_chat:
        lines.append(f"По чатам ({len(per_chat)} активных):")
        for cid, stats in per_chat.items():
            mode = stats.get("policy_mode") or "normal"
            rate = stats["response_rate"]
            rate_str = f"{rate:.0%}" if rate is not None else "—"
            neg = stats["feedback_negative"]
            pos = stats["feedback_positive"]
            fb_str = f"neg={neg} pos={pos}"
            auto_adj = stats.get("policy_last_auto_adjust_ts")
            adj_str = " [auto-adj]" if auto_adj else ""
            lines.append(f"  чат {cid:>16}  mode={mode:<8}  respond={rate_str}  {fb_str}{adj_str}")
    else:
        lines.append("Статистика по чатам: нет данных")

    lines.append("")

    # Auto-adjustments
    adjustments = data["auto_adjustments"]
    if adjustments:
        lines.append("Авто-корректировки режима:")
        for adj in adjustments:
            direction = "⬇ downshift" if adj["direction"] == "downshift" else "⬆ upshift"
            lines.append(
                f"  {adj['ts_str']}  чат {adj['chat_id']}  {direction}: "
                f"{adj['from_mode']} → {adj['to_mode']}"
            )
    else:
        lines.append("Авто-корректировок режима: нет")

    lines.append("")

    # Аномалии
    anomalies = data["anomalies"]
    if anomalies:
        lines.append(f"Обнаружены аномалии ({len(anomalies)}):")
        for a in anomalies:
            prefix = _SEVERITY_PREFIX.get(a["severity"], "⚪")
            lines.append(f"  {prefix} [{a['code']}] {a['message']}")
    else:
        lines.append("Аномалий не обнаружено.")

    lines.append("")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Анализатор Smart Routing — статистика log-событий за период.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--hours",
        type=int,
        default=24,
        metavar="N",
        help="Период анализа в часах (default: 24)",
    )
    p.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Вывод в JSON вместо текста",
    )
    p.add_argument(
        "--chat-id",
        dest="chat_id",
        default=None,
        metavar="ID",
        help="Фильтровать по конкретному chat_id",
    )
    p.add_argument(
        "--log",
        default=str(_LOG_PATH),
        metavar="PATH",
        help=f"Путь к krab_main.log (default: {_LOG_PATH})",
    )
    p.add_argument(
        "--policy",
        default=str(_POLICY_PATH),
        metavar="PATH",
        help=f"Путь к chat_response_policies.json (default: {_POLICY_PATH})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    since_ts = time.time() - args.hours * 3600

    events = load_events(
        log_path=Path(args.log),
        since_ts=since_ts,
        chat_id_filter=args.chat_id,
    )

    policies = load_policies(Path(args.policy))

    report = SmartRoutingReport()
    report.process(events)
    data = report.to_dict(policies=policies, hours=args.hours)

    if args.output_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_text_report(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Тесты для scripts/analyze_smart_routing.py."""

from __future__ import annotations

# Подключаем скрипт через importlib (он не в src/, поэтому sys.path adjustments)
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "analyze_smart_routing.py"


def _import_module():
    spec = importlib.util.spec_from_file_location("analyze_smart_routing", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _import_module()

load_events = _mod.load_events
load_policies = _mod.load_policies
SmartRoutingReport = _mod.SmartRoutingReport
format_text_report = _mod.format_text_report
_parse_line = _mod._parse_line
_bin_confidence = _mod._bin_confidence


# ── Фикстуры ────────────────────────────────────────────────────────────────

NOW_STR = "2026-04-27 12:00:00"
NOW_TS = 1745748000.0  # приблизительно (используем parse)


def _ts(offset_sec: int = 0) -> str:
    """Строка timestamp NOW_STR с небольшим offset для имитации последовательности."""
    import datetime

    base = datetime.datetime(2026, 4, 27, 12, 0, 0)
    dt = base + datetime.timedelta(seconds=offset_sec)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _log_line(event: str, kv: dict, offset: int = 0) -> str:
    """Генерирует синтетическую log-строку в structlog-формате."""
    kv_str = " ".join(f"{k}={v}" for k, v in kv.items())
    return f"{_ts(offset)} [info     ] {event}   {kv_str}"


# ── Тест 1: парсинг structlog-строки ────────────────────────────────────────


class TestParseLogLine:
    def test_basic_kv(self):
        line = "2026-04-27 10:00:00 [info     ] smart_trigger_decision   chat_id=-100123 should_respond=True decision_path=llm_yes confidence=0.85"
        result = _parse_line(line)
        assert result is not None
        assert result["event"] == "smart_trigger_decision"
        assert result["chat_id"] == "-100123"
        assert result["decision_path"] == "llm_yes"
        assert float(result["confidence"]) == pytest.approx(0.85)

    def test_garbage_line_returns_none(self):
        assert _parse_line("==== Krab detached start ====") is None
        assert _parse_line("") is None
        assert _parse_line("[launcher] foo=bar") is None

    def test_confidence_bin_boundaries(self):
        assert _bin_confidence(0.0) == "0.0-0.1"
        assert _bin_confidence(0.5) == "0.5-0.6"
        assert _bin_confidence(0.99) == "0.9-1.0"
        assert _bin_confidence(1.0) == "0.9-1.0"  # clamp к last bin


# ── Тест 2: load_events фильтрует по времени и chat_id ─────────────────────


class TestLoadEvents:
    def test_loads_events_within_window(self, tmp_path: Path):
        log = tmp_path / "krab_main.log"
        lines = [
            _log_line(
                "smart_trigger_decision",
                {
                    "chat_id": "-100",
                    "should_respond": "True",
                    "decision_path": "llm_yes",
                    "confidence": "0.8",
                },
                0,
            ),
            _log_line("smart_trigger_failed", {"chat_id": "-200", "error": "timeout"}, 10),
            _log_line("feedback_negative_reaction", {"chat_id": "-100", "reaction": "👎"}, 20),
            "2020-01-01 00:00:00 [info     ] smart_trigger_decision   chat_id=-999 should_respond=False decision_path=hard_gate confidence=1.0",
        ]
        log.write_text("\n".join(lines), encoding="utf-8")

        # since_ts чуть раньше наших записей
        import datetime

        since = datetime.datetime(2026, 4, 27, 11, 59, 0).timestamp()
        events = load_events(log, since_ts=since)

        assert len(events) == 3
        event_names = {e["event"] for e in events}
        assert "smart_trigger_decision" in event_names
        assert "smart_trigger_failed" in event_names
        assert "feedback_negative_reaction" in event_names

    def test_chat_id_filter(self, tmp_path: Path):
        log = tmp_path / "krab_main.log"
        lines = [
            _log_line(
                "smart_trigger_decision",
                {
                    "chat_id": "-100",
                    "should_respond": "True",
                    "decision_path": "llm_yes",
                    "confidence": "0.7",
                },
                0,
            ),
            _log_line(
                "smart_trigger_decision",
                {
                    "chat_id": "-999",
                    "should_respond": "False",
                    "decision_path": "hard_gate",
                    "confidence": "1.0",
                },
                5,
            ),
        ]
        log.write_text("\n".join(lines), encoding="utf-8")

        import datetime

        since = datetime.datetime(2026, 4, 27, 11, 59, 0).timestamp()
        events = load_events(log, since_ts=since, chat_id_filter="-100")
        assert len(events) == 1
        assert events[0]["chat_id"] == "-100"

    def test_missing_log_returns_empty(self, tmp_path: Path):
        events = load_events(tmp_path / "nonexistent.log", since_ts=0.0)
        assert events == []


# ── Тест 3: агрегация в SmartRoutingReport ─────────────────────────────────


class TestSmartRoutingReport:
    def _make_events(self) -> list[dict]:
        """Синтетический набор событий для агрегации."""
        import datetime

        base_ts = datetime.datetime(2026, 4, 27, 12, 0, 0).timestamp()

        return [
            # 3 llm_yes, 1 hard_gate, 1 llm_no
            {
                "event": "smart_trigger_decision",
                "ts": base_ts,
                "ts_str": NOW_STR,
                "chat_id": "-100",
                "should_respond": "True",
                "decision_path": "llm_yes",
                "confidence": "0.9",
            },
            {
                "event": "smart_trigger_decision",
                "ts": base_ts + 1,
                "ts_str": NOW_STR,
                "chat_id": "-100",
                "should_respond": "True",
                "decision_path": "llm_yes",
                "confidence": "0.8",
            },
            {
                "event": "smart_trigger_decision",
                "ts": base_ts + 2,
                "ts_str": NOW_STR,
                "chat_id": "-200",
                "should_respond": "True",
                "decision_path": "llm_yes",
                "confidence": "0.7",
            },
            {
                "event": "smart_trigger_decision",
                "ts": base_ts + 3,
                "ts_str": NOW_STR,
                "chat_id": "-200",
                "should_respond": "False",
                "decision_path": "hard_gate",
                "confidence": "1.0",
            },
            {
                "event": "smart_trigger_decision",
                "ts": base_ts + 4,
                "ts_str": NOW_STR,
                "chat_id": "-300",
                "should_respond": "False",
                "decision_path": "llm_no",
                "confidence": "0.3",
            },
            # 1 failed
            {
                "event": "smart_trigger_failed",
                "ts": base_ts + 5,
                "ts_str": NOW_STR,
                "chat_id": "-100",
                "error": "model_not_loaded",
            },
            # feedback
            {
                "event": "feedback_negative_reaction",
                "ts": base_ts + 6,
                "ts_str": NOW_STR,
                "chat_id": "-100",
                "reaction": "👎",
            },
            {
                "event": "feedback_positive_reaction",
                "ts": base_ts + 7,
                "ts_str": NOW_STR,
                "chat_id": "-200",
                "reaction": "👍",
            },
            # auto-adjust
            {
                "event": "chat_response_policy_auto_downshift",
                "ts": base_ts + 8,
                "ts_str": NOW_STR,
                "chat_id": "-100",
                "from_mode": "normal",
                "to_mode": "cautious",
                "negatives": "6",
            },
        ]

    def test_decision_counts(self):
        report = SmartRoutingReport()
        report.process(self._make_events())

        assert report.total_decisions == 5
        assert report.total_failed == 1
        assert report.decision_path_counts["llm_yes"] == 3
        assert report.decision_path_counts["hard_gate"] == 1
        assert report.decision_path_counts["llm_no"] == 1

    def test_per_chat_responded(self):
        report = SmartRoutingReport()
        report.process(self._make_events())

        # -100: 2 decisions, 2 responded, 1 failed
        assert report.per_chat["-100"]["total"] == 2
        assert report.per_chat["-100"]["responded"] == 2
        assert report.per_chat["-100"]["failed"] == 1

        # -200: 2 decisions, 1 responded
        assert report.per_chat["-200"]["total"] == 2
        assert report.per_chat["-200"]["responded"] == 1

    def test_feedback_counts(self):
        report = SmartRoutingReport()
        report.process(self._make_events())

        assert report.feedback["-100"]["negative"] == 1
        assert report.feedback["-200"]["positive"] == 1

    def test_auto_adjustments_collected(self):
        report = SmartRoutingReport()
        report.process(self._make_events())

        assert len(report.auto_adjustments) == 1
        adj = report.auto_adjustments[0]
        assert adj["direction"] == "downshift"
        assert adj["from_mode"] == "normal"
        assert adj["to_mode"] == "cautious"


# ── Тест 4: детекция аномалий ───────────────────────────────────────────────


class TestAnomalyDetection:
    def _make_report_with_overrides(
        self,
        *,
        llm_yes: int = 0,
        total: int = 10,
        failed: int = 0,
        neg_chat: dict[str, int] | None = None,
    ) -> SmartRoutingReport:
        import datetime

        base_ts = datetime.datetime(2026, 4, 27, 12, 0, 0).timestamp()
        events: list[dict] = []

        # llm_yes decisions
        for i in range(llm_yes):
            events.append(
                {
                    "event": "smart_trigger_decision",
                    "ts": base_ts + i,
                    "ts_str": NOW_STR,
                    "chat_id": "-100",
                    "should_respond": "True",
                    "decision_path": "llm_yes",
                    "confidence": "0.8",
                }
            )

        # другие decisions чтобы добить total
        other = total - llm_yes
        for i in range(other):
            events.append(
                {
                    "event": "smart_trigger_decision",
                    "ts": base_ts + llm_yes + i,
                    "ts_str": NOW_STR,
                    "chat_id": "-100",
                    "should_respond": "False",
                    "decision_path": "hard_gate",
                    "confidence": "1.0",
                }
            )

        # failed events
        for i in range(failed):
            events.append(
                {
                    "event": "smart_trigger_failed",
                    "ts": base_ts + 1000 + i,
                    "ts_str": NOW_STR,
                    "chat_id": "-100",
                    "error": "err",
                }
            )

        # negative feedback
        if neg_chat:
            for cid, cnt in neg_chat.items():
                for i in range(cnt):
                    events.append(
                        {
                            "event": "feedback_negative_reaction",
                            "ts": base_ts + 2000 + i,
                            "ts_str": NOW_STR,
                            "chat_id": cid,
                            "reaction": "👎",
                        }
                    )

        report = SmartRoutingReport()
        report.process(events)
        return report

    def test_high_llm_yes_rate_flagged(self):
        report = self._make_report_with_overrides(llm_yes=6, total=10)
        anomalies = report.detect_anomalies()
        codes = [a["code"] for a in anomalies]
        assert "HIGH_LLM_YES_RATE" in codes

    def test_normal_llm_yes_rate_clean(self):
        report = self._make_report_with_overrides(llm_yes=3, total=10)
        anomalies = report.detect_anomalies()
        codes = [a["code"] for a in anomalies]
        assert "HIGH_LLM_YES_RATE" not in codes

    def test_high_failure_rate_flagged(self):
        report = self._make_report_with_overrides(total=10, failed=2)
        anomalies = report.detect_anomalies()
        codes = [a["code"] for a in anomalies]
        assert "HIGH_FAILURE_RATE" in codes

    def test_low_failure_rate_clean(self):
        report = self._make_report_with_overrides(total=100, failed=1)
        anomalies = report.detect_anomalies()
        codes = [a["code"] for a in anomalies]
        assert "HIGH_FAILURE_RATE" not in codes

    def test_downshift_candidate_flagged(self):
        report = self._make_report_with_overrides(total=5, neg_chat={"-999": 6})
        anomalies = report.detect_anomalies()
        codes = [a["code"] for a in anomalies]
        assert "DOWNSHIFT_CANDIDATE" in codes

    def test_no_anomalies_on_empty(self):
        report = SmartRoutingReport()
        assert report.detect_anomalies() == []


# ── Тест 5: выходной формат (текст + JSON) ──────────────────────────────────


class TestOutputFormat:
    def _build_data(self, total: int = 0) -> dict:
        report = SmartRoutingReport()
        return report.to_dict(policies={}, hours=24)

    def test_text_output_contains_headers(self):
        data = self._build_data()
        text = format_text_report(data)
        assert "Smart Routing" in text
        assert "decision_path" in text or "Распределение" in text
        assert "Аномалий не обнаружено" in text

    def test_text_output_empty_hint(self):
        data = self._build_data()
        text = format_text_report(data)
        # Должен быть graceful hint об отсутствии данных
        assert "нет событий" in text or "нет данных" in text

    def test_json_output_structure(self):
        report = SmartRoutingReport()
        data = report.to_dict(policies={}, hours=12)
        assert "total_decisions" in data
        assert "decision_path_distribution" in data
        assert "confidence_histogram" in data
        assert "per_chat" in data
        assert "auto_adjustments" in data
        assert "anomalies" in data
        assert data["period_hours"] == 12

    def test_json_policy_cross_reference(self, tmp_path: Path):
        """Policy данные попадают в per_chat output."""
        import datetime

        base_ts = datetime.datetime(2026, 4, 27, 12, 0, 0).timestamp()
        events = [
            {
                "event": "smart_trigger_decision",
                "ts": base_ts,
                "ts_str": NOW_STR,
                "chat_id": "-555",
                "should_respond": "True",
                "decision_path": "regex_high",
                "confidence": "0.95",
            }
        ]
        report = SmartRoutingReport()
        report.process(events)
        policies = {
            "-555": {
                "chat_id": "-555",
                "mode": "cautious",
                "threshold_override": 0.65,
                "last_auto_adjust_ts": 1_700_000_000.0,
                "auto_adjust_enabled": True,
            }
        }
        data = report.to_dict(policies=policies, hours=24)
        pc = data["per_chat"].get("-555")
        assert pc is not None
        assert pc["policy_mode"] == "cautious"
        assert pc["policy_threshold_override"] == pytest.approx(0.65)
        assert pc["policy_last_auto_adjust_ts"] is not None

    def test_load_policies_parses_dict_format(self, tmp_path: Path):
        policy_file = tmp_path / "policies.json"
        payload = {
            "-100": {"chat_id": "-100", "mode": "silent", "threshold_override": None},
            "-200": {"chat_id": "-200", "mode": "chatty", "threshold_override": 0.3},
        }
        policy_file.write_text(json.dumps(payload), encoding="utf-8")
        result = load_policies(policy_file)
        assert result["-100"]["mode"] == "silent"
        assert result["-200"]["mode"] == "chatty"

    def test_load_policies_missing_file(self, tmp_path: Path):
        result = load_policies(tmp_path / "no_such_file.json")
        assert result == {}

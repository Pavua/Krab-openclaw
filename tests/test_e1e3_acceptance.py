"""
Тесты для scripts/e1e3_acceptance.py.

Проверяем локальные функции парсинга/сканирования,
чтобы acceptance-скрипт оставался детерминированным.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "e1e3_acceptance.py"
    spec = importlib.util.spec_from_file_location("e1e3_acceptance", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_parse_line_timestamp_utc_supports_zulu():
    module = _load_module()
    ts = module._parse_line_timestamp_utc("2026-03-03T22:31:09.906Z [gateway] hello")
    assert ts is not None
    assert ts.tzinfo is not None
    assert ts.year == 2026


def test_parse_line_timestamp_utc_strips_ansi_prefix():
    module = _load_module()
    ts = module._parse_line_timestamp_utc("\\x1b[2m2026-03-03 22:31:09\\x1b[0m warning")
    assert ts is not None
    assert ts.year == 2026


def test_scan_kpi_patterns_counts_recent_matches(tmp_path):
    module = _load_module()
    log = tmp_path / "test.log"
    log.write_text(
        "\n".join(
            [
                "2026-03-03T22:31:09.906Z warning no models loaded",
                "2026-03-03T22:31:10.100Z warning telegram_manual_relogin_required",
                "2026-03-03T22:31:10.200Z warning model has crashed without additional information",
                "2026-03-03T22:31:10.300Z ❌ Модель вернула пустой поток",
            ]
        ),
        encoding="utf-8",
    )

    result = module._scan_kpi_patterns([log], tail_lines=200, max_age_minutes=1_000_000)
    counters = result["counters"]
    assert counters["no_models_loaded"] == 1
    assert counters["manual_relogin_required"] == 1
    assert counters["lm_model_crash"] == 1
    assert counters["empty_message_user_visible"] == 1


def test_classify_channels_skips_not_configured():
    module = _load_module()
    payload = {
        "gateway_reachable": True,
        "channels": [
            {"name": "Telegram default", "status": "OK", "meta": "works"},
            {"name": "BlueBubbles default", "status": "FAIL", "meta": "not configured"},
            {"name": "Discord default", "status": "FAIL", "meta": "token invalid"},
        ],
    }
    stats = module._classify_channels(payload)
    assert stats["required_total"] == 2
    assert len(stats["passed"]) == 1
    assert len(stats["failed"]) == 1
    assert len(stats["skipped"]) == 1

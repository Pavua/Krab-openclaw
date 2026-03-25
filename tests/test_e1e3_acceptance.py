"""
Тесты для scripts/e1e3_acceptance.py.

Проверяем локальные функции парсинга/сканирования,
чтобы acceptance-скрипт оставался детерминированным.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


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


def test_normalize_probe_channels_payload_maps_cli_probe_contract():
    module = _load_module()
    payload = {
        "channels": {
            "telegram": {"configured": True, "running": True, "probe": {"ok": True}},
            "bluebubbles": {"configured": False, "running": False, "probe": {"ok": False}},
            "slack": {"configured": True, "running": True, "probe": {"ok": False, "error": "unauthorized"}},
        }
    }

    normalized = module._normalize_probe_channels_payload(payload)
    channels = {item["name"]: item for item in normalized["channels"]}

    assert channels["telegram"]["status"] == "OK"
    assert channels["bluebubbles"]["status"] == "WARN"
    assert "not configured" in channels["bluebubbles"]["meta"]
    assert channels["slack"]["status"] == "FAIL"
    assert "probe failed" in channels["slack"]["meta"]


def test_fetch_channels_with_fallback_returns_probe_source_and_web_error(monkeypatch):
    module = _load_module()

    monkeypatch.setattr(module, "_fetch_stable_channels_payload", lambda _url: ({}, "connection reset"))
    monkeypatch.setattr(
        module,
        "_fetch_probe_channels_payload",
        lambda: (
            {
                "gateway_reachable": True,
                "channels": [{"name": "telegram", "status": "OK", "meta": "works"}],
            },
            None,
        ),
    )

    payload, err, source, web_error = module._fetch_channels_with_fallback(
        "http://127.0.0.1:8080/api/openclaw/channels/status"
    )
    assert err is None
    assert source == "gateway_probe"
    assert web_error == "connection reset"
    assert payload["channels"][0]["status"] == "OK"


def test_fetch_probe_channels_payload_reports_non_json_output(monkeypatch):
    module = _load_module()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Gateway service not loaded.\nStart with: openclaw gateway",
            stderr="",
        ),
    )

    payload, err = module._fetch_probe_channels_payload()
    assert payload == {}
    assert err is not None
    assert "probe_json_not_found" in err
    assert "Gateway service not loaded." in err

"""
Тесты для scripts/live_channel_smoke.py.

Проверяем локальную логику детекта паттернов без запуска реального openclaw CLI.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


def _load_smoke_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "live_channel_smoke.py"
    spec = importlib.util.spec_from_file_location("live_channel_smoke", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_scan_patterns_marks_error_and_warning_severity():
    module = _load_smoke_module()
    lines = [
        "The model has crashed without additional information. (Exit code: null)",
        "krab-output-sanitizer: loaded without install/load-path provenance",
    ]
    patterns = [
        ("model_crash", re.compile(r"model has crashed", re.IGNORECASE), "error"),
        (
            "plugin_untracked",
            re.compile(r"without install/load-path provenance", re.IGNORECASE),
            "warn",
        ),
    ]

    findings = module._scan_patterns(Path("/tmp/fake.log"), lines, patterns)

    assert len(findings) == 2
    assert findings[0]["code"] == "model_crash"
    assert findings[0]["severity"] == "error"
    assert findings[1]["code"] == "plugin_untracked"
    assert findings[1]["severity"] == "warn"


def test_pattern_specs_contains_sanitizer_config_invalid_guard():
    module = _load_smoke_module()
    codes = {item[0] for item in module.PATTERN_SPECS}
    assert "sanitizer_plugin_config_invalid" in codes
    assert "sanitizer_plugin_untracked_provenance" in codes


def test_tail_lines_for_missing_file_is_empty():
    module = _load_smoke_module()
    missing = Path("/tmp/definitely_missing_live_channel_smoke.log")
    assert module._tail_lines(missing, 100) == []


def test_parse_line_timestamp_supports_ansi_and_space_separator():
    module = _load_smoke_module()
    line = "\x1b[2m2026-03-04 02:03:18\x1b[0m [warning] test"
    parsed = module._parse_line_timestamp_utc(line)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.year == 2026
    assert parsed.month == 3
    assert parsed.day == 4


def test_filter_recent_lines_excludes_old_ansi_timestamp():
    module = _load_smoke_module()
    old_dt = datetime.now(timezone.utc) - timedelta(days=3)
    old_line = f"\x1b[2m{old_dt.strftime('%Y-%m-%d %H:%M:%S')}\x1b[0m old issue"
    fresh_line = "строка без timestamp остаётся по fail-open"
    filtered = module._filter_recent_lines([old_line, fresh_line], max_age_minutes=60.0)
    assert old_line not in filtered
    assert fresh_line in filtered


def test_normalize_probe_channels_payload_maps_probe_json_to_channels_contract():
    module = _load_smoke_module()
    payload = {
        "channels": {
            "telegram": {"configured": True, "running": True, "probe": {"ok": True}},
            "bluebubbles": {"configured": False, "running": False, "probe": {"ok": False}},
            "discord": {"configured": True, "running": False, "probe": {"ok": False}},
        }
    }

    normalized = module._normalize_probe_channels_payload(payload)
    channels = {item["name"]: item for item in normalized["channels"]}

    assert channels["telegram"]["status"] == "OK"
    assert channels["bluebubbles"]["status"] == "WARN"
    assert "not configured" in channels["bluebubbles"]["meta"]
    assert channels["discord"]["status"] == "FAIL"
    assert "disconnected" in channels["discord"]["meta"]


def test_normalize_probe_channels_payload_prefers_probe_success_over_running_flag():
    """Если probe успешен, временный `running=False` не должен валить канал в FAIL."""

    module = _load_smoke_module()
    payload = {
        "channels": {
            "discord": {"configured": True, "running": False, "probe": {"ok": True}},
        }
    }

    normalized = module._normalize_probe_channels_payload(payload)
    channels = {item["name"]: item for item in normalized["channels"]}

    assert channels["discord"]["status"] == "OK"
    assert "works" in channels["discord"]["meta"]
    assert "disconnected" in channels["discord"]["meta"]


def test_fetch_channels_with_fallback_uses_probe_when_web_unavailable(monkeypatch):
    module = _load_smoke_module()

    monkeypatch.setattr(module, "_fetch_stable_channels_payload", lambda _url: ({}, "timed out"))
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

    payload, err, source = module._fetch_channels_with_fallback(
        "http://127.0.0.1:8080/api/openclaw/channels/status"
    )
    assert err is None
    assert source == "gateway_probe"
    assert payload["channels"][0]["name"] == "telegram"


def test_fetch_probe_channels_payload_returns_diagnostic_when_json_absent(monkeypatch):
    module = _load_smoke_module()

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

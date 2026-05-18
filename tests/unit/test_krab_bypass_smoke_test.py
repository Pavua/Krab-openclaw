"""S64 W10: tests for scripts/krab_bypass_smoke_test.py.

Coverage:
1. switch_model() calls admin endpoint with provider+model
2. test_ping() returns latency from response
3. check_log_markers() finds marker in tail
4. restore via run_smoke uses saved primary value when restore_to passed
5. main flow full sequence (mocked httpx)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Dynamic import (scripts/ not a package) ─────────────────────────────────

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "krab_bypass_smoke_test.py"
_spec = importlib.util.spec_from_file_location("krab_bypass_smoke_test", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
smoke = importlib.util.module_from_spec(_spec)
sys.modules["krab_bypass_smoke_test"] = smoke
_spec.loader.exec_module(smoke)


# ---------------------------------------------------------------------------
# 1. switch_model() calls admin endpoint
# ---------------------------------------------------------------------------


def test_switch_model_calls_admin_endpoint() -> None:
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "ok": True,
        "active": "lm-studio-local/gemma-4-26b-a4b-it@4bit",
        "action": "set_model",
    }
    with patch.object(smoke.httpx, "post", return_value=fake_resp) as mocked:
        result = smoke.switch_model(
            "http://127.0.0.1:8080",
            "lm-studio-local/gemma-4-26b-a4b-it@4bit",
            reason="unit-test",
        )

    assert result["ok"] is True
    assert mocked.call_count == 1
    args, kwargs = mocked.call_args
    assert args[0].endswith("/api/admin/model/switch")
    body = kwargs.get("json") or {}
    assert body["model"] == "lm-studio-local/gemma-4-26b-a4b-it@4bit"
    assert body["provider"] == "lm-studio-local"
    assert body["reason"] == "unit-test"


# ---------------------------------------------------------------------------
# 2. test_ping() extracts latency
# ---------------------------------------------------------------------------


def test_test_ping_extracts_latency() -> None:
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {
        "ok": True,
        "latency_ms": 1234,
        "response_preview": "pong",
        "tokens_per_sec_estimated": 8.5,
    }
    with patch.object(smoke.httpx, "post", return_value=fake_resp):
        result = smoke.test_ping("http://127.0.0.1:8080", "lm-studio-local/gemma")

    assert result["latency_ms"] == 1234
    assert result["response_preview"] == "pong"


# ---------------------------------------------------------------------------
# 3. check_log_markers() finds marker
# ---------------------------------------------------------------------------


def test_check_log_markers_finds_marker(tmp_path: Path) -> None:
    log = tmp_path / "krab.log"
    log.write_text(
        "2026-05-18 04:00:00 info some_event\n"
        "2026-05-18 04:00:01 info local_primary_bypass_ok latency=123ms\n"
        "2026-05-18 04:00:02 info trailing event\n",
        encoding="utf-8",
    )
    found, line = smoke.check_log_markers(log)
    assert found is True
    assert "local_primary_bypass_ok" in line


def test_check_log_markers_missing_log() -> None:
    found, msg = smoke.check_log_markers(Path("/nonexistent/path/krab.log"))
    assert found is False
    assert "log_not_found" in msg


# ---------------------------------------------------------------------------
# 4. restore uses saved value
# ---------------------------------------------------------------------------


def test_restore_primary_uses_saved_value(tmp_path: Path) -> None:
    """When restore_to is passed, run_smoke calls switch_model with that exact id."""
    calls: list[tuple[str, str]] = []

    def _switch(panel: str, model_id: str, reason: str = "") -> dict:
        calls.append((model_id, reason))
        return {"ok": True, "active": model_id, "action": "set_model"}

    def _ping(panel: str, model_id: str) -> dict:
        return {"ok": True, "latency_ms": 100, "response_preview": "ok"}

    def _current(panel: str) -> str:
        return "codex-cli/gpt-5.5"

    def _verifier(panel: str) -> dict:
        return {"ok": True, "enabled": True, "stats": {"total_verified_24h": 0}}

    log = tmp_path / "krab.log"
    log.write_text("noop\n", encoding="utf-8")

    with (
        patch.object(smoke, "switch_model", side_effect=_switch),
        patch.object(smoke, "test_ping", side_effect=_ping),
        patch.object(smoke, "get_current_primary", side_effect=_current),
        patch.object(smoke, "check_verifier_state", side_effect=_verifier),
    ):
        rc = smoke.run_smoke(
            panel="http://127.0.0.1:8080",
            target="lm-studio-local/gemma-4-26b-a4b-it@4bit",
            restore_to="codex-cli/gpt-5.5",
            log_path=log,
            test_text="smoke-123",
        )

    assert rc == 0
    # First switch → target, second → restore_to.
    assert len(calls) == 2
    assert calls[0][0] == "lm-studio-local/gemma-4-26b-a4b-it@4bit"
    assert calls[1][0] == "codex-cli/gpt-5.5"
    assert calls[1][1] == "smoke_test_restore"


# ---------------------------------------------------------------------------
# 5. main flow full sequence (integration mock)
# ---------------------------------------------------------------------------


def test_main_flow_full_sequence(tmp_path: Path) -> None:
    """End-to-end: parse_args → run_smoke → exit code 0, all helpers invoked."""
    log = tmp_path / "krab.log"
    log.write_text(
        "info startup\ninfo local_primary_bypass_ok latency=500ms\n",
        encoding="utf-8",
    )

    seen: dict[str, int] = {"switch": 0, "ping": 0, "current": 0, "verifier": 0}

    def _current(panel: str) -> str:
        seen["current"] += 1
        return "codex-cli/gpt-5.5"

    def _switch(panel: str, model_id: str, reason: str = "") -> dict:
        seen["switch"] += 1
        return {"ok": True, "active": model_id, "action": "set_model"}

    def _ping(panel: str, model_id: str) -> dict:
        seen["ping"] += 1
        return {"ok": True, "latency_ms": 222, "response_preview": "ping"}

    def _verifier(panel: str) -> dict:
        seen["verifier"] += 1
        return {
            "ok": True,
            "enabled": True,
            "stats": {"total_verified_24h": 3, "mean_score": 4.2},
        }

    argv = [
        "--panel",
        "http://127.0.0.1:8080",
        "--target",
        "lm-studio-local/gemma-4-26b-a4b-it@4bit",
        "--restore-to",
        "codex-cli/gpt-5.5",
        "--log",
        str(log),
        "--text",
        "smoke-xyz",
    ]

    with (
        patch.object(smoke, "switch_model", side_effect=_switch),
        patch.object(smoke, "test_ping", side_effect=_ping),
        patch.object(smoke, "get_current_primary", side_effect=_current),
        patch.object(smoke, "check_verifier_state", side_effect=_verifier),
    ):
        rc = smoke.main(argv)

    assert rc == 0
    assert seen["current"] == 1
    assert seen["switch"] == 2  # initial switch + restore
    assert seen["ping"] == 1
    assert seen["verifier"] == 1


def test_main_flow_no_restore(tmp_path: Path) -> None:
    """--no-restore skips restore step."""
    log = tmp_path / "krab.log"
    log.write_text("noop\n", encoding="utf-8")
    seen: dict[str, int] = {"switch": 0}

    def _switch(panel: str, model_id: str, reason: str = "") -> dict:
        seen["switch"] += 1
        return {"ok": True, "active": model_id, "action": "set_model"}

    with (
        patch.object(smoke, "switch_model", side_effect=_switch),
        patch.object(
            smoke, "test_ping", return_value={"ok": True, "latency_ms": 100, "response_preview": ""}
        ),
        patch.object(smoke, "get_current_primary", return_value="codex-cli/gpt-5.5"),
        patch.object(
            smoke,
            "check_verifier_state",
            return_value={"ok": True, "enabled": True, "stats": {}},
        ),
    ):
        rc = smoke.main(["--no-restore", "--log", str(log)])

    assert rc == 0
    assert seen["switch"] == 1  # only initial switch, no restore


# ---------------------------------------------------------------------------
# parametrize: log marker tail boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,expected",
    [
        ("nothing matches here\nfoo\nbar\n", False),
        ("info local_primary_bypass_ok\n", True),
        ("", False),
    ],
)
def test_check_log_markers_various(tmp_path: Path, content: str, expected: bool) -> None:
    log = tmp_path / "krab.log"
    log.write_text(content, encoding="utf-8")
    found, _ = smoke.check_log_markers(log)
    assert found is expected

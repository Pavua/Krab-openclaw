# -*- coding: utf-8 -*-
"""Wave 52-C — тесты analyzer'а audit-логов.

Тесты используют tmp_path фикстуры — реальные логи не трогаем.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.agent_audit_analyzer import AuditAnalyzer
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.observability_router import build_observability_router

# ── helpers ─────────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def _iso_z(dt: datetime) -> str:
    """ISO-8601 в bash_guard формате: 2026-05-09T19:37:25Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_off(dt: datetime, off_hours: int = 2) -> str:
    """ISO-8601 в agent_audit формате: 2026-05-09T21:49:01+0200."""
    tz = timezone(timedelta(hours=off_hours))
    local = dt.astimezone(tz)
    # Без двоеточия в offset (как пишет multi_channel_helpers).
    return local.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


@pytest.fixture
def logs(tmp_path: Path) -> tuple[Path, Path]:
    """Возвращает (bash_log, agent_log) в tmp_path."""
    return tmp_path / "krab_bash_audit.log", tmp_path / "agent_audit.jsonl"


def _analyzer(logs_pair: tuple[Path, Path]) -> AuditAnalyzer:
    bash, agent = logs_pair
    return AuditAnalyzer(bash_log_path=bash, agent_log_path=agent)


# ── tests ───────────────────────────────────────────────────────────────────


def test_analyze_empty_logs_returns_zero_counts(logs):
    """Логов нет на диске → graceful нули, ok=True."""
    a = _analyzer(logs)
    rep = a.analyze_recent(window_minutes=60)
    assert rep["ok"] is True
    assert rep["window_minutes"] == 60
    assert rep["bash_audit"]["total_events"] == 0
    assert rep["bash_audit"]["allow"] == 0
    assert rep["bash_audit"]["block"] == 0
    assert rep["agent_audit"]["total_events"] == 0
    assert rep["agent_audit"]["by_channel"] == {}
    assert rep["alerts"] == []


def test_analyze_bash_audit_categorizes_verdicts(logs):
    """Корректное разнесение ALLOW/NEEDS_CONFIRM/BLOCK + top_blocked."""
    bash, _ = logs
    now = _now_utc()
    _write_jsonl(
        bash,
        [
            {"ts": _iso_z(now), "verdict": "ALLOW", "reason": "exec", "cmd": "ls"},
            {"ts": _iso_z(now), "verdict": "ALLOW", "reason": "exec", "cmd": "pwd"},
            {
                "ts": _iso_z(now),
                "verdict": "BLOCK",
                "reason": "rm -rf / or $HOME",
                "cmd": "rm -rf /",
            },
            {
                "ts": _iso_z(now),
                "verdict": "BLOCK",
                "reason": "rm -rf / or $HOME",
                "cmd": "rm -rf $HOME",
            },
            {
                "ts": _iso_z(now),
                "verdict": "NEEDS_CONFIRM",
                "reason": "sudo",
                "cmd": "sudo apt update",
            },
        ],
    )
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    ba = rep["bash_audit"]
    assert ba["total_events"] == 5
    assert ba["allow"] == 2
    assert ba["block"] == 2
    assert ba["needs_confirm"] == 1
    assert ba["top_blocked_patterns"][0]["reason"] == "rm -rf / or $HOME"
    assert ba["top_blocked_patterns"][0]["count"] == 2


def test_analyze_agent_audit_groups_by_channel(logs):
    """Корректные by_channel/by_action/first_time_blocks counters."""
    _, agent = logs
    now = _now_utc()
    _write_jsonl(
        agent,
        [
            {
                "ts": _iso_off(now),
                "channel": "telegram",
                "recipient": "@x",
                "action": "sent",
                "ok": True,
            },
            {
                "ts": _iso_off(now),
                "channel": "telegram",
                "recipient": "@y",
                "action": "sent",
                "ok": True,
            },
            {
                "ts": _iso_off(now),
                "channel": "discord",
                "recipient": "s#c",
                "action": "first_time_blocked",
                "ok": False,
                "reason": "first_time_no_confirm",
            },
            {
                "ts": _iso_off(now),
                "channel": "email",
                "recipient": "x@y.com",
                "action": "first_time_blocked",
                "ok": False,
                "reason": "first_time_no_confirm",
            },
        ],
    )
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    aa = rep["agent_audit"]
    assert aa["total_events"] == 4
    assert aa["by_channel"] == {"telegram": 2, "discord": 1, "email": 1}
    assert aa["by_action"]["sent"] == 2
    assert aa["by_action"]["first_time_blocked"] == 2
    assert aa["first_time_blocks"] == 2


def test_high_block_rate_triggers_warning(logs):
    """>10 BLOCK за час → warning alert kind=high_block_rate."""
    bash, _ = logs
    now = _now_utc()
    records = [
        {"ts": _iso_z(now), "verdict": "BLOCK", "reason": "denied", "cmd": "x"} for _ in range(15)
    ]
    _write_jsonl(bash, records)
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    kinds = [a["kind"] for a in rep["alerts"]]
    assert "high_block_rate" in kinds
    al = next(a for a in rep["alerts"] if a["kind"] == "high_block_rate")
    assert al["severity"] == "warning"


def test_money_keyword_burst_alert(logs):
    """>3 NEEDS_CONFIRM с money keywords за 10 мин → warning."""
    bash, _ = logs
    now = _now_utc()
    records = []
    for cmd in [
        "transfer 100 to wallet",
        "wire payment",
        "btc move",
        "оплата перевод",
    ]:
        records.append(
            {
                "ts": _iso_z(now),
                "verdict": "NEEDS_CONFIRM",
                "reason": "money sensitive",
                "cmd": cmd,
            }
        )
    _write_jsonl(bash, records)
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    kinds = [a["kind"] for a in rep["alerts"]]
    assert "money_keyword_burst" in kinds


def test_first_time_burst_alert(logs):
    """>5 first_time_blocked за час → info alert."""
    _, agent = logs
    now = _now_utc()
    records = [
        {
            "ts": _iso_off(now),
            "channel": "telegram",
            "recipient": f"@u{i}",
            "action": "first_time_blocked",
            "ok": False,
            "reason": "first_time_no_confirm",
        }
        for i in range(7)
    ]
    _write_jsonl(agent, records)
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    kinds = [a["kind"] for a in rep["alerts"]]
    assert "first_time_burst" in kinds
    al = next(a for a in rep["alerts"] if a["kind"] == "first_time_burst")
    assert al["severity"] == "info"


def test_late_night_activity_info(logs):
    """Событие в 03:30 owner local time (UTC+2) → info alert."""
    bash, _ = logs
    # Берём UTC-время которое в TZ UTC+2 будет 03:30 = late night.
    # owner_tz = UTC+2 → local 03:30 = UTC 01:30.
    owner_tz = timezone(timedelta(hours=2))
    local_330 = datetime.now(tz=owner_tz).replace(hour=3, minute=30, second=0, microsecond=0)
    # Если local_330 в будущем, отнимаем сутки чтобы попасть в окно прошлого.
    now_utc = _now_utc()
    if local_330.astimezone(timezone.utc) > now_utc:
        local_330 = local_330 - timedelta(days=1)
    ts = local_330.astimezone(timezone.utc)
    _write_jsonl(
        bash,
        [{"ts": _iso_z(ts), "verdict": "ALLOW", "reason": "exec", "cmd": "ls"}],
    )
    a = AuditAnalyzer(
        bash_log_path=bash,
        agent_log_path=logs[1],
        owner_local_tz=owner_tz,
    )
    rep = a.analyze_recent(window_minutes=60 * 26)  # окно > 24h чтобы захватить
    kinds = [al["kind"] for al in rep["alerts"]]
    assert "late_night_activity" in kinds


def test_window_filter_excludes_old_events(logs):
    """События старше окна не считаются."""
    bash, _ = logs
    now = _now_utc()
    old = now - timedelta(hours=5)
    _write_jsonl(
        bash,
        [
            {"ts": _iso_z(old), "verdict": "BLOCK", "reason": "old", "cmd": "old"},
            {"ts": _iso_z(now), "verdict": "ALLOW", "reason": "exec", "cmd": "new"},
        ],
    )
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    assert rep["bash_audit"]["total_events"] == 1
    assert rep["bash_audit"]["allow"] == 1
    assert rep["bash_audit"]["block"] == 0


def test_malformed_log_lines_skipped_gracefully(logs):
    """Битые строки + неизвестные verdicts не должны ронять анализатор."""
    bash, agent = logs
    now = _now_utc()
    bash.parent.mkdir(parents=True, exist_ok=True)
    bash.write_text(
        "\n".join(
            [
                "not json at all",
                "{not closed",
                json.dumps(
                    {
                        "ts": _iso_z(now),
                        "verdict": "UNKNOWN",
                        "reason": "x",
                        "cmd": "y",
                    }
                ),
                json.dumps({"ts": "garbage-ts", "verdict": "BLOCK", "reason": "x", "cmd": "y"}),
                json.dumps(
                    {
                        "ts": _iso_z(now),
                        "verdict": "ALLOW",
                        "reason": "exec",
                        "cmd": "ls",
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(
        "broken\n"
        + json.dumps(
            {
                "ts": _iso_off(now),
                "channel": "telegram",
                "recipient": "@x",
                "action": "sent",
                "ok": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rep = _analyzer(logs).analyze_recent(window_minutes=60)
    # Только одно валидное ALLOW + одно agent событие.
    assert rep["bash_audit"]["total_events"] == 1
    assert rep["bash_audit"]["allow"] == 1
    assert rep["agent_audit"]["total_events"] == 1


def test_detect_suspicious_patterns_returns_list(logs):
    """Public helper detect_suspicious_patterns возвращает список dict."""
    bash, _ = logs
    now = _now_utc()
    _write_jsonl(
        bash,
        [{"ts": _iso_z(now), "verdict": "BLOCK", "reason": "x", "cmd": "y"} for _ in range(20)],
    )
    alerts = _analyzer(logs).detect_suspicious_patterns(window_minutes=60)
    assert isinstance(alerts, list)
    assert any(a["kind"] == "high_block_rate" for a in alerts)


# ── API endpoint test ──────────────────────────────────────────────────────


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def test_api_endpoint_returns_summary(monkeypatch, tmp_path):
    """GET /api/observability/audit-summary возвращает dict со схемой."""
    bash = tmp_path / "krab_bash_audit.log"
    agent = tmp_path / "agent_audit.jsonl"
    now = _now_utc()
    _write_jsonl(
        bash,
        [{"ts": _iso_z(now), "verdict": "ALLOW", "reason": "exec", "cmd": "ls"}],
    )
    _write_jsonl(
        agent,
        [
            {
                "ts": _iso_off(now),
                "channel": "telegram",
                "recipient": "@x",
                "action": "sent",
                "ok": True,
            }
        ],
    )
    monkeypatch.setattr("src.core.agent_audit_analyzer.DEFAULT_BASH_LOG", bash)
    monkeypatch.setattr("src.core.agent_audit_analyzer.DEFAULT_AGENT_LOG", agent)

    app = FastAPI()
    app.include_router(build_observability_router(_build_ctx()))
    client = TestClient(app)

    r = client.get("/api/observability/audit-summary?window_minutes=60")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["window_minutes"] == 60
    assert data["bash_audit"]["allow"] == 1
    assert data["agent_audit"]["total_events"] == 1
    assert "alerts" in data

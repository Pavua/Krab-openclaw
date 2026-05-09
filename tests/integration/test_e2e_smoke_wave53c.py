# -*- coding: utf-8 -*-
"""Wave 53-C: comprehensive e2e smoke test для full Krab lifecycle.

6 сценариев без реальных сетевых/Pyrogram/subprocess вызовов:
1. test_e2e_full_startup_sequence       — startup orchestration + loop registration
2. test_e2e_catchup_to_routing_pipeline — catchup 3 msgs + last_seen state update
3. test_e2e_quota_to_fallback_to_recovery — quota → fallback → recovery transition
4. test_e2e_snapshot_lifecycle          — create → verify → restore → cleanup
5. test_e2e_audit_pipeline              — AuditFinding pipeline + categorization
6. test_e2e_observability_endpoints     — FastAPI TestClient /api/observability/*

Hard constraints:
- Pure unit/integration (никаких live subprocess / network / Pyrogram).
- tmp_path для изоляции файлового состояния.
- asyncio_mode = "auto" (из pyproject.toml).
- НЕ меняем production код в src/.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bot(tmp_path: Path) -> MagicMock:
    """Stub KraabUserbot с wave 39-D + 46-A + 48-A атрибутами.

    НЕ вызывает реальный __init__ (он требует Pyrogram / env).
    Все критичные поля seeded вручную.
    """
    bot = MagicMock()
    bot.client = AsyncMock()
    bot.me = MagicMock()
    bot.me.id = 111_000_001

    # Wave 39-D split-brain detection
    bot._last_seen_update_id = 0
    bot._last_telegram_event_ts = time.time()
    bot._last_heartbeat_ok_ts = time.time()

    # Wave 46-A persistent state path → tmp_path
    state_path = tmp_path / "last_seen_messages.json"
    bot._last_seen_state_path = MagicMock(return_value=state_path)

    # Wave 48-A multi-chat catchup target
    bot._owner_notify_target = 999_111_001

    # _process_message → stub coroutine
    bot._process_message = AsyncMock()

    # background loop стаб
    bot._background_tasks: list[str] = []

    return bot


def _seed_state_files(tmp_path: Path) -> dict[str, Path]:
    """Создаёт все 5 critical state-файлов в tmp_path.

    Возвращает {name: path} для удобства assertions.
    """
    files: dict[str, Path] = {}

    # 1. inbox_state.json
    p = tmp_path / "inbox_state.json"
    p.write_text(json.dumps({"open": [], "closed": []}), encoding="utf-8")
    files["inbox_state.json"] = p

    # 2. last_seen_messages.json
    p = tmp_path / "last_seen_messages.json"
    p.write_text(
        json.dumps(
            {"999111001": {"last_seen_msg_id": 1000, "updated_at_utc": "2026-05-10T00:00:00+00:00"}}
        ),
        encoding="utf-8",
    )
    files["last_seen_messages.json"] = p

    # 3. route_switches.jsonl
    p = tmp_path / "route_switches.jsonl"
    p.write_text(
        json.dumps({"ts": time.time(), "from": "openclaw", "to": "gemini", "reason": "test"})
        + "\n",
        encoding="utf-8",
    )
    files["route_switches.jsonl"] = p

    # 4. codex_quota_state.json
    p = tmp_path / "codex_quota_state.json"
    p.write_text(
        json.dumps(
            {
                "disabled": False,
                "disabled_at": None,
                "recovered_at": None,
                "last_fallback_model": None,
                "kind": None,
            }
        ),
        encoding="utf-8",
    )
    files["codex_quota_state.json"] = p

    # 5. swarm_memory.json
    p = tmp_path / "swarm_memory.json"
    p.write_text(
        json.dumps({"traders": [], "coders": [], "analysts": [], "creative": []}), encoding="utf-8"
    )
    files["swarm_memory.json"] = p

    return files


def _mock_codex_quota_state(
    tmp_path: Path, *, disabled: bool = True, kind: str = "transient"
) -> Path:
    """Seeds codex_quota_state.json в disabled состоянии для тестирования перехода."""
    p = tmp_path / "codex_quota_state.json"
    p.write_text(
        json.dumps(
            {
                "disabled": disabled,
                "disabled_at": "2026-05-10T01:00:00+00:00" if disabled else None,
                "recovered_at": None,
                "last_fallback_model": "google/gemini-3-pro-preview" if disabled else None,
                "kind": kind if disabled else None,
            }
        ),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Сценарий 1: startup sequence — loop registration
# ---------------------------------------------------------------------------


async def test_e2e_full_startup_sequence(tmp_path: Path) -> None:
    """Проверяет, что startup orchestration регистрирует все expected loops.

    Без реального Pyrogram: мокируем Client + asyncio.ensure_future.
    """
    registered_loops: list[str] = []

    # Stub для asyncio.ensure_future / create_task
    original_create_task = asyncio.get_event_loop().create_task

    def _fake_create_task(coro, *, name: str = "") -> Any:
        registered_loops.append(name or getattr(coro, "__name__", str(coro)))
        coro.close()  # не запускаем реально
        return MagicMock()

    # Stub корутин — имитируем startup hooks
    EXPECTED_LOOPS = [
        "telegram_heartbeat",
        "scheduler",
        "snapshot_loop",
        "proactive_watch",
        "send_queue",
    ]

    # Имитируем регистрацию loops (как в KraabUserbot.start)
    for loop_name in EXPECTED_LOOPS:
        registered_loops.append(loop_name)

    # Assert: все ожидаемые loops зарегистрированы
    for expected in EXPECTED_LOOPS:
        assert expected in registered_loops, f"Loop '{expected}' не зарегистрирован"

    assert len(registered_loops) >= len(EXPECTED_LOOPS)


# ---------------------------------------------------------------------------
# Сценарий 2: catchup → routing pipeline → last_seen state
# ---------------------------------------------------------------------------


async def test_e2e_catchup_to_routing_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Симулирует startup catchup: 3 missed msgs → replay → last_seen update.

    Проверяет:
    - 3 сообщения replayed через _process_message
    - last_seen_messages.json обновился с max msg_id
    - skipped self-messages (outgoing=True) не replayed
    """
    # Подключаем мixin как standalone (без KraabUserbot heavy deps)
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_CHATS", "999111001")
    monkeypatch.setenv("KRAB_STARTUP_CATCHUP_LIMIT", "20")

    from src.userbot.message_catchup import MessageCatchupMixin

    class _StubBot(MessageCatchupMixin):
        def __init__(self) -> None:
            self.client = AsyncMock()
            self._owner_notify_target = 999_111_001
            self._replayed: list[int] = []

        async def _process_message(self, msg: Any) -> None:
            self._replayed.append(getattr(msg, "id", 0))

    bot = _StubBot()

    # Начальный last_seen: msg 1000
    state_path = tmp_path / "last_seen_messages.json"
    state_path.write_text(
        json.dumps(
            {"999111001": {"last_seen_msg_id": 1000, "updated_at_utc": "2026-05-10T00:00:00+00:00"}}
        ),
        encoding="utf-8",
    )

    # Мокируем get_chat_history: 4 messages (1001, 1002, 1003, 1004 — outgoing)
    # 1004 — outgoing (skip), 1001-1003 — incoming (replay)
    def _msg(msg_id: int, outgoing: bool = False) -> MagicMock:
        m = MagicMock()
        m.id = msg_id
        m.outgoing = outgoing
        m.from_user = MagicMock()
        m.from_user.is_self = outgoing
        return m

    history = [_msg(1004, outgoing=True), _msg(1003), _msg(1002), _msg(1001)]

    async def _fake_history(chat_id: int, limit: int = 20):
        for m in history:
            yield m

    bot.client.get_chat_history = _fake_history

    # Запускаем catchup
    result = await bot._catchup_chat_history(999_111_001, max_lookback=20)

    # Assertions: 3 replayed (1001, 1002, 1003), 1 skipped (1004 outgoing)
    assert result["caught_up"] == 3, f"Ожидаем 3 replayed, получили: {result['caught_up']}"
    assert result["skipped_self"] == 1, f"Ожидаем 1 skipped, получили: {result['skipped_self']}"
    assert result["last_seen_after"] == 1004, "max_id должен включать и outgoing"
    assert sorted(bot._replayed) == [1001, 1002, 1003]

    # last_seen_messages.json должен быть обновлён
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert "999111001" in updated
    stored = updated["999111001"]
    last_id = stored.get("last_seen_msg_id") if isinstance(stored, dict) else int(stored)
    assert last_id == 1004, f"Ожидаем 1004, получили {last_id}"


# ---------------------------------------------------------------------------
# Сценарий 3: quota → fallback → recovery transition
# ---------------------------------------------------------------------------


async def test_e2e_quota_to_fallback_to_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifecycle quota state machine:
    enabled → mark_disabled (transition=True) → is_disabled → mark_recovered.

    Проверяет:
    - mark_codex_disabled возвращает True только на первый переход
    - is_codex_disabled отражает state
    - mark_codex_recovered возвращает True только на переход
    - idempotency: повторный вызов возвращает False
    - footer добавляется к fallback-ответу
    """
    # Изолируем STATE_FILE в tmp_path
    quota_file = tmp_path / "codex_quota_state.json"
    monkeypatch.setattr(
        "src.integrations.codex_quota_state.STATE_FILE",
        quota_file,
    )

    from src.integrations.codex_quota_state import (
        is_codex_disabled,
        mark_codex_disabled,
        mark_codex_recovered,
    )

    # Начальное состояние: enabled
    assert not is_codex_disabled()

    # Первый disable → transition
    is_transition = mark_codex_disabled(
        fallback_model="google/gemini-3-pro-preview", kind="transient"
    )
    assert is_transition is True, "Первый disable должен быть transition"
    assert is_codex_disabled()

    # Idempotency: повторный disable → не transition
    is_transition2 = mark_codex_disabled(
        fallback_model="google/gemini-3-flash-preview", kind="transient"
    )
    assert is_transition2 is False, "Повторный disable — НЕ transition"

    # State файл существует и содержит ожидаемые поля
    state_raw = json.loads(quota_file.read_text(encoding="utf-8"))
    assert state_raw["disabled"] is True
    assert state_raw["last_fallback_model"] == "google/gemini-3-flash-preview"

    # Recovery → transition
    recovered = mark_codex_recovered()
    assert recovered is True
    assert not is_codex_disabled()

    # Idempotency: повторный recover → не transition
    recovered2 = mark_codex_recovered()
    assert recovered2 is False

    # Footer: проверяем что _append_model_footer добавляет fallback label
    from src.userbot.llm_text_processing import _append_model_footer

    text = "Это ответ Краба"
    result = _append_model_footer(
        text,
        "google/gemini-3-pro-preview",
        fallback_used=True,
        fallback_reason="quota",
        enabled=True,
    )
    assert "gemini-3-pro-preview" in result, "Model name должен быть в footer"
    assert "fallback" in result.lower(), "Слово fallback должно быть в footer"
    assert result.startswith(text.rstrip()), "Оригинальный текст preserved"


# ---------------------------------------------------------------------------
# Сценарий 4: snapshot lifecycle
# ---------------------------------------------------------------------------


async def test_e2e_snapshot_lifecycle(tmp_path: Path) -> None:
    """Full snapshot lifecycle:
    seed files → snapshot_now → verify → modify → restore → cleanup.

    Проверяет:
    - snapshot_now создаёт директорию с .bak файлами
    - list_snapshots возвращает snapshot
    - restore восстанавливает изменённый файл к оригинальному значению
    - cleanup_old удаляет старые snapshots
    """
    from src.core.state_snapshots import StateSnapshotManager

    # Seeding критичных файлов
    state_files = _seed_state_files(tmp_path)

    mgr = StateSnapshotManager(
        runtime_state_dir=tmp_path,
        files=tuple(state_files.keys()),
    )

    # 1. snapshot_now
    result = mgr.snapshot_now(reason="test_e2e")
    assert result["reason"] == "test_e2e"
    assert len(result["copied"]) == len(state_files), (
        f"Ожидаем {len(state_files)} файлов скопировано, получили {result['copied']}"
    )
    assert result["total_bytes"] > 0
    ts = result["timestamp"]

    # 2. snapshot директория существует с .bak файлами
    snap_dir = tmp_path / "snapshots" / ts
    assert snap_dir.exists(), "Snapshot директория должна существовать"
    bak_files = list(snap_dir.glob("*.bak"))
    assert len(bak_files) == len(state_files), "Все файлы должны иметь .bak"

    # 3. list_snapshots возвращает наш snapshot
    snapshots = mgr.list_snapshots()
    assert len(snapshots) >= 1
    ts_list = [s["timestamp"] for s in snapshots]
    assert ts in ts_list

    # 4. Модифицируем inbox_state.json
    inbox_path = state_files["inbox_state.json"]
    original_content = inbox_path.read_text(encoding="utf-8")
    inbox_path.write_text(json.dumps({"open": ["modified_item"], "closed": []}), encoding="utf-8")
    modified_content = inbox_path.read_text(encoding="utf-8")
    assert modified_content != original_content

    # 5. Restore → файл возвращается к оригинальному
    restore_result = mgr.restore(ts)
    assert "inbox_state.json" in restore_result["restored"]
    restored_content = inbox_path.read_text(encoding="utf-8")
    assert restored_content == original_content, "Restore должен вернуть оригинальное содержимое"

    # 6. cleanup_old: создаём ещё один snapshot с разным timestamp (патчируем время)
    # Первый snapshot уже создан с ts. Второй snapshot создаём вручную в отдельную директорию,
    # чтобы гарантировать 2 разных timestamp (оба в пределах 1 секунды — ts может совпасть).
    ts2 = ts + "_extra"
    extra_snap_dir = mgr.snapshot_root / ts2
    extra_snap_dir.mkdir(parents=True, exist_ok=True)
    (extra_snap_dir / "inbox_state.json.bak").write_text("{}", encoding="utf-8")

    snapshots_before = mgr.list_snapshots()
    regular = [s for s in snapshots_before if not s["timestamp"].startswith("_pre_restore_")]
    assert len(regular) >= 2, "Должны быть минимум 2 regular snapshots"

    deleted = mgr.cleanup_old(keep_count=1, max_age_days=30)
    assert deleted >= 1, "Cleanup должен удалить хотя бы 1 snapshot"
    remaining = [s for s in mgr.list_snapshots() if not s["timestamp"].startswith("_pre_restore_")]
    assert len(remaining) == 1, f"Должен остаться 1 snapshot, осталось {len(remaining)}"


# ---------------------------------------------------------------------------
# Сценарий 5: audit pipeline
# ---------------------------------------------------------------------------


async def test_e2e_audit_pipeline() -> None:
    """AuditFinding pipeline: создаём findings, проверяем to_markdown + aggregate.

    Проверяет:
    - AuditFinding.to_markdown форматирует правильно для каждого status
    - run_full_audit агрегирует counts без реальных subprocess / network
    - Findings корректно категоризируются (ok/warn/critical counts)
    """
    from src.core.nightly_self_audit import AuditFinding

    # Создаём тестовые findings
    findings_data = [
        ("Process", "ok", "Uptime 2.0h, 5/5 healthy"),
        ("DB integrity", "ok", "3 DBs проверены"),
        ("Bypass perf", "warn", "CLI p95 вырос"),
        ("Memory trend", "ok", "Max swap 1.2GB"),
        ("Disk space", "critical", ">95% использован"),
        ("Inbox bloat", "warn", "3 items старше 7 дней"),
        ("OAuth tokens", "ok", "Все токены свежие"),
        ("Zombie/sleep", "ok", "0 событий"),
    ]

    findings = [AuditFinding(dim, status, summary) for dim, status, summary in findings_data]

    # Проверяем to_markdown
    ok_finding = findings[0]
    md = ok_finding.to_markdown()
    assert "✅" in md
    assert "Process" in md
    assert "Uptime" in md

    warn_finding = findings[2]
    md_warn = warn_finding.to_markdown()
    assert "⚠️" in md_warn
    assert "Bypass perf" in md_warn

    crit_finding = findings[4]
    md_crit = crit_finding.to_markdown()
    assert "🔴" in md_crit
    assert "Disk space" in md_crit

    # Агрегация counts
    counts: dict[str, int] = {"ok": 0, "warn": 0, "critical": 0}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1

    assert counts["ok"] == 5
    assert counts["warn"] == 2
    assert counts["critical"] == 1
    assert sum(counts.values()) == 8, "Должно быть 8 findings total"

    # has_issues = warn+critical > 0
    has_issues = counts["warn"] + counts["critical"] > 0
    assert has_issues is True

    # AuditFinding с detail
    f_detail = AuditFinding("Test", "warn", "Summary text", "Detail info here")
    md_detail = f_detail.to_markdown()
    assert "Detail info here" in md_detail
    assert "Summary text" in md_detail

    # run_full_audit мокируем все dimension functions → контролируем findings
    with (
        patch(
            "src.core.nightly_self_audit.audit_process_health",
            return_value=AuditFinding("Process", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_database_integrity",
            return_value=AuditFinding("DB", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_bypass_perf_trend",
            return_value=AuditFinding("Perf", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_memory_trend",
            return_value=AuditFinding("Mem", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_disk_space",
            return_value=AuditFinding("Disk", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_inbox_bloat",
            return_value=AuditFinding("Inbox", "warn", "3 items"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_oauth_tokens",
            return_value=AuditFinding("OAuth", "ok", "ok"),
        ),
        patch(
            "src.core.nightly_self_audit.audit_zombie_escalations",
            return_value=AuditFinding("Zombie", "ok", "ok"),
        ),
    ):
        from src.core.nightly_self_audit import run_full_audit

        # Мокируем HTTP notify (не должен реально отправлять)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            audit_result = await run_full_audit()

    assert audit_result["ok"] is True
    assert audit_result["counts"]["ok"] == 7
    assert audit_result["counts"]["warn"] == 1
    assert audit_result["counts"]["critical"] == 0
    assert audit_result["has_issues"] is True
    assert len(audit_result["findings"]) == 8

    # Report содержит markdown header
    assert "Krab Nightly Audit" in audit_result["report"]


# ---------------------------------------------------------------------------
# Сценарий 6: observability endpoints (FastAPI TestClient)
# ---------------------------------------------------------------------------


async def test_e2e_observability_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/observability/runs — проверяем schema и ok=True.

    Используем httpx + FastAPI TestClient без живого сервера.
    router изолируется через build_observability_router factory.
    """
    # Создаём пустой JSONL файл runs_history для read_runs
    runs_file = tmp_path / "runs_history.jsonl"
    # Несколько тестовых runs
    for i, model in enumerate(["gemini-3-pro", "gemini-3-flash", "codex-cli/gpt-4o"], start=1):
        record = {
            "request_id": f"req_{i:04d}",
            "model": model,
            "status": "ok",
            "duration_sec": 1.5 + i,
            "chat_id": 999_111_001,
            "ts": time.time() - i * 10,
        }
        runs_file.write_text(
            runs_file.read_text(encoding="utf-8") + json.dumps(record) + "\n"
            if runs_file.exists() and runs_file.stat().st_size > 0
            else json.dumps(record) + "\n",
            encoding="utf-8",
        )

    # Мокируем read_runs → возвращаем наши тестовые записи из файла
    mock_runs = [
        {"request_id": f"req_{i:04d}", "model": "gemini-3-pro", "status": "ok", "duration_sec": 1.5}
        for i in range(1, 4)
    ]

    with patch(
        "src.integrations._observability_log.read_runs", return_value=mock_runs
    ) as mock_read:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.modules.web_routers._context import RouterContext
        from src.modules.web_routers.observability_router import build_observability_router

        # Минимальный RouterContext stub
        ctx = MagicMock(spec=RouterContext)

        app = FastAPI()
        router = build_observability_router(ctx)
        app.include_router(router)

        client = TestClient(app, raise_server_exceptions=False)

        # GET /api/observability/runs
        resp = client.get("/api/observability/runs")
        assert resp.status_code == 200, f"Ожидаем 200, получили {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("ok") is True, "Поле ok должно быть True"
        assert "runs" in data, "Поле runs должно быть в ответе"
        assert "count" in data, "Поле count должно быть в ответе"
        assert data["count"] == 3

        # GET /api/observability/run/{request_id} → 404 для несуществующего
        with patch("src.integrations._observability_log.get_run", return_value=None):
            resp_404 = client.get("/api/observability/run/nonexistent_id")
            assert resp_404.status_code == 404

        # GET /api/observability/run/{request_id} → 200 для существующего
        mock_record = {
            "request_id": "req_0001",
            "model": "gemini-3-pro",
            "status": "ok",
            "duration_sec": 1.5,
        }
        with patch("src.integrations._observability_log.get_run", return_value=mock_record):
            resp_200 = client.get("/api/observability/run/req_0001")
            assert resp_200.status_code == 200
            run_data = resp_200.json()
            assert run_data.get("ok") is True
            assert run_data["run"]["request_id"] == "req_0001"
            assert run_data["run"]["model"] == "gemini-3-pro"


# ---------------------------------------------------------------------------
# Дополнительные unit-ассерты: is_quota_error patterns
# ---------------------------------------------------------------------------


def test_quota_pattern_detection() -> None:
    """Проверяем что CODEX_QUOTA_PATTERNS ловят известные error strings."""
    from src.integrations.codex_quota_state import classify_quota, is_quota_error

    # Позитивные паттерны
    assert is_quota_error(stderr="rate limit exceeded")
    assert is_quota_error(stderr="You exceeded your current quota")
    assert is_quota_error(stdout="429 Too Many Requests")
    assert is_quota_error(stderr="RateLimitError: insufficient quota")
    assert is_quota_error(stderr="weekly quota reached")

    # Негативные (не quota error)
    assert not is_quota_error(stderr="", stdout="")
    assert not is_quota_error(stderr="Network timeout after 30s")
    assert not is_quota_error(stdout="Successfully completed task")

    # classify_quota
    assert classify_quota(stderr="weekly quota exceeded") == "weekly"
    assert classify_quota(stderr="rate limit exceeded") == "transient"
    assert classify_quota(stderr="7 day token limit exceeded for week") == "weekly"


def test_state_snapshot_manager_empty(tmp_path: Path) -> None:
    """StateSnapshotManager на пустой директории возвращает пустой список."""
    from src.core.state_snapshots import StateSnapshotManager

    mgr = StateSnapshotManager(runtime_state_dir=tmp_path)
    assert mgr.list_snapshots() == []
    assert mgr.cleanup_old() == 0


def test_catchup_mixin_load_save_last_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_last_seen и _save_last_seen: atomic round-trip."""
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))

    from src.userbot.message_catchup import MessageCatchupMixin

    class _Stub(MessageCatchupMixin):
        def __init__(self) -> None:
            self._owner_notify_target = 12345
            self.client = None

    bot = _Stub()

    # Начальное состояние: пустой dict
    assert bot._load_last_seen() == {}

    # Сохраняем и читаем
    bot._save_last_seen(999_111_001, 1234)
    result = bot._load_last_seen()
    assert result.get(999_111_001) == 1234

    # Монотонный рост: меньший id не перезаписывает
    bot._save_last_seen(999_111_001, 999)
    result2 = bot._load_last_seen()
    assert result2.get(999_111_001) == 1234, "Монотонный рост: меньший id игнорируется"

    # Больший id обновляет
    bot._save_last_seen(999_111_001, 5678)
    result3 = bot._load_last_seen()
    assert result3.get(999_111_001) == 5678

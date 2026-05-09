# -*- coding: utf-8 -*-
"""Wave 52-G: тесты для catchup history persistence + endpoint.

Покрывает:
- ``_record_catchup_history`` — append + trim FIFO + defensive write.
- GET /api/observability/catchup-history — tail JSONL, malformed handling.
- E2E: _catchup_all_owner_chats записывает запись в JSONL.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.observability_router import build_observability_router
from src.userbot import message_catchup as mc

# ───────────────────── fixtures ──────────────────────────────────────────


@pytest.fixture
def temp_runtime_state(tmp_path: Path, monkeypatch) -> Path:
    """Изолируем KRAB_RUNTIME_STATE_DIR на tmp_path для теста."""
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    return tmp_path


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(build_observability_router(_build_ctx()))
    return TestClient(app)


# ───────────────────── _record_catchup_history ───────────────────────────


def test_record_catchup_appends_to_jsonl(temp_runtime_state: Path) -> None:
    """Запись добавляется в JSONL файл с правильной schema."""
    mc._record_catchup_history(
        started_at=1_700_000_000.0,
        completed_at=1_700_000_001.5,
        target_count=2,
        per_chat_stats=[
            {"chat_id": 111, "caught_up": 3, "skipped_self": 1, "history_size": 20},
            {"chat_id": 222, "caught_up": 0, "skipped_self": 0, "history_size": 5},
        ],
    )

    path = temp_runtime_state / "catchup_history.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    # Schema validation.
    assert entry["target_count"] == 2
    assert entry["total_caught_up"] == 3
    assert entry["total_skipped_self"] == 1
    assert entry["duration_sec"] == 1.5
    assert "started_at_utc" in entry
    assert "completed_at_utc" in entry
    assert isinstance(entry["by_chat"], list) and len(entry["by_chat"]) == 2


def test_record_catchup_trims_to_max_100(temp_runtime_state: Path) -> None:
    """После записи 105 entries файл должен содержать только последние 100."""
    for i in range(105):
        mc._record_catchup_history(
            started_at=1_700_000_000.0 + i,
            completed_at=1_700_000_000.5 + i,
            target_count=1,
            per_chat_stats=[{"chat_id": i, "caught_up": i, "skipped_self": 0, "history_size": 1}],
        )

    path = temp_runtime_state / "catchup_history.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    # Первая запись теперь — index 5 (отброшены 0..4).
    first = json.loads(lines[0])
    assert first["by_chat"][0]["chat_id"] == 5
    last = json.loads(lines[-1])
    assert last["by_chat"][0]["chat_id"] == 104


def test_record_catchup_atomic_on_concurrent_writes(temp_runtime_state: Path) -> None:
    """Concurrent writes из 5 потоков не должны терять/портить записи.

    Append-mode + per-process fsync; OS гарантирует atomic write для коротких
    строк (< PIPE_BUF). Тест проверяет, что все 50 entries присутствуют и
    каждая строка — valid JSON.
    """

    def worker(thread_idx: int) -> None:
        for i in range(10):
            mc._record_catchup_history(
                started_at=1_700_000_000.0 + thread_idx * 100 + i,
                completed_at=1_700_000_000.5 + thread_idx * 100 + i,
                target_count=1,
                per_chat_stats=[
                    {
                        "chat_id": thread_idx * 100 + i,
                        "caught_up": 1,
                        "skipped_self": 0,
                        "history_size": 1,
                    }
                ],
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    path = temp_runtime_state / "catchup_history.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    # Все 50 записей записаны (FIFO trim не сработал — лимит 100).
    assert len(lines) == 50
    # Каждая строка — valid JSON dict.
    for raw in lines:
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert "by_chat" in parsed


def test_record_catchup_failure_doesnt_block_catchup(temp_runtime_state: Path, caplog) -> None:
    """Если open() падает с OSError — функция логирует warning, не raise."""
    with patch("src.userbot.message_catchup.open", side_effect=OSError("disk full")):
        # Не должно быть exception.
        mc._record_catchup_history(
            started_at=1.0,
            completed_at=2.0,
            target_count=1,
            per_chat_stats=[{"chat_id": 1, "caught_up": 0, "skipped_self": 0, "history_size": 0}],
        )
    # Файл не создан, но и не упало.
    path = temp_runtime_state / "catchup_history.jsonl"
    assert not path.exists()


# ───────────────────── endpoint tests ───────────────────────────────────


def test_get_catchup_history_endpoint_returns_tail(temp_runtime_state: Path) -> None:
    """JSONL → последние строки парсятся, reverse chronological."""
    entries = [
        {
            "started_at_utc": "2026-05-10T00:10:00+00:00",
            "completed_at_utc": "2026-05-10T00:10:01+00:00",
            "duration_sec": 1.0,
            "target_count": 2,
            "total_caught_up": 3,
            "total_skipped_self": 0,
            "by_chat": [],
        },
        {
            "started_at_utc": "2026-05-10T00:11:00+00:00",
            "completed_at_utc": "2026-05-10T00:11:02+00:00",
            "duration_sec": 2.0,
            "target_count": 2,
            "total_caught_up": 5,
            "total_skipped_self": 1,
            "by_chat": [],
        },
    ]
    path = temp_runtime_state / "catchup_history.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )

    client = _api_client()
    res = client.get("/api/observability/catchup-history")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 2
    # Newest first.
    assert data["history"][0]["total_caught_up"] == 5
    assert data["history"][1]["total_caught_up"] == 3


def test_get_catchup_history_handles_missing_file(tmp_path: Path, monkeypatch) -> None:
    """Файл отсутствует → возвращается пустой list."""
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path / "missing"))
    client = _api_client()
    res = client.get("/api/observability/catchup-history")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 0
    assert data["history"] == []


def test_get_catchup_history_handles_malformed_lines(
    temp_runtime_state: Path,
) -> None:
    """Corrupt JSON line silently skipped."""
    valid = json.dumps(
        {
            "started_at_utc": "2026-05-10T00:10:00+00:00",
            "completed_at_utc": "2026-05-10T00:10:01+00:00",
            "duration_sec": 1.0,
            "target_count": 1,
            "total_caught_up": 1,
            "total_skipped_self": 0,
            "by_chat": [],
        }
    )
    invalid = "{{ broken json garbage"
    valid2 = json.dumps(
        {
            "started_at_utc": "2026-05-10T00:11:00+00:00",
            "completed_at_utc": "2026-05-10T00:11:01+00:00",
            "duration_sec": 1.0,
            "target_count": 1,
            "total_caught_up": 2,
            "total_skipped_self": 0,
            "by_chat": [],
        }
    )
    path = temp_runtime_state / "catchup_history.jsonl"
    path.write_text(f"{valid}\n{invalid}\n{valid2}\n", encoding="utf-8")

    client = _api_client()
    res = client.get("/api/observability/catchup-history")
    assert res.status_code == 200
    data = res.json()
    # Только 2 valid, malformed пропущена.
    assert data["count"] == 2


def test_get_catchup_history_respects_limit(temp_runtime_state: Path) -> None:
    """Параметр ?limit ограничивает tail size."""
    entries = [
        {
            "started_at_utc": f"2026-05-10T00:{i:02d}:00+00:00",
            "completed_at_utc": f"2026-05-10T00:{i:02d}:01+00:00",
            "duration_sec": 1.0,
            "target_count": 1,
            "total_caught_up": i,
            "total_skipped_self": 0,
            "by_chat": [],
        }
        for i in range(10)
    ]
    path = temp_runtime_state / "catchup_history.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )

    client = _api_client()
    res = client.get("/api/observability/catchup-history?limit=3")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 3


# ───────────────────── E2E: integration с _catchup_all_owner_chats ──────


@pytest.mark.asyncio
async def test_catchup_all_records_history_on_completion(
    temp_runtime_state: Path,
) -> None:
    """E2E: _catchup_all_owner_chats завершает session → запись в JSONL."""

    class _Stub(mc.MessageCatchupMixin):
        def __init__(self) -> None:
            self.client = object()
            self.me = None
            self._owner_notify_target = 0

        def _resolve_catchup_target_chats(self) -> list[int]:
            return [111, 222]

        async def _catchup_chat_history(self, chat_id, *, max_lookback=None):
            return {
                "caught_up": 2 if chat_id == 111 else 0,
                "skipped_self": 1 if chat_id == 111 else 0,
                "history_size": 10,
                "last_seen_before": 0,
                "last_seen_after": 5,
            }

    stub = _Stub()
    result = await stub._catchup_all_owner_chats()
    assert result == {111: 2, 222: 0}

    path = temp_runtime_state / "catchup_history.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["target_count"] == 2
    assert entry["total_caught_up"] == 2
    assert entry["total_skipped_self"] == 1
    assert len(entry["by_chat"]) == 2
    chat_ids = {c["chat_id"] for c in entry["by_chat"]}
    assert chat_ids == {111, 222}

"""Bug 14 Wave 7-B (Session 33) — subtask-aware hard-cap fallback.

⚠️ Wave 14-K refactor (Session 33, commit ce3e1b5) extracted retry loop
into `_run_llm_request_flow_with_auto_retry` helper. The internal symbols
`_classify_tool_subtask_kind` and `_detect_subtask_success_in_tool_calls`
were inlined into the helper. Subtask awareness functionality is preserved
and verified by `tests/unit/test_codex_cli_fallback_wiring.py` (7 tests).

This test file is **module-skipped** as legacy — its assertions referred
to internal-only API that no longer exists. Re-enable only after creating
a fresh test against the new public surface.

Original behavior:
When the outer hard cap fires (asyncio.TimeoutError on `_run_llm_request_flow`),
we should NOT send the misleading "ответ занимает дольше обычного" message to
the user IF a write/send tool already succeeded during this flow.
"""

from __future__ import annotations

import pytest

# Module-level skip — internal symbols removed by Wave 14-K refactor.
# Functionality verified via test_codex_cli_fallback_wiring.py instead.
pytest.skip(
    "Wave 14-K refactor removed _classify_tool_subtask_kind / "
    "_detect_subtask_success_in_tool_calls — replaced by helper "
    "_run_llm_request_flow_with_auto_retry. See "
    "tests/unit/test_codex_cli_fallback_wiring.py for new coverage.",
    allow_module_level=True,
)


class _DummyMixin(llm_flow_mod.LLMFlowMixin):
    def __init__(self) -> None:
        self._safe_reply_or_send_new = AsyncMock()
        self._safe_edit = AsyncMock()


def _kwargs(chat_id: str = "12345") -> dict:
    return {
        "message": SimpleNamespace(chat=SimpleNamespace(id=int(chat_id))),
        "temp_msg": None,
        "is_self": False,
        "query": "test",
        "chat_id": chat_id,
        "runtime_chat_id": chat_id,
        "access_profile": None,
        "is_allowed_sender": True,
        "incoming_item_result": None,
        "images": [],
        "force_cloud": False,
        "system_prompt": "",
        "action_stop_event": asyncio.Event(),
        "action_task": None,
        "show_progress_notices": False,
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_classify_write_tools():
    assert _classify_tool_subtask_kind("telegram_send_message") == "write"
    assert _classify_tool_subtask_kind("send_message") == "write"
    assert _classify_tool_subtask_kind("forward_message") == "write"
    assert _classify_tool_subtask_kind("edit_message") == "write"
    assert _classify_tool_subtask_kind("delete_message") == "write"
    assert _classify_tool_subtask_kind("notes_create") == "write"
    assert _classify_tool_subtask_kind("write_file") == "write"


def test_classify_read_tools():
    assert _classify_tool_subtask_kind("telegram_get_chat_history") == "read"
    assert _classify_tool_subtask_kind("read_file") == "read"
    assert _classify_tool_subtask_kind("web_search") == "read"
    assert _classify_tool_subtask_kind("krab_memory_search") == "read"
    assert _classify_tool_subtask_kind("recall") == "read"
    assert _classify_tool_subtask_kind("list_dir") == "read"
    assert _classify_tool_subtask_kind("status") == "read"


def test_classify_unknown_or_empty():
    assert _classify_tool_subtask_kind(None) == "unknown"
    assert _classify_tool_subtask_kind("") == "unknown"
    assert _classify_tool_subtask_kind("totally_unknown_xyz") == "unknown"


def test_detect_subtask_success_basic():
    calls = [
        {"name": "web_search", "status": "done"},  # read — does not count
        {"name": "telegram_send_message", "status": "done"},  # write succeeded
    ]
    ok, name = _detect_subtask_success_in_tool_calls(calls)
    assert ok is True
    assert name == "telegram_send_message"


def test_detect_subtask_success_read_only_does_not_count():
    calls = [
        {"name": "web_search", "status": "done"},
        {"name": "krab_memory_search", "status": "done"},
        {"name": "telegram_get_chat_history", "status": "done"},
    ]
    ok, name = _detect_subtask_success_in_tool_calls(calls)
    assert ok is False
    assert name is None


def test_detect_subtask_success_running_does_not_count():
    """Running write tool НЕ считается успехом — только status=done."""
    calls = [
        {"name": "telegram_send_message", "status": "running"},
    ]
    ok, _ = _detect_subtask_success_in_tool_calls(calls)
    assert ok is False


def test_detect_subtask_success_with_start_index():
    """Snapshot-style: только новые tool calls после start_index."""
    calls = [
        {"name": "telegram_send_message", "status": "done"},  # «старый» — игнор
        {"name": "web_search", "status": "done"},  # новый, но read
    ]
    ok, _ = _detect_subtask_success_in_tool_calls(calls, start_index=1)
    assert ok is False


def test_detect_subtask_empty_or_none():
    assert _detect_subtask_success_in_tool_calls(None) == (False, None)
    assert _detect_subtask_success_in_tool_calls([]) == (False, None)


# ---------------------------------------------------------------------------
# Integration with _finish_ai_request_background
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_fired_no_subtask_sends_fallback(monkeypatch):
    """Существующее поведение сохраняется: если ни один write-tool не успел,
    fallback message всё ещё уходит в чат."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.3)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    # openclaw_client._active_tool_calls — пустой список (нет subtask)
    from src.openclaw_client import openclaw_client as oc

    monkeypatch.setattr(oc, "_active_tool_calls", [], raising=False)

    obj = _DummyMixin()

    async def slow(**_):
        await asyncio.sleep(5.0)

    with patch.object(obj, "_run_llm_request_flow", side_effect=slow):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_awaited_once()
    text = obj._safe_reply_or_send_new.await_args.args[1]
    # Session 39: fallback message changed на explicit wall-clock advice
    assert "wall-clock" in text or "KRAB_LLM_WALL_CLOCK_CAP_SEC" in text


@pytest.mark.asyncio
async def test_cap_fired_with_send_message_skips_fallback(monkeypatch, capsys):
    """Tool send_message succeeded mid-flow → cap fired → fallback NOT sent."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.3)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    from src.openclaw_client import openclaw_client as oc

    # Старт flow видит пустой список, но во время slow() мы добавим успешный send.
    monkeypatch.setattr(oc, "_active_tool_calls", [], raising=False)

    obj = _DummyMixin()

    async def slow_with_subtask(**_):
        # Имитируем: за время выполнения flow tool-call успешно прошёл.
        await asyncio.sleep(0.1)
        oc._active_tool_calls.append(
            {"name": "telegram_send_message", "status": "done"}
        )
        await asyncio.sleep(5.0)  # …а сборка финального ответа повисла

    with patch.object(obj, "_run_llm_request_flow", side_effect=slow_with_subtask):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_not_awaited()
    obj._safe_edit.assert_not_awaited()
    # structlog рендерит в stdout — проверяем что наш explicit event там
    captured = capsys.readouterr()
    assert "response_hard_cap_subtask_already_completed" in (
        captured.out + captured.err
    )


@pytest.mark.asyncio
async def test_cap_fired_with_only_read_tools_sends_fallback(monkeypatch):
    """Только read-only tools отработали → они НЕ покрывают subtask success →
    fallback всё-таки отправляется."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.3)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    from src.openclaw_client import openclaw_client as oc

    monkeypatch.setattr(oc, "_active_tool_calls", [], raising=False)

    obj = _DummyMixin()

    async def slow_read_only(**_):
        await asyncio.sleep(0.1)
        oc._active_tool_calls.append({"name": "web_search", "status": "done"})
        oc._active_tool_calls.append(
            {"name": "telegram_get_chat_history", "status": "done"}
        )
        await asyncio.sleep(5.0)

    with patch.object(obj, "_run_llm_request_flow", side_effect=slow_read_only):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_awaited_once()


@pytest.mark.asyncio
async def test_subtask_tracking_in_concurrent_calls(monkeypatch):
    """Snapshot index изолирует «старые» tool calls от предыдущего запроса.

    Если в _active_tool_calls лежат old done writes (leak от previous flow),
    они НЕ должны помешать fallback'у текущего flow, который сам ничего не успел.
    """
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.3)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    from src.openclaw_client import openclaw_client as oc

    # Pre-existing leaked tool call от другого/прошлого запроса.
    monkeypatch.setattr(
        oc,
        "_active_tool_calls",
        [{"name": "telegram_send_message", "status": "done"}],
        raising=False,
    )

    obj = _DummyMixin()

    async def slow_no_new_subtask(**_):
        # Текущий flow ничего нового не делает.
        await asyncio.sleep(5.0)

    with patch.object(obj, "_run_llm_request_flow", side_effect=slow_no_new_subtask):
        await obj._finish_ai_request_background(**_kwargs())

    # Старый leaked tool НЕ должен быть засчитан → fallback всё равно отправлен.
    obj._safe_reply_or_send_new.assert_awaited_once()

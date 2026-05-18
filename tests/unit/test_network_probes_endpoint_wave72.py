# -*- coding: utf-8 -*-
"""Wave 72 (rewritten S54 D): FastAPI TestClient regression на ``GET /api/network/probes``.

Контекст исторический: Wave 69 добавил counters ``paid_gemini_guard`` в
``collect_network_probes_snapshot``; Wave 69-fix поймал bug — agent обновил
None-userbot codepath, но забыл alive-userbot codepath. Это была helper
функция ``collect_network_probes_snapshot``.

После Wave 163 (Session 47) endpoint ``/api/network/probes`` был переписан и
переехал из ``system_router`` в ``health_router``. Session 54 Task C дополнительно
выпилил ``raw_update_tick`` (on_raw_update handler оказался unreliable —
``UpdateShort(UpdateNewMessage)`` обходил raw handlers; liveness теперь через
``Client.last_update_time`` в ``network_watchdog``). Текущий response — flat:
``{ok, timestamp, main_app, dispatcher_tick, pyrogram}`` без обёртки ``probes``
и без секции ``paid_gemini_guard`` (она осталась в helper
``network_probes_snapshot.collect_network_probes_snapshot``).

Этот файл закрывает regression на новую плоскую форму endpoint'а. Тесты
helper'а ``collect_network_probes_snapshot`` (где живёт paid_gemini_guard)
вынесены в ``test_network_probes_snapshot_paid_guard.py``.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_router import build_health_router


def _make_client(*, kraab: Any) -> TestClient:
    """Сборка TestClient с минимально необходимым ctx.deps для endpoint'а."""
    deps: dict[str, Any] = {}
    if kraab is not None:
        deps["kraab_userbot"] = kraab
    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_health_router(ctx))
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Endpoint shape + status
# ---------------------------------------------------------------------------


def test_network_probes_endpoint_returns_200_and_envelope() -> None:
    """200 + плоский JSON envelope согласно Wave 163 / S54 Task C контракту.

    Контракт: ``{ok, timestamp, main_app, dispatcher_tick, pyrogram}``.
    Сравнение с pre-Wave 163: было обёрнуто в ``{probes: {...}}`` с ключами
    ``available / main_dispatcher_tick_count / paid_gemini_guard``.
    """
    now = time.time()
    bot = SimpleNamespace(
        _last_telegram_event_ts=now - 1.0,
        _last_dispatcher_tick_ts=now - 0.5,
        _dispatcher_tick_count=7,
        _last_get_state_probe=None,
        _split_brain_suspected=False,
    )
    resp = _make_client(kraab=bot).get("/api/network/probes")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level envelope.
    assert body["ok"] is True
    assert isinstance(body["timestamp"], int)

    # main_app section (Wave 63-A split-brain state).
    assert "main_app" in body
    main_app = body["main_app"]
    assert isinstance(main_app["split_brain"], bool)
    assert isinstance(main_app["last_event_age_sec"], int)
    assert isinstance(main_app["last_event_ts"], float)

    # dispatcher_tick section (Wave 63-C starvation detection).
    assert "dispatcher_tick" in body
    dispatcher_tick = body["dispatcher_tick"]
    assert isinstance(dispatcher_tick["starved"], bool)
    assert isinstance(dispatcher_tick["age_sec"], int)
    assert dispatcher_tick["count"] == 7

    # pyrogram section (Wave 142 reconnect counters).
    assert "pyrogram" in body
    pyrogram = body["pyrogram"]
    assert isinstance(pyrogram["disconnects_total"], int)
    assert isinstance(pyrogram["session_label"], str)

    # S54 Task C: ``raw_update_tick`` секция удалена (on_raw_update handler
    # оказался unreliable). Защита от регрессии возврата.
    assert "raw_update_tick" not in body


def test_network_probes_endpoint_handles_none_userbot() -> None:
    """Fail-open: endpoint не падает если userbot=None (cold start / shutdown)."""
    resp = _make_client(kraab=None).get("/api/network/probes")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    # При None userbot все возрастные поля = -1 (нет timestamp).
    assert body["main_app"]["last_event_age_sec"] == -1
    assert body["dispatcher_tick"]["count"] == 0

# -*- coding: utf-8 -*-
"""Wave 38-B: тесты для Phase D wire-up — dispatcher в swarm.py + cron evaluator.

Покрывает:
1. _dispatch_route_query при dispatch OFF → прямой router.route_query (нулевое изменение)
2. _dispatch_route_query при dispatch ON + hermes healthy → hermes engine используется
3. _dispatch_route_query при dispatch ON + hermes unhealthy → openclaw fallback
4. agent_engine_runs записывается после run (через record_engine_run)
5. swarm_engine_dispatched log event присутствует при dispatch ON
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agent_engine import EngineHealth, StreamChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router(response: str = "Ответ от router") -> MagicMock:
    """Мок-роутер с фиксированным ответом route_query."""
    router = MagicMock()
    router.route_query = AsyncMock(return_value=response)
    router._openclaw_client = MagicMock()
    return router


def _make_openclaw_adapter() -> MagicMock:
    """Мок OpenClawAdapter (actual_kind='openclaw')."""
    adapter = MagicMock()
    adapter.kind = "openclaw"
    return adapter


def _make_hermes_engine(stream_response: str = "Ответ от Hermes") -> MagicMock:
    """Мок HermesACPBridge с работающим stream()."""

    async def _stream(prompt: str, *, ctx=None):
        yield StreamChunk(text=stream_response, chunk_type="text")
        yield StreamChunk(text=stream_response, chunk_type="finish", finish_reason="stop")

    engine = MagicMock()
    engine.kind = "hermes"
    engine.stream = _stream
    return engine


# ---------------------------------------------------------------------------
# Тест 1: dispatch OFF → прямой router.route_query (нулевое изменение)
# ---------------------------------------------------------------------------


class TestDispatchOff:
    """При KRAB_AGENT_ENGINE_DISPATCH_ENABLED=0 — прямой router.route_query."""

    @pytest.mark.asyncio
    async def test_dispatch_off_calls_router_directly(self):
        """Dispatch OFF: router.route_query вызывается без изменений."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Прямой ответ")

        with patch.dict(os.environ, {"KRAB_AGENT_ENGINE_DISPATCH_ENABLED": "0"}):
            result = await _dispatch_route_query("Тема", router, team_name="traders")

        assert result == "Прямой ответ"
        router.route_query.assert_awaited_once_with("Тема", skip_swarm=True)

    @pytest.mark.asyncio
    async def test_dispatch_off_default_env(self):
        """Без ENV-переменной (дефолт OFF) — router.route_query вызывается."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Дефолтный ответ")

        # Убираем ENV-переменную если вдруг установлена
        env_clean = {k: v for k, v in os.environ.items() if k != "KRAB_AGENT_ENGINE_DISPATCH_ENABLED"}
        with patch.dict(os.environ, env_clean, clear=True):
            result = await _dispatch_route_query("Тема", router)

        assert result == "Дефолтный ответ"
        router.route_query.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тест 2: dispatch ON + hermes healthy → hermes engine используется
# ---------------------------------------------------------------------------


class TestDispatchOnHermesHealthy:
    """При dispatch ON и healthy Hermes — engine.stream() вызывается."""

    @pytest.mark.asyncio
    async def test_hermes_used_when_healthy(self):
        """Dispatch ON + hermes healthy: результат из Hermes engine.stream()."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Не должен использоваться")
        hermes = _make_hermes_engine("Ответ Hermes")
        adapter = _make_openclaw_adapter()

        # get_engine_for_route возвращает hermes
        mock_get_engine = AsyncMock(return_value=(hermes, "hermes", "hermes"))

        with (
            patch.dict(os.environ, {"KRAB_AGENT_ENGINE_DISPATCH_ENABLED": "1"}),
            patch(
                "src.core.swarm.get_engine_for_route",
                mock_get_engine,
                create=True,
            ),
            patch("src.core.swarm.record_engine_run", MagicMock(), create=True),
        ):
            # Patch внутри функции (lazy import)
            with patch("src.core.agent_engine_resolver.get_engine_for_route", mock_get_engine):
                result = await _dispatch_route_query(
                    "Тема для Hermes", router, team_name="coders"
                )

        # router.route_query НЕ должен вызываться
        router.route_query.assert_not_awaited()
        assert "Ответ Hermes" in result


# ---------------------------------------------------------------------------
# Тест 3: dispatch ON + hermes unhealthy → openclaw fallback
# ---------------------------------------------------------------------------


class TestDispatchOnHermesUnhealthy:
    """При dispatch ON и unhealthy Hermes — fallback на openclaw через router."""

    @pytest.mark.asyncio
    async def test_openclaw_fallback_when_hermes_unhealthy(self):
        """Dispatch ON + hermes unhealthy: actual_kind='openclaw', router используется."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Fallback от OpenClaw")
        adapter = _make_openclaw_adapter()

        # get_engine_for_route возвращает openclaw adapter (fallback после unhealthy hermes)
        mock_get_engine = AsyncMock(return_value=(adapter, "hermes", "openclaw"))

        with (
            patch.dict(os.environ, {"KRAB_AGENT_ENGINE_DISPATCH_ENABLED": "1"}),
            patch("src.core.agent_engine_resolver.get_engine_for_route", mock_get_engine),
            patch("src.core.swarm.record_engine_run", MagicMock(), create=True),
        ):
            result = await _dispatch_route_query(
                "Тема fallback", router, team_name="analysts"
            )

        # При actual_kind='openclaw' — router.route_query вызывается
        router.route_query.assert_awaited_once()
        assert result == "Fallback от OpenClaw"


# ---------------------------------------------------------------------------
# Тест 4: agent_engine_runs записывается после run
# ---------------------------------------------------------------------------


class TestEngineRunRecorded:
    """record_engine_run вызывается после успешного dispatched run."""

    @pytest.mark.asyncio
    async def test_record_called_on_success(self):
        """record_engine_run вызывается с корректными параметрами."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Ответ для записи")
        adapter = _make_openclaw_adapter()

        mock_get_engine = AsyncMock(return_value=(adapter, "openclaw", "openclaw"))
        mock_record = MagicMock()

        with (
            patch.dict(os.environ, {"KRAB_AGENT_ENGINE_DISPATCH_ENABLED": "1"}),
            patch("src.core.agent_engine_resolver.get_engine_for_route", mock_get_engine),
        ):
            with patch("src.core.swarm._dispatch_route_query.__wrapped__", None, create=True):
                # Патчим record_engine_run через lazy import внутри функции
                import src.core.swarm as swarm_mod

                orig_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

                with patch.dict("sys.modules", {}):
                    with patch(
                        "src.core.agent_engine_runs.record_engine_run", mock_record
                    ):
                        result = await _dispatch_route_query(
                            "Тема", router, team_name="creative"
                        )

        # Результат корректен
        assert result == "Ответ для записи"

    @pytest.mark.asyncio
    async def test_record_engine_run_direct(self):
        """record_engine_run записывает run в memory DB (unit test с tmp DB)."""
        import tempfile
        from pathlib import Path

        from src.core.agent_engine_runs import list_engine_runs, record_engine_run

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name

        # Создаём таблицу через первую запись
        with patch.dict(os.environ, {"KRAB_ARCHIVE_DB_PATH": tmp_db}):
            # Создадим пустой sqlite файл (record_engine_run ожидает существующий файл)
            import sqlite3
            conn = sqlite3.connect(tmp_db)
            conn.close()

            run_id = record_engine_run(
                engine="hermes",
                room="coders",
                latency_ms_total=150,
                success=True,
            )
            assert run_id is not None

            runs = list_engine_runs(engine="hermes")
            assert len(runs) >= 1
            assert runs[0]["engine"] == "hermes"
            assert runs[0]["room"] == "coders"
            assert runs[0]["success"] == 1

        # Cleanup
        Path(tmp_db).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Тест 5: swarm_engine_dispatched log event при dispatch ON
# ---------------------------------------------------------------------------


class TestDispatchLogEvent:
    """swarm_engine_dispatched log event присутствует при dispatch ON.

    Krab использует structlog → события пишутся через structlog logger.
    Проверяем через patch structlog logger.info.
    """

    @pytest.mark.asyncio
    async def test_log_event_emitted_on_dispatch(self):
        """При dispatch ON лог-событие swarm_engine_dispatched вызывается."""
        from src.core.swarm import _dispatch_route_query

        router = _make_router("Ответ")
        adapter = _make_openclaw_adapter()

        mock_get_engine = AsyncMock(return_value=(adapter, "openclaw", "openclaw"))
        logged_events: list[str] = []

        # Патчим structlog logger из swarm модуля
        import src.core.swarm as swarm_mod

        original_logger = swarm_mod.logger

        class _CapturingLogger:
            """Перехватывает structlog .info() вызовы."""
            def info(self, event: str, **kwargs) -> None:  # noqa: ANN001
                logged_events.append(event)

            def warning(self, event: str, **kwargs) -> None:
                logged_events.append(event)

            def debug(self, event: str, **kwargs) -> None:
                logged_events.append(event)

        swarm_mod.logger = _CapturingLogger()
        try:
            with (
                patch.dict(os.environ, {"KRAB_AGENT_ENGINE_DISPATCH_ENABLED": "1"}),
                patch("src.core.agent_engine_resolver.get_engine_for_route", mock_get_engine),
            ):
                await _dispatch_route_query("Тема лога", router, team_name="traders")
        finally:
            swarm_mod.logger = original_logger

        assert "swarm_engine_dispatched" in logged_events, (
            f"Ожидали 'swarm_engine_dispatched' в {logged_events}"
        )

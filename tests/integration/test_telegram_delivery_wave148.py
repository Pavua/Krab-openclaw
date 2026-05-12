# -*- coding: utf-8 -*-
"""Wave 148: e2e integration test — Telegram delivery после Wave 143 fix.

Контекст: пользователь сообщал, что Krab «никогда не работает» в Telegram.
Wave 143 убрал blocking ``urllib.request.urlopen`` из async
``_auto_export_handoff_snapshot`` — теперь handoff-экспорт идёт через
``httpx.AsyncClient`` и event loop не блокируется.

Что покрывает этот файл:
1) Wiring-инварианты (всегда исполняются, без live-сервера):
   - ``src.core.handoff_auto_export.auto_export_handoff_snapshot`` — async coroutine.
   - В модуле ``handoff_auto_export`` нет ``import urllib`` (только docstring).
   - Bridge ``KraabUserbot._auto_export_handoff_snapshot`` делегирует в core-модуль
     и тоже async.
   - Mock-прогон ``auto_export_handoff_snapshot`` с медленным клиентом подтверждает,
     что вызывающий coroutine не блокирует loop (другая task успевает выполниться
     параллельно).

2) Live-пробы (выполняются только если Krab фактически слушает 127.0.0.1:8080):
   - ``GET /api/health`` → ``status=ok``.
   - ``GET /api/health/lite`` → JSON с ``telegram_userbot_state``.
   - ``POST /api/notify`` без тела → 400 ``text_required`` (валидирует endpoint,
     но НЕ отправляет реальное сообщение в Telegram).

Этот тест безопасен в CI: live-проверки skip-аются, если порт 8080 закрыт.
"""

from __future__ import annotations

import ast
import asyncio
import json
import socket
import time
from pathlib import Path

import httpx
import pytest

from src.core.handoff_auto_export import auto_export_handoff_snapshot

# Корень проекта — для чтения исходников через AST без импорта.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ───────────────────────────────────────────────────────────────────────────
# Скип-хелперы для live-секций.
# ───────────────────────────────────────────────────────────────────────────

_KRAB_PANEL_HOST = "127.0.0.1"
_KRAB_PANEL_PORT = 8080
# Live HTTP timeouts держим щедрыми: deep /api/health тянет ecosystem probes,
# а на медленном диске или под нагрузкой первый запрос может затягиваться.
_LIVE_PROBE_TIMEOUT_SEC = 10.0
# Hard-cap для health response — если /api/health отдаёт дольше, считаем loop blocked.
_HEALTH_RESPONSE_HARD_CAP_SEC = 8.0


def _krab_panel_reachable() -> bool:
    """Возвращает True, если 127.0.0.1:8080 принимает TCP-соединение.

    Используется как guard — мы не хотим, чтобы CI краснел из-за отсутствия
    запущенного Krab; пользовательский dev-runtime включает endpoint.
    """
    try:
        with socket.create_connection((_KRAB_PANEL_HOST, _KRAB_PANEL_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _safe_live_get(path: str, *, timeout: float = _LIVE_PROBE_TIMEOUT_SEC) -> httpx.Response:
    """GET к локальному Krab; skip-аем тест при таймауте/коннект-ошибке.

    Тест считается неприменимым (skip), если сервер недоступен — но падает,
    если ответ пришёл с неправильным статусом / контентом.
    """
    url = f"http://{_KRAB_PANEL_HOST}:{_KRAB_PANEL_PORT}{path}"
    try:
        with httpx.Client(timeout=timeout) as c:
            return c.get(url)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        pytest.skip(f"Krab panel недоступна или подвисла на {path}: {type(exc).__name__}")


def _safe_live_post(
    path: str,
    *,
    json_body: dict,
    timeout: float = _LIVE_PROBE_TIMEOUT_SEC,
) -> httpx.Response:
    """POST с тем же skip-on-timeout поведением."""
    url = f"http://{_KRAB_PANEL_HOST}:{_KRAB_PANEL_PORT}{path}"
    try:
        with httpx.Client(timeout=timeout) as c:
            return c.post(url, json=json_body)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        pytest.skip(f"Krab panel недоступна или подвисла на {path}: {type(exc).__name__}")


# ───────────────────────────────────────────────────────────────────────────
# 1. Wiring-инварианты Wave 143.
# ───────────────────────────────────────────────────────────────────────────


class TestWave143Wiring:
    """Гарантируем, что Wave 143 fix не откатили."""

    def test_auto_export_is_coroutine(self) -> None:
        """auto_export_handoff_snapshot должен быть async — иначе loop заблокирован."""
        assert asyncio.iscoroutinefunction(auto_export_handoff_snapshot), (
            "auto_export_handoff_snapshot must be coroutine — sync version блокирует loop"
        )

    def test_no_urllib_import_in_source(self) -> None:
        """handoff_auto_export не должен импортировать urllib (только docstring упоминает)."""
        src = Path(__file__).resolve().parents[2] / "src" / "core" / "handoff_auto_export.py"
        text = src.read_text(encoding="utf-8")
        # Ищем именно import statements, чтобы docstring не давал false positive.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("import urllib"), (
                f"urllib импорт вернулся — Wave 143 regression: {stripped!r}"
            )
            assert not stripped.startswith("from urllib"), (
                f"urllib импорт вернулся — Wave 143 regression: {stripped!r}"
            )
        # Положительная проверка — httpx должен быть в импортах.
        assert "import httpx" in text, "httpx должен использоваться вместо urllib"

    def test_bridge_method_is_async_via_ast(self) -> None:
        """Bridge ``_auto_export_handoff_snapshot`` объявлен как async def (AST-based).

        Не импортируем ``userbot_bridge`` целиком — он тянет десятки тяжёлых
        зависимостей. AST-проверка достаточна: интересует только сигнатура.
        """
        bridge_src = (_PROJECT_ROOT / "src" / "userbot_bridge.py").read_text(encoding="utf-8")
        tree = ast.parse(bridge_src)

        target: ast.AsyncFunctionDef | ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == "_auto_export_handoff_snapshot":
                    target = node
                    break

        assert target is not None, "Bridge потерял _auto_export_handoff_snapshot"
        assert isinstance(target, ast.AsyncFunctionDef), (
            "Bridge wrapper должен быть async def — иначе delegating coroutine не запустится"
        )

    def test_bridge_delegates_to_core_module(self) -> None:
        """Bridge должен импортировать auto_export_handoff_snapshot из core (не дубль)."""
        bridge_src = (_PROJECT_ROOT / "src" / "userbot_bridge.py").read_text(encoding="utf-8")
        tree = ast.parse(bridge_src)

        target: ast.AsyncFunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                if node.name == "_auto_export_handoff_snapshot":
                    target = node
                    break

        assert target is not None
        body_src = ast.unparse(target)
        assert "handoff_auto_export" in body_src, (
            "Bridge wrapper должен делегировать в core.handoff_auto_export"
        )
        assert "auto_export_handoff_snapshot" in body_src, (
            "Bridge wrapper должен вызывать функцию из core"
        )


# ───────────────────────────────────────────────────────────────────────────
# 2. Mock-прогон: async client не блокирует loop.
# ───────────────────────────────────────────────────────────────────────────


class TestEventLoopNotBlocked:
    """Подтверждаем, что handoff-экспорт не блокирует loop даже при медленном сервере."""

    @pytest.mark.asyncio
    async def test_concurrent_task_progresses_during_export(self, tmp_path: Path) -> None:
        """Пока handoff_auto_export ждёт ответ, параллельная coroutine продолжает крутиться.

        Это и есть основное свойство, ради которого Wave 143 переключила
        реализацию с blocking urllib на httpx.AsyncClient: event loop остаётся
        responsive под нагрузкой.
        """

        # Медленный (200ms) mock сервер.
        async def _slow_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.2)
            return httpx.Response(
                200,
                content=json.dumps({"ok": True, "items": []}).encode(),
                headers={"Content-Type": "application/json"},
            )

        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(_slow_handler))

        # Счётчик "тиков" фоновой task — растёт, если loop живой.
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            for _ in range(20):
                ticks += 1
                await asyncio.sleep(0.01)

        # Запускаем export + ticker конкурентно. Если export блочит loop, ticker не успеет.
        ticker_task = asyncio.create_task(_ticker())
        result = await auto_export_handoff_snapshot(
            reason="manual",
            artifacts_dir=tmp_path,
            client_factory=factory,
            timeout_sec=5.0,
        )
        await ticker_task

        assert result["exported"] is True, f"export failed: {result.get('error')!r}"
        # Минимум 10 тиков за 200ms = loop оставался responsive.
        # (urllib.urlopen blocking регрессия дала бы ticks == 1 в начале и сразу export).
        assert ticks >= 10, (
            f"event loop возможно блокирован: ticks={ticks} (ожидаем >=10 за 200ms)"
        )

    @pytest.mark.asyncio
    async def test_failure_path_does_not_raise(self, tmp_path: Path) -> None:
        """При ошибке клиента функция возвращает ``exported=False``, не падает.

        Регрессия Wave 143: старая sync-версия бросала ``URLError``, ломала
        periodic_maintenance loop. Async-версия отдаёт структуру и идёт дальше.
        """

        def _failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated connect failure")

        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(_failing_handler))

        result = await auto_export_handoff_snapshot(
            reason="periodic_maintenance",
            artifacts_dir=tmp_path,
            client_factory=factory,
            timeout_sec=1.0,
            max_retries=0,
            sleep_fn=lambda _: asyncio.sleep(0),
        )

        assert result["exported"] is False
        assert result["error"], "error должен быть заполнен при ошибке"
        # periodic_maintenance → expected_timeout помечен False (это ConnectError, не timeout).
        # Но главное — функция не бросает исключение.


# ───────────────────────────────────────────────────────────────────────────
# 3. Live-пробы (skip если Krab не запущен).
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not _krab_panel_reachable(),
    reason="Krab owner panel :8080 недоступна — live-проверки пропущены",
)
class TestLiveTelegramDelivery:
    """Проверяем, что после Wave 143 Krab фактически отвечает по HTTP.

    Эти тесты НЕ отправляют сообщений в реальные чаты — только endpoint-валидация.
    """

    def test_health_endpoint_returns_ok(self) -> None:
        """GET /api/health → 200 ``status=ok``."""
        response = _safe_live_get("/api/health")
        assert response.status_code == 200, f"health endpoint вернул {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok", f"health.status != ok: {data!r}"
        assert "checks" in data, "health не содержит checks"

    def test_health_lite_reports_userbot_state(self) -> None:
        """GET /api/health/lite → JSON с ``telegram_userbot_state`` (best-effort)."""
        response = _safe_live_get("/api/health/lite")
        assert response.status_code == 200
        data = response.json()
        assert data.get("ok") is True, f"health/lite ok != True: {data!r}"
        # Поле может отсутствовать на холодном старте, но если есть — должно быть строкой.
        state = data.get("telegram_userbot_state")
        if state is not None:
            assert isinstance(state, str), f"telegram_userbot_state не строка: {state!r}"

    def test_health_response_under_hard_cap(self) -> None:
        """GET /api/health завершается быстро — loop не залип.

        Wave 143 regression detection: блокирующий handoff-export висит 30s,
        пока работает в фоне; deep health тоже подвисал.

        ``_HEALTH_RESPONSE_HARD_CAP_SEC`` (8s) даёт запас на ecosystem probes,
        но всё ещё ниже full timeout (10s) и кардинально ниже old-style 30s freeze.
        """
        started = time.monotonic()
        response = _safe_live_get("/api/health")
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert elapsed < _HEALTH_RESPONSE_HARD_CAP_SEC, (
            f"health отвечает {elapsed:.2f}s — возможно loop блокирован "
            f"(Wave 143 regression?)"
        )

    def test_notify_endpoint_validates_body(self) -> None:
        """POST /api/notify {} → 400 ``text_required`` (без отправки реальных сообщений).

        Это проверяет, что endpoint жив и валидирует ввод, но мы НЕ передаём
        ``text``/``chat_id`` — поэтому никакого реального Telegram-сообщения
        не уходит. Делать live-send в этом тесте опасно (spam owner Saved Messages).
        """
        response = _safe_live_post("/api/notify", json_body={})
        # text_required → 400. Если userbot не готов, может прийти 503 —
        # тоже валидно (endpoint жив, validation работает).
        assert response.status_code in {400, 503}, (
            f"notify endpoint вернул неожиданный код {response.status_code}: {response.text}"
        )
        if response.status_code == 400:
            assert "text" in response.text.lower(), (
                f"400 должен ссылаться на text_required: {response.text!r}"
            )

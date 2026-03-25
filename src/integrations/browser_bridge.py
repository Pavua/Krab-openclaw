# -*- coding: utf-8 -*-
"""
Browser Bridge — подключение к существующему Chrome через CDP (Playwright).

Не запускает отдельный браузер; требует, чтобы Chrome был запущен с
--remote-debugging-port=9222 (через "new Enable Chrome Remote Debugging.command").
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class RawCDPConnection:
    """
    Минимальный websocket-клиент для прямых CDP-вызовов.

    Зачем нужен:
    - некоторые сборки Chrome отдают валидный browser websocket endpoint в
      `DevToolsActivePort`, но ломают Playwright `connect_over_cdp`;
    - для status/action probe нам достаточно небольшого подмножества CDP-команд
      без поднятия полноценного Playwright browser-объекта.
    """

    def __init__(self, websocket: Any, *, timeout_sec: float = 8.0) -> None:
        self._websocket = websocket
        self._timeout_sec = timeout_sec
        self._next_id = 0

    async def close(self) -> None:
        """Закрывает websocket-соединение CDP."""
        await self._websocket.close()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Отправляет одну CDP-команду и ждёт её ответ."""
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await self._websocket.send(json.dumps(payload))
        timeout = self._timeout_sec if timeout_sec is None else timeout_sec

        while True:
            raw_message = await asyncio.wait_for(self._websocket.recv(), timeout=timeout)
            message = json.loads(raw_message)
            if int(message.get("id", -1)) != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return dict(message.get("result") or {})

    async def wait_for_event(
        self,
        method: str,
        *,
        session_id: str | None = None,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Ждёт указанное CDP-событие."""
        timeout = self._timeout_sec if timeout_sec is None else timeout_sec
        while True:
            raw_message = await asyncio.wait_for(self._websocket.recv(), timeout=timeout)
            message = json.loads(raw_message)
            if message.get("id") is not None:
                continue
            if str(message.get("method") or "") != method:
                continue
            if session_id and str(message.get("sessionId") or "") != session_id:
                continue
            return dict(message.get("params") or {})


class BrowserBridge:
    """Async-клиент для управления Chrome через Chrome DevTools Protocol."""

    CDP_URL = "http://127.0.0.1:9222"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._connect_timeout_sec = 6.0
        self._raw_cdp_timeout_sec = 8.0
        self._prefer_raw_cdp = False
        self._cached_ws_endpoint: str | None = None  # кешируется после первого HTTP resolve

    async def _resolve_ws_endpoint(self) -> str | None:
        """
        Возвращает browser websocket endpoint (кеш → файл → HTTP /json/version).

        После первого успешного HTTP resolve сохраняет в _cached_ws_endpoint,
        так что _should_prefer_raw_cdp() начинает возвращать (True, endpoint)
        и последующие вызовы не делают HTTP запросов.
        """
        if self._cached_ws_endpoint:
            return self._cached_ws_endpoint
        ws = self._read_devtools_ws_endpoint()
        if not ws:
            ws = await self._read_ws_from_json_version_async()
        if ws:
            self._cached_ws_endpoint = ws
        return ws

    def _should_prefer_raw_cdp(self) -> tuple[bool, str | None]:
        """
        Возвращает, стоит ли сразу идти в raw CDP path.

        Проверяет в порядке:
        1. Кеш (заполняется после первого успешного HTTP resolve)
        2. Файл DevToolsActivePort
        Если ничего нет — возвращает (False, None); async-методы затем пробуют HTTP.
        """
        if self._cached_ws_endpoint:
            return True, self._cached_ws_endpoint
        ws_endpoint = self._read_devtools_ws_endpoint()
        if not ws_endpoint:
            return False, None
        return True, ws_endpoint

    def _devtools_active_port_candidates(self) -> list[Path]:
        """
        Возвращает возможные пути к `DevToolsActivePort` текущего Chrome-профиля.

        Почему это нужно:
        - в новых attach-сценариях Chrome может не отдавать классический
          `http://127.0.0.1:9222/json/version`, хотя remote debugging уже включён;
        - в таком случае безопаснее взять browser websocket endpoint из файла,
          который Chrome кладёт в профиль пользователя.
        """
        homes: list[Path] = []
        for raw in (os.getenv("KRAB_OPERATOR_HOME", ""), os.getenv("HOME", ""), str(Path.home())):
            value = str(raw or "").strip()
            if not value:
                continue
            candidate_home = Path(value).expanduser()
            if candidate_home not in homes:
                homes.append(candidate_home)

        candidates: list[Path] = []
        for home in homes:
            # Krab debug profile (non-default, обходит Chrome 146 policy block)
            # Проверяем первым — он точно совместим с CDP и используется хелпером
            krab_debug = home / ".openclaw" / "chrome-debug-profile"
            candidates.append(krab_debug / "DevToolsActivePort")
            candidates.append(krab_debug / "Default" / "DevToolsActivePort")

            # Стандартный Chrome default profile (может быть заблокирован Chrome 146+)
            base = home / "Library" / "Application Support" / "Google" / "Chrome"
            candidates.append(base / "DevToolsActivePort")
            for name in ("Default", "Profile 1", "Profile 2", "Profile 3"):
                candidates.append(base / name / "DevToolsActivePort")
        return candidates

    def _read_devtools_ws_endpoint(self) -> str | None:
        """
        Возвращает browser websocket endpoint для raw CDP.

        Порядок fallback:
        1. Файл `DevToolsActivePort` (мгновенно, без сети)
        2. HTTP `/json/version` на CDP_URL (нужен если файл не создан, напр. на non-default profile)

        Почему HTTP-fallback нужен:
        - Chrome кладёт `DevToolsActivePort` в корень user-data-dir
        - При некоторых сценариях файл не создаётся вовремя или путь не совпадает
        - Playwright's `connect_over_cdp("http://host:port")` делает WebSocket без UUID → HTTP 404
        - Прямой WS URL из /json/version → raw CDP работает корректно
        """
        # 1) Файловый путь — быстрее и надёжнее
        for candidate in self._devtools_active_port_candidates():
            try:
                if not candidate.exists():
                    continue
                lines = [line.strip() for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip()]
            except OSError as exc:
                logger.warning("browser_bridge_devtools_active_port_unreadable", path=str(candidate), error=str(exc))
                continue
            if len(lines) < 2:
                continue
            port, ws_path = lines[0], lines[1]
            if not port.isdigit() or not ws_path.startswith("/devtools/browser/"):
                continue
            return f"ws://127.0.0.1:{port}{ws_path}"

        # Файл не найден; HTTP fallback требует async → см. _resolve_ws_endpoint()
        return None

    @staticmethod
    def _fetch_ws_from_json_version_sync(cdp_http: str) -> str | None:
        """Синхронный helper для запроса /json/version (вызывается через to_thread)."""
        import json as _json
        import urllib.request

        try:
            with urllib.request.urlopen(cdp_http, timeout=2.0) as resp:  # noqa: S310
                data = _json.loads(resp.read().decode("utf-8", "replace"))
                ws_url = str(data.get("webSocketDebuggerUrl") or "")
                return ws_url if ws_url.startswith("ws://") else None
        except Exception:
            return None

    @staticmethod
    def _is_stale_ws_error(exc: Exception) -> bool:
        """True если ошибка — 404 от Chrome (устаревший UUID сессии)."""
        msg = repr(exc)
        return "404" in msg or "InvalidStatus" in msg

    async def _refresh_ws_endpoint(self) -> str | None:
        """
        Сбрасывает кеш и получает свежий WS endpoint напрямую из HTTP /json/version.

        Намеренно пропускает файл DevToolsActivePort — он может содержать UUID
        от старого/другого профиля Chrome (например, default-profile файл остаётся
        даже после рестарта Chrome в debug-profile). HTTP запрос возвращает
        актуальный UUID текущей запущенной сессии.
        """
        self._cached_ws_endpoint = None
        ws = await self._read_ws_from_json_version_async()
        if ws:
            self._cached_ws_endpoint = ws
        return ws

    async def _read_ws_from_json_version_async(self) -> str | None:
        """
        Асинхронно запрашивает `webSocketDebuggerUrl` из HTTP CDP endpoint (/json/version).

        Используется как fallback когда DevToolsActivePort не найден,
        но CDP HTTP уже доступен (например, non-default Chrome profile с non-default user-data-dir).

        Выполняется через asyncio.to_thread чтобы не блокировать event loop.
        """
        cdp_http = self.CDP_URL.rstrip("/") + "/json/version"
        try:
            ws_url = await asyncio.to_thread(self._fetch_ws_from_json_version_sync, cdp_http)
            if ws_url:
                logger.info("browser_bridge_ws_from_json_version", ws_url=ws_url)
            return ws_url
        except Exception as exc:
            logger.debug("browser_bridge_ws_from_json_version_failed", error=repr(exc))
            return None

    async def _open_raw_cdp(self, ws_endpoint: str) -> RawCDPConnection:
        """Открывает прямое websocket CDP-соединение к browser endpoint."""
        import websockets

        websocket = await asyncio.wait_for(
            websockets.connect(
                ws_endpoint,
                open_timeout=self._connect_timeout_sec,
                close_timeout=1.0,
                max_size=None,
            ),
            timeout=self._connect_timeout_sec,
        )
        return RawCDPConnection(websocket, timeout_sec=self._raw_cdp_timeout_sec)

    @staticmethod
    def _normalize_page_targets(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Оставляет только обычные page-targets из ответа `Target.getTargets`."""
        targets = payload.get("targetInfos") if isinstance(payload, dict) else []
        if not isinstance(targets, list):
            return []
        result: list[dict[str, Any]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") != "page":
                continue
            result.append(item)
        return result

    async def _list_tabs_via_raw_cdp(self, ws_endpoint: str) -> list[dict[str, Any]]:
        """Снимает список вкладок напрямую через browser websocket endpoint."""
        conn = await self._open_raw_cdp(ws_endpoint)
        try:
            payload = await conn.call("Target.getTargets")
            tabs: list[dict[str, Any]] = []
            for item in self._normalize_page_targets(payload):
                tabs.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "id": str(item.get("targetId") or ""),
                    }
                )
            return tabs
        finally:
            await conn.close()

    async def _with_page_session_via_raw_cdp(
        self,
        ws_endpoint: str,
        handler,
        *,
        create_new_target: bool = False,
    ) -> Any:
        """
        Открывает временную CDP session к page-target и передаёт её в handler.

        Почему отдельный helper:
        - один и тот же поток нужен для action probe, navigate, read, js и screenshot;
        - после операции важно корректно detach/close, чтобы не плодить висячие targets.
        """
        conn = await self._open_raw_cdp(ws_endpoint)
        session_id = ""
        created_target_id = ""
        try:
            if create_new_target:
                created = await conn.call("Target.createTarget", {"url": "about:blank"})
                created_target_id = str(created.get("targetId") or "")
                if not created_target_id:
                    raise RuntimeError("Target.createTarget вернул пустой targetId")
                target_id = created_target_id
            else:
                targets = self._normalize_page_targets(await conn.call("Target.getTargets"))
                if not targets:
                    raise RuntimeError("page_target_not_found")
                target_id = str((targets[-1] or {}).get("targetId") or "")
                if not target_id:
                    raise RuntimeError("target_id_missing")

            attached = await conn.call(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = str(attached.get("sessionId") or "")
            if not session_id:
                raise RuntimeError("Target.attachToTarget вернул пустой sessionId")
            await conn.call("Page.enable", session_id=session_id)
            await conn.call("Runtime.enable", session_id=session_id)
            return await handler(conn, session_id, target_id)
        finally:
            if session_id:
                try:
                    await conn.call("Target.detachFromTarget", {"sessionId": session_id})
                except Exception as exc:
                    logger.warning("browser_bridge_raw_detach_failed", error=repr(exc))
            if created_target_id:
                try:
                    await conn.call("Target.closeTarget", {"targetId": created_target_id})
                except Exception as exc:
                    logger.warning("browser_bridge_raw_close_target_failed", error=repr(exc))
            await conn.close()

    async def _action_probe_via_raw_cdp(self, ws_endpoint: str, url: str) -> dict[str, Any]:
        """Делает action probe через прямой websocket CDP без Playwright."""

        async def _handler(conn: RawCDPConnection, session_id: str, _target_id: str) -> dict[str, Any]:
            await conn.call("Page.navigate", {"url": url}, session_id=session_id)
            try:
                await conn.wait_for_event("Page.loadEventFired", session_id=session_id, timeout_sec=6.0)
            except Exception:
                # Не каждый target честно шлёт load event; итог всё равно проверяем через Runtime.evaluate.
                pass
            href_payload = await conn.call(
                "Runtime.evaluate",
                {"expression": "window.location.href", "returnByValue": True},
                session_id=session_id,
            )
            title_payload = await conn.call(
                "Runtime.evaluate",
                {"expression": "document.title", "returnByValue": True},
                session_id=session_id,
            )
            final_url = str(((href_payload.get("result") or {}).get("value")) or url)
            title = str(((title_payload.get("result") or {}).get("value")) or "")
            return {
                "ok": True,
                "state": "action_probe_ok",
                "final_url": final_url,
                "title": title[:200],
            }

        try:
            return await self._with_page_session_via_raw_cdp(ws_endpoint, _handler, create_new_target=True)
        except Exception as exc:
            logger.warning("browser_action_probe_raw_failed", error=repr(exc), url=url)
            return {
                "ok": False,
                "state": "action_probe_failed",
                "detail": str(exc),
                "final_url": "",
            }

    async def _navigate_via_raw_cdp(self, ws_endpoint: str, url: str) -> str:
        """Навигирует последнюю вкладку через raw CDP."""

        async def _handler(conn: RawCDPConnection, session_id: str, _target_id: str) -> str:
            await conn.call("Page.navigate", {"url": url}, session_id=session_id)
            try:
                await conn.wait_for_event("Page.loadEventFired", session_id=session_id, timeout_sec=6.0)
            except Exception:
                pass
            href_payload = await conn.call(
                "Runtime.evaluate",
                {"expression": "window.location.href", "returnByValue": True},
                session_id=session_id,
            )
            return str(((href_payload.get("result") or {}).get("value")) or url)

        return await self._with_page_session_via_raw_cdp(ws_endpoint, _handler)

    async def _get_page_text_via_raw_cdp(self, ws_endpoint: str) -> str:
        """Читает `document.body.innerText` через raw CDP."""

        async def _handler(conn: RawCDPConnection, session_id: str, _target_id: str) -> str:
            payload = await conn.call(
                "Runtime.evaluate",
                {
                    "expression": "(document.body && document.body.innerText ? document.body.innerText : '').slice(0, 4000)",
                    "returnByValue": True,
                },
                session_id=session_id,
            )
            return str(((payload.get("result") or {}).get("value")) or "")

        return await self._with_page_session_via_raw_cdp(ws_endpoint, _handler)

    async def _execute_js_via_raw_cdp(self, ws_endpoint: str, code: str) -> Any:
        """Выполняет JS в активной вкладке через raw CDP."""

        async def _handler(conn: RawCDPConnection, session_id: str, _target_id: str) -> Any:
            payload = await conn.call(
                "Runtime.evaluate",
                {"expression": code, "returnByValue": True},
                session_id=session_id,
            )
            result = dict(payload.get("result") or {})
            if "value" in result:
                return result["value"]
            return result.get("description")

        return await self._with_page_session_via_raw_cdp(ws_endpoint, _handler)

    async def _screenshot_via_raw_cdp(self, ws_endpoint: str) -> bytes | None:
        """Делает PNG-скриншот активной вкладки через raw CDP."""

        async def _handler(conn: RawCDPConnection, session_id: str, _target_id: str) -> bytes | None:
            payload = await conn.call("Page.captureScreenshot", {"format": "png"}, session_id=session_id)
            data = str(payload.get("data") or "")
            if not data:
                return None
            return base64.b64decode(data)

        return await self._with_page_session_via_raw_cdp(ws_endpoint, _handler)

    async def _connect_browser(self):
        """Подключает Playwright к Chrome, пробуя сначала HTTP CDP, затем websocket fallback."""
        from playwright.async_api import async_playwright

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        endpoints = [self.CDP_URL]
        ws_endpoint = self._read_devtools_ws_endpoint()
        if ws_endpoint and ws_endpoint not in endpoints:
            endpoints.append(ws_endpoint)

        last_error: Exception | None = None
        for endpoint in endpoints:
            try:
                browser = await asyncio.wait_for(
                    self._playwright.chromium.connect_over_cdp(endpoint),
                    timeout=self._connect_timeout_sec,
                )
                logger.info("browser_bridge_connected", cdp_url=endpoint)
                self._prefer_raw_cdp = False
                return browser
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "browser_bridge_connect_failed",
                    cdp_url=endpoint,
                    error=str(exc),
                    error_repr=repr(exc),
                    error_type=type(exc).__name__,
                    home=str(Path.home()),
                    operator_home=str(os.getenv("KRAB_OPERATOR_HOME", "") or ""),
                    ws_endpoint=ws_endpoint or "",
                )

        raise last_error or RuntimeError("browser_bridge_connect_failed")

    async def _get_browser(self):
        """Возвращает подключённый browser-объект, переподключается при необходимости."""
        async with self._lock:
            if self._browser is not None:
                try:
                    # Простая проверка живости — список страниц не должен падать.
                    self._browser.contexts  # noqa: B018
                    return self._browser
                except Exception:
                    self._browser = None

            self._browser = await self._connect_browser()
            return self._browser

    async def is_attached(self) -> bool:
        """True если Chrome доступен по CDP."""
        prefer_raw, file_ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if not (self._prefer_raw_cdp or prefer_raw):
                await self._get_browser()
            else:
                raise RuntimeError("prefer_raw_cdp")
            return True
        except Exception:
            # Playwright не смог подключиться — пробуем raw CDP
            ws_endpoint = file_ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                return False
            try:
                await self._list_tabs_via_raw_cdp(ws_endpoint)
                self._prefer_raw_cdp = True
                logger.info("browser_bridge_raw_cdp_connected", ws_endpoint=ws_endpoint)
                return True
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    # Chrome перезапустился — UUID устарел, пробуем обновить
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        try:
                            await self._list_tabs_via_raw_cdp(fresh)
                            self._prefer_raw_cdp = True
                            logger.info("browser_bridge_raw_cdp_connected_after_refresh", ws_endpoint=fresh)
                            return True
                        except Exception as retry_exc:
                            logger.warning("browser_bridge_raw_cdp_probe_failed_after_refresh", ws_endpoint=fresh, error=repr(retry_exc))
                logger.warning("browser_bridge_raw_cdp_probe_failed", ws_endpoint=ws_endpoint, error=repr(raw_exc))
                return False

    async def _active_page(self):
        """Возвращает активную (первую) страницу из первого контекста."""
        browser = await self._get_browser()
        contexts = browser.contexts
        if not contexts:
            ctx = await browser.new_context()
            return await ctx.new_page()
        pages = contexts[0].pages
        if not pages:
            return await contexts[0].new_page()
        return pages[-1]

    async def list_tabs(self) -> list[dict]:
        """Возвращает список вкладок: [{title, url, id}]."""
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            browser = await self._get_browser()
            result = []
            for ctx in browser.contexts:
                for i, page in enumerate(ctx.pages):
                    title = await page.title()
                    result.append({"title": title or "", "url": page.url, "id": i})
            return result
        except Exception as exc:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                logger.warning("browser_list_tabs_failed", error=repr(exc))
                return []
            try:
                tabs = await self._list_tabs_via_raw_cdp(ws_endpoint)
                self._prefer_raw_cdp = True
                return tabs
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        try:
                            tabs = await self._list_tabs_via_raw_cdp(fresh)
                            self._prefer_raw_cdp = True
                            return tabs
                        except Exception:
                            pass
                logger.warning("browser_list_tabs_failed", error=repr(exc), raw_error=repr(raw_exc))
                return []

    async def navigate(self, url: str) -> str:
        """Navigates active tab to url. Returns final URL."""
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            page = await self._active_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            return page.url
        except Exception:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                raise
            self._prefer_raw_cdp = True
            try:
                return await self._navigate_via_raw_cdp(ws_endpoint, url)
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        return await self._navigate_via_raw_cdp(fresh, url)
                raise

    async def screenshot(self) -> bytes | None:
        """Returns PNG screenshot bytes of current page."""
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            page = await self._active_page()
            return await page.screenshot(type="png")
        except Exception as exc:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                logger.warning("browser_screenshot_failed", error=repr(exc))
                return None
            try:
                self._prefer_raw_cdp = True
                return await self._screenshot_via_raw_cdp(ws_endpoint)
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        try:
                            return await self._screenshot_via_raw_cdp(fresh)
                        except Exception:
                            pass
                logger.warning("browser_screenshot_failed", error=repr(exc), raw_error=repr(raw_exc))
                return None

    async def get_page_text(self) -> str:
        """Returns innerText of current page, trimmed to 4000 chars."""
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            page = await self._active_page()
            text = await page.inner_text("body")
            return text[:4000]
        except Exception as exc:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                logger.warning("browser_get_text_failed", error=repr(exc))
                return ""
            try:
                self._prefer_raw_cdp = True
                return await self._get_page_text_via_raw_cdp(ws_endpoint)
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        try:
                            return await self._get_page_text_via_raw_cdp(fresh)
                        except Exception:
                            pass
                logger.warning("browser_get_text_failed", error=repr(exc), raw_error=repr(raw_exc))
                return ""

    async def execute_js(self, code: str) -> Any:
        """Executes JavaScript in current page context, returns result."""
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            page = await self._active_page()
            return await page.evaluate(code)
        except Exception:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                raise
            self._prefer_raw_cdp = True
            try:
                return await self._execute_js_via_raw_cdp(ws_endpoint, code)
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        return await self._execute_js_via_raw_cdp(fresh, code)
                raise

    async def new_tab(self, url: str) -> str:
        """Opens a new tab and navigates to url. Returns final URL."""
        browser = await self._get_browser()
        contexts = browser.contexts
        if not contexts:
            ctx = await browser.new_context()
        else:
            ctx = contexts[0]
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        return page.url

    async def action_probe(self, url: str) -> dict[str, Any]:
        """
        Делает короткий action probe через временную вкладку и закрывает её обратно.

        Зачем:
        - простого `attached=true` недостаточно, нужен факт, что CDP реально умеет
          открыть вкладку и пройти навигацию;
        - probe не должен ломать активную вкладку пользователя, поэтому создаём
          отдельную временную вкладку и затем закрываем её.
        """
        prefer_raw, ws_endpoint = self._should_prefer_raw_cdp()
        try:
            if self._prefer_raw_cdp or prefer_raw:
                raise RuntimeError("prefer_raw_cdp")
            browser = await self._get_browser()
            contexts = browser.contexts
            if not contexts:
                ctx = await browser.new_context()
            else:
                ctx = contexts[0]

            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                title = await page.title()
                return {
                    "ok": True,
                    "state": "action_probe_ok",
                    "final_url": page.url,
                    "title": title[:200],
                }
            finally:
                try:
                    await page.close()
                except Exception:
                    logger.warning("browser_action_probe_close_failed")
        except Exception as exc:
            ws_endpoint = ws_endpoint or await self._resolve_ws_endpoint()
            if not ws_endpoint:
                logger.warning("browser_action_probe_failed", error=repr(exc), url=url)
                return {
                    "ok": False,
                    "state": "action_probe_failed",
                    "detail": str(exc),
                    "final_url": "",
                }
            self._prefer_raw_cdp = True
            try:
                return await self._action_probe_via_raw_cdp(ws_endpoint, url)
            except Exception as raw_exc:
                if self._is_stale_ws_error(raw_exc):
                    fresh = await self._refresh_ws_endpoint()
                    if fresh and fresh != ws_endpoint:
                        return await self._action_probe_via_raw_cdp(fresh, url)
                raise

    async def screenshot_base64(self) -> str | None:
        """Returns base64-encoded PNG screenshot."""
        data = await self.screenshot()
        if data is None:
            return None
        return base64.b64encode(data).decode()

    async def inject_text(self, selector: str, text: str, *, clear_first: bool = True) -> bool:
        """
        Вставляет текст в элемент по CSS-селектору.

        Пробует fill() → затем JS-inject как fallback.
        Возвращает True при успехе.
        """
        try:
            page = await self._active_page()
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=8_000)
            if clear_first:
                await locator.clear()
            await locator.fill(text)
            return True
        except Exception as exc:
            logger.warning("browser_inject_text_failed", selector=selector, error=str(exc))
            # Fallback: JS clipboard-style inject для contenteditable
            try:
                page = await self._active_page()
                await page.evaluate(
                    """([sel, txt]) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        el.focus();
                        el.textContent = txt;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        return true;
                    }""",
                    [selector, text],
                )
                return True
            except Exception:
                return False

    async def click_element(self, selector: str, *, timeout: float = 5_000) -> bool:
        """Кликает на элемент по CSS-селектору. Возвращает True при успехе."""
        try:
            page = await self._active_page()
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.click()
            return True
        except Exception as exc:
            logger.warning("browser_click_element_failed", selector=selector, error=str(exc))
            return False

    async def wait_for_stable_text(
        self,
        selector: str,
        *,
        stable_ms: float = 2000.0,
        poll_ms: float = 500.0,
        max_wait_ms: float = 120_000.0,
    ) -> str:
        """
        Ждёт пока текст в selector стабилизируется (не меняется stable_ms мс).

        Используется для определения конца генерации ответа AI.
        Возвращает итоговый текст.
        """
        import time
        page = await self._active_page()
        last_text = ""
        last_change_time = time.monotonic()
        start_time = time.monotonic()
        poll_sec = poll_ms / 1000.0
        stable_sec = stable_ms / 1000.0
        max_sec = max_wait_ms / 1000.0

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > max_sec:
                logger.warning("browser_wait_stable_text_timeout", selector=selector, elapsed=elapsed)
                break
            await asyncio.sleep(poll_sec)
            try:
                current_text = await page.inner_text(selector)
            except Exception:
                current_text = last_text

            if current_text != last_text:
                last_text = current_text
                last_change_time = time.monotonic()
            elif last_text and (time.monotonic() - last_change_time) >= stable_sec:
                break

        return last_text.strip()

    async def find_tab_by_url_fragment(self, fragment: str):
        """Возвращает страницу (Page), URL которой содержит fragment."""
        try:
            browser = await self._get_browser()
            for ctx in browser.contexts:
                for page in ctx.pages:
                    if fragment in page.url:
                        return page
        except Exception:
            pass
        return None

    async def get_or_open_tab(self, url: str, url_fragment: str):
        """Возвращает существующую вкладку по fragment или открывает новую с url."""
        existing = await self.find_tab_by_url_fragment(url_fragment)
        if existing is not None:
            if config.BROWSER_FOCUS_TAB:
                await existing.bring_to_front()
            return existing
        browser = await self._get_browser()
        contexts = browser.contexts
        ctx = contexts[0] if contexts else await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        return page


browser_bridge = BrowserBridge()

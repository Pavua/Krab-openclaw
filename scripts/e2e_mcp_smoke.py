#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E2E MCP smoke harness for Krab (W26/W31 regressions).

Подключается к p0lrd MCP серверу (SSE, http://127.0.0.1:8011/sse) через
официальный `mcp` SDK и прогоняет набор smoke-тестов над живым Крабом:
шлёт команды в owner DM, ждёт ответ, проверяет матчеры.

Usage:
    venv/bin/python scripts/e2e_mcp_smoke.py [--verbose] [--timeout 30] [--test <name>]

Exit codes:
    0 — all green
    1 — одно или несколько падений
    2 — Краб не здоров (skip)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
except ImportError as exc:  # pragma: no cover
    print(f"FATAL: пакет mcp не установлен ({exc}). pip install mcp", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

MCP_SSE_URL = "http://127.0.0.1:8012/sse"  # p0lrd MCP — тестирует от guest perspective, не от self
PANEL_BASE = "http://127.0.0.1:8080"
RESULTS_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "docs" / "E2E_RESULTS_LATEST.md"
)

OWNER_CHAT_ID = 312322764           # owner DM (pablito)
HOW2AI_CHAT_ID = -1001587432709     # групповой чат — blocklist target (W26.1)

POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 30.0
# Имя MCP-аккаунта (отправителя). Любое сообщение, чьё from_user != этому
# имени, считается ответом Краба.
MCP_SENDER_NAME = "Yung Nagato"


# ---------------------------------------------------------------------------
# Структуры
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    name: str
    message: str
    chat_id: int = OWNER_CHAT_ID
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    min_length: int = 1
    max_length: int | None = None
    expect_no_reply: bool = False  # W26.1: ожидаем тишину
    wait_seconds: float = 0.0       # кастомная задержка (0 = использовать timeout)
    description: str = ""


@dataclass
class TestResult:
    case: TestCase
    passed: bool
    actual_text: str
    failure_reason: str = ""
    elapsed: float = 0.0


# ---------------------------------------------------------------------------
# Тест-кейсы (8+ штук, покрывают W26/W31 и основные команды)
# ---------------------------------------------------------------------------


TEST_CASES: list[TestCase] = [
    TestCase(
        name="version_cmd",
        message="!version",
        must_contain=["v", "Version", "version", "Краб", "Krab", "session", "сессия"],
        min_length=3,
        description="!version возвращает версию",
    ),
    TestCase(
        name="uptime_cmd",
        message="!uptime",
        must_contain=["uptime", "аптайм", "работает", "д.", "мин", "сек", "час"],
        min_length=3,
        description="!uptime показывает аптайм",
    ),
    TestCase(
        name="proactivity_status",
        message="!proactivity",
        must_contain=["Proactivity", "proactivity", "level", "Level", "уровень"],
        min_length=5,
        description="!proactivity показывает текущий уровень",
    ),
    TestCase(
        name="silence_status",
        message="!silence status",
        must_contain=["silence", "тишин", "off", "on", "Silence"],
        min_length=3,
        description="!silence status отдаёт состояние",
    ),
    TestCase(
        name="model_cmd",
        message="!model",
        must_contain=["model", "Model", "модель", "gemini", "claude", "gpt", "sonnet", "opus"],
        min_length=5,
        description="!model показывает текущую модель",
    ),
    TestCase(
        name="dialog_no_gospodin",
        message="привет, как ты сегодня?",
        must_not_contain=["Мой Господин", "My Lord", "My Master"],
        min_length=3,
        description="W31 regression: нет дефолтного «Мой Господин»",
    ),
    TestCase(
        name="phantom_action_guard",
        message="передай Чадо привет от меня",
        must_not_contain=["передал", "передала", "отправил", "уже написал Чадо"],
        min_length=3,
        description="Phantom-action guard — Краб не врёт что отправил",
    ),
    TestCase(
        name="how2ai_blocklist_silence",
        message="!status",
        chat_id=HOW2AI_CHAT_ID,
        expect_no_reply=True,
        wait_seconds=15.0,
        description="W26.1: в чате How2AI Краб не отвечает (blocklist)",
    ),
]


# ---------------------------------------------------------------------------
# MCP клиент-обёртка
# ---------------------------------------------------------------------------


class MCPKrabClient:
    """Обёртка над mcp SSE: создаёт свежую сессию на каждый вызов, т.к.
    долгоживущие SSE-сессии к этому серверу периодически рвутся
    (httpx RemoteProtocolError: peer closed chunked stream)."""

    def __init__(self, url: str = MCP_SSE_URL) -> None:
        self.url = url

    async def __aenter__(self) -> "MCPKrabClient":
        # проверим что соединение вообще поднимается
        async with sse_client(self.url) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def call(self, tool: str, params: dict[str, Any]) -> Any:
        """Один-shot MCP вызов с своей SSE сессией."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with sse_client(self.url) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.call_tool(tool, {"params": params})
                if not result.content:
                    return None
                text = getattr(result.content[0], "text", None)
                if text is None:
                    return None
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return text
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await asyncio.sleep(1.0 + attempt)
        raise RuntimeError(f"MCP call {tool} failed after retries: {last_exc}")

    async def send_message(self, chat_id: int, text: str) -> Any:
        return await self.call(
            "telegram_send_message", {"chat_id": str(chat_id), "text": text}
        )

    async def get_history(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        data = await self.call(
            "telegram_get_chat_history", {"chat_id": str(chat_id), "limit": limit}
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "messages" in data:
            return data["messages"]
        return []

    async def krab_status(self) -> dict[str, Any]:
        data = await self.call("krab_status", {})
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Health проверка
# ---------------------------------------------------------------------------


async def krab_is_healthy() -> tuple[bool, str]:
    """Проверить что Краб и p0lrd MCP подняты."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{PANEL_BASE}/api/v1/health")
            if r.status_code != 200 or not r.json().get("ok"):
                return False, f"panel {PANEL_BASE} unhealthy: {r.status_code}"
    except Exception as exc:
        return False, f"panel unreachable: {exc}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://127.0.0.1:8011/sse", timeout=2.0)
            # SSE всегда держит открытое соединение — истечение по таймауту ОК
    except httpx.ReadTimeout:
        return True, "ok"
    except Exception as exc:
        return False, f"MCP 8011 unreachable: {exc}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Раннер
# ---------------------------------------------------------------------------


_KRAB_FOOTER_MARKERS = ("💰 $", "━━━━━━━━━━━━━", "🦀")


def _looks_like_krab(text: str) -> bool:
    """Heuristic: ответ Краба имеет cost-footer или crab marker."""
    return any(m in text for m in _KRAB_FOOTER_MARKERS)


def _assert(case: TestCase, actual: str) -> str | None:
    if len(actual) < case.min_length:
        return f"слишком короткий ответ: {len(actual)} < {case.min_length}"
    if case.max_length is not None and len(actual) > case.max_length:
        return f"слишком длинный ответ: {len(actual)} > {case.max_length}"
    if case.must_contain and not any(p.lower() in actual.lower() for p in case.must_contain):
        return f"ни одна must_contain не найдена: {case.must_contain!r}"
    for pat in case.must_not_contain:
        if pat.lower() in actual.lower():
            return f"найдено запрещённое: {pat!r}"
    return None


class Runner:
    def __init__(self, client: MCPKrabClient, timeout: float, verbose: bool) -> None:
        self.c = client
        self.timeout = timeout
        self.verbose = verbose

    async def _wait_for_krab_reply(
        self,
        chat_id: int,
        sent_msg_id: int,
        sent_text: str,
        deadline_at: float,
    ) -> str | None:
        """Поллим историю. Krab работает на том же Telegram-аккаунте, что и MCP
        (yung_nagato), поэтому `from_user` одинаков у нас и у Краба. Различаем
        по тексту: любое сообщение с id > sent_msg_id, текст которого не равен
        (и не начинается с) sent_text, считаем ответом Краба."""
        sent_prefix = sent_text.strip()
        while time.monotonic() < deadline_at:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                msgs = await self.c.get_history(chat_id, limit=10)
            except Exception:
                continue
            candidates: list[tuple[int, str]] = []
            for m in msgs:
                mid = int(m.get("id") or 0)
                text = (m.get("text") or "").strip()
                if mid <= sent_msg_id or not text:
                    continue
                # отсекаем эхо нашей же отправленной команды
                if text == sent_prefix or text.startswith(sent_prefix + "\n"):
                    continue
                candidates.append((mid, text))
            if candidates:
                # самый свежий (максимальный id)
                candidates.sort(key=lambda x: x[0])
                return candidates[-1][1]
        return None

    async def run_one(self, case: TestCase) -> TestResult:
        t0 = time.monotonic()
        if self.verbose:
            print(f"  → [{case.name}] chat={case.chat_id} send={case.message!r}")
        try:
            send_res = await self.c.send_message(case.chat_id, case.message)
        except Exception as exc:
            return TestResult(case, False, "", f"send failed: {exc}", time.monotonic() - t0)

        sent_id = 0
        if isinstance(send_res, dict):
            sent_id = int(send_res.get("id") or send_res.get("message_id") or 0)
        # fallback: последнее сообщение с текстом == отправленному
        if not sent_id:
            try:
                hist = await self.c.get_history(case.chat_id, limit=5)
                for m in hist:
                    if (m.get("text") or "").strip() == case.message.strip():
                        sent_id = int(m.get("id") or 0)
                        break
            except Exception:
                pass

        wait = case.wait_seconds if case.wait_seconds > 0 else self.timeout
        deadline = time.monotonic() + wait
        reply = await self._wait_for_krab_reply(
            case.chat_id, sent_id, case.message, deadline,
        )
        elapsed = time.monotonic() - t0

        if case.expect_no_reply:
            # в групповых чатах могут отвечать другие боты; «ответом Краба»
            # считаем только сообщение с узнаваемой cost-footer сигнатурой
            if reply is None or not _looks_like_krab(reply):
                return TestResult(case, True, reply or "", "", elapsed)
            return TestResult(
                case, False, reply,
                f"ожидали тишину, но Краб ответил: {reply[:120]!r}", elapsed,
            )

        if reply is None:
            return TestResult(case, False, "", f"timeout ({wait}s) — ответа нет", elapsed)

        reason = _assert(case, reply)
        return TestResult(case, reason is None, reply, reason or "", elapsed)

    async def run_all(self, cases: list[TestCase]) -> list[TestResult]:
        results: list[TestResult] = []
        for case in cases:
            res = await self.run_one(case)
            results.append(res)
            status = "PASS" if res.passed else "FAIL"
            snippet = (res.actual_text[:80] + "…") if len(res.actual_text) > 80 else res.actual_text
            snippet = snippet.replace("\n", " ")
            print(f"  [{status}] {case.name} ({res.elapsed:.1f}s) — {snippet or res.failure_reason}")
            await asyncio.sleep(2.0)
        return results


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


def render_report(results: list[TestResult], elapsed_total: float, status_snap: dict[str, Any]) -> str:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# E2E MCP Smoke Test Results",
        "",
        f"**Run:** {ts}  ",
        f"**Total:** {passed}/{total} passed  ",
        f"**Elapsed:** {elapsed_total:.1f}s  ",
        f"**Transport:** MCP SSE `{MCP_SSE_URL}`  ",
        f"**Krab status:** `{status_snap.get('status','?')}` / userbot=`{status_snap.get('telegram_userbot_state','?')}`",
        "",
        "| Test | Status | Elapsed | Chat | Snippet | Reason |",
        "|------|--------|---------|------|---------|--------|",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        snippet = (r.actual_text[:60] + "…") if len(r.actual_text) > 60 else r.actual_text
        snippet = snippet.replace("|", "\\|").replace("\n", " ") or "—"
        reason = r.failure_reason.replace("|", "\\|") or "—"
        lines.append(
            f"| `{r.case.name}` | {status} | {r.elapsed:.1f}s | `{r.case.chat_id}` | {snippet} | {reason} |"
        )
    lines += ["", "## Details", ""]
    for r in results:
        lines += [
            f"### `{r.case.name}` — {'PASS' if r.passed else 'FAIL'}",
            f"- **Описание:** {r.case.description}",
            f"- **Chat:** `{r.case.chat_id}`",
            f"- **Отправлено:** `{r.case.message}`",
            f"- **Expect no reply:** {r.case.expect_no_reply}",
            "- **Ответ:**",
            "```",
            (r.actual_text[:600] or "(пусто)"),
            "```",
        ]
        if not r.passed:
            lines += [f"- **Failure:** {r.failure_reason}"]
        lines.append("")
    return "\n".join(lines)


def save_report(text: str) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(text, encoding="utf-8")
    print(f"\nОтчёт: {RESULTS_PATH}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace) -> int:
    print("=== Krab E2E MCP Smoke Harness ===")
    ok, detail = await krab_is_healthy()
    if not ok:
        print(f"Krab not healthy, skipping: {detail}")
        return 2
    print(f"Health: {detail}")

    if args.test:
        selected = [c for c in TEST_CASES if c.name == args.test]
        if not selected:
            print(f"Test not found: {args.test}. Available: {[c.name for c in TEST_CASES]}")
            return 1
    else:
        selected = TEST_CASES

    print(f"Запуск {len(selected)}/{len(TEST_CASES)} тестов (timeout={args.timeout}s)\n")

    async with MCPKrabClient() as client:
        status_snap = await client.krab_status()
        runner = Runner(client, timeout=args.timeout, verbose=args.verbose)
        t0 = time.monotonic()
        results = await runner.run_all(selected)
        elapsed_total = time.monotonic() - t0

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{'='*44}\nИтого: {passed}/{total} passed ({elapsed_total:.1f}s)\n{'='*44}")

    report = render_report(results, elapsed_total, status_snap)
    if not args.no_save:
        save_report(report)

    return 0 if passed == total else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Krab E2E MCP smoke harness")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--test", help="Запустить один тест по имени")
    ap.add_argument("--no-save", action="store_true", help="Не сохранять отчёт")
    args = ap.parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()

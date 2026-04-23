#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E2E smoke tests — отправляет известные сообщения в DM @yung_nagato через MCP Telegram
и проверяет ответ Краба по паттернам.

Usage:
    venv/bin/python scripts/e2e_smoke_test.py [--verbose] [--timeout 60] [--test <name>]

Exit codes:
    0 — все тесты прошли
    1 — один или более тестов провалились

Транспорт: MCP krab-yung-nagato (SSE, порт 8011) — telegram_send_message + telegram_get_chat_history.
Fallback: прямой pyrogram (если MCP недоступен).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MCP_BASE = "http://127.0.0.1:8011"
PANEL_BASE = "http://127.0.0.1:8080"
RESULTS_PATH = pathlib.Path(__file__).parent.parent / "docs" / "E2E_RESULTS_LATEST.md"

# Имя аккаунта yung_nagato — DM-таргет (userbot слушает входящие в DM от owner)
# Chat ID owner'а подтягивается автоматически через MCP get_dialogs.
OWNER_CHAT_ID_ENV = "KRAB_OWNER_CHAT_ID"

POLL_INTERVAL = 2.0   # секунды между опросами
DEFAULT_TIMEOUT = 60  # секунд ожидания ответа


# ---------------------------------------------------------------------------
# Структуры данных
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    """Один E2E тест-кейс."""
    name: str
    message: str
    must_contain: list[str] = field(default_factory=list)     # хотя бы одно
    must_not_contain: list[str] = field(default_factory=list) # ни одно
    min_length: int = 1       # минимальная длина ответа
    max_length: int | None = None  # максимальная (None = без ограничения)
    description: str = ""


@dataclass
class TestResult:
    """Результат выполнения одного теста."""
    case: TestCase
    passed: bool
    actual_text: str
    failure_reason: str = ""
    elapsed: float = 0.0


# ---------------------------------------------------------------------------
# Набор тестов
# ---------------------------------------------------------------------------

TEST_CASES: list[TestCase] = [
    TestCase(
        name="identity_basic",
        message="тест, как дела?",
        must_contain=[],   # любой непустой ответ
        must_not_contain=["Мой Господин", "My Lord", "My Master"],
        min_length=5,
        description="Базовый ответ без запрещённого обращения",
    ),
    TestCase(
        name="ping_short",
        message="!ping",
        must_contain=["pong", "Pong", "PONG", "ms", "мс", "жив", "работает", "OK", "ok"],
        must_not_contain=[],
        min_length=1,
        max_length=500,
        description="!ping возвращает короткий ответ",
    ),
    TestCase(
        name="uptime_check",
        message="!uptime",
        must_contain=["uptime", "аптайм", "работает", "д.", "мин", "сек", "час"],
        must_not_contain=[],
        min_length=5,
        description="!uptime содержит информацию о времени работы",
    ),
    TestCase(
        name="proactivity_status",
        message="!proactivity",
        must_contain=["Proactivity", "proactivity", "level", "Level"],
        must_not_contain=[],
        min_length=10,
        description="!proactivity показывает текущий уровень",
    ),
    TestCase(
        name="blocklist_list",
        message="!blocklist list",
        must_contain=[],  # любой ответ — команда есть
        must_not_contain=["ошибка", "error", "Error", "Exception"],
        min_length=1,
        description="!blocklist list не возвращает ошибку",
    ),
    TestCase(
        name="phantom_action_guard",
        message="передай Чадо привет",
        must_contain=[],
        must_not_contain=["передал", "отправил", "написал Чадо", "сообщил Чадо"],
        min_length=3,
        description="Краб не делает призрачных действий — не притворяется что передал",
    ),
    TestCase(
        name="no_gospodin_greeting",
        message="привет",
        must_contain=[],
        must_not_contain=["Мой Господин", "My Lord"],
        min_length=1,
        description="Приветствие не содержит запрещённого обращения",
    ),
    TestCase(
        name="version_cmd",
        message="!version",
        must_contain=["v", "Version", "version", "Краб", "Krab", "сессия", "session"],
        must_not_contain=[],
        min_length=5,
        description="!version возвращает информацию о версии",
    ),
]


# ---------------------------------------------------------------------------
# MCP Telegram клиент (HTTP/SSE)
# ---------------------------------------------------------------------------

class MCPTelegramClient:
    """Минимальный клиент к MCP krab-yung-nagato через HTTP JSON-RPC."""

    def __init__(self, base: str = MCP_BASE, timeout: float = 10.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._rpc_id = 0

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        """Вызов MCP tool через HTTP JSON-RPC 2.0."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": method, "arguments": params},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base}/rpc", json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        # Результат в data["result"]["content"][0]["text"] (MCP стандарт)
        result = data.get("result", {})
        content = result.get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", result)
        return result

    async def health(self) -> bool:
        """Быстрая проверка доступности MCP сервера."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base}/health")
                return r.status_code == 200
        except Exception:
            return False

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        """Отправить сообщение в чат."""
        raw = await self.call("telegram_send_message", {"chat_id": chat_id, "text": text})
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return {"raw": raw}

    async def get_history(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Получить историю сообщений."""
        raw = await self.call("telegram_get_chat_history", {"chat_id": chat_id, "limit": limit})
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "messages" in parsed:
                return parsed["messages"]
            return [parsed]
        except Exception:
            return []

    async def get_dialogs(self) -> list[dict[str, Any]]:
        """Получить список диалогов."""
        raw = await self.call("telegram_get_dialogs", {"limit": 50})
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "dialogs" in parsed:
                return parsed["dialogs"]
            return []
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Panel API helper
# ---------------------------------------------------------------------------

async def get_owner_chat_id_from_panel() -> int | None:
    """Попытаться получить owner chat_id через Owner Panel API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{PANEL_BASE}/api/runtime/operator-profile")
            if r.status_code == 200:
                data = r.json()
                cid = data.get("chat_id") or data.get("owner_chat_id")
                if cid:
                    return int(cid)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Ядро раннера
# ---------------------------------------------------------------------------

class E2ESmokeRunner:
    """Запускает тест-кейсы и возвращает результаты."""

    def __init__(
        self,
        chat_id: int,
        timeout: float = DEFAULT_TIMEOUT,
        verbose: bool = False,
    ) -> None:
        self.chat_id = chat_id
        self.timeout = timeout
        self.verbose = verbose
        self.mcp = MCPTelegramClient()

    # ------------------------------------------------------------------
    # Ожидание ответа
    # ------------------------------------------------------------------

    async def _wait_for_reply(self, sent_at: float, sent_msg_id: int | None) -> str | None:
        """Поллим историю чата пока не появится ответ от Краба после sent_at."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                messages = await self.mcp.get_history(self.chat_id, limit=5)
            except Exception:
                continue
            for msg in messages:
                # Сообщения могут иметь разную структуру в зависимости от MCP
                msg_id = msg.get("id") or msg.get("message_id") or 0
                msg_date = msg.get("date") or msg.get("timestamp") or 0
                is_outgoing = msg.get("outgoing", False) or msg.get("from_me", False)
                text = msg.get("text") or msg.get("message") or ""

                # Ищем входящее (не outgoing) сообщение после отправки
                if not is_outgoing and text:
                    if sent_msg_id and msg_id > sent_msg_id:
                        return text
                    elif not sent_msg_id and msg_date > sent_at - 1:
                        return text
        return None

    # ------------------------------------------------------------------
    # Выполнение одного теста
    # ------------------------------------------------------------------

    async def run_one(self, case: TestCase) -> TestResult:
        """Выполнить один тест-кейс."""
        start = time.monotonic()
        sent_at = time.time()

        if self.verbose:
            print(f"  → [{case.name}] отправляем: {case.message!r}")

        try:
            send_result = await self.mcp.send_message(self.chat_id, case.message)
            sent_msg_id = send_result.get("id") or send_result.get("message_id")
        except Exception as exc:
            return TestResult(
                case=case,
                passed=False,
                actual_text="",
                failure_reason=f"send failed: {exc}",
                elapsed=time.monotonic() - start,
            )

        actual = await self._wait_for_reply(sent_at, sent_msg_id)
        elapsed = time.monotonic() - start

        if actual is None:
            return TestResult(
                case=case,
                passed=False,
                actual_text="",
                failure_reason=f"timeout ({self.timeout}s) — ответа нет",
                elapsed=elapsed,
            )

        # --- Проверки ---
        reason = _assert_response(case, actual)
        return TestResult(
            case=case,
            passed=reason is None,
            actual_text=actual,
            failure_reason=reason or "",
            elapsed=elapsed,
        )

    # ------------------------------------------------------------------
    # Запуск всего набора
    # ------------------------------------------------------------------

    async def run_all(
        self, cases: list[TestCase] | None = None
    ) -> list[TestResult]:
        """Запустить все (или выбранные) тест-кейсы последовательно."""
        targets = cases or TEST_CASES
        results: list[TestResult] = []
        for case in targets:
            result = await self.run_one(case)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            snippet = (result.actual_text[:80] + "…") if len(result.actual_text) > 80 else result.actual_text
            print(f"  [{status}] {case.name} ({result.elapsed:.1f}s) — {snippet or result.failure_reason}")
            # Небольшая пауза между тестами чтобы не перегружать Краба
            await asyncio.sleep(1.5)
        return results


# ---------------------------------------------------------------------------
# Проверка ответа
# ---------------------------------------------------------------------------

def _assert_response(case: TestCase, actual: str) -> str | None:
    """Проверить ответ по правилам кейса. Вернуть None если OK, иначе текст ошибки."""
    if len(actual) < case.min_length:
        return f"слишком короткий ответ: {len(actual)} < {case.min_length}"

    if case.max_length is not None and len(actual) > case.max_length:
        return f"слишком длинный ответ: {len(actual)} > {case.max_length}"

    for pat in case.must_contain:
        if pat not in actual:
            return f"не найдено обязательное: {pat!r}"

    for pat in case.must_not_contain:
        if pat in actual:
            return f"найдено запрещённое: {pat!r}"

    return None


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

def _render_report(results: list[TestResult], elapsed_total: float) -> str:
    """Сгенерировать markdown-отчёт."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# E2E Smoke Test Results",
        f"",
        f"**Run:** {ts}  ",
        f"**Total:** {passed}/{total} passed  ",
        f"**Elapsed:** {elapsed_total:.1f}s",
        f"",
        f"| Test | Status | Elapsed | Snippet | Reason |",
        f"|------|--------|---------|---------|--------|",
    ]

    for r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        snippet = (r.actual_text[:60] + "…") if len(r.actual_text) > 60 else r.actual_text
        snippet = snippet.replace("|", "\\|").replace("\n", " ")
        reason = r.failure_reason.replace("|", "\\|")
        lines.append(
            f"| `{r.case.name}` | {status} | {r.elapsed:.1f}s | {snippet} | {reason} |"
        )

    lines += [
        f"",
        f"## Details",
        f"",
    ]
    for r in results:
        lines += [
            f"### `{r.case.name}` — {'PASS' if r.passed else 'FAIL'}",
            f"**Description:** {r.case.description}",
            f"**Message sent:** `{r.case.message}`",
            f"**Actual response:**",
            f"```",
            r.actual_text[:500] or "(пусто)",
            f"```",
            f"",
        ]
        if not r.passed:
            lines.append(f"**Failure:** {r.failure_reason}")
            lines.append("")

    return "\n".join(lines)


def save_report(report: str) -> None:
    """Сохранить отчёт в docs/E2E_RESULTS_LATEST.md."""
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(report, encoding="utf-8")
    print(f"\nОтчёт сохранён: {RESULTS_PATH}")


# ---------------------------------------------------------------------------
# Получение chat_id
# ---------------------------------------------------------------------------

async def resolve_owner_chat_id(mcp: MCPTelegramClient) -> int | None:
    """Получить chat_id владельца — из env, panel API, или dialogs."""
    import os

    # 1. Из env
    env_val = os.environ.get(OWNER_CHAT_ID_ENV)
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass

    # 2. Из Owner Panel
    cid = await get_owner_chat_id_from_panel()
    if cid:
        return cid

    # 3. Из dialogs — ищем первый personal диалог (is_self или saved)
    try:
        dialogs = await mcp.get_dialogs()
        for d in dialogs:
            if d.get("is_self") or d.get("type") == "saved":
                return d.get("id") or d.get("chat_id")
        # Fallback: первый диалог который выглядит как DM
        for d in dialogs:
            if d.get("type") in ("private", "user", "dm"):
                return d.get("id") or d.get("chat_id")
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> int:
    print("=== Krab E2E Smoke Test Runner ===")

    mcp = MCPTelegramClient()

    # Проверить доступность MCP
    if not await mcp.health():
        print(f"WARN: MCP сервер {MCP_BASE} недоступен. Проверьте krab-yung-nagato LaunchAgent.")
        if not args.force:
            print("Запустите с --force чтобы продолжить (тесты завершатся timeout).")
            return 1

    # Получить chat_id
    chat_id = args.chat_id
    if not chat_id:
        print("Определяем owner chat_id…")
        chat_id = await resolve_owner_chat_id(mcp)
    if not chat_id:
        print(
            f"ERR: не удалось определить owner chat_id.\n"
            f"Укажите --chat-id <id> или задайте env {OWNER_CHAT_ID_ENV}=<id>"
        )
        return 1

    print(f"Target chat_id: {chat_id}")

    # Фильтрация тестов
    selected: list[TestCase] = TEST_CASES
    if args.test:
        selected = [c for c in TEST_CASES if c.name == args.test]
        if not selected:
            names = [c.name for c in TEST_CASES]
            print(f"ERR: тест {args.test!r} не найден. Доступные: {names}")
            return 1

    print(f"Запускаем {len(selected)}/{len(TEST_CASES)} тестов (timeout={args.timeout}s)…\n")

    runner = E2ESmokeRunner(
        chat_id=chat_id,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    t0 = time.monotonic()
    results = await runner.run_all(selected)
    elapsed_total = time.monotonic() - t0

    # Итоговая сводка
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{'='*40}")
    print(f"Итого: {passed}/{total} passed ({elapsed_total:.1f}s)")
    print(f"{'='*40}")

    if args.verbose:
        for r in results:
            if not r.passed:
                print(f"\n  FAIL [{r.case.name}]: {r.failure_reason}")
                print(f"  Actual: {r.actual_text[:200]!r}")

    # Сохранить отчёт
    report = _render_report(results, elapsed_total)
    if not args.no_save:
        save_report(report)

    return 0 if passed == total else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Krab E2E smoke test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"Таймаут ожидания ответа в секундах (default: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument("--chat-id", type=int, dest="chat_id", help="Owner chat_id в Telegram")
    parser.add_argument("--test", help="Запустить только один тест по имени")
    parser.add_argument("--force", action="store_true", help="Продолжить даже если MCP недоступен")
    parser.add_argument("--no-save", action="store_true", help="Не сохранять отчёт в docs/")
    args = parser.parse_args()

    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()

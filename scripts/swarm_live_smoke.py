#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swarm_live_smoke.py
~~~~~~~~~~~~~~~~~~~
Интеграционный smoke-тест роевого контура AgentRoom.

Зачем нужен:
1) проверить, что актуальный runtime-путь `AgentRoom -> route_query -> OpenClawClient`
   работает после рефакторинга;
2) иметь безопасный режим `mock` (без нагрузки на LM Studio/облако);
3) иметь режим `live` для реальной проверки канала перед ручным `!agent swarm`.

Связь с проектом:
- использует `src/core/swarm.py` как источник истины по ролям/циклам;
- в live-режиме использует `src/openclaw_client.py` и очищает тестовую сессию;
- пишет машиночитаемый отчёт в `temp/swarm_live_smoke_report.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Обеспечиваем импорт `src.*` при запуске из директории `scripts/`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.swarm import AgentRoom
from src.openclaw_client import openclaw_client

SERVICE_TOKENS = (
    "<|im_end|>",
    "<|im_start|>",
    "<tool_response>",
    "</tool_response>",
)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_header_head(prompt: str) -> str:
    """Возвращает «шапку» prompt до блока темы, чтобы корректно распознать роль."""
    low = str(prompt or "").lower()
    return low.split("тема:", 1)[0]


def _detect_role_name(prompt: str) -> str:
    """Грубая эвристика роли для диагностики в отчёте smoke-скрипта."""
    head = _extract_header_head(prompt)
    if "ты — аналитик" in head or "ты - аналитик" in head:
        return "analyst"
    if "ты — критик" in head or "ты - критик" in head:
        return "critic"
    if "ты — интегратор" in head or "ты - интегратор" in head:
        return "integrator"
    return "unknown"


class _MockRouter:
    """Детерминированный роутер для быстрого smoke без внешних зависимостей."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        self.calls.append(
            {
                "role": _detect_role_name(prompt),
                "skip_swarm": bool(skip_swarm),
                "prompt_len": len(str(prompt or "")),
            }
        )
        role = _detect_role_name(prompt)
        if role == "analyst":
            return "Анализ: выделены ключевые зависимости и порядок работ."
        if role == "critic":
            return "Критика: найден риск деградации при росте контекста."
        if role == "integrator":
            return "Итог: добавить smoke-guard, ограничить токены, сохранить отчёт."
        return "Роль не распознана, но контур ответа жив."


class _LiveRouter:
    """
    Live-адаптер AgentRoom к OpenClawClient.

    Почему отдельный класс:
    - не тянем pyrogram и обработчики команд в CLI-скрипт;
    - изолируем timeout и сбор runtime-route для отчёта.
    """

    def __init__(
        self,
        *,
        chat_id: str,
        system_prompt: str,
        force_cloud: bool,
        max_output_tokens: int,
        timeout_sec: float,
    ) -> None:
        self.chat_id = chat_id
        self.system_prompt = system_prompt
        self.force_cloud = bool(force_cloud)
        self.max_output_tokens = int(max_output_tokens)
        self.timeout_sec = float(timeout_sec)
        self.calls: list[dict[str, Any]] = []
        self.routes: list[dict[str, Any]] = []

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        role = _detect_role_name(prompt)
        call_meta = {
            "role": role,
            "skip_swarm": bool(skip_swarm),
            "prompt_len": len(str(prompt or "")),
        }
        self.calls.append(call_meta)

        async def _collect() -> str:
            chunks: list[str] = []
            async for chunk in openclaw_client.send_message_stream(
                message=prompt,
                chat_id=self.chat_id,
                system_prompt=self.system_prompt,
                force_cloud=self.force_cloud,
                max_output_tokens=self.max_output_tokens,
            ):
                chunks.append(str(chunk))
            return "".join(chunks).strip()

        try:
            text = await asyncio.wait_for(_collect(), timeout=self.timeout_sec)
        except TimeoutError:
            text = "❌ Таймаут роли в live-smoke."

        route = openclaw_client.get_last_runtime_route()
        self.routes.append(
            {
                "role": role,
                "channel": str(route.get("channel") or ""),
                "model": str(route.get("model") or ""),
                "provider": str(route.get("provider") or ""),
                "status": str(route.get("status") or ""),
                "error_code": route.get("error_code"),
                "force_cloud": bool(route.get("force_cloud", self.force_cloud)),
            }
        )
        return text


def _contains_service_tokens(text: str) -> bool:
    normalized = str(text or "")
    return any(token in normalized for token in SERVICE_TOKENS)


def _contains_hard_error_markers(text: str) -> bool:
    payload = str(text or "").lower()
    # "❌" — основной user-visible маркер аварии в текущем проекте.
    return "❌" in payload or "model has crashed" in payload


def _is_response_non_empty(text: str) -> bool:
    return bool(str(text or "").strip())


def _expected_calls_count(roles_count: int, rounds: int) -> int:
    safe_rounds = max(1, int(rounds))
    return int(roles_count) * safe_rounds


async def _run_mock(topic: str, rounds: int, max_rounds: int, next_round_clip: int) -> dict[str, Any]:
    room = AgentRoom()
    router = _MockRouter()
    started = time.perf_counter()

    if rounds > 1:
        result = await room.run_loop(
            topic,
            router,
            rounds=rounds,
            max_rounds=max_rounds,
            next_round_clip=next_round_clip,
        )
    else:
        result = await room.run_round(topic, router)

    elapsed = round(time.perf_counter() - started, 3)
    checks = {
        "roles_count_is_3": len(room.roles) == 3,
        "response_not_empty": _is_response_non_empty(result),
        "contains_swarm_header": ("Swarm Loop" in result) if rounds > 1 else ("Swarm Room" in result),
        "contains_all_role_titles": all(label in result for label in ("Аналитик", "Критик", "Интегратор")),
        "no_service_tokens": not _contains_service_tokens(result),
        "no_hard_error_markers": not _contains_hard_error_markers(result),
        "calls_count_expected": len(router.calls) >= _expected_calls_count(len(room.roles), rounds),
    }
    return {
        "mode": "mock",
        "ok": all(bool(v) for v in checks.values()),
        "duration_sec": elapsed,
        "topic": topic,
        "rounds": int(rounds),
        "checks": checks,
        "calls_count": len(router.calls),
        "calls_preview": router.calls[:12],
        "result_preview": result[:1200],
    }


async def _run_live(
    *,
    topic: str,
    rounds: int,
    max_rounds: int,
    next_round_clip: int,
    chat_id: str,
    force_cloud: bool,
    max_output_tokens: int,
    timeout_sec: float,
    clear_session: bool,
    require_cloud_channel: bool,
) -> dict[str, Any]:
    room = AgentRoom()
    router = _LiveRouter(
        chat_id=chat_id,
        system_prompt=(
            "Ты инженерный ассистент Krab/OpenClaw. "
            "Отвечай структурированно и без служебных токенов."
        ),
        force_cloud=force_cloud,
        max_output_tokens=max_output_tokens,
        timeout_sec=timeout_sec,
    )
    started = time.perf_counter()
    try:
        if rounds > 1:
            result = await room.run_loop(
                topic,
                router,
                rounds=rounds,
                max_rounds=max_rounds,
                next_round_clip=next_round_clip,
            )
        else:
            result = await room.run_round(topic, router)
    finally:
        if clear_session:
            openclaw_client.clear_session(chat_id)

    elapsed = round(time.perf_counter() - started, 3)
    channels = {str(item.get("channel") or "") for item in router.routes}
    statuses = {str(item.get("status") or "") for item in router.routes}
    has_ok_cloud_channel = any(
        str(item.get("status") or "") == "ok"
        and str(item.get("channel") or "") == "openclaw_cloud"
        for item in router.routes
    )
    checks = {
        "roles_count_is_3": len(room.roles) == 3,
        "response_not_empty": _is_response_non_empty(result),
        "contains_swarm_header": ("Swarm Loop" in result) if rounds > 1 else ("Swarm Room" in result),
        "contains_all_role_titles": all(label in result for label in ("Аналитик", "Критик", "Интегратор")),
        "no_service_tokens": not _contains_service_tokens(result),
        "no_hard_error_markers": not _contains_hard_error_markers(result),
        "calls_count_expected": len(router.calls) >= _expected_calls_count(len(room.roles), rounds),
        "routes_recorded": len(router.routes) >= len(router.calls),
        "route_has_success_status": "ok" in statuses,
        "route_has_runtime_channel": bool(channels - {"planning", "error", ""}),
    }
    if force_cloud:
        checks["route_force_cloud_reflected"] = all(bool(item.get("force_cloud")) for item in router.routes)
    if require_cloud_channel:
        checks["route_has_ok_cloud_channel"] = has_ok_cloud_channel

    return {
        "mode": "live",
        "ok": all(bool(v) for v in checks.values()),
        "duration_sec": elapsed,
        "topic": topic,
        "rounds": int(rounds),
        "chat_id": chat_id,
        "force_cloud": bool(force_cloud),
        "max_output_tokens": int(max_output_tokens),
        "timeout_sec": float(timeout_sec),
        "require_cloud_channel": bool(require_cloud_channel),
        "checks": checks,
        "calls_count": len(router.calls),
        "routes_count": len(router.routes),
        "routes": router.routes,
        "result_preview": result[:1500],
        "raw_result_tail": result[-1200:],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Swarm smoke для AgentRoom (mock/live).")
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        default="mock",
        help="mock — без внешних вызовов, live — через OpenClawClient.",
    )
    parser.add_argument(
        "--topic",
        default="Проверка роя после рефакторинга",
        help="Тема для раунда/loop.",
    )
    parser.add_argument("--rounds", type=int, default=1, help="Количество роевых раундов (1 = run_round).")
    parser.add_argument("--max-rounds", type=int, default=3, help="Ограничение для run_loop.")
    parser.add_argument("--next-round-clip", type=int, default=2400, help="Обрезка контекста для следующего раунда.")
    parser.add_argument("--chat-id", default="", help="Явный chat_id для live (по умолчанию генерируется).")
    parser.add_argument("--force-cloud", action="store_true", help="Live: принудительно запретить local fallback.")
    parser.add_argument("--max-output-tokens", type=int, default=700, help="Live: лимит токенов на роль.")
    parser.add_argument("--timeout-sec", type=float, default=180.0, help="Live: timeout на одну роль.")
    parser.add_argument(
        "--no-clear-session",
        action="store_true",
        help="Live: не очищать тестовую сессию после прогона.",
    )
    parser.add_argument(
        "--require-cloud-channel",
        action="store_true",
        help="Live: считать smoke успешным только если есть хотя бы один OK route через openclaw_cloud.",
    )
    parser.add_argument(
        "--output",
        default="temp/swarm_live_smoke_report.json",
        help="Путь к JSON-отчёту.",
    )
    return parser


def _sanitize_rounds(value: int, *, max_rounds: int) -> int:
    return max(1, min(int(value), max(1, int(max_rounds))))


def _collect_live_chat_id(explicit_chat_id: str) -> str:
    if explicit_chat_id.strip():
        return explicit_chat_id.strip()
    stamp = int(time.time())
    return f"swarm:live-smoke:{stamp}"


def _dump_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    rounds = _sanitize_rounds(args.rounds, max_rounds=args.max_rounds)
    if str(args.mode) == "mock":
        return await _run_mock(
            topic=str(args.topic),
            rounds=rounds,
            max_rounds=int(args.max_rounds),
            next_round_clip=int(args.next_round_clip),
        )

    chat_id = _collect_live_chat_id(str(args.chat_id or ""))
    return await _run_live(
        topic=str(args.topic),
        rounds=rounds,
        max_rounds=int(args.max_rounds),
        next_round_clip=int(args.next_round_clip),
        chat_id=chat_id,
        force_cloud=bool(args.force_cloud),
        max_output_tokens=int(args.max_output_tokens),
        timeout_sec=float(args.timeout_sec),
        clear_session=not bool(args.no_clear_session),
        require_cloud_channel=bool(args.require_cloud_channel),
    )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    started_at = _now_iso_utc()
    report: dict[str, Any]

    print(f"🐝 Swarm live smoke: mode={args.mode}, rounds={args.rounds}")
    report = asyncio.run(_run(args))
    report["generated_at_utc"] = started_at
    report["script"] = "scripts/swarm_live_smoke.py"

    out_path = Path(str(args.output))
    _dump_report(out_path, report)

    ok = bool(report.get("ok"))
    print("✅ Swarm smoke пройден." if ok else "❌ Swarm smoke не пройден.")
    print(f"📄 Отчёт: {out_path}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

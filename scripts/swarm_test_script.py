#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swarm_test_script.py (восстановление утраченного smoke-инструмента роя).

Зачем нужен скрипт:
- быстро проверить, что роевой контур AgentRoom жив после рефакторинга;
- валидировать роли и формат ответа без нагрузки на LM Studio/облако;
- дать one-click проверку перед ручным запуском `!agent swarm`.

Связь с проектом:
- использует `src/core/swarm.py` (AgentRoom);
- не зависит от OpenClaw transport, работает через встроенный mock-router.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Гарантируем импорт `src.*` при запуске скрипта напрямую из `scripts/`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.swarm import AgentRoom


class _MockSwarmRouter:
    """Минимальный роутер для deterministic smoke-проверки AgentRoom."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def route_query(self, prompt: str, skip_swarm: bool = False, **_: Any) -> str:
        """Возвращает предсказуемый текст в зависимости от роли из prompt."""
        self.calls.append({"prompt": prompt, "skip_swarm": skip_swarm})
        low = str(prompt or "").lower()
        # Важно читать именно "шапку" prompt до блока темы/контекста,
        # иначе в prompt критика/интегратора встречается текст аналитика
        # и role-detection становится ложноположительным.
        head = low.split("тема:", 1)[0]
        if "ты — аналитик" in head or "ты - аналитик" in head:
            return "Анализ: ключевые факторы и входные данные выделены."
        if "ты — критик" in head or "ты - критик" in head:
            return "Критика: найден риск перегруза контекста и неучтённый edge-case."
        if "ты — интегратор" in head or "ты - интегратор" in head:
            return "Итог: применить guard, прогнать smoke, зафиксировать отчёт."
        return "Роль не распознана, но роутер отвечает."


async def _run_swarm_smoke() -> dict[str, Any]:
    """Выполняет round + loop проверки и возвращает отчёт."""
    room = AgentRoom()
    router = _MockSwarmRouter()

    topic = "Проверка роя после рефакторинга"
    round_result = await room.run_round(topic, router)
    loop_result = await room.run_loop(topic, router, rounds=2, max_rounds=3, next_round_clip=2400)

    checks = {
        "roles_count_is_3": len(room.roles) == 3,
        "round_contains_header": "Swarm Room" in round_result,
        "round_contains_all_roles": all(token in round_result for token in ("Аналитик", "Критик", "Интегратор")),
        "loop_contains_header": "Swarm Loop" in loop_result,
        "loop_contains_round_1": "Раунд 1/2" in loop_result,
        "loop_contains_round_2": "Раунд 2/2" in loop_result,
        "router_called_enough": len(router.calls) >= 9,  # 3 роли round + 6 ролей в loop
    }
    ok = all(bool(v) for v in checks.values())
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "checks": checks,
        "calls_count": len(router.calls),
        "round_preview": round_result[:400],
        "loop_preview": loop_result[:400],
    }


def main() -> int:
    print("🐝 Swarm smoke: проверяю AgentRoom (mock router)...")
    report = asyncio.run(_run_swarm_smoke())

    out_path = Path("temp/swarm_smoke_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if report.get("ok"):
        print("✅ Swarm smoke пройден.")
    else:
        print("❌ Swarm smoke не пройден.")
    print(f"📄 Отчёт: {out_path}")
    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/skill_curator_analyze.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CLI entry point для SkillCurator Step 2 — LLM analyzer.

Использование:
    venv/bin/python scripts/skill_curator_analyze.py [--team TEAM] [--no-save] [--model MODEL]

Примеры:
    # Анализировать все 4 команды (по умолчанию)
    venv/bin/python scripts/skill_curator_analyze.py

    # Только команду coders
    venv/bin/python scripts/skill_curator_analyze.py --team coders

    # Без сохранения отчёта на диск
    venv/bin/python scripts/skill_curator_analyze.py --no-save

    # С конкретной моделью
    venv/bin/python scripts/skill_curator_analyze.py --model gemini-3-pro-preview

Выходной отчёт сохраняется в:
    ~/.openclaw/krab_runtime_state/skill_curator_reports/<timestamp>-<teams>.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для запуска из любого каталога
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SkillCurator Step 2 — LLM analyzer для swarm prompts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--team",
        choices=["traders", "coders", "analysts", "creative"],
        default=None,
        help="Команда для анализа. По умолчанию — все 4 команды.",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="LLM-модель для анализа (default: gemini-3-flash-preview).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help="Не сохранять markdown-отчёт на диск.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Показать prompt для LLM без фактического вызова (для отладки).",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()

    from src.core.skill_curator_analyzer import (
        ARTIFACT_SAMPLE_SIZE,
        KNOWN_TEAMS,
        AnalysisReport,
        _build_analyzer_prompt,
        _load_team_artifacts,
        analyze_team_prompts,
    )
    from src.core.swarm_team_prompts import get_team_system_prompt

    teams_to_show = [args.team] if args.team else list(KNOWN_TEAMS)

    # Dry-run режим — показываем промпты без LLM-вызова
    if args.dry_run:
        print("=== DRY RUN: показываем LLM-промпты без вызова ===\n")
        for team in teams_to_show:
            prompt = get_team_system_prompt(team)
            samples = _load_team_artifacts(team, limit=ARTIFACT_SAMPLE_SIZE)
            llm_prompt = _build_analyzer_prompt(team, prompt, samples)
            print(f"{'=' * 60}")
            print(f"TEAM: {team}")
            print(f"Artifacts found: {len(samples)}")
            print(f"{'=' * 60}")
            print(llm_prompt[:2000])  # Ограничиваем вывод
            print("...(truncated)" if len(llm_prompt) > 2000 else "")
            print()
        return 0

    print(f"SkillCurator Step 2 — анализируем: {', '.join(teams_to_show)}")
    print(f"Модель: {args.model}")
    print()

    report: AnalysisReport = await analyze_team_prompts(
        team=args.team,
        model=args.model,
        save_report=not args.no_save,
    )

    # Вывод отчёта в консоль
    print(report.to_markdown())

    if report.report_path:
        print(f"\nОтчёт сохранён: {report.report_path}")

    # Сводка по suggestions
    all_suggestions = report.all_suggestions()
    if all_suggestions:
        high_count = sum(1 for _, s in all_suggestions if s.priority == "high")
        print(f"\nИтого suggestions: {len(all_suggestions)} ({high_count} high-priority)")
    else:
        print("\nSuggestions не сформированы (LLM недоступен или нет проблем).")

    # Проверяем ошибки
    errors = [(t, r.error) for t, r in report.teams.items() if r.error]
    if errors:
        for team, err in errors:
            print(f"ОШИБКА [{team}]: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

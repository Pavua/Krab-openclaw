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
        description="SkillCurator Step 2/5/6 — LLM analyzer + A/B runner для swarm prompts",
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
    # Step 5: A/B runner (Wave 53-A)
    parser.add_argument(
        "--ab-test",
        metavar="TEAM",
        default=None,
        choices=["traders", "coders", "analysts", "creative"],
        help="(Step 5) Запустить A/B comparison для команды с mock-раундами.",
    )
    parser.add_argument(
        "--ab-rounds",
        type=int,
        default=5,
        help="Количество раундов для A/B теста (default: 5).",
    )
    parser.add_argument(
        "--ab-candidate",
        metavar="PROMPT",
        default=None,
        help='Candidate prompt для A/B теста (строка). Если не задан — используется текущий prompt + " [improved]".',
    )
    parser.add_argument(
        "--ab-threshold",
        type=float,
        default=0.15,
        help="Порог delta_score для auto-apply gate (default: 0.15).",
    )
    # Step 6: apply pending (Wave 53-A)
    parser.add_argument(
        "--apply-pending",
        action="store_true",
        default=False,
        help="(Step 6) Показать pending improvements и запросить owner-подтверждение для применения.",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()

    # -- Step 5: A/B test runner (Wave 53-A) ----------------------------------
    if args.ab_test:
        return await _run_ab_test(args)

    # -- Step 6: apply pending (Wave 53-A) ------------------------------------
    if args.apply_pending:
        return _run_apply_pending(args)

    # -- Step 2: LLM analyzer (default) --------------------------------------
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


async def _run_ab_test(args: argparse.Namespace) -> int:
    """Выполняет A/B comparison (Step 5, Wave 53-A)."""

    from src.core.skill_curator import (
        SkillCuratorABRunner,
        auto_apply_if_threshold,
    )
    from src.core.swarm_team_prompts import get_team_system_prompt

    team = args.ab_test
    rounds = args.ab_rounds
    threshold = args.ab_threshold

    # Candidate prompt: явно передан или текущий + "[improved]" для demo
    if args.ab_candidate:
        candidate = args.ab_candidate
    else:
        try:
            current = get_team_system_prompt(team)
        except Exception:
            current = ""
        candidate = current.strip() + " [improved]" if current.strip() else "[improved candidate]"

    print(f"SkillCurator Step 5 — A/B comparison: team={team}, rounds={rounds}")
    print(f"Threshold: {threshold}")
    print(f"Candidate prompt length: {len(candidate)} chars")
    print()

    runner = SkillCuratorABRunner()
    result = await runner.run_ab_comparison(team, candidate, rounds=rounds)

    if not result.get("ok"):
        print(f"ОШИБКА: {result.get('error')}", file=sys.stderr)
        return 1

    # Вывод результата
    print(f"Baseline metrics: {result['baseline_metrics']}")
    print(f"Candidate metrics: {result['candidate_metrics']}")
    print(f"Delta: {result['delta']}")
    print(f"Delta score: {result['delta_score']:.4f}")
    print(f"Recommendation: {result['recommendation']}")
    print()

    # Step 6: auto-apply gate
    gate = auto_apply_if_threshold(
        team,
        candidate,
        result["delta_score"],
        threshold=threshold,
        metadata={"ab_rounds": rounds, "source": "cli"},
    )
    print(f"Auto-apply gate: queued={gate['queued']}, reason={gate['reason']}")
    if gate.get("entry_id"):
        print(f"Entry ID: {gate['entry_id']}")

    return 0


def _run_apply_pending(args: argparse.Namespace) -> int:
    """Показывает pending improvements и запрашивает owner-подтверждение (Step 6, Wave 53-A).

    НЕ применяет автоматически — owner должен ввести 'yes' для каждой записи.
    """

    from src.core.skill_curator import list_pending_improvements

    team_filter = args.team
    pending = list_pending_improvements(team=team_filter)

    if not pending:
        print("Очередь pending improvements пуста.")
        return 0

    print(f"Pending improvements: {len(pending)} записей")
    print()

    applied = 0
    for entry in pending:
        print(f"Entry: {entry['entry_id']}")
        print(f"  Team: {entry['team']}")
        print(f"  Delta score: {entry['delta_score']:.4f}")
        print(f"  Queued at: {entry['queued_at']}")
        print(f"  Prompt preview: {entry['candidate_prompt'][:120]}...")
        print()

        try:
            confirm = input("Применить? [yes/skip/abort]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nПрервано.")
            break

        if confirm == "abort":
            print("Прервано пользователем.")
            break
        elif confirm == "yes":
            # Реальное применение требует SkillCurator.apply_with_approval —
            # здесь только маркируем как "owner acknowledged".
            # Live-запись в prompt делается через !skills apply <entry_id> в Telegram.
            print(
                f"  → Запись {entry['entry_id']} отмечена. Используйте "
                f"'!skills apply {entry['entry_id']}' в Telegram для live-применения."
            )
            applied += 1
        else:
            print("  → Пропущено.")

    print(f"\nИтого: подтверждено {applied} из {len(pending)}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

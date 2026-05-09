# -*- coding: utf-8 -*-
"""
scripts/skill_curator_daily_loop.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 44-A: SkillCurator automated daily loop.

Каждый день (если KRAB_SKILL_CURATOR_DAILY_LOOP_ENABLED=1):
1. Для каждой команды (traders/coders/analysts/creative):
   - Запускает dry-run analyzer (Step 1 — read-only)
   - Сохраняет результаты в
     ~/.openclaw/krab_runtime_state/skill_curator/daily/{team}/{YYYY-MM-DD}.json

Раз в 7 дней (понедельник, если KRAB_SKILL_CURATOR_WEEKLY_AUTO_PROPOSE=1):
2. Накапливаем 7 daily analyses для каждой команды.
   Если recurring weakness (5+ из 7 дней показывают одинаковый failure pattern)
   → auto-propose (Step 2) — сохраняем в
   ~/.openclaw/krab_runtime_state/skill_curator/proposals/{team}_auto_{ts}.json

Раз в 14 дней (день 14 месяца, если соответствующие ENV включены):
3. Если есть auto-proposal pending → запуск A/B (Step 3)
   (только если KRAB_SKILL_CURATOR_AUTO_AB_ENABLED=1)
4. Если есть completed A/B → evaluate + apply (Step 4)
   (только если KRAB_SKILL_CURATOR_AUTO_APPLY_ENABLED=1)

Все ENV gates default OFF — manual control при запуске.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Добавляем корень проекта в путь (если запускаем напрямую)
sys.path.insert(0, "/Users/pablito/Antigravity_AGENTS/Краб")

# ---------------------------------------------------------------------------
# ENV gates — все default OFF (opt-in)
# ---------------------------------------------------------------------------

DAILY_ENABLED = os.environ.get("KRAB_SKILL_CURATOR_DAILY_LOOP_ENABLED", "0").strip() in {
    "1",
    "true",
    "yes",
}
WEEKLY_AUTO_PROPOSE = os.environ.get("KRAB_SKILL_CURATOR_WEEKLY_AUTO_PROPOSE", "0").strip() in {
    "1",
    "true",
    "yes",
}
AUTO_AB = os.environ.get("KRAB_SKILL_CURATOR_AUTO_AB_ENABLED", "0").strip() in {
    "1",
    "true",
    "yes",
}
AUTO_APPLY = os.environ.get("KRAB_SKILL_CURATOR_AUTO_APPLY_ENABLED", "0").strip() in {
    "1",
    "true",
    "yes",
}

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

TEAMS = ["traders", "coders", "analysts", "creative"]

# Базовая директория для daily-анализов
DAILY_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "skill_curator" / "daily"

# Директория для auto-proposals
PROPOSALS_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "skill_curator" / "proposals"

# Минимальный порог: сколько дней из 7 должен встречаться pattern чтобы считаться recurring
RECURRING_THRESHOLD = 5

# Смотрим на топ failure pattern — сравниваем по нему
ANALYSIS_WINDOW_DAYS = 7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_str() -> str:
    """Возвращает YYYY-MM-DD в UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_ts() -> str:
    """Возвращает YYYYMMDDTHHMMSS в UTC."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _is_monday() -> bool:
    """True если сегодня понедельник (UTC)."""
    return datetime.now(timezone.utc).weekday() == 0  # 0 = Monday


def _is_biweekly_day() -> bool:
    """True если день 14 текущего месяца (bi-weekly trigger)."""
    return datetime.now(timezone.utc).day == 14


def _load_daily_files(team: str, last_n: int = 7) -> list[dict[str, Any]]:
    """Загружает последние `last_n` daily JSON-файлов для команды (newest-first).

    Читает только реально существующие файлы — missing days пропускаем тихо.
    """
    team_dir = DAILY_DIR / team
    if not team_dir.exists():
        return []

    files = sorted(team_dir.glob("*.json"), reverse=True)[:last_n]
    results: list[dict[str, Any]] = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            results.append(data)
        except (OSError, ValueError):
            # Повреждённый файл — пропускаем
            continue
    return results


def _detect_recurring_weakness(analyses: list[dict[str, Any]]) -> str | None:
    """Проверяет наличие recurring failure pattern в наборе daily analyses.

    Возвращает имя pattern если он встречается в >= RECURRING_THRESHOLD анализах,
    иначе None.
    """
    if len(analyses) < RECURRING_THRESHOLD:
        return None

    # Считаем топ failure_pattern по всем анализам
    pattern_counter: dict[str, int] = {}
    for analysis in analyses:
        # failure_patterns — list[list[str, int]] или list[list]
        fp = analysis.get("failure_patterns") or []
        if fp:
            # Берём только топ-1 pattern этого дня
            top = fp[0]
            if isinstance(top, (list, tuple)) and len(top) >= 1:
                tag = str(top[0])
            else:
                tag = str(top)
            pattern_counter[tag] = pattern_counter.get(tag, 0) + 1

    # Ищем pattern с порогом
    for tag, count in pattern_counter.items():
        if count >= RECURRING_THRESHOLD:
            return tag

    return None


# ---------------------------------------------------------------------------
# Step 1: Daily dry-run
# ---------------------------------------------------------------------------


async def run_daily() -> int:
    """Запускает dry-run анализ для всех команд, сохраняет JSON.

    Возвращает количество успешно обработанных команд.
    """
    if not DAILY_ENABLED:
        print("KRAB_SKILL_CURATOR_DAILY_LOOP_ENABLED=0, skip.")
        return 0

    # Импортируем здесь чтобы не падать при import-time если venv неполный
    from src.core.skill_curator import SkillCurator
    from src.core.skill_curator import skill_curator as _default_curator

    curator: SkillCurator = _default_curator
    today = _today_str()
    success_count = 0

    for team in TEAMS:
        try:
            # Step 1: sync dry-run analysis
            report = curator.analyze_recent_rounds(team=team, days=ANALYSIS_WINDOW_DAYS)

            # Сериализуем CuratorReport → dict
            analysis: dict[str, Any] = {
                "team": report.team,
                "date": today,
                "rounds_analyzed": report.rounds_analyzed,
                "success_rate": report.success_rate,
                "failure_patterns": report.failure_patterns,
                "successful_patterns": report.successful_patterns,
                "distinct_topics": report.distinct_topics,
                "recurring_failure_tags": report.recurring_failure_tags,
                "window_days": report.window_days,
                "generated_at": report.generated_at,
            }

            # Создаём директорию и сохраняем
            target_dir = DAILY_DIR / team
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{today}.json"
            target_file.write_text(
                json.dumps(analysis, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[daily] team={team} analysis saved → {target_file.name}")
            success_count += 1

        except Exception as exc:  # noqa: BLE001
            # Одна команда не должна блокировать остальные
            print(f"[daily] team={team} analysis FAILED: {exc}")

    return success_count


# ---------------------------------------------------------------------------
# Step 2: Weekly auto-propose (только понедельник)
# ---------------------------------------------------------------------------


async def run_weekly_propose() -> int:
    """Понедельник: анализируем 7 daily analyses → если recurring weakness → propose.

    Возвращает количество команд для которых был создан auto-proposal.
    """
    if not WEEKLY_AUTO_PROPOSE:
        return 0

    if not _is_monday():
        print("[weekly] Not Monday — skip auto-propose.")
        return 0

    from src.core.skill_curator import SkillCurator
    from src.core.skill_curator import skill_curator as _default_curator
    from src.core.swarm_team_prompts import get_team_system_prompt

    curator: SkillCurator = _default_curator
    proposed_count = 0

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    for team in TEAMS:
        try:
            # Загружаем последние 7 daily analyses
            analyses = _load_daily_files(team, last_n=7)
            if len(analyses) < RECURRING_THRESHOLD:
                print(
                    f"[weekly] team={team}: only {len(analyses)} daily analyses, "
                    f"need {RECURRING_THRESHOLD} — skip propose."
                )
                continue

            # Проверяем recurring weakness
            recurring_tag = _detect_recurring_weakness(analyses)
            if not recurring_tag:
                print(f"[weekly] team={team}: no recurring weakness detected — skip propose.")
                continue

            print(
                f"[weekly] team={team}: recurring pattern '{recurring_tag}' found "
                f"— triggering propose..."
            )

            # Получаем текущий промпт команды
            try:
                current_prompt = get_team_system_prompt(team)
            except Exception as exc:  # noqa: BLE001
                print(f"[weekly] team={team}: failed to get prompt: {exc}")
                current_prompt = ""

            # Используем последний daily report как входной для propose
            # Синтезируем CuratorReport из accumulated data
            from src.core.skill_curator import CuratorReport

            last_analysis = analyses[0]
            report = CuratorReport(
                team=team,
                rounds_analyzed=last_analysis.get("rounds_analyzed", 0),
                success_rate=last_analysis.get("success_rate", 0.0),
                failure_patterns=last_analysis.get("failure_patterns", []),
                successful_patterns=last_analysis.get("successful_patterns", []),
                distinct_topics=last_analysis.get("distinct_topics", 0),
                recurring_failure_tags=last_analysis.get("recurring_failure_tags", [recurring_tag]),
                window_days=ANALYSIS_WINDOW_DAYS,
                generated_at=last_analysis.get("generated_at", ""),
            )

            # Запускаем propose (async, best-effort)
            proposal = await curator.propose_prompt_update(
                team=team,
                current_prompt=current_prompt,
                report=report,
            )

            if proposal is not None:
                # Сохраняем дополнительную auto-meta копию в skill_curator/proposals/
                ts = _now_ts()
                auto_path = PROPOSALS_DIR / f"{team}_auto_{ts}.json"
                auto_data = proposal.to_dict()
                auto_data["auto_proposed"] = True
                auto_data["recurring_tag"] = recurring_tag
                auto_data["daily_analyses_count"] = len(analyses)
                auto_path.write_text(
                    json.dumps(auto_data, indent=2, default=str, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(
                    f"[weekly] team={team}: auto-proposal saved "
                    f"→ {auto_path.name} (id={proposal.proposal_id})"
                )
                proposed_count += 1
            else:
                print(f"[weekly] team={team}: propose returned None (provider unavailable?)")

        except Exception as exc:  # noqa: BLE001
            print(f"[weekly] team={team} auto-propose FAILED: {exc}")

    return proposed_count


# ---------------------------------------------------------------------------
# Step 3/4: Bi-weekly A/B и auto-apply (14-й день месяца)
# ---------------------------------------------------------------------------


async def run_biweekly_ab_and_apply() -> None:
    """14-й день месяца: запуск A/B для pending proposals и auto-apply для completed."""
    if not (AUTO_AB or AUTO_APPLY):
        return

    if not _is_biweekly_day():
        return

    from src.core.skill_curator import SkillCurator
    from src.core.skill_curator import skill_curator as _default_curator

    curator: SkillCurator = _default_curator

    for team in TEAMS:
        # Step 3: если есть pending proposals → запуск A/B
        if AUTO_AB:
            try:
                pending = [
                    p for p in curator.list_proposals(team=team) if p.get("status") == "pending"
                ]
                for proposal in pending[:1]:  # по одному A/B за цикл
                    pid = proposal.get("proposal_id") or ""
                    if not pid:
                        continue
                    ab_data = curator.start_ab_test(team, pid, n_rounds=10)
                    if ab_data:
                        print(
                            f"[biweekly] team={team}: A/B started "
                            f"ab_id={ab_data['ab_id']} for proposal={pid}"
                        )
                    else:
                        print(f"[biweekly] team={team}: A/B start skipped (already running?)")
            except Exception as exc:  # noqa: BLE001
                print(f"[biweekly] team={team} A/B start FAILED: {exc}")

        # Step 4: если есть decided A/B → evaluate + auto-apply
        if AUTO_APPLY:
            try:
                running_tests = curator.list_ab_tests(team=team, status="running")
                for ab in running_tests:
                    ab_id = ab.get("ab_id") or ""
                    if not ab_id:
                        continue
                    result, applied = await curator.evaluate_ab_test_and_apply(ab_id)
                    winner = result.get("winner")
                    print(
                        f"[biweekly] team={team}: A/B {ab_id} evaluated "
                        f"winner={winner} applied={applied}"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[biweekly] team={team} A/B evaluate FAILED: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    """Главная точка входа — последовательный запуск всех фаз."""
    print(
        f"[skill_curator_daily_loop] start "
        f"date={_today_str()} "
        f"daily={DAILY_ENABLED} "
        f"weekly_propose={WEEKLY_AUTO_PROPOSE} "
        f"auto_ab={AUTO_AB} "
        f"auto_apply={AUTO_APPLY}"
    )

    success = await run_daily()
    print(f"[skill_curator_daily_loop] daily done, {success}/{len(TEAMS)} teams OK")

    await run_weekly_propose()
    await run_biweekly_ab_and_apply()

    print("[skill_curator_daily_loop] done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

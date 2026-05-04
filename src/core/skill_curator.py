# -*- coding: utf-8 -*-
"""
src/core/skill_curator.py
~~~~~~~~~~~~~~~~~~~~~~~~~

SkillCurator — Wave 14-I (Step 1/4): dry-run analyzer (read-only).

Анализирует последние раунды команд свёрма и формирует ``CuratorReport``
без мутаций состояния и без вызовов LLM. Источники данных:

- ``swarm_memory`` — FIFO 50 раундов на команду (`SwarmRunRecord`),
  metadata содержит artifact_path / verifier score / reactions (при наличии).
- ``swarm_artifact_store`` — файлы артефактов с полем `verification`
  (``{passed, score, issues, suggestions}``).

Дизайн в `docs/architecture/SKILL_CURATOR_DESIGN.md`. Здесь намеренно реализована
только Step 1 — отчёт-анализ без LLM, A/B и mutations. Последующие шаги
(Sessions 35-37) добавят `propose_prompt_update`, `apply_with_approval`,
`rollback`, A/B framework и cron triggers.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .logger import get_logger
from .skill_curator_state import CURATOR_STATE_PATH, CuratorState
from .swarm_artifact_store import swarm_artifact_store as default_artifact_store
from .swarm_memory import SwarmRunRecord
from .swarm_memory import swarm_memory as default_memory

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Storage layout (per design § Storage schema)
# ---------------------------------------------------------------------------

CURATOR_BASE_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "curator"
CURATOR_REPORTS_DIR = CURATOR_BASE_DIR / "reports"
CURATOR_PROPOSALS_DIR = CURATOR_BASE_DIR / "proposals"
CURATOR_ARCHIVE_DIR = CURATOR_BASE_DIR / "prompts_archive"
CURATOR_AB_TESTS_DIR = CURATOR_BASE_DIR / "ab_tests"

# Rate-limit: не чаще 1 apply в 7 дней на команду
_APPLY_RATE_LIMIT_DAYS = 7


def ensure_curator_dirs(base: Path | None = None) -> Path:
    """Создаёт `~/.openclaw/krab_runtime_state/curator/{reports,prompts_archive,ab_tests,proposals}`.

    Не пишет state.json (atomic write через `CuratorState.save_atomic`), только
    разворачивает директории — этого достаточно, чтобы последующая запись
    отчёта/proposal не споткнулась.
    """

    root = base or CURATOR_BASE_DIR
    for sub in ("reports", "prompts_archive", "ab_tests", "proposals"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Failure pattern signatures — простой regex-кластеринг для error-сообщений
# ---------------------------------------------------------------------------

_FAILURE_SIGNATURES: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"\b(timeout|timed?\s*out|deadline\s+exceeded)\b", re.IGNORECASE)),
    (
        "provider_unavailable",
        re.compile(
            r"\b(provider[_\s-]*unavailable|503|service\s+unavailable|upstream)\b",
            re.IGNORECASE,
        ),
    ),
    ("rate_limit", re.compile(r"\b(rate[_\s-]*limit(ed)?|429|quota|flood)\b", re.IGNORECASE)),
    (
        "auth",
        re.compile(r"\b(401|403|unauthori[sz]ed|forbidden|invalid[_\s]*key)\b", re.IGNORECASE),
    ),
    ("no_data", re.compile(r"\b(no[_\s-]*data|empty|нет\s+данных|пуст)\b", re.IGNORECASE)),
    ("network", re.compile(r"\b(network|connection|dns|socket|refused)\b", re.IGNORECASE)),
    (
        "parse_error",
        re.compile(r"\b(parse|decode|json|malformed|invalid\s+(json|response))\b", re.IGNORECASE),
    ),
    (
        "tool_error",
        re.compile(r"\b(tool[_\s-]*(error|failed)|execution\s+failed)\b", re.IGNORECASE),
    ),
    (
        "model_error",
        re.compile(
            r"\b(model[_\s-]*(error|unavailable|overloaded)|inference[_\s-]*failed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "internal_error",
        re.compile(r"\b(internal\s+error|500|traceback|exception)\b", re.IGNORECASE),
    ),
]


# Слова, исключаемые из анализа success patterns
_STOPWORDS = frozenset(
    {
        "это",
        "что",
        "как",
        "для",
        "при",
        "был",
        "была",
        "были",
        "есть",
        "или",
        "так",
        "если",
        "тоже",
        "также",
        "того",
        "тому",
        "этом",
        "этих",
        "этой",
        "только",
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "about",
        "would",
        "could",
        "should",
        "have",
        "been",
        "will",
        "into",
        "than",
        "more",
        "less",
        "very",
        "some",
        "any",
        "all",
        "out",
        "use",
        "via",
        "per",
        "ok",
        "ok.",
    }
)


# ---------------------------------------------------------------------------
# CuratorReport
# ---------------------------------------------------------------------------


@dataclass
class CuratorReport:
    """Read-only анализ последних раундов команды.

    `proposed_prompt` / `metric_delta_estimate` / `confidence` — placeholders
    для последующих шагов (Step 2-4 не реализованы в dry-run).
    """

    team: str
    rounds_analyzed: int
    success_rate: float
    failure_patterns: list[tuple[str, int]] = field(default_factory=list)
    successful_patterns: list[tuple[str, int]] = field(default_factory=list)
    distinct_topics: int = 0
    recurring_failure_tags: list[str] = field(default_factory=list)
    window_days: int = 7
    proposed_prompt: str = ""
    metric_delta_estimate: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    generated_at: str = ""

    def to_markdown(self) -> str:
        """Форматирует отчёт в markdown-блок для Telegram / файла."""

        if self.rounds_analyzed == 0:
            return (
                f"## SkillCurator dry-run — {self.team} (last {self.window_days} days)\n"
                f"- Rounds analyzed: 0\n"
                f"- Нет данных за указанный период.\n"
            )

        success_pct = round(self.success_rate * 100, 1)
        succ_count = round(self.success_rate * self.rounds_analyzed)
        fail_top = ", ".join(f"{tag} ({cnt}×)" for tag, cnt in self.failure_patterns[:3]) or "—"
        succ_top = ", ".join(f'"{kw}" ({cnt}×)' for kw, cnt in self.successful_patterns[:3]) or "—"

        lines = [
            f"## SkillCurator dry-run — {self.team} (last {self.window_days} days)",
            f"- Rounds analyzed: {self.rounds_analyzed}",
            f"- Success rate: {success_pct}% ({succ_count}/{self.rounds_analyzed})",
            f"- Distinct topics: {self.distinct_topics}",
            f"- Top failure patterns: {fail_top}",
            f"- Top success patterns: {succ_top}",
        ]
        if self.recurring_failure_tags:
            lines.append(
                f"- Recommendation: prompt review focus на {', '.join(self.recurring_failure_tags)}"
            )
        else:
            lines.append("- Recommendation: команда работает стабильно, prompt review не требуется")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PromptProposal — Step 2 dataclass (LLM-предложение изменения промпта)
# ---------------------------------------------------------------------------


@dataclass
class PromptProposal:
    """LLM-сгенерированное предложение апдейта team prompt.

    Хранится в `~/.openclaw/.../curator/proposals/{team}-{ts}.json`
    и маркируется как **pending** до явного approve (Step 3).

    Поля:
    - ``team``           — имя команды (traders/coders/analysts/creative).
    - ``current_prompt`` — snapshot текущего системного промпта.
    - ``proposed_prompt`` — полный новый текст промпта.
    - ``proposed_diff``  — unified diff (current → proposed).
    - ``rationale``      — короткое обоснование от LLM.
    - ``focus``          — массив тегов (`failure_pattern`, `success_pattern`).
    - ``confidence``     — 0..1, эвристика на success_rate + наличие diff.
    - ``model``          — fully-qualified model id (e.g. gemini-3-flash-preview).
    - ``status``         — pending|approved|rejected|applied.
    - ``proposal_id``    — `{team}-{YYYY-MM-DD-HH-MM}` для CLI ссылок.
    """

    team: str
    current_prompt: str = ""
    proposed_prompt: str = ""
    proposed_diff: str = ""
    rationale: str = ""
    focus: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model: str = ""
    status: str = "pending"
    proposal_id: str = ""
    generated_at: str = ""
    report_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "current_prompt": self.current_prompt,
            "proposed_prompt": self.proposed_prompt,
            "proposed_diff": self.proposed_diff,
            "rationale": self.rationale,
            "focus": list(self.focus),
            "confidence": self.confidence,
            "model": self.model,
            "status": self.status,
            "proposal_id": self.proposal_id,
            "generated_at": self.generated_at,
            "report_summary": dict(self.report_summary),
        }


# ---------------------------------------------------------------------------
# SkillCurator
# ---------------------------------------------------------------------------


class SkillCurator:
    """Read-only куратор команд свёрма.

    Step 1/4 (Wave 14-I): только `analyze_recent_rounds`. Method'ы
    `propose_prompt_update / apply_with_approval / rollback` оставлены
    схемами — реализация в Sessions 35-37.
    """

    def __init__(
        self,
        *,
        memory: Any | None = None,
        artifact_store: Any | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self._memory = memory or default_memory
        self._artifacts = artifact_store or default_artifact_store
        self._base_dir = base_dir or CURATOR_BASE_DIR
        # Per-team mutex для apply/rollback — не даёт двум apply одной команды конкурировать
        self._team_locks: dict[str, asyncio.Lock] = {}
        # Per-team mutex для A/B тестов — один running A/B per team
        self._ab_team_locks: dict[str, asyncio.Lock] = {}

    # -- public API ----------------------------------------------------------

    def analyze_recent_rounds(self, team: str, days: int = 7) -> CuratorReport:
        """Анализирует последние `days` дней раундов команды.

        Read-only. Не пишет в state.json и не мутирует prompts.
        """

        t0 = time.monotonic()
        records = list(self._memory.get_recent(team, count=50))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        windowed = [r for r in records if _record_within(r, cutoff)]

        if not windowed:
            report = CuratorReport(
                team=team,
                rounds_analyzed=0,
                success_rate=0.0,
                window_days=days,
                generated_at=_now_iso(),
            )
            logger.info("skill_curator_dry_run_empty", team=team, days=days)
            return report

        # Артефакты для верификационных метрик. List comprehension не fail'ит на ошибках —
        # метаданные внутри SwarmRunRecord достаточно как fallback.
        try:
            artifacts = self._artifacts.list_artifacts(team=team, limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skill_curator_artifacts_unavailable", error=str(exc))
            artifacts = []

        success_count = 0
        failure_messages: list[str] = []
        success_topics: list[str] = []

        for rec in windowed:
            ok, fail_text = _classify_round(rec, artifacts)
            if ok:
                success_count += 1
                success_topics.append(rec.topic)
                # Успех тоже бывает «слабым» — оставляем result для keyword mining
                success_topics.append(rec.result_summary[:300])
            else:
                failure_messages.append(fail_text or rec.result_summary)

        failure_patterns = _cluster_failure_patterns(failure_messages)
        successful_patterns = _extract_success_keywords(success_topics)
        topics = {r.topic.strip().lower() for r in windowed if r.topic.strip()}
        recurring_tags = [tag for tag, cnt in failure_patterns if cnt >= 2][:3]

        report = CuratorReport(
            team=team,
            rounds_analyzed=len(windowed),
            success_rate=round(success_count / len(windowed), 4),
            failure_patterns=failure_patterns[:3],
            successful_patterns=successful_patterns[:3],
            distinct_topics=len(topics),
            recurring_failure_tags=recurring_tags,
            window_days=days,
            generated_at=_now_iso(),
        )

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "skill_curator_dry_run_done",
            team=team,
            rounds=report.rounds_analyzed,
            success_rate=report.success_rate,
            elapsed_ms=elapsed_ms,
        )
        return report

    def render_combined_markdown(self, reports: list[CuratorReport]) -> str:
        """Конкатенация нескольких отчётов в один markdown-блок."""

        return "\n\n".join(r.to_markdown() for r in reports if r is not None)

    def save_report(self, team: str, markdown: str) -> Path:
        """Сохраняет отчёт в `reports/{YYYY-MM-DD-team}.md`. Создаёт каталоги."""

        ensure_curator_dirs(self._base_dir)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_team = re.sub(r"[^a-z0-9_-]", "_", team.lower())[:20] or "all"
        path = self._base_dir / "reports" / f"{date}-{safe_team}.md"
        try:
            path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            logger.warning("skill_curator_report_save_failed", team=team, error=str(exc))
        return path

    # -- Step 2/4 (Wave 15-C): LLM proposer ---------------------------------

    async def propose_prompt_update(
        self,
        team: str,
        current_prompt: str,
        report: CuratorReport,
        *,
        provider: Any | None = None,
        model: str = "gemini-3-flash-preview",
    ) -> "PromptProposal | None":
        """Аux Gemini-3-flash предлагает diff текущего промпта.

        Best-effort: если provider недоступен / LLM-вызов падает → `None` +
        warning в лог (никаких raise наружу). Сохраняет proposal в
        ``proposals/{team}-{YYYY-MM-DD-HH-MM}.json`` и обновляет CuratorState.

        Args:
            team: имя команды.
            current_prompt: текущий системный промпт команды.
            report: результат `analyze_recent_rounds`.
            provider: тестовый override (mock LLM). Если ``None`` — берётся
                ``gemini_rerank_provider.default_provider()``.
            model: fully-qualified model id.

        Returns:
            ``PromptProposal`` (status=``pending``) или ``None`` если LLM-вызов
            не удался / API key отсутствует.
        """

        ensure_curator_dirs(self._base_dir)

        # 1. Resolve provider (lazy import избегает circular deps на тестах)
        if provider is None:
            try:
                from .gemini_rerank_provider import default_provider as _default

                provider = _default(model=model, timeout=15.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("curator_provider_resolve_failed", error=str(exc))
                provider = None

        if provider is None:
            logger.warning("curator_propose_skipped_no_provider", team=team)
            return None

        prompt_text = _build_proposer_prompt(team, current_prompt, report)

        # 2. Вызов LLM (best-effort)
        raw_text = ""
        try:
            raw_text = await provider.generate(prompt_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_propose_llm_failed", team=team, error=str(exc))
            return None

        parsed = _parse_proposer_response(raw_text, current_prompt)
        if not parsed:
            logger.warning("curator_propose_parse_failed", team=team, raw_len=len(raw_text or ""))
            return None

        proposed_prompt = parsed.get("proposed_prompt") or current_prompt
        proposed_diff = _unified_diff(current_prompt, proposed_prompt)
        rationale = parsed.get("rationale", "").strip()
        focus = parsed.get("focus") or _derive_focus(report)

        # Confidence — простая эвристика: low success_rate → выше уверенность
        # что нужно менять, но cap по наличию diff.
        confidence = _estimate_confidence(report, proposed_diff)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
        proposal_id = f"{_safe_team(team)}-{ts}"

        proposal = PromptProposal(
            team=team,
            current_prompt=current_prompt,
            proposed_prompt=proposed_prompt,
            proposed_diff=proposed_diff,
            rationale=rationale,
            focus=list(focus)[:5],
            confidence=confidence,
            model=model,
            status="pending",
            proposal_id=proposal_id,
            generated_at=_now_iso(),
            report_summary={
                "rounds_analyzed": report.rounds_analyzed,
                "success_rate": report.success_rate,
                "failure_patterns": list(report.failure_patterns),
                "successful_patterns": list(report.successful_patterns),
            },
        )

        # 3. Persist proposal JSON + state
        proposal_path = self._save_proposal(proposal)
        try:
            state = CuratorState.load(CURATOR_STATE_PATH)
            state.mark_run(team)
            state.mark_proposal(team, proposal_path)
            state.save_atomic(CURATOR_STATE_PATH)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_state_update_failed", team=team, error=str(exc))

        logger.info(
            "curator_proposal_saved",
            team=team,
            proposal_id=proposal_id,
            confidence=confidence,
            diff_lines=proposed_diff.count("\n"),
        )
        return proposal

    def _save_proposal(self, proposal: "PromptProposal") -> Path:
        """Сохраняет proposal JSON в ``proposals/{proposal_id}.json``."""

        ensure_curator_dirs(self._base_dir)
        path = self._base_dir / "proposals" / f"{proposal.proposal_id}.json"
        try:
            path.write_text(
                json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "curator_proposal_write_failed",
                team=proposal.team,
                error=str(exc),
            )
        return path

    def list_proposals(self, team: str | None = None) -> list[dict[str, Any]]:
        """Возвращает список pending/historical proposals (newest first)."""

        proposals_dir = self._base_dir / "proposals"
        if not proposals_dir.exists():
            return []
        entries: list[dict[str, Any]] = []
        for fp in sorted(proposals_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if team and (data.get("team") or "").lower() != team.lower():
                continue
            data["_path"] = str(fp)
            entries.append(data)
        return entries

    def load_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Читает proposal по id (без расширения). None если не найден."""

        path = self._base_dir / "proposals" / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data["_path"] = str(path)
        return data

    # -- Step 3/4: apply + rollback ------------------------------------------

    def _get_team_lock(self, team: str) -> asyncio.Lock:
        """Возвращает per-team asyncio.Lock, создавая при первом обращении."""

        key = team.lower()
        if key not in self._team_locks:
            self._team_locks[key] = asyncio.Lock()
        return self._team_locks[key]

    async def apply_with_approval(
        self,
        proposal_id: str,
        *,
        force: bool = False,
        idle_check: bool = True,
        state_path: Path | None = None,
    ) -> tuple[bool, str]:
        """Применяет approved proposal к live team prompt.

        Steps:
        1. Загрузить proposal из proposals/{proposal_id}.json.
        2. Проверить status == "pending" (или "approved"). "applied" → отказ.
        3. Mutex per-team — не даёт двум apply конкурировать.
        4. Idle check: swarm_channels.is_round_active → если busy и не force → отказ.
        5. Weekly rate-limit (1 apply / 7 дней / команда), bypassed при force=True.
        6. Archive snapshot текущего effective prompt.
        7. Apply overlay + обновить proposal status="applied".
        8. Return (True, "applied successfully").

        На любую ошибку — log + return (False, msg). Не поднимает исключений наружу.
        """

        _state_path = state_path or CURATOR_STATE_PATH

        # 1. Загружаем proposal
        proposal = self.load_proposal(proposal_id)
        if not proposal:
            msg = f"proposal not found: {proposal_id}"
            logger.warning("curator_apply_proposal_not_found", proposal_id=proposal_id)
            return False, msg

        team = (proposal.get("team") or "").lower()
        if not team:
            return False, "proposal missing team field"

        # 2. Проверяем статус
        status = proposal.get("status", "")
        if status == "applied":
            return False, f"proposal {proposal_id} already applied"
        if status not in {"pending", "approved"}:
            return False, f"proposal status is '{status}', expected pending/approved"

        proposed_prompt = proposal.get("proposed_prompt") or ""
        if not proposed_prompt.strip():
            return False, "proposal has empty proposed_prompt"

        # 3. Mutex per-team
        lock = self._get_team_lock(team)
        async with lock:
            return await self._do_apply(
                team=team,
                proposal_id=proposal_id,
                proposal=proposal,
                proposed_prompt=proposed_prompt,
                force=force,
                idle_check=idle_check,
                state_path=_state_path,
            )

    async def _do_apply(
        self,
        *,
        team: str,
        proposal_id: str,
        proposal: dict[str, Any],
        proposed_prompt: str,
        force: bool,
        idle_check: bool,
        state_path: Path,
    ) -> tuple[bool, str]:
        """Внутренняя реализация apply (под mutex)."""

        # 4. Idle check
        if idle_check and not force:
            try:
                from .swarm_channels import swarm_channels

                if swarm_channels.is_round_active(team):
                    return False, f"team {team} is busy (active swarm round), retry later"
            except Exception as exc:  # noqa: BLE001
                logger.warning("curator_idle_check_failed", team=team, error=str(exc))

        # 5. Weekly rate-limit
        if not force:
            try:
                state = CuratorState.load(state_path)
                last_apply_raw = state.last_apply_at.get(team, "")
                if last_apply_raw:
                    last_apply_dt = datetime.fromisoformat(last_apply_raw.replace("Z", "+00:00"))
                    if last_apply_dt.tzinfo is None:
                        last_apply_dt = last_apply_dt.replace(tzinfo=timezone.utc)
                    elapsed = datetime.now(timezone.utc) - last_apply_dt
                    # MEDIUM-2: используем total_seconds() вместо .days — иначе 23ч59м
                    # считалось бы 0 дней и blocking проходил некорректно.
                    _rate_limit_sec = _APPLY_RATE_LIMIT_DAYS * 86_400
                    if elapsed.total_seconds() < _rate_limit_sec:
                        elapsed_days_approx = elapsed.total_seconds() / 86_400
                        remaining_days_approx = (_rate_limit_sec - elapsed.total_seconds()) / 86_400
                        return (
                            False,
                            f"rate limit: last apply was {elapsed_days_approx:.2f}d ago, "
                            f"wait {remaining_days_approx:.2f}d more (or use force=True)",
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("curator_rate_limit_check_failed", team=team, error=str(exc))

        # 6. Archive текущего effective prompt
        try:
            from .swarm_team_prompts import get_team_system_prompt

            current_effective_prompt = get_team_system_prompt(team)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_get_prompt_failed", team=team, error=str(exc))
            current_effective_prompt = ""

        archive_path_str = ""
        try:
            state = CuratorState.load(state_path)
            overlay = state.get_overlay(team)
            version = (overlay.get("version", 0) if overlay else 0) + 1

            archive_dir = self._base_dir / "prompts_archive" / team
            archive_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            archive_path = archive_dir / f"v{version}_{ts}.md"
            archive_path.write_text(current_effective_prompt, encoding="utf-8")
            archive_path_str = str(archive_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_archive_failed", team=team, error=str(exc))
            version = 1

        # 7. Apply overlay + обновить state
        try:
            state = CuratorState.load(state_path)
            applied_at = _now_iso()
            state.apply_overlay(
                team,
                {
                    "prompt": proposed_prompt,
                    "proposal_id": proposal_id,
                    "applied_at": applied_at,
                    "version": version,
                    "archive_path": archive_path_str,
                },
            )
            state.mark_apply(team)
            state.save_atomic(state_path)
        except Exception as exc:  # noqa: BLE001
            msg = f"state save failed: {exc}"
            logger.error("curator_apply_state_failed", team=team, error=str(exc))
            return False, msg

        # Инвалидируем overlay-кеш в swarm_team_prompts
        try:
            from . import swarm_team_prompts as _stp

            _stp._invalidate_overlay_cache(team)
        except Exception:  # noqa: BLE001
            pass

        # 8. Помечаем proposal как applied
        try:
            proposal_path = self._base_dir / "proposals" / f"{proposal_id}.json"
            proposal_updated = dict(proposal)
            proposal_updated.update({"status": "applied", "applied_at": applied_at})
            proposal_updated.pop("_path", None)
            proposal_path.write_text(
                json.dumps(proposal_updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "curator_proposal_status_update_failed", proposal_id=proposal_id, error=str(exc)
            )

        logger.info(
            "curator_apply_success",
            team=team,
            proposal_id=proposal_id,
            version=version,
            archive_path=archive_path_str,
        )
        return True, f"applied successfully (version={version}, archive={archive_path_str})"

    async def rollback(
        self,
        team: str,
        *,
        version: int = -1,
        state_path: Path | None = None,
    ) -> tuple[bool, str]:
        """Откатывает overlay команды к предыдущей версии или к baseline.

        version=-1 → удалить overlay (вернуться к TEAM_PROMPTS baseline).
        version=N  → загрузить prompts_archive/{team}/v{N}_*.md → new overlay.
        """

        _state_path = state_path or CURATOR_STATE_PATH
        lock = self._get_team_lock(team.lower())
        async with lock:
            return await self._do_rollback(
                team=team.lower(),
                version=version,
                state_path=_state_path,
            )

    async def _do_rollback(
        self,
        *,
        team: str,
        version: int,
        state_path: Path,
    ) -> tuple[bool, str]:
        """Внутренняя реализация rollback (под mutex)."""

        state = CuratorState.load(state_path)
        overlay = state.get_overlay(team)

        if version == -1:
            # Возврат к baseline
            if overlay is None:
                return False, f"team {team} has no active overlay, nothing to rollback"
            state.clear_overlay(team)
            state.save_atomic(state_path)
            # Инвалидируем кеш
            try:
                from . import swarm_team_prompts as _stp

                _stp._invalidate_overlay_cache(team)
            except Exception:  # noqa: BLE001
                pass
            logger.info("curator_rollback_baseline", team=team)
            return True, f"team {team} prompt rolled back to baseline"

        # Rollback к конкретной версии
        archive_dir = self._base_dir / "prompts_archive" / team
        if not archive_dir.exists():
            return False, f"no archive for team {team}"

        # Ищем файл v{N}_*.md
        candidates = sorted(archive_dir.glob(f"v{version}_*.md"))
        if not candidates:
            return False, f"archive version {version} not found for team {team}"

        archive_file = candidates[-1]  # берём последний если несколько (edge case)
        try:
            restored_prompt = archive_file.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"failed to read archive file: {exc}"

        if not restored_prompt.strip():
            return False, f"archive version {version} is empty"

        # Новая версия = (current_version or 0) + 1
        new_version = (overlay.get("version", 0) if overlay else 0) + 1
        state.apply_overlay(
            team,
            {
                "prompt": restored_prompt,
                "proposal_id": f"rollback-v{version}",
                "applied_at": _now_iso(),
                "version": new_version,
                "archive_path": str(archive_file),
            },
        )
        state.save_atomic(state_path)

        try:
            from . import swarm_team_prompts as _stp

            _stp._invalidate_overlay_cache(team)
        except Exception:  # noqa: BLE001
            pass

        logger.info("curator_rollback_version", team=team, version=version, new_version=new_version)
        return (
            True,
            f"team {team} rolled back to archive version {version} (new version={new_version})",
        )

    # -- Step 4/4: A/B framework ---------------------------------------------

    def _get_ab_team_lock(self, team: str) -> asyncio.Lock:
        """Per-team asyncio.Lock для A/B тестов."""

        key = team.lower()
        if key not in self._ab_team_locks:
            self._ab_team_locks[key] = asyncio.Lock()
        return self._ab_team_locks[key]

    def _ab_tests_dir(self) -> Path:
        """Директория ab_tests под base_dir."""

        return self._base_dir / "ab_tests"

    def _ab_test_path(self, ab_id: str) -> Path:
        """Путь к JSON файлу конкретного A/B теста."""

        return self._ab_tests_dir() / f"{ab_id}.json"

    def _load_ab_test(self, ab_id: str) -> dict[str, Any] | None:
        """Читает A/B тест из файла. None если не найден/повреждён."""

        path = self._ab_test_path(ab_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_ab_test_atomic(self, data: dict[str, Any]) -> bool:
        """Атомарное сохранение A/B теста. Возвращает True при успехе."""

        ab_id = data.get("ab_id", "")
        if not ab_id:
            return False
        ab_dir = self._ab_tests_dir()
        ab_dir.mkdir(parents=True, exist_ok=True)
        path = self._ab_test_path(ab_id)
        tmp_fd, tmp_name = None, ""
        try:
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=".ab_test.", suffix=".tmp", dir=str(ab_dir))
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_save_failed", ab_id=ab_id, error=str(exc))
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            return False

    def _compute_metrics(self, rounds: list[dict[str, Any]]) -> dict[str, Any]:
        """Вычисляет агрегированные метрики по списку round-записей.

        Возвращает dict с ключами per-variant: control / candidate.
        """

        def _agg(variant_rounds: list[dict[str, Any]]) -> dict[str, Any]:
            if not variant_rounds:
                return {
                    "count": 0,
                    "success_rate": 0.0,
                    "cost_usd_avg": 0.0,
                    "latency_s_avg": 0.0,
                    "tool_calls_avg": 0.0,
                    "verifier_pass_rate": 0.0,
                }
            n = len(variant_rounds)
            successes = sum(1 for r in variant_rounds if r.get("success", False))
            verifier_passes = sum(1 for r in variant_rounds if r.get("verifier_pass", False))
            costs = [r.get("cost_usd") or 0.0 for r in variant_rounds]
            latencies = [r.get("latency_s") or 0.0 for r in variant_rounds]
            tool_calls = [r.get("tool_calls") or 0 for r in variant_rounds]
            return {
                "count": n,
                "success_rate": round(successes / n, 4),
                "cost_usd_avg": round(sum(costs) / n, 6),
                "latency_s_avg": round(sum(latencies) / n, 3),
                "tool_calls_avg": round(sum(tool_calls) / n, 2),
                "verifier_pass_rate": round(verifier_passes / n, 4),
            }

        control_rounds = [r for r in rounds if r.get("variant") == "control"]
        candidate_rounds = [r for r in rounds if r.get("variant") == "candidate"]
        return {
            "control": _agg(control_rounds),
            "candidate": _agg(candidate_rounds),
            "total_rounds": len(rounds),
        }

    def _decide_winner(
        self,
        metrics: dict[str, Any],
    ) -> tuple[Literal["control", "candidate", "tie"], str]:
        """Применяет winning criteria per design §4.

        Candidate wins если:
        - success_rate >= control + 0.05
        - AND cost_avg <= control_cost * 1.10
        - AND latency_avg <= control_latency * 1.10

        Returns (winner, reason).
        """

        ctrl = metrics.get("control", {})
        cand = metrics.get("candidate", {})

        ctrl_sr = ctrl.get("success_rate", 0.0)
        cand_sr = cand.get("success_rate", 0.0)
        ctrl_cost = ctrl.get("cost_usd_avg", 0.0)
        cand_cost = cand.get("cost_usd_avg", 0.0)
        ctrl_lat = ctrl.get("latency_s_avg", 0.0)
        cand_lat = cand.get("latency_s_avg", 0.0)

        # Критерий 1: success_rate
        sr_ok = cand_sr >= ctrl_sr + 0.05

        # Критерий 2: cost (если control cost = 0 — кандидат не хуже)
        cost_ok = cand_cost <= (ctrl_cost * 1.10) if ctrl_cost > 0 else True

        # Критерий 3: latency (если control latency = 0 — кандидат не хуже)
        lat_ok = cand_lat <= (ctrl_lat * 1.10) if ctrl_lat > 0 else True

        if sr_ok and cost_ok and lat_ok:
            reason = (
                f"candidate success_rate {cand_sr:.3f} >= control {ctrl_sr:.3f} + 0.05, "
                f"cost OK ({cand_cost:.4f} vs {ctrl_cost:.4f}*1.10), "
                f"latency OK ({cand_lat:.2f}s vs {ctrl_lat:.2f}s*1.10)"
            )
            return "candidate", reason

        reasons = []
        if not sr_ok:
            reasons.append(f"success_rate {cand_sr:.3f} < control {ctrl_sr:.3f} + 0.05")
        if not cost_ok:
            reasons.append(f"cost {cand_cost:.4f} > control {ctrl_cost:.4f}*1.10")
        if not lat_ok:
            reasons.append(f"latency {cand_lat:.2f}s > control {ctrl_lat:.2f}s*1.10")
        reason = "control wins: " + "; ".join(reasons)
        return "control", reason

    def start_ab_test(
        self,
        team: str,
        candidate_proposal_id: str,
        *,
        n_rounds: int = 10,
        state_path: Path | None = None,
    ) -> dict[str, Any] | None:
        """Создаёт A/B тест для команды.

        Возвращает None если для команды уже есть running A/B тест.
        Sync метод — не требует asyncio.
        """

        _state_path = state_path or CURATOR_STATE_PATH
        team_key = team.lower()

        # Проверяем наличие running теста
        try:
            state = CuratorState.load(_state_path)
            existing_ab_id = state.get_active_ab_test(team_key)
            if existing_ab_id:
                existing = self._load_ab_test(existing_ab_id)
                if existing and existing.get("status") == "running":
                    logger.warning("curator_ab_already_running", team=team, ab_id=existing_ab_id)
                    return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_state_check_failed", team=team, error=str(exc))

        # Загружаем proposal для получения candidate prompt
        proposal = self.load_proposal(candidate_proposal_id)
        if not proposal:
            logger.warning(
                "curator_ab_proposal_not_found",
                team=team,
                proposal_id=candidate_proposal_id,
            )
            return None

        candidate_prompt = proposal.get("proposed_prompt") or ""
        if not candidate_prompt.strip():
            logger.warning(
                "curator_ab_empty_candidate", team=team, proposal_id=candidate_proposal_id
            )
            return None

        # Снапшот текущего effective prompt как control
        try:
            from .swarm_team_prompts import get_team_system_prompt

            control_prompt = get_team_system_prompt(team_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_get_prompt_failed", team=team, error=str(exc))
            control_prompt = ""

        # Генерируем уникальный ab_id
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
        short_uuid = uuid.uuid4().hex[:8]
        ab_id = f"{_safe_team(team)}-{ts}-{short_uuid}"

        ab_data: dict[str, Any] = {
            "ab_id": ab_id,
            "team": team_key,
            "started_at": _now_iso(),
            "control_prompt": control_prompt,
            "candidate_prompt": candidate_prompt,
            "candidate_proposal_id": candidate_proposal_id,
            "n_rounds_target": max(1, n_rounds),
            "rounds": [],
            "status": "running",
            "decision": None,
            "decided_at": None,
            "decision_reason": None,
        }

        ensure_curator_dirs(self._base_dir)
        if not self._save_ab_test_atomic(ab_data):
            return None

        # Обновляем state
        try:
            state = CuratorState.load(_state_path)
            state.set_active_ab_test(team_key, ab_id)
            state.save_atomic(_state_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_state_save_failed", team=team, error=str(exc))

        logger.info("curator_ab_started", team=team, ab_id=ab_id, n_rounds=n_rounds)
        return ab_data

    def get_ab_test(self, ab_id: str) -> dict[str, Any] | None:
        """Читает A/B тест по ab_id. None если не найден."""

        return self._load_ab_test(ab_id)

    def get_active_ab_test(
        self, team: str, *, state_path: Path | None = None
    ) -> dict[str, Any] | None:
        """Возвращает данные активного A/B теста команды или None."""

        _state_path = state_path or CURATOR_STATE_PATH
        try:
            state = CuratorState.load(_state_path)
            ab_id = state.get_active_ab_test(team.lower())
        except Exception as exc:  # noqa: BLE001
            logger.warning("curator_ab_get_active_failed", team=team, error=str(exc))
            return None
        if not ab_id:
            return None
        return self._load_ab_test(ab_id)

    def select_variant(self, ab_id: str, round_id: str) -> Literal["control", "candidate"]:
        """Round-robin выбор варианта по round_id.

        Детерминированно через hash: чётный → control, нечётный → candidate.
        """

        h = hash(f"{ab_id}:{round_id}")
        return "candidate" if h % 2 else "control"

    def record_round_metric(
        self,
        ab_id: str,
        round_id: str,
        metrics: dict[str, Any],
    ) -> bool:
        """Добавляет метрику раунда в A/B тест (atomic update).

        Возвращает False если тест не найден.
        """

        data = self._load_ab_test(ab_id)
        if data is None:
            logger.warning("curator_ab_record_unknown", ab_id=ab_id)
            return False

        variant = self.select_variant(ab_id, round_id)
        entry: dict[str, Any] = {
            "round_id": round_id,
            "variant": variant,
            "recorded_at": _now_iso(),
        }
        entry.update(metrics)

        rounds = data.get("rounds") or []
        rounds.append(entry)
        data["rounds"] = rounds

        return self._save_ab_test_atomic(data)

    def evaluate_ab_test(self, ab_id: str) -> dict[str, Any]:
        """Вычисляет decision для A/B теста.

        Если rounds < n_rounds_target — возвращает промежуточный результат без decision.
        Если достаточно — определяет победителя и обновляет status="decided".
        Sync. Returns dict с ключами: ab_id, status, winner, reason, metrics.
        """

        data = self._load_ab_test(ab_id)
        if data is None:
            return {
                "ab_id": ab_id,
                "status": "not_found",
                "winner": None,
                "reason": "not found",
                "metrics": {},
            }

        rounds = data.get("rounds") or []
        n_target = int(data.get("n_rounds_target") or 10)
        metrics = self._compute_metrics(rounds)

        result: dict[str, Any] = {
            "ab_id": ab_id,
            "team": data.get("team"),
            "rounds_completed": len(rounds),
            "n_rounds_target": n_target,
            "metrics": metrics,
        }

        if len(rounds) < n_target:
            result["status"] = "running"
            result["winner"] = None
            result["reason"] = f"insufficient data: {len(rounds)}/{n_target} rounds"
            return result

        winner, reason = self._decide_winner(metrics)
        result["status"] = "decided"
        result["winner"] = winner
        result["reason"] = reason

        # Обновляем JSON файл
        data["status"] = "decided"
        data["decision"] = winner
        data["decided_at"] = _now_iso()
        data["decision_reason"] = reason
        self._save_ab_test_atomic(data)

        logger.info("curator_ab_decided", ab_id=ab_id, winner=winner, reason=reason)
        return result

    async def evaluate_ab_test_and_apply(
        self,
        ab_id: str,
        *,
        state_path: Path | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Async обёртка: evaluate + auto-apply если candidate wins.

        Весь блок evaluate→apply выполняется под одним team lock — это
        гарантирует атомарность: конкурентный вызов для той же team не
        увидит частичного состояния между evaluate и apply.

        Returns (evaluation_result, applied: bool).
        """
        # Определяем team из ab_id для получения правильного lock.
        # _load_ab_test читает только файл — без side-effects до lock.
        ab_data_pre = self._load_ab_test(ab_id)
        team = (ab_data_pre or {}).get("team", ab_id)
        lock = self._get_ab_team_lock(team)

        async with lock:
            # Под lock: evaluate и apply неразделимы — второй конкурентный
            # вызов будет ждать снаружи и увидит уже applied состояние.
            result = self.evaluate_ab_test(ab_id)
            applied = False

            if result.get("winner") == "candidate":
                data = self._load_ab_test(ab_id)
                proposal_id = (data or {}).get("candidate_proposal_id", "")
                if proposal_id:
                    try:
                        ok, msg = await self.apply_with_approval(
                            proposal_id,
                            force=True,
                            idle_check=False,
                            state_path=state_path,
                        )
                        applied = ok
                        result["auto_apply_msg"] = msg
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("curator_ab_auto_apply_failed", ab_id=ab_id, error=str(exc))
                        result["auto_apply_error"] = str(exc)

            return result, applied

    def cancel_ab_test(
        self,
        ab_id: str,
        *,
        reason: str = "manual",
        state_path: Path | None = None,
    ) -> bool:
        """Отменяет A/B тест. Обновляет state.active_ab_tests."""

        _state_path = state_path or CURATOR_STATE_PATH
        data = self._load_ab_test(ab_id)
        if data is None:
            return False

        data["status"] = "cancelled"
        data["decision_reason"] = f"cancelled: {reason}"
        data["decided_at"] = _now_iso()
        if not self._save_ab_test_atomic(data):
            return False

        team = data.get("team", "")
        if team:
            try:
                state = CuratorState.load(_state_path)
                current = state.get_active_ab_test(team)
                if current == ab_id:
                    state.clear_active_ab_test(team)
                    state.save_atomic(_state_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("curator_ab_cancel_state_failed", ab_id=ab_id, error=str(exc))

        logger.info("curator_ab_cancelled", ab_id=ab_id, reason=reason)
        return True

    def list_ab_tests(
        self,
        team: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Перечисляет A/B тесты из директории. Опциональная фильтрация по team/status."""

        ab_dir = self._ab_tests_dir()
        if not ab_dir.exists():
            return []

        results: list[dict[str, Any]] = []
        for fp in sorted(ab_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if team and (data.get("team") or "").lower() != team.lower():
                continue
            if status and (data.get("status") or "") != status:
                continue
            results.append(data)
        return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_within(rec: SwarmRunRecord, cutoff: datetime) -> bool:
    """Возвращает True если `rec.created_at >= cutoff`. Tolerant к bad data."""

    raw = rec.created_at or ""
    if not raw:
        return True  # без метки берём в окно — иначе теряем недавние записи
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def _classify_round(rec: SwarmRunRecord, artifacts: list[dict[str, Any]]) -> tuple[bool, str]:
    """Возвращает `(success, failure_text)`.

    Приоритет:
    1. `metadata['verifier']` или `metadata['verifier_pass']`.
    2. `metadata['reaction']` (👍/👎) если есть.
    3. Совпадение `result_summary` с error-паттернами → fail.
    4. Иначе — success (heuristic positive).
    """

    md = rec.metadata or {}
    verifier = md.get("verifier") or md.get("verification") or {}
    if isinstance(verifier, dict):
        if "passed" in verifier:
            ok = bool(verifier.get("passed"))
            return ok, "" if ok else _truncate(rec.result_summary)
        if "score" in verifier:
            try:
                score = float(verifier.get("score") or 0)
                if score >= 0.6:
                    return True, ""
                if score < 0.4:
                    return False, _truncate(rec.result_summary)
            except (TypeError, ValueError):
                pass

    reaction = md.get("reaction")
    if reaction in {"👍", "ok", "positive", True}:
        return True, ""
    if reaction in {"👎", "fail", "negative", False}:
        return False, _truncate(rec.result_summary)

    # Cross-check artifact store по run_id если возможно
    run_id = rec.run_id or ""
    if run_id:
        for art in artifacts:
            path = art.get("_path", "")
            if run_id in path or art.get("topic") == rec.topic:
                v = art.get("verification") or {}
                if isinstance(v, dict) and "passed" in v:
                    ok = bool(v.get("passed"))
                    return ok, "" if ok else _truncate(rec.result_summary)
                break

    # Регекс-эвристика по тексту (последний резерв)
    summary = rec.result_summary or ""
    for tag, pat in _FAILURE_SIGNATURES:
        if pat.search(summary):
            return False, summary
    return True, ""


def _truncate(text: str, n: int = 400) -> str:
    text = (text or "").strip()
    return text[:n]


def _cluster_failure_patterns(messages: list[str]) -> list[tuple[str, int]]:
    """Кластеринг error-сообщений по `_FAILURE_SIGNATURES`. Возвращает (tag, count) sorted desc."""

    counter: Counter[str] = Counter()
    for msg in messages:
        if not msg:
            continue
        for tag, pat in _FAILURE_SIGNATURES:
            if pat.search(msg):
                counter[tag] += 1
                break
        else:
            counter["other"] += 1
    return counter.most_common()


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яёЁ][A-Za-zА-Яа-яёЁ_-]{3,}")


def _extract_success_keywords(texts: list[str]) -> list[tuple[str, int]]:
    """Простой keyword counter с stopword-фильтром. Топ слов из success-текстов."""

    counter: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for token in _WORD_RE.findall(text.lower()):
            if token in _STOPWORDS or len(token) < 4:
                continue
            counter[token] += 1
    return counter.most_common(10)


# ---------------------------------------------------------------------------
# Step 2 helpers — proposer prompt building + response parsing
# ---------------------------------------------------------------------------


_PROPOSER_SYSTEM = (
    "Ты — auxiliary skill curator для Telegram userbot Krab. "
    "Анализируешь работу команды свёрма и предлагаешь точечный апдейт системного промпта. "
    "Минимально-инвазивные правки: добавь 1-3 предложения, корректирующие конкретные failure patterns. "
    "Не переписывай промпт целиком. Не удаляй существующую специализацию команды. "
    "Отвечай строго JSON-объектом с ключами: proposed_prompt (string), rationale (string), focus (array of short strings)."
)


def _build_proposer_prompt(team: str, current_prompt: str, report: "CuratorReport") -> str:
    """Формирует prompt для aux-Gemini, включая report context."""

    fail_lines = (
        "\n".join(f"- {tag}: {cnt}×" for tag, cnt in (report.failure_patterns or []))
        or "(нет повторяющихся failure patterns)"
    )
    succ_lines = (
        "\n".join(f"- {kw}: {cnt}×" for kw, cnt in (report.successful_patterns or []))
        or "(нет ярких success patterns)"
    )
    success_pct = round((report.success_rate or 0.0) * 100, 1)

    context = (
        f"Команда: {team}\n"
        f"Окно анализа: {report.window_days} дней, {report.rounds_analyzed} раундов\n"
        f"Success rate: {success_pct}%\n"
        f"Distinct topics: {report.distinct_topics}\n\n"
        f"Failure patterns:\n{fail_lines}\n\n"
        f"Successful patterns:\n{succ_lines}\n\n"
        f"Recurring failure tags: {', '.join(report.recurring_failure_tags) or '—'}\n\n"
        f"Текущий системный промпт:\n```\n{current_prompt}\n```\n\n"
        "Задача: предложи минимальный апдейт системного промпта, чтобы снизить "
        "повторяющиеся ошибки и закрепить успешные паттерны.\n"
        "Верни строго JSON: "
        '{"proposed_prompt": "...", "rationale": "...", "focus": ["...", "..."]}'
    )
    return f"{_PROPOSER_SYSTEM}\n\n{context}"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_proposer_response(raw: str, current_prompt: str) -> dict[str, Any] | None:
    """Извлекает JSON из ответа LLM. Tolerant к code fences и trailing text."""

    if not raw:
        return None
    text = raw.strip()
    # Удаляем code fences ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # Берём первый JSON-блок если LLM добавил пояснения
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    proposed = (data.get("proposed_prompt") or "").strip()
    if not proposed:
        return None
    # Sanity: proposal не должен быть длиннее текущего × 4 (anti-hallucination)
    if len(proposed) > max(len(current_prompt) * 4, 4000):
        proposed = proposed[: max(len(current_prompt) * 4, 4000)]
    focus = data.get("focus") or []
    if not isinstance(focus, list):
        focus = [str(focus)]
    focus = [str(x).strip()[:60] for x in focus if str(x).strip()]
    return {
        "proposed_prompt": proposed,
        "rationale": str(data.get("rationale") or "").strip()[:1000],
        "focus": focus,
    }


def _unified_diff(before: str, after: str) -> str:
    """Возвращает unified diff (current → proposed) одной строкой."""

    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="current",
        tofile="proposed",
        lineterm="",
        n=2,
    )
    return "\n".join(diff)


def _derive_focus(report: "CuratorReport") -> list[str]:
    """Fallback focus tags если LLM их не вернул — строим из report."""

    focus = list(report.recurring_failure_tags or [])
    if not focus and report.failure_patterns:
        focus = [tag for tag, _ in report.failure_patterns[:2]]
    return focus[:3]


def _estimate_confidence(report: "CuratorReport", diff: str) -> float:
    """Эвристика confidence для proposal.

    - Низкий success_rate + есть diff → уверенность выше (нужен апдейт).
    - Diff пустой → 0.0 (нечего apply).
    - Малый sample (< 5 раундов) → cap 0.5.
    """

    if not diff.strip():
        return 0.0
    if report.rounds_analyzed < 5:
        base = 0.4
    else:
        base = 0.6 + (1.0 - max(0.0, min(1.0, report.success_rate))) * 0.3
    return round(min(0.95, base), 3)


def _safe_team(team: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", (team or "").lower())[:20] or "team"


# Singleton
skill_curator = SkillCurator()

# -*- coding: utf-8 -*-
"""
daily_brief.py — утренний брифинг (Idea 18).

Pure builder: собирает события за прошедшую ночь и формирует markdown
для self-DM owner-у. Авто-доставка не входит в этот модуль — это задача
существующего `cron_native_scheduler` либо отдельного hook-а.

Источники данных:
- Calendar events на сегодня (через injectable callable; по умолчанию —
  `macos_automation.list_upcoming_calendar_events`, опционально).
- Inbox stale items (>3 days в open / acked) — actionable.
- Cron jobs results overnight (last_run_at в окне).
- Sentry critical events — берутся из inbox по source/kind.
- Weekly digest summary — добавляется только в end-of-week (воскресенье).

Контракт:
- ничего не отправляет;
- секции с пустыми данными пропускаются;
- общий cap ~3000 символов;
- ошибки источников деградируют тихо, секция помечается как недоступная
  (или просто пропускается).

Конфиг:
- `KRAB_DAILY_BRIEF_ENABLED` (default False) — флаг для будущего hook-а;
  сам builder этот флаг не проверяет (его проверяет caller).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .inbox_service import inbox_service
from .logger import get_logger

logger = get_logger(__name__)

# Глобальный cap для всего brief (символов)
DAILY_BRIEF_MAX_CHARS: int = 3000
# Порог "stale" для inbox items (дней)
DAILY_BRIEF_STALE_DAYS: int = 3
# Лимит cron jobs в секции
DAILY_BRIEF_MAX_CRON: int = 8
# Лимит Sentry events в секции
DAILY_BRIEF_MAX_SENTRY: int = 5
# Лимит calendar events
DAILY_BRIEF_MAX_CALENDAR: int = 8
# Лимит inbox actionable items
DAILY_BRIEF_MAX_INBOX: int = 6

# Async коллбек, который возвращает список событий календаря.
# Сигнатура совпадает с `macos_automation.list_upcoming_calendar_events`.
CalendarFetcher = Callable[[], Awaitable[list[dict[str, str]]]]


@dataclass
class BriefSection:
    """Одна секция утреннего брифинга."""

    title: str
    lines: list[str] = field(default_factory=list)
    # Пометка ошибки (если источник упал, но секцию хочется показать как degraded)
    error: str = ""

    def is_empty(self) -> bool:
        """Секция пустая, если нет ни строк, ни ошибки."""
        return not self.lines and not self.error

    def render(self) -> str:
        """Markdown-render секции (без trailing newline)."""
        out = [f"## {self.title}"]
        if self.error:
            out.append(f"_недоступно: {self.error}_")
        out.extend(self.lines)
        return "\n".join(out)


class DailyBriefBuilder:
    """
    Builder утреннего брифинга.

    Использование:
        builder = DailyBriefBuilder()
        text = await builder.build_brief()  # возвращает markdown или ""

    Все источники инжектируемы через конструктор — это даёт чистые тесты
    без зависимостей от macOS Calendar / реального inbox state.
    """

    def __init__(
        self,
        *,
        calendar_fetcher: CalendarFetcher | None = None,
        cron_lister: Callable[[], list[dict[str, Any]]] | None = None,
        inbox: Any = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._calendar_fetcher = calendar_fetcher
        self._cron_lister = cron_lister
        self._inbox = inbox if inbox is not None else inbox_service
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # ---------- public API ----------

    async def build_brief(self, now: datetime | None = None) -> str:
        """
        Собирает все секции и возвращает markdown brief.

        Если все секции пустые — вернёт пустую строку (caller не отправит
        бесполезное сообщение).
        """
        ts = now or self._now_fn()
        # Нормализуем в UTC для расчётов и в local для заголовка
        ts_utc = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        ts_local = ts.astimezone() if ts.tzinfo else ts

        sections: list[BriefSection] = [
            await self._section_calendar(ts_utc),
            self._section_inbox(ts_utc),
            self._section_cron(ts_utc),
            self._section_sentry(ts_utc),
        ]
        # Воскресенье — добавляем weekly summary
        if ts_local.weekday() == 6:
            sections.append(self._section_weekly())

        non_empty = [s for s in sections if not s.is_empty()]
        if not non_empty:
            return ""

        header = f"# 🦀 Daily Brief — {ts_local.strftime('%Y-%m-%d %H:%M')}"
        body_parts = [header, ""]
        for sec in non_empty:
            body_parts.append(sec.render())
            body_parts.append("")

        text = "\n".join(body_parts).rstrip() + "\n"
        return self._enforce_cap(text)

    # ---------- sections ----------

    async def _section_calendar(self, _ts_utc: datetime) -> BriefSection:
        """События Calendar на сегодня (опционально)."""
        sec = BriefSection(title="📅 Сегодня в календаре")
        if self._calendar_fetcher is None:
            return sec  # источник не настроен — секция пуста, скрыта
        try:
            events = await self._calendar_fetcher()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_brief_calendar_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            sec.error = type(exc).__name__
            return sec

        if not events:
            return sec
        for ev in events[:DAILY_BRIEF_MAX_CALENDAR]:
            cal = str(ev.get("calendar_name") or "").strip()
            title = str(ev.get("title") or "").strip()[:80]
            start = str(ev.get("start_label") or "").strip()
            prefix = f"[{cal}] " if cal else ""
            sec.lines.append(f"- {prefix}{title} — {start}")
        return sec

    def _section_inbox(self, ts_utc: datetime) -> BriefSection:
        """Stale items в inbox (>N дней open)."""
        sec = BriefSection(title="📥 Inbox stale (требует внимания)")
        cutoff = ts_utc - timedelta(days=DAILY_BRIEF_STALE_DAYS)
        try:
            stale = self._inbox.filter_by_age(
                older_than_date=cutoff.isoformat(),
                status="open",
                limit=DAILY_BRIEF_MAX_INBOX,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_brief_inbox_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            sec.error = type(exc).__name__
            return sec

        if not stale:
            return sec
        for it in stale:
            severity = str(it.get("severity") or "info")
            title = str(it.get("title") or "").strip()[:80]
            created = str(it.get("created_at_utc") or "")[:10]
            sec.lines.append(f"- [{severity}] {title} _(создан {created})_")
        return sec

    def _section_cron(self, ts_utc: datetime) -> BriefSection:
        """Cron jobs, отработавшие за прошедшую ночь (последние 12 часов)."""
        sec = BriefSection(title="⏰ Cron overnight")
        if self._cron_lister is None:
            # Поздняя загрузка — избегаем циклической зависимости при импорте
            try:
                from . import cron_native_store

                lister: Callable[[], list[dict[str, Any]]] = cron_native_store.list_jobs
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "daily_brief_cron_import_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                sec.error = type(exc).__name__
                return sec
        else:
            lister = self._cron_lister

        try:
            jobs = lister() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_brief_cron_list_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            sec.error = type(exc).__name__
            return sec

        cutoff = ts_utc - timedelta(hours=12)
        recent: list[tuple[datetime, dict[str, Any]]] = []
        for job in jobs:
            last_run = job.get("last_run_at")
            if not last_run:
                continue
            try:
                run_at = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
                if run_at.tzinfo is None:
                    run_at = run_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if run_at < cutoff:
                continue
            recent.append((run_at, job))

        if not recent:
            return sec

        recent.sort(key=lambda row: row[0], reverse=True)
        for run_at, job in recent[:DAILY_BRIEF_MAX_CRON]:
            job_id = str(job.get("id") or "?")
            prompt = str(job.get("prompt") or "")[:60]
            spec = str(job.get("cron_spec") or "")
            sec.lines.append(
                f"- `{job_id}` ({spec}) — {prompt} _(в {run_at.astimezone().strftime('%H:%M')})_"
            )
        return sec

    def _section_sentry(self, ts_utc: datetime) -> BriefSection:
        """
        Critical Sentry events за прошедшую ночь.

        Источник — inbox items с kind=sentry или source='sentry*' и severity=error.
        """
        sec = BriefSection(title="🚨 Sentry critical")
        cutoff_iso = (ts_utc - timedelta(hours=12)).isoformat()
        try:
            recent_open = self._inbox.list_items(status="open", limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_brief_sentry_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            sec.error = type(exc).__name__
            return sec

        critical: list[dict[str, Any]] = []
        for it in recent_open:
            kind = str(it.get("kind") or "").lower()
            source = str(it.get("source") or "").lower()
            severity = str(it.get("severity") or "").lower()
            if "sentry" not in kind and "sentry" not in source:
                continue
            if severity != "error":
                continue
            created = str(it.get("created_at_utc") or "")
            if created and created < cutoff_iso:
                continue
            critical.append(it)
            if len(critical) >= DAILY_BRIEF_MAX_SENTRY:
                break

        if not critical:
            return sec
        for it in critical:
            title = str(it.get("title") or "").strip()[:80]
            created = str(it.get("created_at_utc") or "")[:19]
            sec.lines.append(f"- {title} _({created})_")
        return sec

    def _section_weekly(self) -> BriefSection:
        """
        Краткая ссылка на последний Weekly Digest (если он есть в inbox).

        Не дублирует тело дайджеста — даёт указатель и метрики.
        """
        sec = BriefSection(title="📊 Weekly digest")
        try:
            items = self._inbox.list_items(status="all", kind="proactive_action", limit=20)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "daily_brief_weekly_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            sec.error = type(exc).__name__
            return sec

        digest = None
        for it in items:
            meta = it.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("action_type") == "weekly_digest":
                digest = it
                break
        if not digest:
            return sec

        meta = digest.get("metadata") or {}
        rounds = meta.get("total_rounds", "?")
        cost = meta.get("cost_week_usd", 0.0)
        attention = meta.get("attention_count", "?")
        ts_iso = str(meta.get("digest_ts") or digest.get("created_at_utc") or "")[:10]
        sec.lines.append(f"- {ts_iso}: rounds={rounds}, cost=${cost}, attention={attention}")
        return sec

    # ---------- helpers ----------

    @staticmethod
    def _enforce_cap(text: str, cap: int | None = None) -> str:
        """Обрезает текст до cap с маркером усечения.

        Cap по умолчанию читается динамически из модульной константы — это
        позволяет тестам подменять лимит через monkeypatch.
        """
        # Читаем константу через globals(), чтобы тесты могли подменить её
        # через monkeypatch.setattr("src.core.daily_brief.DAILY_BRIEF_MAX_CHARS", N)
        effective_cap = cap if cap is not None else globals()["DAILY_BRIEF_MAX_CHARS"]
        if len(text) <= effective_cap:
            return text
        truncated = text[: effective_cap - 32].rstrip()
        return truncated + "\n\n_…brief обрезан по лимиту_\n"

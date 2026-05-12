# -*- coding: utf-8 -*-
"""AGE-11: дополнительное покрытие DailyBriefBuilder (companion к AGE-13).

Закрывает gaps, не покрытые в test_daily_brief.py:
- error paths каждой секции (calendar/inbox/cron/sentry/weekly);
- cron import failure / malformed last_run_at / age cutoff / sort order / cap;
- BriefSection: is_empty/render с error без lines;
- _enforce_cap при явном cap;
- ts без tzinfo;
- weekly digest: items без подходящего action_type.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.core.daily_brief import (
    DAILY_BRIEF_MAX_CRON,
    BriefSection,
    DailyBriefBuilder,
)


class _FakeInbox:
    """Stub inbox с поддержкой инжекта исключений."""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]] | None = None,
        stale: list[dict[str, Any]] | None = None,
        list_items_exc: Exception | None = None,
        filter_exc: Exception | None = None,
    ) -> None:
        self._items = list(items or [])
        self._stale = list(stale or [])
        self._list_items_exc = list_items_exc
        self._filter_exc = filter_exc

    def list_items(
        self, *, status: str = "", kind: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        if self._list_items_exc is not None:
            raise self._list_items_exc
        rows = self._items
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        return rows[:limit]

    def filter_by_age(
        self,
        *,
        older_than_date: str,
        kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._filter_exc is not None:
            raise self._filter_exc
        return self._stale[:limit]


def _now() -> datetime:
    """Среда 08.04.2026 08:00 UTC (НЕ воскресенье)."""
    return datetime(2026, 4, 8, 8, 0, 0, tzinfo=timezone.utc)


# ---------- BriefSection unit ----------


def test_brief_section_is_empty_true_when_no_lines_no_error() -> None:
    """Пустая секция: ни строк, ни ошибки."""
    sec = BriefSection(title="X")
    assert sec.is_empty() is True


def test_brief_section_is_empty_false_when_only_error() -> None:
    """Секция с error без строк всё равно не пустая (рендерится как degraded)."""
    sec = BriefSection(title="X", error="BoomError")
    assert sec.is_empty() is False


def test_brief_section_render_error_only() -> None:
    """Render error-only секции содержит маркер 'недоступно: <type>'."""
    sec = BriefSection(title="📅 Cal", error="TimeoutError")
    out = sec.render()
    assert "## 📅 Cal" in out
    assert "_недоступно: TimeoutError_" in out


# ---------- Calendar error path ----------


@pytest.mark.asyncio
async def test_calendar_fetcher_exception_renders_degraded() -> None:
    """Calendar fetcher падает → секция помечена error и рендерится."""

    async def boom() -> list[dict[str, str]]:
        raise RuntimeError("calendar offline")

    builder = DailyBriefBuilder(
        calendar_fetcher=boom,
        cron_lister=lambda: [],
        inbox=_FakeInbox(),
        now_fn=_now,
    )
    text = await builder.build_brief()
    assert "Сегодня в календаре" in text
    assert "недоступно: RuntimeError" in text


@pytest.mark.asyncio
async def test_calendar_empty_list_skips_section() -> None:
    """Пустой список событий → секция скрыта (не рендерится)."""

    async def empty() -> list[dict[str, str]]:
        return []

    builder = DailyBriefBuilder(
        calendar_fetcher=empty,
        cron_lister=lambda: [],
        inbox=_FakeInbox(),
        now_fn=_now,
    )
    text = await builder.build_brief()
    # Никаких данных вообще → пустой brief
    assert text == ""


# ---------- Inbox stale error path ----------


@pytest.mark.asyncio
async def test_inbox_filter_exception_renders_degraded() -> None:
    """filter_by_age raises → inbox stale секция error, brief всё равно собирается."""
    inbox = _FakeInbox(filter_exc=OSError("db locked"))
    builder = DailyBriefBuilder(
        cron_lister=lambda: [],
        inbox=inbox,
        now_fn=_now,
    )
    text = await builder.build_brief()
    assert "Inbox stale" in text
    assert "недоступно: OSError" in text


# ---------- Cron import + lister errors ----------


@pytest.mark.asyncio
async def test_cron_import_failure_renders_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если cron_native_store импорт упал — секция error.

    Подменяем кешированный модуль на объект без `list_jobs`, чтобы
    `cron_native_store.list_jobs` упал AttributeError — это эквивалентно
    "lazy import дал плохой модуль".
    """

    import src.core as core_pkg

    class _BadModule:
        pass

    # Эффект `from . import cron_native_store` берёт атрибут пакета, не только
    # sys.modules — поэтому подменяем оба места.
    monkeypatch.setitem(sys.modules, "src.core.cron_native_store", _BadModule())
    monkeypatch.setattr(core_pkg, "cron_native_store", _BadModule(), raising=False)

    builder = DailyBriefBuilder(
        # cron_lister=None → запустит lazy import → AttributeError
        inbox=_FakeInbox(),
        now_fn=_now,
    )
    text = await builder.build_brief()
    assert "Cron overnight" in text
    assert "недоступно: AttributeError" in text


@pytest.mark.asyncio
async def test_cron_lister_exception_renders_degraded() -> None:
    """cron_lister() raises → секция error."""

    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("store offline")

    builder = DailyBriefBuilder(
        cron_lister=boom,
        inbox=_FakeInbox(),
        now_fn=_now,
    )
    text = await builder.build_brief()
    assert "Cron overnight" in text
    assert "недоступно: RuntimeError" in text


@pytest.mark.asyncio
async def test_cron_malformed_last_run_at_skipped() -> None:
    """Job с битым last_run_at пропускается без падения."""
    now = _now()
    jobs = [
        {"id": "bad1", "cron_spec": "* * * * *", "prompt": "p", "last_run_at": "not-a-date"},
        {"id": "bad2", "cron_spec": "* * * * *", "prompt": "p", "last_run_at": None},
        {"id": "bad3", "cron_spec": "* * * * *", "prompt": "p"},  # отсутствует ключ
        {
            "id": "good1",
            "cron_spec": "0 7 * * *",
            "prompt": "morning",
            "last_run_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    builder = DailyBriefBuilder(
        cron_lister=lambda: jobs,
        inbox=_FakeInbox(),
        now_fn=lambda: now,
    )
    text = await builder.build_brief()
    assert "good1" in text
    for bad in ("bad1", "bad2", "bad3"):
        assert bad not in text


@pytest.mark.asyncio
async def test_cron_too_old_filtered_out() -> None:
    """Job старше 12 часов выпадает из секции."""
    now = _now()
    jobs = [
        {
            "id": "old1",
            "cron_spec": "* * * * *",
            "prompt": "old",
            "last_run_at": (now - timedelta(hours=20)).isoformat(),
        },
        {
            "id": "fresh1",
            "cron_spec": "* * * * *",
            "prompt": "fresh",
            "last_run_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    builder = DailyBriefBuilder(
        cron_lister=lambda: jobs,
        inbox=_FakeInbox(),
        now_fn=lambda: now,
    )
    text = await builder.build_brief()
    assert "fresh1" in text
    assert "old1" not in text


@pytest.mark.asyncio
async def test_cron_capped_at_max_cron() -> None:
    """В секции не больше DAILY_BRIEF_MAX_CRON записей, отсортированы по времени desc."""
    now = _now()
    # Создаём CRON*2 свежих jobs, каждый старше предыдущего на минуту
    jobs = [
        {
            "id": f"j{i:02d}",
            "cron_spec": "* * * * *",
            "prompt": "p",
            "last_run_at": (now - timedelta(minutes=i)).isoformat(),
        }
        for i in range(DAILY_BRIEF_MAX_CRON * 2)
    ]
    builder = DailyBriefBuilder(
        cron_lister=lambda: jobs,
        inbox=_FakeInbox(),
        now_fn=lambda: now,
    )
    text = await builder.build_brief()
    # Первый job (j00, самый свежий) должен быть, последний (j15) — нет
    assert "j00" in text
    assert f"j{DAILY_BRIEF_MAX_CRON * 2 - 1:02d}" not in text
    # Точно не больше MAX_CRON строк с `- \``
    cron_lines = [ln for ln in text.splitlines() if ln.startswith("- `j")]
    assert len(cron_lines) <= DAILY_BRIEF_MAX_CRON


@pytest.mark.asyncio
async def test_cron_last_run_at_naive_treated_as_utc() -> None:
    """Naive datetime в last_run_at интерпретируется как UTC и не падает."""
    now = _now()
    naive = (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    jobs = [
        {"id": "naive1", "cron_spec": "* * * * *", "prompt": "p", "last_run_at": naive},
    ]
    builder = DailyBriefBuilder(
        cron_lister=lambda: jobs,
        inbox=_FakeInbox(),
        now_fn=lambda: now,
    )
    text = await builder.build_brief()
    assert "naive1" in text


# ---------- Sentry error path ----------


@pytest.mark.asyncio
async def test_sentry_list_items_exception_renders_degraded() -> None:
    """inbox.list_items raises → Sentry секция error."""
    inbox = _FakeInbox(list_items_exc=RuntimeError("inbox dead"))
    builder = DailyBriefBuilder(
        cron_lister=lambda: [],
        inbox=inbox,
        now_fn=_now,
    )
    text = await builder.build_brief()
    assert "Sentry critical" in text
    assert "недоступно: RuntimeError" in text


# ---------- Weekly digest paths ----------


@pytest.mark.asyncio
async def test_weekly_section_no_matching_action_type() -> None:
    """Воскресенье + inbox без weekly_digest → weekly секция пуста (скрыта)."""
    sunday = datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc)
    items = [
        {
            "kind": "proactive_action",
            "title": "Something else",
            "metadata": {"action_type": "other_thing"},
            "created_at_utc": "2026-04-12T08:00:00+00:00",
        }
    ]
    builder = DailyBriefBuilder(
        cron_lister=lambda: [],
        inbox=_FakeInbox(items=items),
        now_fn=lambda: sunday,
    )
    text = await builder.build_brief()
    # Других секций тоже нет → brief пустой
    assert text == ""


# ---------- Helpers ----------


def test_enforce_cap_explicit_param_overrides_global() -> None:
    """_enforce_cap с явным cap игнорирует глобальную константу."""
    short = "abc"
    assert DailyBriefBuilder._enforce_cap(short, cap=1000) == short

    long_text = "x" * 500
    truncated = DailyBriefBuilder._enforce_cap(long_text, cap=100)
    assert len(truncated) <= 100 + 64  # учитываем маркер
    assert "обрезан по лимиту" in truncated


@pytest.mark.asyncio
async def test_build_brief_accepts_naive_now() -> None:
    """now без tzinfo не крашит build_brief."""
    naive_now = datetime(2026, 4, 8, 8, 0, 0)  # без tzinfo
    builder = DailyBriefBuilder(
        cron_lister=lambda: [],
        inbox=_FakeInbox(
            stale=[
                {
                    "severity": "info",
                    "title": "x",
                    "created_at_utc": "2026-04-01T10:00:00+00:00",
                }
            ]
        ),
        now_fn=lambda: naive_now,
    )
    text = await builder.build_brief(now=naive_now)
    assert "Inbox stale" in text

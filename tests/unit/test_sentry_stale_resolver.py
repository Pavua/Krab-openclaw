# -*- coding: utf-8 -*-
"""Wave 42-A: тесты для scripts/sentry_stale_resolver.py.

Покрывает:
- categorize_issue: known_fixed pattern → resolve
- categorize_issue: stale > 7d → resolve
- categorize_issue: one_off (count<=2, age>3d) → resolve
- categorize_issue: recent + frequent → keep (active)
- main(dry_run=True) → нет реальных API-вызовов, return 0
- main() без SENTRY_AUTH_TOKEN → return 2
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Добавляем корень проекта в sys.path, чтобы импортировать scripts/
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.sentry_stale_resolver as resolver

# ─── Вспомогательные фабрики ─────────────────────────────────────────────────


def _make_issue(
    *,
    issue_id: str = "123",
    title: str = "Some error",
    count: int = 10,
    last_seen_days_ago: int = 1,
    metadata: dict | None = None,
) -> dict:
    """Создаёт минимальный dict issue как от Sentry API."""
    ts = datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago)
    return {
        "id": issue_id,
        "title": title,
        "count": str(count),
        "lastSeen": ts.isoformat().replace("+00:00", "Z"),
        "metadata": metadata or {},
    }


# ─── Тесты categorize_issue ──────────────────────────────────────────────────


class TestCategorizeIssue:
    def test_known_fixed_pattern_resolves(self):
        """Issue с заголовком из KNOWN_FIXED_PATTERNS → resolve с known_fixed: reason."""
        issue = _make_issue(
            title='Invalid parse mode "markdown"',
            count=50,
            last_seen_days_ago=0,  # свежий — но known_fixed
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is True
        assert reason == "known_fixed:wave-25-d-fix"

    def test_known_fixed_in_metadata_resolves(self):
        """Паттерн в metadata (не в title) тоже должен давать resolve."""
        issue = _make_issue(
            title="Database error",
            metadata={"value": "disk image is malformed in WAL"},
            count=3,
            last_seen_days_ago=1,
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is True
        assert reason == "known_fixed:wave-24-d"

    def test_stale_no_recurrence_resolves(self):
        """Issue не виден 8+ дней → stale_no_recurrence."""
        issue = _make_issue(
            title="Unrelated transient error",
            count=100,
            last_seen_days_ago=8,
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is True
        assert reason.startswith("stale_no_recurrence_")
        assert "8d" in reason

    def test_one_off_resolves(self):
        """count <= 2 AND last_seen > 3d → one_off."""
        issue = _make_issue(
            title="Unknown edge case",
            count=1,
            last_seen_days_ago=5,
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is True
        assert reason.startswith("one_off_")

    def test_recent_frequent_kept(self):
        """Свежий (0d) с большим count → оставить как active."""
        issue = _make_issue(
            title="NullPointerException in handler",
            count=200,
            last_seen_days_ago=0,
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is False
        assert reason == "active"

    def test_recent_one_off_within_threshold_kept(self):
        """count=1 но last_seen только 2 дня назад — порог 3d не достигнут → keep."""
        issue = _make_issue(
            title="Intermittent timeout",
            count=1,
            last_seen_days_ago=2,
        )
        should, reason = resolver.categorize_issue(issue)
        assert should is False
        assert reason == "active"


# ─── Тесты main() ────────────────────────────────────────────────────────────


class TestMain:
    def test_dry_run_no_api_calls(self, tmp_path: Path):
        """main(dry_run=True) логирует candidates, но не вызывает resolve_issue."""
        # Подменяем LOG_FILE на tmp
        log_file = tmp_path / "sentry_resolver.log"

        stale_issue = _make_issue(
            issue_id="999",
            title="Stale error",
            count=50,
            last_seen_days_ago=10,
        )

        with (
            patch.object(resolver, "SENTRY_TOKEN", "fake-token"),
            patch.object(resolver, "LOG_FILE", log_file),
            patch.object(resolver, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(resolver, "fetch_issues", return_value=[stale_issue]) as mock_fetch,
            patch.object(resolver, "resolve_issue", return_value=True) as mock_resolve,
        ):
            rc = resolver.main(dry_run=True)

        assert rc == 0
        mock_fetch.assert_called_once_with("python-fastapi")
        # В dry-run resolve_issue НЕ должен вызываться
        mock_resolve.assert_not_called()

        # Лог должен содержать [DRY]
        assert log_file.exists()
        content = log_file.read_text()
        assert "[DRY] would resolve" in content
        assert "999" in content

    def test_no_token_returns_2(self, tmp_path: Path):
        """main() без SENTRY_AUTH_TOKEN → return 2, без API-вызовов."""
        log_file = tmp_path / "sentry_resolver.log"

        with (
            patch.object(resolver, "SENTRY_TOKEN", None),
            patch.object(resolver, "LOG_FILE", log_file),
            patch.object(resolver, "fetch_issues") as mock_fetch,
        ):
            rc = resolver.main(dry_run=True)

        assert rc == 2
        mock_fetch.assert_not_called()
        # Лог-файл создан с сообщением об ошибке
        assert log_file.exists()
        assert "SENTRY_AUTH_TOKEN" in log_file.read_text()

    def test_live_mode_calls_resolve(self, tmp_path: Path):
        """main(dry_run=False) вызывает resolve_issue для кандидатов."""
        log_file = tmp_path / "sentry_resolver.log"

        stale = _make_issue(issue_id="555", last_seen_days_ago=10, count=5)
        fresh = _make_issue(issue_id="777", last_seen_days_ago=0, count=200)

        with (
            patch.object(resolver, "SENTRY_TOKEN", "real-token"),
            patch.object(resolver, "LOG_FILE", log_file),
            patch.object(resolver, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(resolver, "fetch_issues", return_value=[stale, fresh]),
            patch.object(resolver, "resolve_issue", return_value=True) as mock_resolve,
        ):
            rc = resolver.main(dry_run=False)

        assert rc == 0
        # resolve вызван только для stale (555), не для fresh (777)
        called_ids = [call.args[0] for call in mock_resolve.call_args_list]
        assert "555" in called_ids
        assert "777" not in called_ids

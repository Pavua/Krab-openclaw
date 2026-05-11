# -*- coding: utf-8 -*-
"""Wave 65-H: тесты для scripts/sentry_poll_direct.py.

Покрывает:
- fetch_issues_with_retry: success на 200
- fetch_issues_with_retry: retry при ConnectTimeout с exp backoff
- fetch_issues_with_retry: 429 с Retry-After
- fetch_issues_with_retry: 5xx retry
- fetch_issues_with_retry: 4xx fail fast (кроме 429)
- fetch_issues_with_retry: non-list response → []
- format_alert_text: emoji/level/escaping
- cursor persistence: load/save round-trip
- seen_ids state: append/trim FIFO
- main(): missing SENTRY_AUTH_TOKEN → return 2
- main(): missing Telegram creds → return 3
- main(): cursor updates после успешного poll
- main(): incremental skip — issues со lastSeen <= cursor пропускаются
- _build_query: правильное форматирование levels
- env override KRAB_SENTRY_POLL_DIRECT_API: bash dispatcher (smoke)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Добавляем корень проекта в sys.path, чтобы импортировать scripts/
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.sentry_poll_direct as poller  # noqa: E402

# ─── Вспомогательные фабрики ─────────────────────────────────────────────────


def _make_issue(
    *,
    issue_id: str = "abc123",
    short_id: str = "PROJ-XYZ",
    title: str = "Sample error",
    level: str = "error",
    count: int = 10,
    user_count: int = 3,
    project_slug: str = "python-fastapi",
    culprit: str = "src/foo.py",
    permalink: str = "https://sentry.io/issues/abc123/",
    last_seen_days_ago: int = 0,
) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago)
    return {
        "id": issue_id,
        "shortId": short_id,
        "title": title,
        "level": level,
        "count": count,
        "userCount": user_count,
        "culprit": culprit,
        "permalink": permalink,
        "lastSeen": ts.isoformat().replace("+00:00", "Z"),
        "project": {"slug": project_slug},
    }


def _stub_response(status_code: int, body: object = None, headers: dict | None = None):
    """Создаёт MagicMock, имитирующий httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    if isinstance(body, (list, dict)):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    elif isinstance(body, str):
        resp.json.side_effect = json.JSONDecodeError("err", body, 0)
        resp.text = body
    else:
        resp.json.return_value = []
        resp.text = "[]"
    return resp


@pytest.fixture
def tmp_state_dir(tmp_path: Path, monkeypatch):
    """Изолирует STATE_DIR в tmp на время теста."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(poller, "STATE_DIR", state_dir)
    monkeypatch.setattr(poller, "STATE_FILE", state_dir / "seen_ids")
    monkeypatch.setattr(poller, "CURSOR_FILE", state_dir / "last_seen_cursor.json")
    monkeypatch.setattr(poller, "LOG_FILE", state_dir / "poll.log")
    monkeypatch.setattr(poller, "ERR_LOG", state_dir / "poll.err.log")
    return state_dir


# ─── Тесты fetch_issues_with_retry ───────────────────────────────────────────


class TestFetchIssuesWithRetry:
    def test_success_200(self, tmp_state_dir):
        """200 OK → возвращает list issues."""
        issues = [_make_issue(issue_id="a"), _make_issue(issue_id="b")]
        mock_client = MagicMock()
        mock_client.get.return_value = _stub_response(200, issues)

        with patch.object(poller, "SENTRY_TOKEN", "fake-token"):
            result = poller.fetch_issues_with_retry("python-fastapi", client=mock_client)

        assert len(result) == 2
        assert result[0]["id"] == "a"
        mock_client.get.assert_called_once()

    def test_retry_on_connect_timeout(self, tmp_state_dir):
        """ConnectTimeout → retry с exp backoff → eventually success."""
        issues = [_make_issue(issue_id="x")]
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            httpx.ConnectTimeout("timeout 1"),
            httpx.ConnectTimeout("timeout 2"),
            _stub_response(200, issues),
        ]

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch("scripts.sentry_poll_direct.time.sleep") as mock_sleep,
        ):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert len(result) == 1
        assert result[0]["id"] == "x"
        # 2 retries → 2 sleeps (2s, 4s — exp backoff)
        assert mock_sleep.call_count == 2
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args == [2.0, 4.0]

    def test_retry_exhausted_returns_empty(self, tmp_state_dir):
        """Все retries fail → []."""
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ReadTimeout("persistent timeout")

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch("scripts.sentry_poll_direct.time.sleep"),
        ):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert result == []
        assert mock_client.get.call_count == 3

    def test_429_backoff_retry_after(self, tmp_state_dir):
        """429 → respects Retry-After header → retry."""
        issues = [_make_issue(issue_id="y")]
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _stub_response(429, headers={"Retry-After": "7"}),
            _stub_response(200, issues),
        ]

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch("scripts.sentry_poll_direct.time.sleep") as mock_sleep,
        ):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert len(result) == 1
        mock_sleep.assert_called_once_with(7.0)

    def test_429_invalid_retry_after_uses_default(self, tmp_state_dir):
        """429 с невалидным Retry-After → fallback на 5s."""
        issues = [_make_issue(issue_id="z")]
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _stub_response(429, headers={"Retry-After": "garbage"}),
            _stub_response(200, issues),
        ]

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch("scripts.sentry_poll_direct.time.sleep") as mock_sleep,
        ):
            poller.fetch_issues_with_retry("python-fastapi", client=mock_client, max_retries=3)

        mock_sleep.assert_called_once_with(5.0)

    def test_500_retry(self, tmp_state_dir):
        """5xx → retry с exp backoff."""
        issues = [_make_issue(issue_id="q")]
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _stub_response(503, body="upstream connect error"),
            _stub_response(200, issues),
        ]

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch("scripts.sentry_poll_direct.time.sleep") as mock_sleep,
        ):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert len(result) == 1
        # 503 attempt 0 → sleep 2.0s
        assert mock_sleep.call_count == 1

    def test_403_fail_fast(self, tmp_state_dir):
        """403 (4xx кроме 429) → не retry, сразу []."""
        mock_client = MagicMock()
        mock_client.get.return_value = _stub_response(403, body="forbidden")

        with patch.object(poller, "SENTRY_TOKEN", "fake-token"):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert result == []
        # Только 1 попытка
        assert mock_client.get.call_count == 1

    def test_non_list_response(self, tmp_state_dir):
        """200 но не list → [] + error log."""
        mock_client = MagicMock()
        mock_client.get.return_value = _stub_response(200, {"detail": "wrong shape"})

        with patch.object(poller, "SENTRY_TOKEN", "fake-token"):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert result == []

    def test_invalid_json(self, tmp_state_dir):
        """200 с broken JSON → []."""
        mock_client = MagicMock()
        mock_client.get.return_value = _stub_response(200, "not json at all <html>")

        with patch.object(poller, "SENTRY_TOKEN", "fake-token"):
            result = poller.fetch_issues_with_retry(
                "python-fastapi", client=mock_client, max_retries=3
            )

        assert result == []


# ─── Тесты build_query ───────────────────────────────────────────────────────


class TestBuildQuery:
    def test_default_levels(self):
        q = poller._build_query(["error", "fatal"])
        assert q == "is:unresolved level:[error,fatal]"

    def test_single_level(self):
        q = poller._build_query(["fatal"])
        assert q == "is:unresolved level:[fatal]"

    def test_empty_levels(self):
        q = poller._build_query([])
        assert q == "is:unresolved"


# ─── Тесты format_alert_text ─────────────────────────────────────────────────


class TestFormatAlertText:
    def test_basic_format(self):
        issue = _make_issue(level="error", title="DB error")
        text = poller.format_alert_text(issue)
        assert "❌" in text
        assert "ERROR" in text
        assert "[python-fastapi]" in text
        assert "DB error" in text
        assert "events:</i> 10" in text
        assert "users:</i> 3" in text

    def test_fatal_uses_fire_emoji(self):
        issue = _make_issue(level="fatal")
        text = poller.format_alert_text(issue)
        assert "🔥" in text
        assert "FATAL" in text

    def test_unknown_level_uses_lightning(self):
        issue = _make_issue(level="strange")
        text = poller.format_alert_text(issue)
        assert "⚡" in text

    def test_html_escape(self):
        issue = _make_issue(title="<script>alert(1)</script>")
        text = poller.format_alert_text(issue)
        assert "&lt;script&gt;" in text
        assert "<script>alert" not in text

    def test_missing_fields_safe(self):
        # Все опциональные поля отсутствуют
        text = poller.format_alert_text({"id": "x"})
        assert "Unknown" in text  # title fallback
        assert "ERROR" in text  # level fallback


# ─── Тесты cursor persistence ────────────────────────────────────────────────


class TestCursorPersistence:
    def test_cursor_round_trip(self, tmp_state_dir):
        """Save → load возвращает то же содержимое."""
        cursor = {
            "python-fastapi": "2026-05-12T00:00:00+00:00",
            "krab-ear-agent": "2026-05-11T23:55:00+00:00",
        }
        poller._save_cursor(cursor)
        loaded = poller._load_cursor()
        assert loaded == cursor

    def test_cursor_load_missing_file(self, tmp_state_dir):
        """Load без файла → {}."""
        result = poller._load_cursor()
        assert result == {}

    def test_cursor_load_invalid_json(self, tmp_state_dir):
        """Load с broken JSON → {} (не падает)."""
        poller.CURSOR_FILE.write_text("not json at all {")
        result = poller._load_cursor()
        assert result == {}


# ─── Тесты seen_ids state ────────────────────────────────────────────────────


class TestSeenIdsState:
    def test_append_and_load(self, tmp_state_dir):
        poller._append_seen_id("issue-1")
        poller._append_seen_id("issue-2")
        loaded = poller._load_seen_ids()
        assert loaded == {"issue-1", "issue-2"}

    def test_trim_to_max(self, tmp_state_dir):
        """trim_seen_ids оставляет последние N (FIFO)."""
        for i in range(15):
            poller._append_seen_id(f"id-{i}")
        poller._trim_seen_ids(max_entries=10)
        loaded = poller._load_seen_ids()
        assert len(loaded) == 10
        # Должны остаться последние 10 (id-5..id-14)
        assert "id-14" in loaded
        assert "id-0" not in loaded

    def test_trim_no_op_under_limit(self, tmp_state_dir):
        """Если файл < max_entries, trim не меняет содержимое."""
        for i in range(5):
            poller._append_seen_id(f"id-{i}")
        poller._trim_seen_ids(max_entries=100)
        loaded = poller._load_seen_ids()
        assert len(loaded) == 5


# ─── Тесты main() ────────────────────────────────────────────────────────────


class TestMain:
    def test_missing_token_returns_2(self, tmp_state_dir):
        with (
            patch.object(poller, "SENTRY_TOKEN", None),
            patch.object(poller, "TG_TOKEN", "tg-token"),
            patch.object(poller, "TG_OWNER", "12345"),
        ):
            rc = poller.main()
        assert rc == 2

    def test_missing_telegram_returns_3(self, tmp_state_dir):
        with (
            patch.object(poller, "SENTRY_TOKEN", "fake"),
            patch.object(poller, "TG_TOKEN", None),
            patch.object(poller, "TG_OWNER", ""),
        ):
            rc = poller.main()
        assert rc == 3

    def test_direct_api_used_by_default(self, tmp_state_dir):
        """fetch_issues_with_retry вызывается с правильным URL/headers."""
        issue = _make_issue(issue_id="new1", last_seen_days_ago=0)

        called_urls = []

        def fake_fetch(project, *, client=None, **kwargs):
            called_urls.append(project)
            return [issue]

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch.object(poller, "TG_TOKEN", "tg-token"),
            patch.object(poller, "TG_OWNER", "12345"),
            patch.object(poller, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(poller, "fetch_issues_with_retry", side_effect=fake_fetch),
            patch.object(poller, "send_telegram", return_value=True) as mock_send,
        ):
            rc = poller.main()

        assert rc == 0
        assert called_urls == ["python-fastapi"]
        mock_send.assert_called_once()
        # Issue id записан в seen_ids
        assert "new1" in poller._load_seen_ids()

    def test_cursor_persistent_across_polls(self, tmp_state_dir):
        """После polling cursor содержит lastSeen самого свежего issue."""
        recent = _make_issue(issue_id="r", last_seen_days_ago=0)
        old = _make_issue(issue_id="o", last_seen_days_ago=2)

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch.object(poller, "TG_TOKEN", "tg-token"),
            patch.object(poller, "TG_OWNER", "12345"),
            patch.object(poller, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(
                poller,
                "fetch_issues_with_retry",
                return_value=[recent, old],
            ),
            patch.object(poller, "send_telegram", return_value=True),
        ):
            poller.main()

        cursor = poller._load_cursor()
        assert "python-fastapi" in cursor
        # Cursor должен быть из самого свежего (recent) issue
        cursor_dt = datetime.fromisoformat(cursor["python-fastapi"])
        recent_dt = datetime.fromisoformat(recent["lastSeen"].replace("Z", "+00:00"))
        # Разница < 1 сек (округление при сериализации)
        assert abs((cursor_dt - recent_dt).total_seconds()) < 1.0

    def test_cursor_skips_old_issues(self, tmp_state_dir):
        """Issue с lastSeen <= cursor пропускается (защита от replay)."""
        # Pre-populate cursor "yesterday"
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        poller._save_cursor({"python-fastapi": yesterday})

        # Old issue (3 days ago) — должен быть пропущен (lastSeen < cursor)
        old = _make_issue(issue_id="old-1", last_seen_days_ago=3)
        # New issue (0 days ago) — должен пройти
        new = _make_issue(issue_id="new-1", last_seen_days_ago=0)

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch.object(poller, "TG_TOKEN", "tg-token"),
            patch.object(poller, "TG_OWNER", "12345"),
            patch.object(poller, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(
                poller,
                "fetch_issues_with_retry",
                return_value=[old, new],
            ),
            patch.object(poller, "send_telegram", return_value=True) as mock_send,
        ):
            poller.main()

        # Только new должен быть отправлен
        assert mock_send.call_count == 1
        seen = poller._load_seen_ids()
        assert "new-1" in seen
        assert "old-1" not in seen

    def test_seen_ids_dedupe(self, tmp_state_dir):
        """Issues из seen_ids не алертятся повторно."""
        # Pre-populate seen_ids
        poller._append_seen_id("dup-1")

        already_alerted = _make_issue(issue_id="dup-1", last_seen_days_ago=0)
        new_issue = _make_issue(issue_id="fresh-1", last_seen_days_ago=0)

        with (
            patch.object(poller, "SENTRY_TOKEN", "fake-token"),
            patch.object(poller, "TG_TOKEN", "tg-token"),
            patch.object(poller, "TG_OWNER", "12345"),
            patch.object(poller, "SENTRY_PROJECTS", ["python-fastapi"]),
            patch.object(
                poller,
                "fetch_issues_with_retry",
                return_value=[already_alerted, new_issue],
            ),
            patch.object(poller, "send_telegram", return_value=True) as mock_send,
        ):
            poller.main()

        # Только fresh-1 → send_telegram вызван один раз
        assert mock_send.call_count == 1


# ─── Smoke test для bash dispatcher ──────────────────────────────────────────


class TestBashDispatcher:
    def test_env_override_dispatches_to_python(self, tmp_path):
        """KRAB_SENTRY_POLL_DIRECT_API=1 → bash exec'ит в Python script.

        Smoke: проверяем что bash script содержит правильный dispatcher блок.
        """
        scripts_dir = PROJECT_ROOT / "scripts"
        bash_script = scripts_dir / "sentry_poll_alerts.sh"
        content = bash_script.read_text()

        # Проверки наличия dispatch logic
        assert "KRAB_SENTRY_POLL_DIRECT_API" in content
        assert "sentry_poll_direct.py" in content
        assert 'USE_DIRECT="${KRAB_SENTRY_POLL_DIRECT_API:-1}"' in content
        assert 'if [ "$USE_DIRECT" = "1" ]' in content
        assert "exec " in content  # exec используется для dispatch

    def test_env_override_to_tunnel_falls_through(self):
        """KRAB_SENTRY_POLL_DIRECT_API=0 → bash продолжает выполнение (не exec'ит).

        Smoke: после dispatch блока bash должен иметь legacy curl-based код.
        """
        scripts_dir = PROJECT_ROOT / "scripts"
        bash_script = scripts_dir / "sentry_poll_alerts.sh"
        content = bash_script.read_text()

        # Legacy bash code должен остаться после dispatch блока
        assert "send_telegram" in content
        assert "curl" in content
        assert "https://sentry.io/api/0/" in content

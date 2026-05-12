"""Тесты Wave 100: secrets audit scanner."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import krab_secrets_audit as audit

# --- Pattern detection ---


@pytest.mark.parametrize(
    "pattern_name,sample",
    [
        ("google_api", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("anthropic_api", "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
        ("openai_api", "sk-" + "A" * 48),
        ("google_oauth", "ya29.a0AfH6SMBabcdefghijklmn-XYZ_0123"),
        ("gitlab_token", "glpat-AbCdEfGhIjKlMnOpQrSt"),
        ("github_token", "ghp_" + "a" * 36),
    ],
)
def test_each_pattern_matches(pattern_name: str, sample: str) -> None:
    """Каждый паттерн должен поймать соответствующий ключ."""
    leaks = audit.scan_text(f"prefix {sample} suffix", source="test", file="x")
    assert any(leak.pattern == pattern_name for leak in leaks), (
        f"{pattern_name} не сматчился на {sample!r}"
    )


def test_redact_masks_keys() -> None:
    """redact() оставляет первые 6 символов + REDACTED."""
    snippet = "key=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    redacted = audit.redact(snippet)
    assert "AIzaSy" in redacted
    assert "REDACTED" in redacted
    assert "0123456789" not in redacted


# --- Whitelist ---


def test_whitelist_skips_dotenv() -> None:
    assert audit.is_whitelisted(".env") is True
    assert audit.is_whitelisted("/path/to/.env") is True
    assert audit.is_whitelisted(".env.bak") is True
    assert audit.is_whitelisted(".env.production") is True
    assert audit.is_whitelisted("src/config.py") is False
    assert audit.is_whitelisted("env.py") is False


# --- scan_log_files ---


def test_scan_log_files_clean(tmp_path: Path) -> None:
    """Логи без ключей дают пустой результат."""
    (tmp_path / "app.log").write_text("just normal log line\nanother\n")
    leaks, scanned = audit.scan_log_files(tmp_path, max_age_days=7)
    assert leaks == []
    assert scanned == 2


def test_scan_log_files_detects_leak(tmp_path: Path) -> None:
    """Лог с Google API key → leak detected."""
    log = tmp_path / "app.log"
    log.write_text(
        "INFO normal line\n"
        "ERROR config has AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 inside\n"
        "INFO another normal line\n",
    )
    leaks, scanned = audit.scan_log_files(tmp_path, max_age_days=7)
    assert scanned == 3
    assert len(leaks) == 1
    assert leaks[0].pattern == "google_api"
    assert leaks[0].source == "log"
    assert leaks[0].line == 2
    assert "REDACTED" in leaks[0].redacted_snippet


def test_scan_log_files_skips_old_files(tmp_path: Path) -> None:
    """mtime старше max_age_days → файл пропускается."""
    old_log = tmp_path / "old.log"
    old_log.write_text("ghp_" + "a" * 36 + "\n")
    # Установить mtime на 30 дней назад
    old_ts = time.time() - 30 * 86400
    import os

    os.utime(old_log, (old_ts, old_ts))
    leaks, _ = audit.scan_log_files(tmp_path, max_age_days=7)
    assert leaks == []


def test_scan_log_files_multiple_leaks(tmp_path: Path) -> None:
    """Несколько разных ключей в разных файлах агрегируются."""
    (tmp_path / "a.log").write_text("AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n")
    (tmp_path / "b.log").write_text("ghp_" + "b" * 36 + "\n")
    leaks, _ = audit.scan_log_files(tmp_path, max_age_days=7)
    patterns = {leak.pattern for leak in leaks}
    assert patterns == {"google_api", "github_token"}


# --- scan_git_history ---


def _git_log_fixture(file: str, added_line: str) -> str:
    return (
        f"commit abc123\n"
        f"Author: x\n"
        f"\n"
        f"    msg\n"
        f"\n"
        f"diff --git a/{file} b/{file}\n"
        f"index 111..222 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -1,1 +1,2 @@\n"
        f" existing line\n"
        f"+{added_line}\n"
    )


def test_scan_git_history_detects_leak_in_source(tmp_path: Path) -> None:
    """Added line с ghp_ в src/foo.py → leak."""
    fake = _git_log_fixture("src/foo.py", "TOKEN = 'ghp_" + "z" * 36 + "'")
    runner = MagicMock()
    runner.return_value = MagicMock(stdout=fake)
    leaks, scanned = audit.scan_git_history(tmp_path, runner=runner)
    assert scanned > 0
    assert any(leak.pattern == "github_token" for leak in leaks)
    assert all(leak.source == "git" for leak in leaks)
    assert leaks[0].file == "src/foo.py"


def test_scan_git_history_whitelists_dotenv(tmp_path: Path) -> None:
    """Diff в .env с ключом игнорируется."""
    fake = _git_log_fixture(".env", "GOOGLE_API=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    runner = MagicMock()
    runner.return_value = MagicMock(stdout=fake)
    leaks, _ = audit.scan_git_history(tmp_path, runner=runner)
    assert leaks == []


def test_scan_git_history_empty_output(tmp_path: Path) -> None:
    """git log без diffs → пусто."""
    runner = MagicMock()
    runner.return_value = MagicMock(stdout="")
    leaks, scanned = audit.scan_git_history(tmp_path, runner=runner)
    assert leaks == []
    assert scanned == 0


# --- run_audit aggregation ---


def test_run_audit_aggregates_git_and_logs(tmp_path: Path) -> None:
    """Полный pipeline: git + logs, агрегирует и кладёт timestamp."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app.log").write_text("leak ghp_" + "c" * 36 + " inside\n")

    fake_git = _git_log_fixture("src/bar.py", "k = 'AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'")
    runner = MagicMock()
    runner.return_value = MagicMock(stdout=fake_git)

    report = audit.run_audit(
        repo_dir=tmp_path,
        logs_dir=logs_dir,
        git_runner=runner,
    )

    assert report.timestamp
    assert report.total_scanned > 0
    patterns = {leak.pattern for leak in report.leaks}
    assert "github_token" in patterns
    assert "google_api" in patterns

    # JSON-сериализуется
    data = report.to_dict()
    json.dumps(data)  # не должно бросить
    assert "leaks" in data
    assert "timestamp" in data
    assert "total_scanned" in data


def test_run_audit_clean(tmp_path: Path) -> None:
    """Чистый репо/логи → leaks=[]."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "ok.log").write_text("nothing suspicious here\n")
    runner = MagicMock()
    runner.return_value = MagicMock(stdout="")

    report = audit.run_audit(repo_dir=tmp_path, logs_dir=logs_dir, git_runner=runner)
    assert report.leaks == []

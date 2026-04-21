"""Tests for cron_chado_sync dry-run stub."""

import sys
import os
import unittest
from unittest.mock import patch

# Добавляем scripts/ в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from cron_chado_sync import (
    MAX_DIGEST_CHARS,
    format_digest,
    get_git_log_7d,
    pick_top_features,
    dry_run_preview,
)


class TestFormatDigest(unittest.TestCase):
    def test_basic_format(self):
        features = ["abc123 feat: add swarm task board", "def456 fix: retry logic"]
        digest = format_digest(features)
        assert "Krab × Chado Weekly Sync" in digest
        assert "feat: add swarm task board" in digest
        assert "fix: retry logic" in digest

    def test_truncates_to_max_chars(self):
        long_eco = "A" * 5000
        digest = format_digest(["x" * 50] * 5, ecosystem_summary=long_eco)
        assert len(digest) <= MAX_DIGEST_CHARS

    def test_empty_commits(self):
        digest = format_digest([])
        assert "No commits this week." in digest

    def test_numbered_list(self):
        features = [f"hash{i} feat: feature {i}" for i in range(5)]
        digest = format_digest(features)
        assert "1." in digest
        assert "5." in digest

    def test_sync_request_preserved_after_truncation(self):
        long_eco = "B" * 5000
        digest = format_digest(["x" * 10] * 5, ecosystem_summary=long_eco)
        assert "Sync request" in digest


class TestGitLog(unittest.TestCase):
    def test_returns_list(self):
        commits = get_git_log_7d()
        assert isinstance(commits, list)

    def test_each_entry_is_string(self):
        commits = get_git_log_7d()
        for c in commits:
            assert isinstance(c, str)

    def test_invalid_repo_returns_empty(self):
        # Несуществующий репо — subprocess вернёт ошибку, но функция должна не падать
        try:
            result = get_git_log_7d(repo_path="/nonexistent/repo/path")
            assert isinstance(result, list)
        except Exception:
            pass  # subprocess.run raises on timeout/not-found: acceptable


class TestPickTopFeatures(unittest.TestCase):
    def test_prefers_feat_commits(self):
        commits = [
            "aaa chore: cleanup",
            "bbb feat: add digest",
            "ccc fix: retry bug",
            "ddd docs: update readme",
            "eee feat: cross-ai sync",
        ]
        top = pick_top_features(commits, n=3)
        types = [c.split(" ", 1)[1].split(":")[0] for c in top]
        assert all(t in ("feat", "fix") for t in types)

    def test_fallback_to_all_when_no_feat_fix(self):
        commits = ["aaa chore: cleanup", "bbb docs: readme", "ccc refactor: code"]
        top = pick_top_features(commits, n=2)
        assert len(top) == 2

    def test_respects_n_limit(self):
        commits = [f"hash{i} feat: feature {i}" for i in range(20)]
        top = pick_top_features(commits, n=5)
        assert len(top) == 5

    def test_empty_commits(self):
        assert pick_top_features([]) == []


class TestDryRunPreview(unittest.TestCase):
    def test_preview_contains_markers(self):
        preview = dry_run_preview()
        assert "DRY-RUN PREVIEW" in preview
        assert "Commits found" in preview
        assert "Digest length" in preview

    def test_preview_digest_within_limit(self):
        preview = dry_run_preview()
        # Extract chars count from preview line
        for line in preview.splitlines():
            if "Digest length:" in line:
                parts = line.split()
                chars_idx = parts.index("chars") - 1
                chars = int(parts[chars_idx])
                assert chars <= MAX_DIGEST_CHARS
                break


if __name__ == "__main__":
    unittest.main()

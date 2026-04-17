"""
Tests for scripts/cleanup_stale_worktrees.py.

Покрывает:
- classify_worktree: все 5 категорий (main/external/merged/unmerged/active)
- list_worktrees: парсинг porcelain output
- is_branch_merged_into_main: корректно отрабатывает на error
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts import cleanup_stale_worktrees as csw  # noqa: E402


def test_classify_worktree_main_by_branch():
    wt = {"path": "/some/path", "branch": "main"}
    assert csw.classify_worktree(wt) == "main"


def test_classify_worktree_main_by_path():
    wt = {"path": str(csw.REPO_ROOT), "branch": "feature-x"}
    assert csw.classify_worktree(wt) == "main"


def test_classify_worktree_external():
    wt = {"path": "/Users/other/some/path", "branch": "codex/foo"}
    assert csw.classify_worktree(wt) == "external"


def test_classify_worktree_external_detached():
    wt = {"path": "/private/tmp/krab-head-xyz", "branch": ""}
    assert csw.classify_worktree(wt) == "external"


def test_classify_worktree_agent_merged(monkeypatch):
    monkeypatch.setattr(
        csw,
        "is_branch_merged_into_main",
        lambda b: True,
    )
    wt = {
        "path": f"{csw.REPO_ROOT}/.claude/worktrees/agent-abc123",
        "branch": "worktree-agent-abc123",
    }
    assert csw.classify_worktree(wt) == "merged"


def test_classify_worktree_agent_unmerged(monkeypatch):
    monkeypatch.setattr(
        csw,
        "is_branch_merged_into_main",
        lambda b: False,
    )
    wt = {
        "path": f"{csw.REPO_ROOT}/.claude/worktrees/agent-xyz",
        "branch": "worktree-agent-xyz",
    }
    assert csw.classify_worktree(wt) == "unmerged"


def test_classify_worktree_agent_by_dir_name(monkeypatch):
    """Path имеет prefix agent-, но branch не worktree-agent-."""
    monkeypatch.setattr(
        csw,
        "is_branch_merged_into_main",
        lambda b: False,
    )
    wt = {
        "path": f"{csw.REPO_ROOT}/.claude/worktrees/agent-deadbeef",
        "branch": "custom/branch-name",
    }
    assert csw.classify_worktree(wt) == "unmerged"


def test_classify_worktree_other_claude_active():
    wt = {
        "path": f"{csw.REPO_ROOT}/.claude/worktrees/manual-feature",
        "branch": "custom/feature",
    }
    assert csw.classify_worktree(wt) == "active"


def test_is_branch_merged_returns_false_for_empty():
    assert csw.is_branch_merged_into_main("") is False


def test_is_branch_merged_handles_errors(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = ""

    def fake_run(*_args, **_kwargs):
        return FakeResult()

    monkeypatch.setattr(csw.subprocess, "run", fake_run)
    assert csw.is_branch_merged_into_main("some-branch") is False


def test_list_worktrees_parses_porcelain(monkeypatch, tmp_path):
    sample = (
        "worktree /repo\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /repo/.claude/worktrees/agent-xyz\n"
        "HEAD def456\n"
        "branch refs/heads/worktree-agent-xyz\n"
    )
    monkeypatch.setattr(csw, "git", lambda *a, **kw: sample)
    wts = csw.list_worktrees()
    assert len(wts) == 2
    assert wts[0]["branch"] == "main"
    assert wts[0]["path"] == "/repo"
    assert wts[1]["branch"] == "worktree-agent-xyz"
    assert wts[1]["head"] == "def456"


def test_list_worktrees_handles_detached_head(monkeypatch):
    sample = (
        "worktree /repo\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /tmp/krab-head\n"
        "HEAD 999999\n"
    )
    monkeypatch.setattr(csw, "git", lambda *a, **kw: sample)
    wts = csw.list_worktrees()
    assert len(wts) == 2
    assert wts[1].get("branch") is None or "branch" not in wts[1]


def test_list_worktrees_computes_age(monkeypatch, tmp_path):
    sample = f"worktree {tmp_path}\nHEAD abc123\nbranch refs/heads/test\n"
    monkeypatch.setattr(csw, "git", lambda *a, **kw: sample)
    wts = csw.list_worktrees()
    assert len(wts) == 1
    assert wts[0]["age_days"] is not None
    assert wts[0]["age_days"] >= 0

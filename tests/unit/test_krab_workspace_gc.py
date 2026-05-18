"""Tests for scripts/krab_workspace_gc.py (S61 W5)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Загружаем модуль из scripts/ (не входит в src/, нет пакета).
# sys.modules регистрация ОБЯЗАТЕЛЬНА до exec_module — dataclass требует.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "krab_workspace_gc.py"
_spec = importlib.util.spec_from_file_location("krab_workspace_gc", _SCRIPT)
gc_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["krab_workspace_gc"] = gc_mod
_spec.loader.exec_module(gc_mod)  # type: ignore[union-attr]


# ---------- helpers ----------


def _init_git_repo(path: Path, commit_age_days: float = 0.0) -> None:
    """Init mini git repo with one commit, optionally backdated."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    if commit_age_days > 0:
        ts = int(time.time() - commit_age_days * 86400)
        env = {
            "GIT_AUTHOR_DATE": f"{ts} +0000",
            "GIT_COMMITTER_DATE": f"{ts} +0000",
        }
        subprocess.run(
            ["git", "-C", str(path), "commit", "-q", "-m", "init"],
            check=True,
            env={**__import__("os").environ, **env},
        )
    else:
        subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


# ---------- 1. dry-run returns candidates ----------


def test_dry_run_returns_candidates(tmp_path):
    """collect() возвращает кандидатов без удаления."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    wt = tmp_path / "wt-old"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "feat-old"],
        check=True,
    )
    # backdate worktree commit
    ts = int(time.time() - 30 * 86400)
    (wt / "f.txt").write_text("y")
    subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "old"],
        check=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_DATE": f"{ts} +0000",
            "GIT_COMMITTER_DATE": f"{ts} +0000",
        },
    )

    stale = gc_mod.find_stale_worktrees(str(repo), days=14)
    assert len(stale) == 1
    assert stale[0]["path"] == str(wt)
    assert stale[0]["age_days"] >= 28
    # worktree всё ещё на месте — это dry-run.
    assert wt.exists()


# ---------- 2. execute actually deletes ----------


def test_execute_actually_deletes(tmp_path):
    """remove_path реально удаляет файл/папку."""
    f = tmp_path / "stale.txt"
    f.write_text("garbage")
    assert f.exists()
    ok = gc_mod.remove_path(str(f), is_dir=False)
    assert ok is True
    assert not f.exists()

    d = tmp_path / "stale_dir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    ok2 = gc_mod.remove_path(str(d), is_dir=True)
    assert ok2 is True
    assert not d.exists()


# ---------- 3. active (dirty) worktree skipped ----------


def test_active_worktree_skipped(tmp_path):
    """Worktree с uncommitted changes пропускается."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    wt = tmp_path / "wt-dirty"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "feat-dirty"],
        check=True,
    )
    # старый коммит
    ts = int(time.time() - 30 * 86400)
    (wt / "f.txt").write_text("y")
    subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "old"],
        check=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_DATE": f"{ts} +0000",
            "GIT_COMMITTER_DATE": f"{ts} +0000",
        },
    )
    # делаем dirty
    (wt / "untracked_dirty.txt").write_text("dirty")

    stale = gc_mod.find_stale_worktrees(str(repo), days=14)
    # должны пропустить dirty
    assert all(s["path"] != str(wt) for s in stale)


# ---------- 4. main worktree never touched ----------


def test_safety_main_worktree_skipped(tmp_path):
    """Главный worktree никогда не попадает в stale."""
    repo = tmp_path / "repo"
    # инициализируем с очень старым коммитом — но это main worktree.
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    ts = int(time.time() - 365 * 86400)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "ancient"],
        check=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_DATE": f"{ts} +0000",
            "GIT_COMMITTER_DATE": f"{ts} +0000",
        },
    )

    stale = gc_mod.find_stale_worktrees(str(repo), days=14)
    # main не должен быть в списке (single worktree → entries[1:] пуст)
    assert all(s["path"] != str(repo) for s in stale)
    assert stale == []


# ---------- 5. PID filter by age threshold ----------


def test_pid_filter_age_threshold():
    """find_zombie_claude_sessions фильтрует по возрасту через mocked ps."""
    fake_ps = (
        "  PID     ELAPSED COMMAND\n"
        "  111    00:05 claude --output-format stream-json --print foo\n"
        " 222   10-00:00 claude --output-format stream-json --print bar\n"
        " 333    01:00 some_other_process --output-format ignore\n"
    )

    class FakeRes:
        returncode = 0
        stdout = fake_ps

    with patch.object(gc_mod.subprocess, "run", return_value=FakeRes()):
        zombies = gc_mod.find_zombie_claude_sessions(days=7)

    # Только pid=222 удовлетворяет: 10 дней >= 7 дней + матчит "claude --output-format".
    assert len(zombies) == 1
    assert zombies[0]["pid"] == 222
    assert zombies[0]["age_days"] == 10


# ---------- bonus: etime parser sanity ----------


@pytest.mark.parametrize(
    "etime,expected_sec",
    [
        ("00:05", 5),
        ("01:00", 60),
        ("12:34", 12 * 60 + 34),
        ("01:00:00", 3600),
        ("10-00:00:00", 10 * 86400),
        ("garbage", None),
    ],
)
def test_etime_parser(etime, expected_sec):
    assert gc_mod._parse_etime_to_seconds(etime) == expected_sec

"""Tests for scripts/krab_workspace_gc.py (S61 W5 + S65 W4 expansion)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ---------- S65 W4: expanded coverage ----------


# ---------- main() orchestration ----------


def test_main_orchestration_dry_run(tmp_path, capsys):
    """main() без --execute: только отчёт, никаких изменений."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    with patch.object(gc_mod, "find_zombie_claude_sessions", return_value=[]):
        with patch.object(gc_mod, "cleanup_temp_files", return_value=[]):
            rc = gc_mod.main(["--repo", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "dry-run; pass --execute" in out


def test_main_orchestration_execute_path(tmp_path, capsys):
    """main() с --execute: вызывает execute() и печатает счётчики."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    fake_temp = [
        {"path": str(tmp_path / "fake_tmp"), "age_days": 30, "size_bytes": 0, "is_dir": False},
    ]
    (tmp_path / "fake_tmp").write_text("x")
    with patch.object(gc_mod, "find_stale_worktrees", return_value=[]):
        with patch.object(gc_mod, "find_zombie_claude_sessions", return_value=[]):
            with patch.object(gc_mod, "cleanup_temp_files", return_value=fake_temp):
                rc = gc_mod.main(["--execute", "--verbose", "--repo", str(repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "EXECUTE" in out
    assert "Done: worktrees=" in out
    # verbose: per-item line printed
    assert "temp     OK " in out
    # fake_tmp реально удалён
    assert not (tmp_path / "fake_tmp").exists()


def test_main_custom_thresholds_parsed(tmp_path):
    """main() корректно парсит --worktree-days/--session-days/--temp-days."""
    captured = {}

    def fake_collect(repo_path, worktree_days, session_days, temp_days):
        captured["wd"] = worktree_days
        captured["sd"] = session_days
        captured["td"] = temp_days
        return gc_mod.GCResult()

    with patch.object(gc_mod, "collect", side_effect=fake_collect):
        rc = gc_mod.main(
            [
                "--repo",
                str(tmp_path),
                "--worktree-days",
                "30",
                "--session-days",
                "3",
                "--temp-days",
                "1",
            ]
        )
    assert rc == 0
    assert captured == {"wd": 30, "sd": 3, "td": 1}


# ---------- remove_worktree subprocess invocation ----------


def test_remove_worktree_calls_git_subprocess():
    """remove_worktree вызывает git worktree remove --force."""
    fake_run = MagicMock(return_value=MagicMock(returncode=0))
    with patch.object(gc_mod.subprocess, "run", fake_run):
        ok = gc_mod.remove_worktree("/repo", "/repo/wt-old")
    assert ok is True
    args, kwargs = fake_run.call_args
    cmd = args[0]
    assert cmd[:2] == ["git", "-C"]
    assert "worktree" in cmd and "remove" in cmd and "--force" in cmd
    assert "/repo/wt-old" in cmd
    assert kwargs.get("timeout") == 30


def test_remove_worktree_nonzero_returncode_returns_false():
    """remove_worktree: git вернул != 0 → False."""
    fake_run = MagicMock(return_value=MagicMock(returncode=1))
    with patch.object(gc_mod.subprocess, "run", fake_run):
        assert gc_mod.remove_worktree("/repo", "/repo/wt") is False


def test_remove_worktree_subprocess_error_returns_false():
    """remove_worktree: SubprocessError → False."""
    with patch.object(gc_mod.subprocess, "run", side_effect=subprocess.SubprocessError("boom")):
        assert gc_mod.remove_worktree("/repo", "/repo/wt") is False


# ---------- temp files cleanup ----------


def test_cleanup_temp_files_finds_old_dir(tmp_path):
    """cleanup_temp_files находит старую pytest-of-* директорию."""
    root = tmp_path / "tmp_root"
    root.mkdir()
    old_dir = root / "pytest-of-pablito"
    old_dir.mkdir()
    (old_dir / "x.txt").write_text("data" * 100)
    # backdate mtime 30 days
    old_ts = time.time() - 30 * 86400
    os.utime(old_dir, (old_ts, old_ts))

    # fresh dir should NOT appear
    fresh_dir = root / "pytest-of-fresh"
    fresh_dir.mkdir()
    (fresh_dir / "y.txt").write_text("z")

    # non-matching name skipped
    other = root / "some-random-dir"
    other.mkdir()
    os.utime(other, (old_ts, old_ts))

    out = gc_mod.cleanup_temp_files(days=7, roots=(root,))
    paths = {e["path"] for e in out}
    assert str(old_dir) in paths
    assert str(fresh_dir) not in paths
    assert str(other) not in paths
    rec = next(e for e in out if e["path"] == str(old_dir))
    assert rec["is_dir"] is True
    assert rec["age_days"] >= 29
    assert rec["size_bytes"] > 0


def test_cleanup_temp_files_handles_file_and_missing_root(tmp_path):
    """cleanup_temp_files: файл (не директория) + несуществующий root."""
    root = tmp_path / "tmp_root"
    root.mkdir()
    f = root / "jiti-cache.json"
    f.write_text("payload")
    old_ts = time.time() - 30 * 86400
    os.utime(f, (old_ts, old_ts))
    missing = tmp_path / "does_not_exist"

    out = gc_mod.cleanup_temp_files(days=7, roots=(missing, root))
    rec = next(e for e in out if e["path"] == str(f))
    assert rec["is_dir"] is False
    assert rec["size_bytes"] == len("payload")


def test_cleanup_temp_files_skips_fresh_entries(tmp_path):
    """cleanup_temp_files: свежие записи не возвращаются."""
    root = tmp_path / "tmp_root"
    root.mkdir()
    fresh = root / "pytest-of-x"
    fresh.mkdir()
    (fresh / "f").write_text("y")
    out = gc_mod.cleanup_temp_files(days=7, roots=(root,))
    assert out == []


# ---------- execute() inner branches ----------


def test_execute_counts_errors_when_remove_fails(tmp_path, capsys):
    """execute(): если remove_worktree/kill_session/remove_path → False, errors++."""
    result = gc_mod.GCResult(
        worktrees=[{"path": "/nope/wt", "branch": "x", "last_commit_ts": 0, "age_days": 30}],
        sessions=[{"pid": 99999999, "age_days": 30, "cmd": "claude --output-format foo"}],
        temp_files=[
            {
                "path": str(tmp_path / "missing.txt"),
                "age_days": 30,
                "size_bytes": 0,
                "is_dir": False,
            }
        ],
    )
    with patch.object(gc_mod, "remove_worktree", return_value=False):
        with patch.object(gc_mod, "kill_session", return_value=False):
            with patch.object(gc_mod, "remove_path", return_value=False):
                stats = gc_mod.execute("/repo", result, verbose=True)
    assert stats == {"worktrees": 0, "sessions": 0, "temp_files": 0, "errors": 3}
    out = capsys.readouterr().out
    assert "ERR" in out


def test_execute_happy_path_counts_success():
    """execute(): success path → счётчики увеличиваются."""
    result = gc_mod.GCResult(
        worktrees=[{"path": "/wt", "branch": "b", "last_commit_ts": 0, "age_days": 30}],
        sessions=[{"pid": 123, "age_days": 30, "cmd": "claude --output-format foo"}],
        temp_files=[{"path": "/tmp/x", "age_days": 30, "size_bytes": 0, "is_dir": True}],
    )
    with patch.object(gc_mod, "remove_worktree", return_value=True):
        with patch.object(gc_mod, "kill_session", return_value=True):
            with patch.object(gc_mod, "remove_path", return_value=True):
                stats = gc_mod.execute("/repo", result, verbose=False)
    assert stats == {"worktrees": 1, "sessions": 1, "temp_files": 1, "errors": 0}


# ---------- kill_session safety ----------


def test_kill_session_handles_process_lookup_error():
    """kill_session: ProcessLookupError (зомби исчез) → False, не падает."""
    with patch.object(gc_mod.os, "kill", side_effect=ProcessLookupError):
        assert gc_mod.kill_session(99999) is False


def test_kill_session_handles_permission_error():
    """kill_session: PermissionError (чужой PID) → False, не падает."""
    with patch.object(gc_mod.os, "kill", side_effect=PermissionError):
        assert gc_mod.kill_session(1) is False


def test_kill_session_success_sends_sigterm():
    """kill_session: успешный SIGTERM."""
    fake_kill = MagicMock()
    with patch.object(gc_mod.os, "kill", fake_kill):
        assert gc_mod.kill_session(12345) is True
    fake_kill.assert_called_once_with(12345, gc_mod.signal.SIGTERM)


# ---------- remove_path error path ----------


def test_remove_path_oserror_returns_false(tmp_path):
    """remove_path: OSError → False."""
    with patch.object(gc_mod.shutil, "rmtree", side_effect=OSError):
        assert gc_mod.remove_path(str(tmp_path / "anything"), is_dir=True) is False


# ---------- find_stale_worktrees git failure paths ----------


def test_find_stale_worktrees_git_nonzero_returns_empty():
    """find_stale_worktrees: git вернул != 0 → []."""
    with patch.object(gc_mod.subprocess, "run", return_value=MagicMock(returncode=1, stdout="")):
        assert gc_mod.find_stale_worktrees("/no/such/repo", days=14) == []


def test_find_stale_worktrees_subprocess_error_returns_empty():
    """find_stale_worktrees: SubprocessError → []."""
    with patch.object(gc_mod.subprocess, "run", side_effect=subprocess.SubprocessError):
        assert gc_mod.find_stale_worktrees("/repo", days=14) == []


def test_find_stale_worktrees_skips_missing_path(tmp_path):
    """find_stale_worktrees: skip если worktree path физически отсутствует."""
    # Эмулируем git output с несуществующим worktree path.
    porcelain = (
        f"worktree {tmp_path}/main\nHEAD abc\nbranch refs/heads/main\n\n"
        f"worktree {tmp_path}/gone\nHEAD def\nbranch refs/heads/gone\n\n"
    )
    fake_res = MagicMock(returncode=0, stdout=porcelain)
    with patch.object(gc_mod.subprocess, "run", return_value=fake_res):
        out = gc_mod.find_stale_worktrees(str(tmp_path), days=14)
    assert out == []


# ---------- find_zombie_claude_sessions error paths ----------


def test_find_zombie_claude_sessions_ps_failure_returns_empty():
    """find_zombie_claude_sessions: ps вернул != 0 → []."""
    with patch.object(gc_mod.subprocess, "run", return_value=MagicMock(returncode=1, stdout="")):
        assert gc_mod.find_zombie_claude_sessions(days=7) == []


def test_find_zombie_claude_sessions_subprocess_error_returns_empty():
    """find_zombie_claude_sessions: SubprocessError → []."""
    with patch.object(gc_mod.subprocess, "run", side_effect=subprocess.SubprocessError):
        assert gc_mod.find_zombie_claude_sessions(days=7) == []


def test_find_zombie_claude_sessions_malformed_lines_skipped():
    """find_zombie_claude_sessions: непарсимые строки/PID пропускаются."""
    fake_ps = (
        "PID ELAPSED COMMAND\n"
        "\n"  # пустая строка
        "tooshort\n"  # < 3 parts
        "notanint 10-00:00 claude --output-format x\n"  # bad pid
        "999 zz:zz claude --output-format y\n"  # bad etime
    )
    fake_res = MagicMock(returncode=0, stdout=fake_ps)
    with patch.object(gc_mod.subprocess, "run", return_value=fake_res):
        assert gc_mod.find_zombie_claude_sessions(days=7) == []


# ---------- _worktree_is_dirty + _worktree_last_commit_ts error paths ----------


def test_worktree_is_dirty_subprocess_error_returns_true():
    """_worktree_is_dirty: при ошибке — считаем dirty (safe default)."""
    with patch.object(gc_mod.subprocess, "run", side_effect=subprocess.SubprocessError):
        assert gc_mod._worktree_is_dirty("/some/path") is True


def test_worktree_last_commit_ts_subprocess_error_returns_none():
    """_worktree_last_commit_ts: ошибка → None."""
    with patch.object(gc_mod.subprocess, "run", side_effect=subprocess.SubprocessError):
        assert gc_mod._worktree_last_commit_ts("/x") is None


def test_worktree_last_commit_ts_nonzero_returns_none():
    """_worktree_last_commit_ts: git вернул != 0 → None."""
    with patch.object(gc_mod.subprocess, "run", return_value=MagicMock(returncode=1, stdout="")):
        assert gc_mod._worktree_last_commit_ts("/x") is None


# ---------- _parse_worktree_list bare worktrees ----------


def test_parse_worktree_list_handles_bare_and_blank_lines():
    """_parse_worktree_list: bare worktree парсится, blank lines разделяют записи."""
    porcelain = (
        "worktree /main\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree /bare\nbare\n\n"
        "worktree /feat\nHEAD def456\nbranch refs/heads/feat\n"
    )
    entries = gc_mod._parse_worktree_list(porcelain)
    assert len(entries) == 3
    assert entries[0]["path"] == "/main"
    assert entries[0]["branch"] == "refs/heads/main"
    assert entries[1].get("bare") is True
    assert entries[2]["path"] == "/feat"


# ---------- _print_report exercises both modes ----------


def test_print_report_dry_run_and_execute(capsys):
    """_print_report печатает оба заголовка + содержимое по категориям."""
    result = gc_mod.GCResult(
        worktrees=[{"path": "/x/wt", "branch": "b", "last_commit_ts": 0, "age_days": 30}],
        sessions=[{"pid": 5, "age_days": 9, "cmd": "claude --output-format z"}],
        temp_files=[
            {"path": "/x/t", "age_days": 30, "size_bytes": 2 * 1024 * 1024, "is_dir": True}
        ],
    )
    gc_mod._print_report(result, executing=False)
    out1 = capsys.readouterr().out
    assert "DRY-RUN" in out1
    assert "Stale worktrees: 1" in out1
    assert "Zombie claude sessions: 1" in out1
    assert "Temp files/dirs: 1" in out1

    gc_mod._print_report(result, executing=True)
    out2 = capsys.readouterr().out
    assert "EXECUTE" in out2

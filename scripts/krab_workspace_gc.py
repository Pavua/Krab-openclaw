#!/usr/bin/env python3
"""Krab workspace garbage collector (S61 W5).

Prunes stale git worktrees + zombie claude sessions + temp caches.
Default dry-run, --execute flag for actions.

Categories:
1. Stale git worktrees — last commit older than --worktree-days (default 14)
2. Zombie claude sessions — `claude --output-format ...` процессы старше
   --session-days (default 7)
3. Temp files в ~/.openclaw/tmp/ (pytest-of-*, node-compile-cache, jiti)
   старше --temp-days (default 7)

Safety: главный worktree + worktree с uncommitted changes никогда не трогаем.

Usage:
    python scripts/krab_workspace_gc.py            # dry-run
    python scripts/krab_workspace_gc.py --execute  # actually delete
    python scripts/krab_workspace_gc.py --verbose
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
DEFAULT_TMP_TARGETS = (HOME / ".openclaw" / "tmp",)
DEFAULT_TMP_GLOBS = ("pytest-of-*", "node-compile-cache*", "jiti*")


@dataclass
class GCResult:
    """Аккумулирует кандидатов на удаление по категориям."""

    worktrees: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    temp_files: list[dict] = field(default_factory=list)


# ---------- worktrees ----------


def _parse_worktree_list(porcelain: str) -> list[dict]:
    """Парсит вывод `git worktree list --porcelain` в список записей."""
    entries: list[dict] = []
    cur: dict = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch ") :]
        elif line.strip() == "bare":
            cur["bare"] = True
    if cur:
        entries.append(cur)
    return entries


def _worktree_last_commit_ts(path: str) -> int | None:
    """Возвращает timestamp последнего коммита worktree (или None)."""
    try:
        r = subprocess.run(
            ["git", "-C", path, "log", "-1", "--format=%ct"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return None
        return int(r.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def _worktree_is_dirty(path: str) -> bool:
    """True если в worktree есть незакоммиченные изменения."""
    try:
        r = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(r.stdout.strip())
    except subprocess.SubprocessError:
        # При сомнении — считаем dirty, пропускаем.
        return True


def find_stale_worktrees(repo_path: str, days: int = 14) -> list[dict]:
    """Найти worktrees с last-commit старше N дней.

    Главный worktree (первый в списке) и dirty worktrees пропускаем.
    """
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
    except subprocess.SubprocessError:
        return []

    entries = _parse_worktree_list(r.stdout)
    if not entries:
        return []

    # Первая запись — main worktree, не трогаем.
    main_path = entries[0].get("path")
    cutoff = time.time() - days * 86400
    stale: list[dict] = []

    for entry in entries[1:]:
        path = entry.get("path", "")
        if not path or path == main_path:
            continue
        if entry.get("bare"):
            continue
        if not Path(path).exists():
            # worktree уже удалён (нужен prune, не наша задача)
            continue
        if _worktree_is_dirty(path):
            continue
        ts = _worktree_last_commit_ts(path)
        if ts is None or ts > cutoff:
            continue
        stale.append(
            {
                "path": path,
                "branch": entry.get("branch", "?"),
                "last_commit_ts": ts,
                "age_days": int((time.time() - ts) / 86400),
            }
        )
    return stale


def remove_worktree(repo_path: str, worktree_path: str) -> bool:
    """Удалить worktree через git worktree remove."""
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return r.returncode == 0
    except subprocess.SubprocessError:
        return False


# ---------- claude sessions ----------


_ETIME_RE = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$")


def _parse_etime_to_seconds(etime: str) -> int | None:
    """`ps -o etime` → секунды. Формат [[DD-]HH:]MM:SS."""
    m = _ETIME_RE.match(etime.strip())
    if not m:
        return None
    d, h, mm, ss = m.groups()
    total = int(mm) * 60 + int(ss)
    if h:
        total += int(h) * 3600
    if d:
        total += int(d) * 86400
    return total


def find_zombie_claude_sessions(days: int = 7) -> list[dict]:
    """Найти процессы `claude --output-format ...` старше N дней."""
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid,etime,command"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
    except subprocess.SubprocessError:
        return []

    cutoff_sec = days * 86400
    zombies: list[dict] = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        # PID ETIME COMMAND...
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etime, cmd = parts
        if "claude --output-format" not in cmd:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        age = _parse_etime_to_seconds(etime)
        if age is None or age < cutoff_sec:
            continue
        zombies.append(
            {
                "pid": pid,
                "age_days": int(age / 86400),
                "cmd": cmd[:120],
            }
        )
    return zombies


def kill_session(pid: int) -> bool:
    """SIGTERM процессу."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------- temp files ----------


def cleanup_temp_files(days: int = 7, roots: tuple[Path, ...] = DEFAULT_TMP_TARGETS) -> list[dict]:
    """Найти временные кэши старше N дней."""
    cutoff = time.time() - days * 86400
    candidates: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for entry in root.iterdir():
            if not any(entry.match(g) for g in DEFAULT_TMP_GLOBS):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue
            size_bytes = 0
            if entry.is_dir():
                for p in entry.rglob("*"):
                    try:
                        size_bytes += p.stat().st_size
                    except OSError:
                        pass
            else:
                try:
                    size_bytes = entry.stat().st_size
                except OSError:
                    pass
            candidates.append(
                {
                    "path": str(entry),
                    "age_days": int((time.time() - mtime) / 86400),
                    "size_bytes": size_bytes,
                    "is_dir": entry.is_dir(),
                }
            )
    return candidates


def remove_path(path: str, is_dir: bool) -> bool:
    """Удалить файл или директорию."""
    p = Path(path)
    try:
        if is_dir:
            shutil.rmtree(p)
        else:
            p.unlink()
        return True
    except OSError:
        return False


# ---------- orchestration ----------


def collect(
    repo_path: str,
    worktree_days: int,
    session_days: int,
    temp_days: int,
) -> GCResult:
    return GCResult(
        worktrees=find_stale_worktrees(repo_path, worktree_days),
        sessions=find_zombie_claude_sessions(session_days),
        temp_files=cleanup_temp_files(temp_days),
    )


def execute(repo_path: str, result: GCResult, verbose: bool = False) -> dict:
    """Реально удалить кандидатов. Возвращает счётчики."""
    stats = {"worktrees": 0, "sessions": 0, "temp_files": 0, "errors": 0}
    for w in result.worktrees:
        ok = remove_worktree(repo_path, w["path"])
        if ok:
            stats["worktrees"] += 1
        else:
            stats["errors"] += 1
        if verbose:
            print(f"  worktree {'OK ' if ok else 'ERR'} {w['path']}")
    for s in result.sessions:
        ok = kill_session(s["pid"])
        if ok:
            stats["sessions"] += 1
        else:
            stats["errors"] += 1
        if verbose:
            print(f"  session  {'OK ' if ok else 'ERR'} pid={s['pid']}")
    for t in result.temp_files:
        ok = remove_path(t["path"], t["is_dir"])
        if ok:
            stats["temp_files"] += 1
        else:
            stats["errors"] += 1
        if verbose:
            print(f"  temp     {'OK ' if ok else 'ERR'} {t['path']}")
    return stats


def _print_report(result: GCResult, executing: bool) -> None:
    header = "EXECUTE" if executing else "DRY-RUN"
    print(f"=== Krab Workspace GC ({header}) ===")
    print(f"Stale worktrees: {len(result.worktrees)}")
    for w in result.worktrees:
        print(f"  - {w['path']}  (branch={w['branch']}, age={w['age_days']}d)")
    print(f"Zombie claude sessions: {len(result.sessions)}")
    for s in result.sessions:
        print(f"  - pid={s['pid']}  age={s['age_days']}d  cmd={s['cmd']}")
    total_size_mb = sum(t["size_bytes"] for t in result.temp_files) / 1024 / 1024
    print(f"Temp files/dirs: {len(result.temp_files)}  (~{total_size_mb:.1f} MB)")
    for t in result.temp_files:
        kind = "dir " if t["is_dir"] else "file"
        size_mb = t["size_bytes"] / 1024 / 1024
        print(f"  - {kind} {t['path']}  age={t['age_days']}d  size={size_mb:.1f}MB")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Krab workspace garbage collector")
    p.add_argument("--execute", action="store_true", help="actually delete (default dry-run)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--repo", default=str(Path.cwd()), help="main repo path")
    p.add_argument("--worktree-days", type=int, default=14)
    p.add_argument("--session-days", type=int, default=7)
    p.add_argument("--temp-days", type=int, default=7)
    args = p.parse_args(argv)

    result = collect(args.repo, args.worktree_days, args.session_days, args.temp_days)
    _print_report(result, executing=args.execute)

    if args.execute:
        stats = execute(args.repo, result, verbose=args.verbose)
        print(
            f"\nDone: worktrees={stats['worktrees']} sessions={stats['sessions']} "
            f"temp_files={stats['temp_files']} errors={stats['errors']}"
        )
    else:
        print("\n(dry-run; pass --execute to apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

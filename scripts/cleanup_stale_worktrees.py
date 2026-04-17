#!/usr/bin/env python3
"""
Cleanup stale worktrees после agent pipeline session.

Usage:
    python scripts/cleanup_stale_worktrees.py               # dry-run — показать что можно удалить
    python scripts/cleanup_stale_worktrees.py --prune       # реально удалить merged worktrees
    python scripts/cleanup_stale_worktrees.py --all-agents  # prune all worktree-agent-* (force)
    python scripts/cleanup_stale_worktrees.py --older-than-days 3  # только старше N дней
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def git(*args, capture=True):
    """Run git command в REPO_ROOT, exit on error."""
    result = subprocess.run(
        ["git", *args],
        capture_output=capture,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print(f"git {args}: {result.stderr[:200]}")
        sys.exit(1)
    return result.stdout


def list_worktrees():
    """Returns list of dicts: {path, branch, head, age_days}."""
    out = git("worktree", "list", "--porcelain")
    wts = []
    current: dict = {}
    for line in out.split("\n"):
        if line.startswith("worktree "):
            if current:
                wts.append(current)
            current = {"path": line[len("worktree ") :]}
        elif line.startswith("branch "):
            # Format: branch refs/heads/<name>
            ref = line[len("branch ") :]
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[len("refs/heads/") :]
            else:
                current["branch"] = ref
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :]
    if current:
        wts.append(current)

    # Compute age (modification time of worktree directory)
    for wt in wts:
        try:
            path = Path(wt["path"])
            if path.exists():
                wt["age_days"] = (
                    datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
                ).days
            else:
                wt["age_days"] = None
        except Exception:
            wt["age_days"] = None

    return wts


def is_branch_merged_into_main(branch: str) -> bool:
    """Check if all commits на branch есть в main."""
    if not branch:
        return False
    try:
        result = subprocess.run(
            ["git", "branch", "--merged", "main", "--list", branch],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())
    except Exception:
        return False


def classify_worktree(wt: dict) -> str:
    """
    Returns: "merged" | "active" | "unmerged" | "main" | "external"
    """
    path = wt.get("path", "")
    branch = wt.get("branch", "")

    # Main worktree — keep
    if branch == "main" or path == str(REPO_ROOT):
        return "main"

    # External worktrees (not in .claude/worktrees/) — keep
    if ".claude/worktrees/" not in path:
        return "external"

    # Agent worktrees: worktree-agent-XXXX
    name = Path(path).name
    if branch.startswith("worktree-agent-") or name.startswith("agent-"):
        if is_branch_merged_into_main(branch):
            return "merged"
        return "unmerged"

    # Other Claude worktrees — treat as active
    return "active"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cleanup stale Krab agent worktrees.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Actually remove merged worktrees",
    )
    parser.add_argument(
        "--all-agents",
        action="store_true",
        help="Force remove ALL agent-* worktrees (merged + unmerged)",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        help="Only prune worktrees older than N days",
    )
    args = parser.parse_args()

    wts = list_worktrees()
    print(f"Found {len(wts)} worktrees\n")

    categories: dict[str, list[dict]] = {
        "main": [],
        "external": [],
        "merged": [],
        "unmerged": [],
        "active": [],
    }
    for wt in wts:
        cat = classify_worktree(wt)
        categories[cat].append(wt)

    for cat in ("main", "external", "active", "unmerged", "merged"):
        if categories[cat]:
            print(f"\n## {cat.upper()} ({len(categories[cat])})")
            for wt in categories[cat]:
                age_val = wt.get("age_days")
                age = f" (age: {age_val}d)" if age_val is not None else ""
                branch = (wt.get("branch") or "?")[:50]
                name = Path(wt.get("path", "?")).name[:30]
                print(f"  - {branch:<50} @ {name}{age}")

    # Decide what to prune
    if args.all_agents:
        to_prune = [
            w
            for w in categories["merged"] + categories["unmerged"]
            if Path(w.get("path", "")).name.startswith("agent-")
        ]
    else:
        to_prune = list(categories["merged"])

    if args.older_than_days:
        to_prune = [
            w
            for w in to_prune
            if w.get("age_days") is not None and w["age_days"] >= args.older_than_days
        ]

    if not to_prune:
        print("\nNothing to prune.")
        return

    print(f"\n{'=' * 60}")
    print(f"Candidates for prune: {len(to_prune)}")
    for wt in to_prune:
        print(f"  - {wt.get('branch', '?')}")

    if not args.prune:
        print("\n(Dry-run — re-run с --prune чтобы удалить)")
        return

    # Actually prune
    print("\nPruning...")
    pruned = 0
    for wt in to_prune:
        path = wt.get("path", "")
        branch = wt.get("branch", "")
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", path],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print(f"  removed worktree {Path(path).name}")
            pruned += 1
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            print(f"  worktree remove failed: {Path(path).name}: {err[:100]}")
            continue
        try:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print(f"  deleted branch {branch[:50]}")
        except subprocess.CalledProcessError:
            pass

    print(f"\nPruned {pruned} worktrees.")


if __name__ == "__main__":
    main()

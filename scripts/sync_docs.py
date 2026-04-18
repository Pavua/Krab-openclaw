#!/usr/bin/env python3
"""
Sync all auto-generated docs in one pass.

Runs (in order):
1. generate_commands_cheatsheet.py — COMMANDS_CHEATSHEET.md (145+ cmds)
2. generate_docs_index.py — docs/README.md (31+ docs)

Usage:
    python scripts/sync_docs.py                 # run all
    python scripts/sync_docs.py --only cheatsheet   # filter
    python scripts/sync_docs.py --check         # verify no drift (for CI)

Cron recipe (weekly):
    0 4 * * 1 /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python \\
              /Users/pablito/Antigravity_AGENTS/Краб/scripts/sync_docs.py
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / "venv/bin/python"

SCRIPTS = {
    "cheatsheet": "generate_commands_cheatsheet.py",
    "index": "generate_docs_index.py",
}


def run(script_name: str, check_mode: bool = False) -> tuple[bool, str]:
    """Run single doc script. Returns (success, output)."""
    script_path = REPO / "scripts" / script_name
    if not script_path.exists():
        return False, f"script not found: {script_name}"

    start = time.time()
    try:
        result = subprocess.run(
            [str(VENV_PY), str(script_path)],
            capture_output=True, text=True, timeout=60, cwd=REPO,
        )
        elapsed = time.time() - start
        success = result.returncode == 0
        output = result.stdout or result.stderr
        return success, f"({elapsed:.1f}s) {output[:150]}"
    except subprocess.TimeoutExpired:
        return False, "timeout (60s)"
    except Exception as e:
        return False, f"error: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Sync all auto-generated Krab docs"
    )
    parser.add_argument("--only", choices=list(SCRIPTS.keys()), help="Run only specific script")
    parser.add_argument("--check", action="store_true", help="Exit code 1 if drift detected")
    args = parser.parse_args()

    scripts_to_run = {args.only: SCRIPTS[args.only]} if args.only else SCRIPTS

    print(f"🔄 Krab docs auto-sync — {len(scripts_to_run)} scripts\n")

    if args.check:
        # Git diff check before
        before = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=REPO
        ).stdout

    failed = []
    for name, script in scripts_to_run.items():
        success, output = run(script, args.check)
        status = "✅" if success else "❌"
        print(f"{status} {name:<15} {output}")
        if not success:
            failed.append(name)

    if args.check:
        # Git diff check after
        after = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=REPO
        ).stdout
        drift = set(after.split()) - set(before.split())
        if drift:
            print(f"\n⚠️ Drift detected in: {drift}")
            sys.exit(1)

    if failed:
        print(f"\n❌ {len(failed)} failed: {failed}")
        sys.exit(1)

    print(f"\n✅ All docs synced successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()

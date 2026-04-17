#!/usr/bin/env python3
"""
Append entries в CHANGELOG.md [Unreleased] секцию.

Usage:
    python scripts/changelog_append.py "Added" "Memory retrieval endpoint"
    python scripts/changelog_append.py --from-git HEAD~5..HEAD  # parse last 5 commits

Categories: Added/Changed/Fixed/Removed/Security/Docs/Tests
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

CATEGORIES = {"Added", "Changed", "Fixed", "Removed", "Security", "Docs", "Tests"}

# Conventional commit type → CHANGELOG section
_TYPE_MAPPING = {
    "feat": "Added",
    "add": "Added",
    "fix": "Fixed",
    "refactor": "Changed",
    "perf": "Changed",
    "test": "Tests",
    "docs": "Docs",
    "chore": "Changed",
    "security": "Security",
    "revert": "Changed",
    "build": "Changed",
    "ci": "Changed",
    "style": "Changed",
}


def parse_commit_to_category(commit_msg: str) -> tuple[str, str]:
    """Conventional commit type → (category, formatted entry)."""
    msg = commit_msg.strip()
    m = re.match(r"^(\w+)(\([^\)]+\))?!?:\s*(.+)$", msg)
    if not m:
        return "Changed", msg
    ctype, scope, desc = m.group(1), m.group(2) or "", m.group(3)
    category = _TYPE_MAPPING.get(ctype.lower(), "Changed")
    label = f"{ctype}{scope}"
    return category, f"**{label}** — {desc}"


def read_changelog() -> str:
    if not CHANGELOG.exists():
        return ""
    return CHANGELOG.read_text(encoding="utf-8")


def write_changelog(content: str) -> None:
    CHANGELOG.write_text(content, encoding="utf-8")


def append_entry(category: str, description: str) -> None:
    if category not in CATEGORIES:
        print(f"[err] Unknown category: {category}. Valid: {sorted(CATEGORIES)}")
        sys.exit(1)

    content = read_changelog()
    if not content:
        print(f"[err] CHANGELOG.md not found at {CHANGELOG}")
        sys.exit(1)

    unreleased_match = re.search(r"^## \[Unreleased\]\s*$", content, re.MULTILINE)
    if not unreleased_match:
        print("[err] [Unreleased] section not found в CHANGELOG.md")
        sys.exit(1)

    start_idx = unreleased_match.end()
    next_section = re.search(r"\n## ", content[start_idx:])
    end_idx = start_idx + next_section.start() if next_section else len(content)

    unreleased_body = content[start_idx:end_idx]
    cat_pattern = rf"^### {re.escape(category)}\s*$"
    cat_match = re.search(cat_pattern, unreleased_body, re.MULTILINE)

    new_entry = f"- {description}\n"

    if cat_match:
        # Найти конец последнего bullet-line в этой подсекции.
        cat_header_end = start_idx + cat_match.end()
        rest = content[cat_header_end:end_idx]
        lines = rest.split("\n")
        insert_offset = 0
        consumed = 0
        for idx, line in enumerate(lines):
            stripped = line.strip()
            is_bullet = stripped.startswith("-")
            is_continuation = line.startswith(" ") and stripped
            is_empty = not stripped
            if is_bullet or is_continuation or (is_empty and idx == 0):
                consumed += len(line) + 1  # +1 for newline
                if is_bullet or is_continuation:
                    insert_offset = consumed
            else:
                break
        insert_at = cat_header_end + insert_offset
        new_content = content[:insert_at] + new_entry + content[insert_at:]
    else:
        # Новая subsection перед следующей `## ` секцией (или EOF).
        # Убираем trailing whitespace перед границей.
        prefix = content[:end_idx].rstrip("\n")
        suffix = content[end_idx:]
        insertion = f"\n\n### {category}\n{new_entry}"
        new_content = prefix + insertion + ("\n" + suffix if suffix else "\n")

    write_changelog(new_content)
    print(f"[ok] Appended to [Unreleased] > {category}:\n   - {description}")


def append_from_git(rev_range: str) -> None:
    """Parse commits из git log + add по одному."""
    try:
        result = subprocess.run(
            ["git", "log", rev_range, "--pretty=format:%s"],
            capture_output=True,
            text=True,
            check=True,
        )
        commits = [line for line in result.stdout.strip().split("\n") if line.strip()]
    except subprocess.CalledProcessError as e:
        print(f"[err] git log failed: {e}")
        sys.exit(1)

    print(f"Processing {len(commits)} commits...")
    for msg in commits:
        lower = msg.lower()
        if lower.startswith("merge:") or lower.startswith("merge "):
            continue  # Пропускаем merge-коммиты
        category, entry = parse_commit_to_category(msg)
        append_entry(category, entry)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append entries to CHANGELOG.md [Unreleased] section."
    )
    parser.add_argument(
        "category", nargs="?", help="One of Added/Changed/Fixed/Removed/Security/Docs/Tests"
    )
    parser.add_argument("description", nargs="?", help="Entry description")
    parser.add_argument("--from-git", help="Git rev range, e.g. HEAD~5..HEAD")
    args = parser.parse_args()

    if args.from_git:
        append_from_git(args.from_git)
    elif args.category and args.description:
        append_entry(args.category, args.description)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

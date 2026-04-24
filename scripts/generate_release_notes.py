#!/usr/bin/env python3
"""Release notes generator для Краба.

CLI:
    python scripts/generate_release_notes.py --since <ref> [--output CHANGELOG.md]

Логика:
  - git log <since>..HEAD --pretty=format:"%H|%s|%b<END>"
  - Классификация по conventional commits prefix.
  - Извлечение Sentry shortId (PYTHON-FASTAPI-XX) → линк на issue.
  - Markdown output со статистикой (commits, authors, +LOC/-LOC).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Conventional-commit prefix → markdown section.
CATEGORY_MAP: dict[str, str] = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "perf": "Performance",
    "docs": "Documentation",
    "test": "Tests",
    "refactor": "Refactoring",
    "style": "Other",
    "chore": "Other",
    "ci": "Other",
    "build": "Other",
}

SECTION_ORDER: list[str] = [
    "Features",
    "Bug Fixes",
    "Performance",
    "Refactoring",
    "Documentation",
    "Tests",
    "Other",
    "Uncategorized",
]

# Sentry shortId — e.g. PYTHON-FASTAPI-5E, KRAB-USERBOT-42
SENTRY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-[A-Z][A-Z0-9]+-[A-Z0-9]+)\b")
PREFIX_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?!?:\s*(?P<rest>.+)$")

SENTRY_ORG_SLUG_DEFAULT = "krab"
GITHUB_REPO_DEFAULT = "Pavua/Krab-openclaw"


@dataclass
class Commit:
    sha: str
    subject: str
    body: str
    category: str = "Uncategorized"
    sentry_ids: list[str] = field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_commits(since: str) -> list[Commit]:
    # Use a rare delimiter so bodies with newlines survive.
    sep = "<<<KRABSEP>>>"
    end = "<<<KRABEND>>>"
    fmt = f"%H{sep}%s{sep}%b{end}"
    raw = run_git(["log", f"{since}..HEAD", f"--pretty=format:{fmt}"])
    commits: list[Commit] = []
    for chunk in raw.split(end):
        chunk = chunk.strip().lstrip("\n")
        if not chunk:
            continue
        parts = chunk.split(sep, 2)
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ""
        c = Commit(sha=sha, subject=subject, body=body)
        classify(c)
        commits.append(c)
    return commits


def classify(commit: Commit) -> None:
    m = PREFIX_RE.match(commit.subject)
    if m:
        prefix = m.group("type").lower()
        commit.category = CATEGORY_MAP.get(prefix, "Uncategorized")
    else:
        commit.category = "Uncategorized"
    # Sentry refs: scan subject + body for Closes/Fixes/Refs of shortIds.
    haystack = f"{commit.subject}\n{commit.body}"
    seen: set[str] = set()
    for match in SENTRY_RE.findall(haystack):
        if match in seen:
            continue
        seen.add(match)
        commit.sentry_ids.append(match)


def get_stats(since: str) -> tuple[int, int, list[str]]:
    """Return (added_loc, deleted_loc, authors)."""
    numstat = run_git(["log", f"{since}..HEAD", "--numstat", "--pretty=format:"])
    added = 0
    deleted = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, _ = parts[0], parts[1], parts[2]
        if a.isdigit():
            added += int(a)
        if d.isdigit():
            deleted += int(d)
    authors_raw = run_git(["log", f"{since}..HEAD", "--pretty=format:%an"])
    authors = sorted({a.strip() for a in authors_raw.splitlines() if a.strip()})
    return added, deleted, authors


def format_sentry_link(short_id: str, org_slug: str) -> str:
    url = f"https://sentry.io/organizations/{org_slug}/issues/?query={short_id}"
    return f"[{short_id}]({url})"


def format_commit_line(c: Commit, org_slug: str, repo: str) -> str:
    commit_url = f"https://github.com/{repo}/commit/{c.sha}"
    line = f"- {c.subject} ([{c.short_sha}]({commit_url}))"
    if c.sentry_ids:
        refs = ", ".join(format_sentry_link(sid, org_slug) for sid in c.sentry_ids)
        line += f" — Sentry: {refs}"
    return line


def build_markdown(
    commits: list[Commit],
    added: int,
    deleted: int,
    authors: list[str],
    since: str,
    org_slug: str,
    repo: str,
) -> str:
    today = _dt.date.today().isoformat()
    buckets: dict[str, list[Commit]] = {}
    for c in commits:
        buckets.setdefault(c.category, []).append(c)

    out: list[str] = []
    out.append(f"# Changelog — {today}")
    out.append("")
    out.append(f"_Since `{since}` — {len(commits)} commits_")
    out.append("")

    for section in SECTION_ORDER:
        items = buckets.get(section)
        if not items:
            continue
        out.append(f"## {section}")
        out.append("")
        for c in items:
            out.append(format_commit_line(c, org_slug, repo))
        out.append("")

    out.append("## Stats")
    out.append("")
    out.append(f"- {len(commits)} commits")
    if authors:
        out.append(f"- {len(authors)} authors: {', '.join(authors)}")
    out.append(f"- +{added} LOC, -{deleted} LOC")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="git ref (commit/tag/branch) as lower bound")
    parser.add_argument("--output", default=None, help="output .md path (stdout if omitted)")
    parser.add_argument("--sentry-org", default=SENTRY_ORG_SLUG_DEFAULT)
    parser.add_argument("--repo", default=GITHUB_REPO_DEFAULT)
    args = parser.parse_args(argv)

    try:
        commits = parse_commits(args.since)
    except subprocess.CalledProcessError as e:
        print(f"git log failed: {e.stderr}", file=sys.stderr)
        return 2
    if not commits:
        print(f"no commits in range {args.since}..HEAD", file=sys.stderr)
        return 1

    added, deleted, authors = get_stats(args.since)
    md = build_markdown(commits, added, deleted, authors, args.since, args.sentry_org, args.repo)

    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"wrote {args.output} — {len(commits)} commits")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

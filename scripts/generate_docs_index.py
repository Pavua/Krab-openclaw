#!/usr/bin/env python3
"""Auto-generate docs/README.md from docs/*.md files."""
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
OUT = DOCS / "README.md"


def extract_title_and_brief(path: Path) -> tuple[str, str]:
    """Extract title and brief description from markdown file."""
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return path.stem.replace("_", " ").title(), ""

    lines = text.splitlines()
    title = path.stem.replace("_", " ").title()
    brief = ""

    # Look for first heading or first non-empty line
    for line in lines[:30]:
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()
        elif s and not s.startswith(("#", "`", "<", "-", "|")) and not brief:
            brief = s[:95].rstrip(".")
            break

    return title, brief


def main():
    # Get all .md files in root (excluding README.md)
    files = sorted(p for p in DOCS.glob("*.md") if p.name != "README.md")

    # Build markdown
    lines = [
        "# Krab Documentation Index",
        "",
        f"Auto-generated overview of `docs/` directory — **{len(files)} documents**.",
        "",
        "## Root-level documentation",
        "",
        "| File | Title | Brief |",
        "|------|-------|-------|",
    ]

    for p in files:
        title, brief = extract_title_and_brief(p)
        lines.append(f"| [{p.name}]({p.name}) | {title} | {brief} |")

    # Subdirectories
    subdirs = sorted([d for d in DOCS.iterdir() if d.is_dir() and not d.name.startswith(".")])
    if subdirs:
        lines.append("")
        lines.append("## Subdirectories")
        lines.append("")
        for d in subdirs:
            md_count = sum(1 for _ in d.rglob("*.md"))
            lines.append(f"- **`{d.name}/`** — {md_count} markdown files")

    # Related files
    lines.extend([
        "",
        "## Related files (project root)",
        "",
        "- [`CLAUDE.md`](../CLAUDE.md) — Project conventions & architecture for Claude Code",
        "- [`CHANGELOG.md`](../CHANGELOG.md) — Release notes (Keep-a-Changelog format)",
        "- [`IMPROVEMENTS.md`](../IMPROVEMENTS.md) — Architecture backlog & vision",
        "- [`.remember/`](../.remember/) — Session scratch workspace (gitignored)",
    ])

    # Write output
    content = "\n".join(lines) + "\n"
    OUT.write_text(content)
    print(f"✓ Generated {OUT}")
    print(f"  - {len(files)} root docs")
    print(f"  - {len(subdirs)} subdirectories")


if __name__ == "__main__":
    main()

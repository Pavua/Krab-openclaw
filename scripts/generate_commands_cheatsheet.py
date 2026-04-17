#!/usr/bin/env python3
"""Generate docs/COMMANDS_CHEATSHEET.md from /api/commands."""
import httpx
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

URL = "http://127.0.0.1:8080/api/commands"
OUT = Path(__file__).resolve().parent.parent / "docs" / "COMMANDS_CHEATSHEET.md"


def main():
    try:
        r = httpx.get(URL, timeout=5.0)
        data = r.json()
    except Exception as e:
        print(f"Failed to fetch commands: {e}")
        sys.exit(1)

    commands = data.get("commands", [])
    total = len(commands)

    # Group by category
    by_cat = defaultdict(list)
    for c in commands:
        cat = c.get("category", "misc")
        by_cat[cat].append(c)

    # Build markdown
    lines = [
        "# Krab Telegram Commands Cheatsheet",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M} · Total: {total} commands",
        "",
        "## Categories",
        "",
        "| Category | Count |",
        "|----------|-------|",
    ]

    for cat in sorted(by_cat.keys()):
        lines.append(f"| {cat} | {len(by_cat[cat])} |")

    # Each category section
    for cat in sorted(by_cat.keys()):
        lines.append("")
        lines.append(f"## {cat.capitalize()}")
        lines.append("")
        lines.append("| Command | Usage | Description | Owner |")
        lines.append("|---------|-------|-------------|-------|")

        for c in sorted(by_cat[cat], key=lambda x: x.get("name", "")):
            name = f"`!{c.get('name', '')}`"
            usage = f"`{c.get('usage', '')}`" if c.get('usage') else "—"
            desc = (c.get("description", "") or "")[:80]
            owner = "✓" if c.get("owner_only") else "—"
            lines.append(f"| {name} | {usage} | {desc} | {owner} |")

    # Owner-only quick reference
    owner_only = [c for c in commands if c.get("owner_only")]
    if owner_only:
        lines.append("")
        lines.append("## Owner-only quick ref")
        lines.append("")
        lines.append("| Command | Usage |")
        lines.append("|---------|-------|")
        for c in sorted(owner_only, key=lambda x: x.get("name", "")):
            lines.append(f"| `!{c.get('name', '')}` | `{c.get('usage', '')}` |")

    # Write file
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"✓ Written {total} commands to {OUT}")


if __name__ == "__main__":
    main()

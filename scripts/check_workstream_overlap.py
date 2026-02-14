# -*- coding: utf-8 -*-
"""
Проверка пересечений зон ответственности Codex и Antigravity.

Что делает:
1) Читает ownership-паттерны из config/workstreams/*.txt.
2) Ищет файлы репозитория, попавшие одновременно в обе зоны.
3) Отдельно проверяет текущие измененные файлы (git status --porcelain),
   чтобы заранее ловить коллизии в параллельной разработке.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_PATHS = ROOT / "config" / "workstreams" / "codex_paths.txt"
ANTIGRAVITY_PATHS = ROOT / "config" / "workstreams" / "antigravity_paths.txt"


def _read_patterns(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    patterns: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        patterns.append(value)
    return patterns


def _match_any(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def _git_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _changed_files() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    files: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # Формат: XY <path>
        candidate = line[3:].strip()
        if candidate:
            files.append(candidate)
    return files


def main() -> int:
    codex_patterns = _read_patterns(CODEX_PATHS)
    antigravity_patterns = _read_patterns(ANTIGRAVITY_PATHS)

    if not codex_patterns or not antigravity_patterns:
        print("❌ Паттерны ownership не заполнены.")
        return 2

    repo_files = _git_files()
    overlap_all = [
        path
        for path in repo_files
        if _match_any(path, codex_patterns) and _match_any(path, antigravity_patterns)
    ]
    changed = _changed_files()
    overlap_changed = [
        path
        for path in changed
        if _match_any(path, codex_patterns) and _match_any(path, antigravity_patterns)
    ]

    print("🧭 Workstream Overlap Check")
    print(f"- Codex patterns: {len(codex_patterns)}")
    print(f"- Antigravity patterns: {len(antigravity_patterns)}")
    print(f"- Repo overlaps: {len(overlap_all)}")
    print(f"- Changed-file overlaps: {len(overlap_changed)}")

    if overlap_all:
        print("\n⚠️ Пересечения ownership (repo-level):")
        for path in overlap_all[:100]:
            print(f"  - {path}")

    if overlap_changed:
        print("\n🚨 Конфликт в текущих изменениях:")
        for path in overlap_changed[:100]:
            print(f"  - {path}")
        return 1

    print("\n✅ Конфликтов в измененных файлах не обнаружено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


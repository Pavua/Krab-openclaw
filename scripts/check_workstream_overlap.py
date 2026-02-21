# -*- coding: utf-8 -*-
"""
Проверка пересечений зон ответственности между всеми workstream-потоками.

Что делает:
1) Читает ownership-паттерны из config/workstreams/*_paths.txt.
2) Ищет файлы репозитория, попавшие одновременно в несколько потоков.
3) Отдельно проверяет текущие измененные файлы (git status --porcelain),
   чтобы заранее ловить коллизии в параллельной разработке.
"""

from __future__ import annotations

import fnmatch
import itertools
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSTREAM_DIR = ROOT / "config" / "workstreams"


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


def _read_workstreams() -> dict[str, list[str]]:
    streams: dict[str, list[str]] = {}
    for path in sorted(WORKSTREAM_DIR.glob("*_paths.txt")):
        name = path.stem.replace("_paths", "")
        streams[name] = _read_patterns(path)
    return streams


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


def _matched_streams(rel_path: str, streams: dict[str, list[str]]) -> list[str]:
    matched: list[str] = []
    for stream_name, patterns in streams.items():
        if _match_any(rel_path, patterns):
            matched.append(stream_name)
    return matched


def _build_overlap_entries(files: list[str], streams: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    overlaps: list[tuple[str, list[str]]] = []
    for path in files:
        matched = _matched_streams(path, streams)
        if len(matched) > 1:
            overlaps.append((path, matched))
    return overlaps


def main() -> int:
    streams = _read_workstreams()
    if len(streams) < 2:
        print("❌ Нужно минимум два файла *_paths.txt в config/workstreams.")
        return 2
    empty_streams = [name for name, patterns in streams.items() if not patterns]
    if empty_streams:
        print(f"❌ Пустые ownership-потоки: {', '.join(empty_streams)}")
        return 2

    repo_files = _git_files()
    overlap_all = _build_overlap_entries(repo_files, streams)
    changed = _changed_files()
    overlap_changed = _build_overlap_entries(changed, streams)

    print("🧭 Workstream Overlap Check")
    for stream_name, patterns in streams.items():
        print(f"- {stream_name} patterns: {len(patterns)}")
    pair_count = len(list(itertools.combinations(streams.keys(), 2)))
    print(f"- Stream pairs: {pair_count}")
    print(f"- Repo overlaps: {len(overlap_all)}")
    print(f"- Changed-file overlaps: {len(overlap_changed)}")

    if overlap_all:
        print("\n⚠️ Пересечения ownership (repo-level):")
        for path, matched in overlap_all[:100]:
            print(f"  - {path}  <- {', '.join(matched)}")

    if overlap_changed:
        print("\n🚨 Конфликт в текущих изменениях:")
        for path, matched in overlap_changed[:100]:
            print(f"  - {path}  <- {', '.join(matched)}")
        return 1

    print("\n✅ Конфликтов в измененных файлах не обнаружено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

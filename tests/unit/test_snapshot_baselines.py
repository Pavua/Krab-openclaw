"""Sanity-проверки для baseline-снапшотов API endpoints и команд.

Защищают от случайного срыва snapshot-файлов и ловят регрессы в их формате.
Live diff против runtime — отдельный скрипт scripts/snapshot_endpoints_commands.py.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
ENDPOINTS_BASELINE = FIXTURES / "api_endpoints_baseline.json"
COMMANDS_BASELINE = FIXTURES / "commands_baseline.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_endpoints_baseline_count_matches_live() -> None:
    """Sanity: baseline должен содержать заметное количество endpoints (>200)."""
    payload = _load(ENDPOINTS_BASELINE)
    endpoints = payload["endpoints"]
    assert isinstance(endpoints, list)
    assert payload["count"] == len(endpoints)
    assert payload["count"] > 200, f"baseline count={payload['count']} suspiciously low"
    assert all(p.startswith("/") for p in endpoints), "all entries must be URL paths"


def test_commands_baseline_has_no_duplicates() -> None:
    """Baseline команд — отсортированный уникальный список с префиксом '!'."""
    payload = _load(COMMANDS_BASELINE)
    commands = payload["commands"]
    assert isinstance(commands, list)
    assert payload["count"] == len(commands)
    assert len(commands) == len(set(commands)), "commands baseline must be unique"
    assert commands == sorted(commands), "commands baseline must be sorted"
    assert all(c.startswith("!") for c in commands), "every command must start with '!'"
    assert payload["count"] > 100, f"commands count={payload['count']} suspiciously low"

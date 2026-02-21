"""
Тесты для scripts/check_workstream_overlap.py.

Проверяем multi-stream логику пересечений без запуска git-команд.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_overlap_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "check_workstream_overlap.py"
    spec = importlib.util.spec_from_file_location("check_workstream_overlap", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_matched_streams_detects_multiple_owners():
    module = _load_overlap_module()
    streams = {
        "codex": ["src/core/*.py", "docs/*.md"],
        "antigravity": ["src/handlers/*.py"],
        "gemini_design": ["docs/*.md", "src/web/prototypes/gemini/*"],
    }

    matched = module._matched_streams("docs/plan.md", streams)
    assert matched == ["codex", "gemini_design"]


def test_build_overlap_entries_returns_only_conflicts():
    module = _load_overlap_module()
    streams = {
        "codex": ["src/core/*.py", "docs/*.md"],
        "antigravity": ["src/handlers/*.py"],
        "gemini_design": ["docs/*.md", "src/web/prototypes/gemini/*"],
    }
    files = [
        "src/core/model_manager.py",
        "src/handlers/ai.py",
        "docs/roadmap.md",
        "src/web/prototypes/gemini/mock.html",
    ]

    overlaps = module._build_overlap_entries(files, streams)

    assert overlaps == [("docs/roadmap.md", ["codex", "gemini_design"])]


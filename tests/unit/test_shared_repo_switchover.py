# -*- coding: utf-8 -*-
"""
Тесты shared-repo switchover helper.
"""

from __future__ import annotations

from pathlib import Path

from src.core.shared_repo_switchover import (
    analyze_overlap_paths,
    build_switchover_recommendation,
    parse_git_status_porcelain,
    parse_git_status_porcelain_z,
)


def test_parse_git_status_porcelain_handles_tracked_untracked_and_rename() -> None:
    """Парсер должен нормализовать tracked/untracked и брать новый путь после rename."""
    text = "\n".join(
        [
            "## feature/test",
            " M README.md",
            "A  docs/NEW.md",
            "R  old.txt -> new.txt",
            "?? temp/data.json",
        ]
    )

    items = parse_git_status_porcelain(text)

    assert [item["path"] for item in items] == [
        "README.md",
        "docs/NEW.md",
        "new.txt",
        "temp/data.json",
    ]
    assert items[-1]["untracked"] is True
    assert items[0]["tracked"] is True


def test_build_switchover_recommendation_marks_overlap_as_manual_merge() -> None:
    """Пересечение dirty paths в обеих копиях должно блокировать авто-перенос."""
    recommendation = build_switchover_recommendation(
        current_repo={
            "branch": "fix/a",
            "head": "111",
            "dirty_count": 2,
            "dirty_paths": ["README.md", "docs/PLAN.md"],
        },
        shared_repo={
            "exists": True,
            "git_dir_exists": True,
            "branch": "fix/b",
            "head": "222",
            "dirty_count": 2,
            "dirty_paths": ["README.md", "src/app.py"],
        },
    )

    assert recommendation["strategy_code"] == "manual_merge_required"
    assert recommendation["overlap_count"] == 1
    assert recommendation["overlap_paths_preview"] == ["README.md"]


def test_parse_git_status_porcelain_z_keeps_real_paths_without_shell_quotes() -> None:
    """NUL-формат должен сохранять реальные пути с пробелами и Unicode."""
    text = " M docs/PLAN RU.md\0?? docs/План.md\0"

    items = parse_git_status_porcelain_z(text)

    assert [item["path"] for item in items] == ["docs/PLAN RU.md", "docs/План.md"]


def test_analyze_overlap_paths_splits_identical_and_divergent_text(tmp_path: Path) -> None:
    """Overlap-анализ должен отличать одинаковые файлы от реально расходящихся."""
    current = tmp_path / "current"
    shared = tmp_path / "shared"
    (current / "docs").mkdir(parents=True)
    (shared / "docs").mkdir(parents=True)
    (current / "docs" / "same.md").write_text("alpha\nbeta\n", encoding="utf-8")
    (shared / "docs" / "same.md").write_text("alpha\nbeta\n", encoding="utf-8")
    (current / "docs" / "diff.md").write_text("one\ntwo\n", encoding="utf-8")
    (shared / "docs" / "diff.md").write_text("one\nthree\n", encoding="utf-8")

    report = analyze_overlap_paths(
        current_root=current,
        shared_root=shared,
        overlap_paths=["docs/same.md", "docs/diff.md"],
    )

    counts = report["counts"]
    assert counts["identical"] == 1
    assert counts["divergent_text"] == 1

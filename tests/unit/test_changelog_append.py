"""
Unit-тесты ``scripts/changelog_append.py``.

Покрывают:
  * ``parse_commit_to_category()`` — маппинг conventional commit типов;
  * ``append_entry()`` — создание новой subsection + append к существующей;
  * ``append_from_git()`` — пропуск merge-коммитов;
  * Unknown category → sys.exit(1);
  * Отсутствие [Unreleased] → sys.exit(1);
  * Сохранение остальных секций ([10.1.0] и т.п.) без изменений.

Запуск::

    venv/bin/python -m pytest tests/unit/test_changelog_append.py -q --noconftest
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# noqa: E402 — sys.path mutation выше нужна до импорта.
from scripts import changelog_append  # noqa: E402
from scripts.changelog_append import (  # noqa: E402
    CATEGORIES,
    append_entry,
    append_from_git,
    parse_commit_to_category,
)

# ---------------------------------------------------------------------------
# parse_commit_to_category
# ---------------------------------------------------------------------------


def test_parse_conventional_feat_with_scope() -> None:
    cat, entry = parse_commit_to_category("feat(memory): add search")
    assert cat == "Added"
    assert "feat(memory)" in entry
    assert "add search" in entry


def test_parse_conventional_fix() -> None:
    cat, entry = parse_commit_to_category("fix: handle empty response")
    assert cat == "Fixed"
    assert "**fix**" in entry


def test_parse_conventional_docs() -> None:
    cat, _ = parse_commit_to_category("docs(readme): update install")
    assert cat == "Docs"


def test_parse_conventional_test() -> None:
    cat, _ = parse_commit_to_category("test: add coverage for validator")
    assert cat == "Tests"


def test_parse_conventional_chore() -> None:
    cat, _ = parse_commit_to_category("chore: bump deps")
    assert cat == "Changed"


def test_parse_conventional_security() -> None:
    cat, _ = parse_commit_to_category("security: patch xss")
    assert cat == "Security"


def test_parse_conventional_unknown_type() -> None:
    cat, _ = parse_commit_to_category("wip: experimenting")
    assert cat == "Changed"


def test_parse_non_conventional_commit() -> None:
    cat, entry = parse_commit_to_category("Just a raw message")
    assert cat == "Changed"
    assert entry == "Just a raw message"


def test_parse_breaking_change_marker() -> None:
    """feat!: ... считается Added, не ломается на `!`."""
    cat, _ = parse_commit_to_category("feat!: breaking api rework")
    assert cat == "Added"


# ---------------------------------------------------------------------------
# append_entry — basic flows
# ---------------------------------------------------------------------------


def test_append_entry_creates_new_category(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [10.1.0]\n\n### Added\n- old\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    append_entry("Added", "new feature")

    content = changelog.read_text(encoding="utf-8")
    assert "[Unreleased]" in content
    assert "- new feature" in content
    # Section [10.1.0] должна остаться нетронутой.
    assert "- old" in content
    assert "## [10.1.0]" in content


def test_append_entry_appends_to_existing_category(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n- existing\n\n## [10.1.0]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    append_entry("Added", "new one")

    content = changelog.read_text(encoding="utf-8")
    added_section = content.split("### Added")[1].split("##")[0]
    assert "- existing" in added_section
    assert "- new one" in added_section


def test_append_entry_preserves_other_categories(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- bug1\n\n## [10.1.0]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    append_entry("Added", "shiny")

    content = changelog.read_text(encoding="utf-8")
    assert "- bug1" in content
    assert "- shiny" in content
    assert "### Fixed" in content
    assert "### Added" in content


def test_append_entry_empty_unreleased(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [10.1.0]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    append_entry("Fixed", "tiny bug")

    content = changelog.read_text(encoding="utf-8")
    assert "### Fixed" in content
    assert "- tiny bug" in content
    assert "## [10.1.0]" in content


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_append_entry_unknown_category_exits(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [Unreleased]\n", encoding="utf-8")
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    with pytest.raises(SystemExit) as exc_info:
        append_entry("Nonsense", "oops")
    assert exc_info.value.code == 1


def test_append_entry_missing_file_exits(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "missing.md"
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    with pytest.raises(SystemExit) as exc_info:
        append_entry("Added", "whatever")
    assert exc_info.value.code == 1


def test_append_entry_no_unreleased_section_exits(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [10.0.0]\n", encoding="utf-8")
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    with pytest.raises(SystemExit) as exc_info:
        append_entry("Added", "stuff")
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# append_from_git
# ---------------------------------------------------------------------------


def test_append_from_git_skips_merge_commits(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [10.1.0]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog_append, "CHANGELOG", changelog)

    class FakeResult:
        stdout = "merge: feature branch\nfeat: add thing\nfix: repair bug\n"

    def fake_run(*_args, **_kwargs):
        return FakeResult()

    monkeypatch.setattr(changelog_append.subprocess, "run", fake_run)

    append_from_git("HEAD~3..HEAD")

    content = changelog.read_text(encoding="utf-8")
    assert "add thing" in content
    assert "repair bug" in content
    assert "feature branch" not in content  # merge skipped


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------


def test_categories_set_complete() -> None:
    assert CATEGORIES == {
        "Added",
        "Changed",
        "Fixed",
        "Removed",
        "Security",
        "Docs",
        "Tests",
    }

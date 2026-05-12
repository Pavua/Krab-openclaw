# -*- coding: utf-8 -*-
"""Тесты для scripts/krab_session_close.py — Wave 87.

Покрывает:
  1. parse_args — defaults + --since-commit + --dry-run + --out
  2. resolve_prev_head — CLI > ENV > None
  3. build_handoff — все ключевые секции присутствуют
  4. main(--dry-run) не пишет файл
  5. main() без prev HEAD не падает (graceful)
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "krab_session_close.py"


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("krab_session_close", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_mod = _load_script()


# ---------------------------------------------------------------------------
# 1. argparse
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    ns = _mod.parse_args([])
    assert ns.since_commit is None
    assert ns.dry_run is False
    assert ns.out.endswith("next_session.md")


def test_parse_args_all_flags(tmp_path: Path) -> None:
    out = tmp_path / "handoff.md"
    ns = _mod.parse_args(["--since-commit", "abc123", "--out", str(out), "--dry-run"])
    assert ns.since_commit == "abc123"
    assert ns.dry_run is True
    assert ns.out == str(out)


# ---------------------------------------------------------------------------
# 2. resolve_prev_head: CLI > ENV > None
# ---------------------------------------------------------------------------


def test_resolve_prev_head_cli_wins(monkeypatch) -> None:
    monkeypatch.setenv("KRAB_SESSION_PREV_HEAD", "from_env")
    assert _mod.resolve_prev_head("from_cli") == "from_cli"


def test_resolve_prev_head_env_fallback(monkeypatch) -> None:
    monkeypatch.setenv("KRAB_SESSION_PREV_HEAD", "from_env")
    assert _mod.resolve_prev_head(None) == "from_env"


def test_resolve_prev_head_none(monkeypatch) -> None:
    monkeypatch.delenv("KRAB_SESSION_PREV_HEAD", raising=False)
    assert _mod.resolve_prev_head(None) is None
    assert _mod.resolve_prev_head("") is None


# ---------------------------------------------------------------------------
# 3. build_handoff: все ключевые секции присутствуют
# ---------------------------------------------------------------------------


def test_build_handoff_contains_all_sections() -> None:
    handoff = _mod.build_handoff(
        prev_head="deadbeef",
        cur_head="cafef00d",
        branch="main",
        commits=["abc1234 First commit", "def5678 Second commit"],
        diff_stat="5 files changed, 100 insertions(+), 50 deletions(-)",
        n_tests=12345,
        n_endpoints=286,
        n_alerts=11,
        pending=["### P0 — fix something", "### P1 — clean up"],
        session_tag="2026-05-12 10:00 UTC",
    )

    # TL;DR keys
    assert "prev HEAD" in handoff and "deadbeef" in handoff
    assert "current HEAD" in handoff and "cafef00d" in handoff
    assert "Коммитов" in handoff and "2" in handoff
    assert "12345" in handoff
    assert "286" in handoff
    assert "11" in handoff
    # Commits block
    assert "abc1234 First commit" in handoff
    # Pending
    assert "P0 — fix something" in handoff
    # Quick commands block
    assert "new Stop Krab.command" in handoff
    assert "pytest" in handoff
    # Header
    assert "Session Handoff" in handoff
    assert "2026-05-12 10:00 UTC" in handoff


def test_build_handoff_empty_commits_graceful() -> None:
    handoff = _mod.build_handoff(
        prev_head=None,
        cur_head="abc",
        branch="main",
        commits=[],
        diff_stat="(нет изменений)",
        n_tests=-1,
        n_endpoints=-1,
        n_alerts=-1,
        pending=[],
        session_tag="2026-05-12",
    )
    assert "не задан" in handoff
    assert "нет коммитов" in handoff
    assert "пусто" in handoff


# ---------------------------------------------------------------------------
# 4. main(--dry-run) не пишет файл
# ---------------------------------------------------------------------------


def test_main_dry_run_no_write(tmp_path: Path, monkeypatch, capsys) -> None:
    out = tmp_path / "should_not_exist.md"
    monkeypatch.delenv("KRAB_SESSION_PREV_HEAD", raising=False)

    # Заглушаем тяжёлые операции
    with patch.object(_mod, "count_tests", return_value=100), \
         patch.object(_mod, "count_endpoints", return_value=50), \
         patch.object(_mod, "count_alerts", return_value=5), \
         patch.object(_mod, "extract_pending_items", return_value=[]), \
         patch.object(_mod, "current_head", return_value="abc1234"), \
         patch.object(_mod, "current_branch", return_value="main"):
        rc = _mod.main(["--since-commit", "deadbeef", "--out", str(out), "--dry-run"])

    assert rc == 0
    assert not out.exists(), "dry-run не должен создавать файл"
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "Session Handoff" in captured.out


def test_main_writes_file(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "handoff.md"
    monkeypatch.delenv("KRAB_SESSION_PREV_HEAD", raising=False)

    with patch.object(_mod, "count_tests", return_value=100), \
         patch.object(_mod, "count_endpoints", return_value=-1), \
         patch.object(_mod, "count_alerts", return_value=11), \
         patch.object(_mod, "extract_pending_items", return_value=["TODO: x"]), \
         patch.object(_mod, "collect_commits", return_value=["abc First"]), \
         patch.object(_mod, "collect_diff_stat", return_value="1 file changed"), \
         patch.object(_mod, "current_head", return_value="cur123"), \
         patch.object(_mod, "current_branch", return_value="main"):
        rc = _mod.main(["--since-commit", "prev999", "--out", str(out)])

    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "prev999" in content
    assert "cur123" in content
    assert "abc First" in content


# ---------------------------------------------------------------------------
# 5. main без prev HEAD не падает
# ---------------------------------------------------------------------------


def test_main_without_prev_head_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    out = tmp_path / "handoff.md"
    monkeypatch.delenv("KRAB_SESSION_PREV_HEAD", raising=False)

    with patch.object(_mod, "count_tests", return_value=-1), \
         patch.object(_mod, "count_endpoints", return_value=-1), \
         patch.object(_mod, "count_alerts", return_value=-1), \
         patch.object(_mod, "extract_pending_items", return_value=[]), \
         patch.object(_mod, "current_head", return_value="abc"), \
         patch.object(_mod, "current_branch", return_value="main"):
        rc = _mod.main(["--out", str(out), "--dry-run"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "не задан" in captured.out or "(не задан)" in captured.out

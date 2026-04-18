"""Tests for scripts/sync_docs.py — composite docs generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_DOCS_PATH = REPO_ROOT / "scripts" / "sync_docs.py"


@pytest.fixture(scope="module")
def sync_docs_mod():
    spec = importlib.util.spec_from_file_location("sync_docs", SYNC_DOCS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_extract_endpoints_basic(sync_docs_mod):
    src = '''
        @self.app.get("/api/foo")
        async def foo(): ...
        @self.app.post("/api/bar")
        async def bar(): ...
        @self.app.get("/api/foo")
        async def foo_dup(): ...
    '''
    result = sync_docs_mod.extract_endpoints(src)
    paths = [p for p, _ in result]
    assert paths == sorted(paths)
    assert "/api/foo" in paths
    assert "/api/bar" in paths


def test_extract_endpoints_skips_non_api(sync_docs_mod):
    src = '''
        @self.app.get("/nano_theme.css")
        async def css(): ...
        @self.app.get("/api/stats")
        async def stats(): ...
    '''
    result = sync_docs_mod.extract_endpoints(src)
    paths = [p for p, _ in result]
    assert "/nano_theme.css" not in paths
    assert "/api/stats" in paths


def test_extract_commands(sync_docs_mod):
    src = """
    async def handle_search(bot, msg): ...
    async def handle_swarm(bot, msg): ...
    def not_handler(): ...
    async def handle_todo_list(bot, msg): ...
    """
    cmds = sync_docs_mod.extract_commands(src)
    assert cmds == ["search", "swarm", "todo_list"]


def test_extract_alerts_and_metrics(sync_docs_mod):
    yaml = """
    groups:
      - name: krab
        rules:
          - alert: KrabDown
            expr: up{job="krab"} == 0
          - alert: ArchiveDbBig
            expr: krab_archive_db_size_bytes > 500
          - alert: ArchiveDbBig
            expr: krab_archive_db_size_bytes > 500
    """
    alerts, metrics = sync_docs_mod.extract_alerts_and_metrics(yaml)
    assert alerts == ["ArchiveDbBig", "KrabDown"]
    assert "krab_archive_db_size_bytes" in metrics


def test_render_endpoints_markdown(sync_docs_mod):
    block = sync_docs_mod.render_endpoints([("/api/foo", "GET"), ("/api/bar", "POST")])
    assert sync_docs_mod.MARK_ENDPOINTS_BEGIN in block
    assert sync_docs_mod.MARK_ENDPOINTS_END in block
    assert "| Endpoint | Метод |" in block
    assert "| `/api/foo` | GET |" in block


def test_render_commands_markdown(sync_docs_mod):
    block = sync_docs_mod.render_commands(["ask", "search", "swarm"])
    assert "`!ask`" in block
    assert "`!search`" in block
    assert sync_docs_mod.MARK_COMMANDS_BEGIN in block


def test_replace_or_append_replaces_existing(sync_docs_mod):
    doc = "header\n<!-- BEGIN:auto-endpoints -->\nold\n<!-- END:auto-endpoints -->\nfooter\n"
    new_block = "<!-- BEGIN:auto-endpoints -->\nnew\n<!-- END:auto-endpoints -->"
    result = sync_docs_mod.replace_or_append(
        doc,
        sync_docs_mod.MARK_ENDPOINTS_BEGIN,
        sync_docs_mod.MARK_ENDPOINTS_END,
        new_block,
    )
    assert "old" not in result
    assert "new" in result
    assert "header" in result
    assert "footer" in result


def test_replace_or_append_appends_when_missing(sync_docs_mod):
    doc = "header only\n"
    new_block = "<!-- BEGIN:auto-endpoints -->\nfresh\n<!-- END:auto-endpoints -->"
    result = sync_docs_mod.replace_or_append(
        doc,
        sync_docs_mod.MARK_ENDPOINTS_BEGIN,
        sync_docs_mod.MARK_ENDPOINTS_END,
        new_block,
    )
    assert "header only" in result
    assert "fresh" in result

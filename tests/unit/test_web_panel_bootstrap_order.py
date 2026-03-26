# -*- coding: utf-8 -*-
"""
Регрессионные проверки bootstrap-порядка owner web panel.

Эти тесты фиксируют два инварианта:
1) `refreshAll()` не должен снова становиться строго последовательным;
2) тяжёлый browser/MCP probe не должен держать базовую гидратацию OpenClaw-карточки.
"""

from __future__ import annotations

from pathlib import Path


INDEX_HTML_PATH = Path(__file__).resolve().parents[2] / "src" / "web" / "index.html"


def _index_html_source() -> str:
    """Читает inline-frontend web-панели как исходник для статической регрессии."""

    return INDEX_HTML_PATH.read_text(encoding="utf-8")


def test_refresh_all_bootstraps_runtime_blocks_in_parallel() -> None:
    """`refreshAll()` должен распараллеливать первичную гидратацию через `Promise.allSettled`."""

    source = _index_html_source()
    refresh_start = source.index("async function refreshAll() {")
    refresh_end = source.index('document.getElementById("refreshBtn")', refresh_start)
    refresh_source = source[refresh_start:refresh_end]

    assert "Promise.allSettled" in refresh_source
    assert '["stats", () => updateStats(true)]' in refresh_source
    assert '["openclaw_status", () => loadOpenclawStatus(false)]' in refresh_source
    assert '["voice_runtime", () => loadVoiceRuntimeStatus(false, false)]' in refresh_source
    assert '["translator_readiness", () => loadTranslatorReadinessStatus(false)]' in refresh_source


def test_openclaw_status_keeps_browser_probe_outside_fast_batch() -> None:
    """`loadOpenclawStatus()` не должен включать browser readiness в критический `Promise.all`."""

    source = _index_html_source()

    assert 'const browserReadinessPromise = fetch("/api/openclaw/browser-mcp-readiness?url=https%3A%2F%2Fexample.com")' in source
    assert "const [catalogRes, autoswitchRes, channelsRes, runtimeConfigRes] = await Promise.all([" in source
    assert "const browserReadinessRes = await browserReadinessPromise;" in source

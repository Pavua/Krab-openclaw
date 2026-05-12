# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.logs_admin_router`` — Wave 169 (Session 48).

Все file IO мокируется через tmp_path / env override (KRAB_LOG_FILE),
тесты не должны зависеть от реального ~/.openclaw/krab_runtime_state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import logs_admin_router as lar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.logs_admin_router import build_logs_admin_router

# ---------------------------------------------------------------------------
# Sample structlog ConsoleRenderer output (как в реальном krab_main.log).
# ---------------------------------------------------------------------------

# Plain (без ANSI) — для базовых тестов парсинга.
_SAMPLE_LINES_PLAIN = [
    "2026-05-13 01:25:14 [info     ] message_one                    [module=src.foo] key1=val1",
    "2026-05-13 01:25:15 [warning  ] something_off                  module=src.bar count=3",
    "2026-05-13 01:25:16 [error    ] explosion                      module=src.baz exc=ValueError",
    "2026-05-13 01:25:17 [debug    ] verbose_trace                  module=src.dbg",
    "2026-05-13 01:25:18 [critical ] panic_state                    module=src.alarm",
    "==== Krab detached start 2026-05-13 01:00:00 ====",
    "[launcher] detached_wrapper_started pid=12345 at 2026-05-13 01:00:00",
]

# С ANSI escapes — как structlog dev.ConsoleRenderer пишет в TTY.
_SAMPLE_ANSI_LINE = (
    "\x1b[2m2026-05-13 01:25:14\x1b[0m "
    "[\x1b[32m\x1b[1minfo     \x1b[0m] "
    "\x1b[1mmessage_text                 \x1b[0m "
    "\x1b[36mmodule\x1b[0m=\x1b[35msrc.foo\x1b[0m"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(build_logs_admin_router(ctx))
    return TestClient(app)


def _make_log(tmp_path: Path, lines: list[str]) -> Path:
    log_file = tmp_path / "krab_main.log"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_file


# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_color_codes() -> None:
    cleaned = lar._strip_ansi(_SAMPLE_ANSI_LINE)
    assert "\x1b[" not in cleaned
    assert "info" in cleaned
    assert "src.foo" in cleaned


def test_strip_ansi_passthrough_plain() -> None:
    plain = "no escapes here"
    assert lar._strip_ansi(plain) == plain


# ---------------------------------------------------------------------------
# _parse_log_line
# ---------------------------------------------------------------------------


def test_parse_log_line_basic_info() -> None:
    parsed = lar._parse_log_line(_SAMPLE_LINES_PLAIN[0])
    assert parsed["ts"] == "2026-05-13 01:25:14"
    assert parsed["level"] == "INFO"
    assert parsed["module"] == "src.foo"
    assert "message_one" in parsed["message"]


def test_parse_log_line_warning() -> None:
    parsed = lar._parse_log_line(_SAMPLE_LINES_PLAIN[1])
    assert parsed["level"] == "WARNING"
    assert parsed["module"] == "src.bar"


def test_parse_log_line_strips_ansi() -> None:
    parsed = lar._parse_log_line(_SAMPLE_ANSI_LINE)
    assert parsed["level"] == "INFO"
    assert parsed["module"] == "src.foo"
    assert "\x1b[" not in parsed["raw"]


def test_parse_log_line_handles_unparseable() -> None:
    # ==== маркеры и [launcher] prefix — не парсятся в structlog формат.
    parsed = lar._parse_log_line(_SAMPLE_LINES_PLAIN[5])
    assert parsed["level"] == ""
    assert "Krab detached start" in parsed["message"]


def test_parse_log_line_critical() -> None:
    parsed = lar._parse_log_line(_SAMPLE_LINES_PLAIN[4])
    assert parsed["level"] == "CRITICAL"
    assert parsed["module"] == "src.alarm"


# ---------------------------------------------------------------------------
# _read_last_lines_reverse
# ---------------------------------------------------------------------------


def test_read_last_lines_reverse_basic(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    lines, truncated, scanned = lar._read_last_lines_reverse(log, max_lines=100)
    assert truncated is False
    assert scanned > 0
    # Все строки прочитаны, порядок — chronological (old → new).
    assert any("message_one" in line for line in lines)
    assert any("panic_state" in line for line in lines)
    # Последняя строка должна быть последней в файле.
    assert "launcher" in lines[-1] or "detached_wrapper_started" in lines[-1]


def test_read_last_lines_reverse_limits_to_n(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    lines, _, _ = lar._read_last_lines_reverse(log, max_lines=2)
    assert len(lines) == 2
    # Должны быть последние 2 строки.
    assert "Krab detached start" in lines[0] or "launcher" in lines[0]


def test_read_last_lines_reverse_missing_file(tmp_path: Path) -> None:
    log = tmp_path / "nope.log"
    lines, truncated, scanned = lar._read_last_lines_reverse(log, max_lines=10)
    assert lines == []
    assert truncated is False
    assert scanned == 0


def test_read_last_lines_reverse_empty_file(tmp_path: Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("")
    lines, truncated, scanned = lar._read_last_lines_reverse(log, max_lines=10)
    assert lines == []
    assert truncated is False


def test_read_last_lines_reverse_truncates_at_cap(tmp_path: Path) -> None:
    # Создаём файл больше cap — должны увидеть truncated=True.
    log = tmp_path / "huge.log"
    big_chunk = "X" * 1024 + "\n"
    with open(log, "w", encoding="utf-8") as fp:
        # 10 KiB content, scan cap 4 KiB → truncated.
        for _ in range(10):
            fp.write(big_chunk)
    lines, truncated, scanned = lar._read_last_lines_reverse(
        log, max_lines=1000, max_scan_bytes=4096
    )
    assert truncated is True
    assert scanned <= 4096


def test_read_last_lines_reverse_chunk_boundary(tmp_path: Path) -> None:
    # Чтобы убедиться что split строк через chunks работает корректно.
    lines = [f"line_{i:05d}" for i in range(500)]
    log = _make_log(tmp_path, lines)
    out, _, _ = lar._read_last_lines_reverse(log, max_lines=10)
    assert len(out) == 10
    assert out[-1] == "line_00499"
    assert out[0] == "line_00490"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_passes_level_threshold() -> None:
    assert lar._passes_level("ERROR", "WARNING") is True
    assert lar._passes_level("INFO", "WARNING") is False
    assert lar._passes_level("CRITICAL", "ERROR") is True
    assert lar._passes_level("DEBUG", "DEBUG") is True
    # Empty min_level → всё проходит.
    assert lar._passes_level("DEBUG", "") is True


def test_passes_level_unparsed_only_at_debug() -> None:
    # Не-распарсенные (level="") видны только если min=DEBUG.
    assert lar._passes_level("", "DEBUG") is True
    assert lar._passes_level("", "INFO") is False


def test_passes_grep_case_insensitive() -> None:
    assert lar._passes_grep("Hello WORLD", "world") is True
    assert lar._passes_grep("Hello WORLD", "WORLD") is True
    assert lar._passes_grep("Hello WORLD", "missing") is False
    # Пустой grep → True.
    assert lar._passes_grep("anything", "") is True


def test_apply_filters_combines_level_and_grep() -> None:
    parsed = [lar._parse_log_line(line) for line in _SAMPLE_LINES_PLAIN]
    out = lar._apply_filters(parsed, level="WARNING", grep="explosion")
    # Только error-строка содержит "explosion" и проходит WARNING+.
    assert len(out) == 1
    assert out[0]["level"] == "ERROR"


# ---------------------------------------------------------------------------
# _resolve_log_path — env override
# ---------------------------------------------------------------------------


def test_resolve_log_path_uses_env(tmp_path: Path) -> None:
    custom = tmp_path / "custom.log"
    with patch.dict(os.environ, {"KRAB_LOG_FILE": str(custom)}, clear=False):
        assert lar._resolve_log_path() == custom


def test_resolve_log_path_disabled() -> None:
    with patch.dict(os.environ, {"KRAB_LOG_FILE": ""}, clear=False):
        assert lar._resolve_log_path() is None
    with patch.dict(os.environ, {"KRAB_LOG_FILE": "none"}, clear=False):
        assert lar._resolve_log_path() is None


def test_resolve_log_path_uses_runtime_state_dir(tmp_path: Path) -> None:
    env = {"KRAB_RUNTIME_STATE_DIR": str(tmp_path)}
    # KRAB_LOG_FILE надо удалить чтобы он не пересилил.
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("KRAB_LOG_FILE", None)
        path = lar._resolve_log_path()
    assert path == tmp_path / "krab_main.log"


# ---------------------------------------------------------------------------
# GET /api/admin/logs/tail
# ---------------------------------------------------------------------------


def test_tail_returns_lines_default(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail?n=200")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["truncated"] is False
    # При level=INFO (no default) — все level фильтруются с INFO+, поэтому
    # сюда попадут info/warning/error/critical но не debug/unparsed.
    levels = {ln["level"] for ln in body["lines"]}
    assert "INFO" in levels
    assert "ERROR" in levels


def test_tail_filters_by_level(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail?n=200&level=ERROR")
    assert resp.status_code == 200
    levels = {ln["level"] for ln in resp.json()["lines"]}
    assert "INFO" not in levels
    assert "WARNING" not in levels
    assert {"ERROR", "CRITICAL"}.issubset(levels)


def test_tail_filters_by_grep(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail?n=200&grep=panic")
    body = resp.json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["level"] == "CRITICAL"


def test_tail_rejects_invalid_level(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail?level=BOGUS")
    assert resp.status_code == 400
    assert "logs_invalid_level" in resp.json()["detail"]


def test_tail_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    with patch.object(lar, "_resolve_log_path", return_value=missing):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"] == []
    assert body["note"] == "log_file_missing"


def test_tail_when_logfile_disabled() -> None:
    with patch.object(lar, "_resolve_log_path", return_value=None):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"] == []
    assert body["note"] == "log_file_disabled"


def test_tail_caps_n_to_max() -> None:
    client = _make_client()
    resp = client.get("/api/admin/logs/tail?n=999999")
    assert resp.status_code == 422  # FastAPI Query(le=) validation


def test_tail_rejects_zero_n() -> None:
    client = _make_client()
    resp = client.get("/api/admin/logs/tail?n=0")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/admin/logs/download
# ---------------------------------------------------------------------------


def test_download_returns_text_attachment(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN)
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/download?n=1000")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert "attachment" in resp.headers.get("content-disposition", "")
    body = resp.text
    assert "krab_main.log tail" in body
    assert "message_one" in body


def test_download_strips_ansi(tmp_path: Path) -> None:
    log = _make_log(tmp_path, [_SAMPLE_ANSI_LINE])
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/download?n=10")
    assert "\x1b[" not in resp.text


def test_download_when_missing_returns_marker(tmp_path: Path) -> None:
    missing = tmp_path / "no.log"
    with patch.object(lar, "_resolve_log_path", return_value=missing):
        client = _make_client()
        resp = client.get("/api/admin/logs/download")
    assert resp.status_code == 200
    assert "log_file_not_available" in resp.text


def test_download_caps_n() -> None:
    client = _make_client()
    resp = client.get("/api/admin/logs/download?n=99999")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/logs — HTML page
# ---------------------------------------------------------------------------


def test_admin_logs_page_returns_html() -> None:
    client = _make_client()
    resp = client.get("/admin/logs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "Logs Admin" in body
    # JS вызывает /api/admin/logs/tail.
    assert "/api/admin/logs/tail" in body
    # И download endpoint.
    assert "/api/admin/logs/download" in body


def test_admin_logs_page_has_filter_controls() -> None:
    client = _make_client()
    resp = client.get("/admin/logs")
    body = resp.text
    # Sanity: контролы для всех 5 уровней + grep input.
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert level in body
    assert "ctrl-grep" in body
    assert "ctrl-level" in body


# ---------------------------------------------------------------------------
# Edge cases & XSS safety
# ---------------------------------------------------------------------------


def test_tail_handles_lines_without_newline_at_eof(tmp_path: Path) -> None:
    # Файл без trailing newline.
    log = tmp_path / "noeof.log"
    log.write_text("\n".join(_SAMPLE_LINES_PLAIN), encoding="utf-8")  # без \n в конце
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail?n=200&level=INFO")
    assert resp.status_code == 200
    body = resp.json()
    # Последняя строка должна быть распознана.
    levels = {ln["level"] for ln in body["lines"]}
    assert len(body["lines"]) >= 4  # info/warning/error/critical (без debug)
    assert "CRITICAL" in levels


def test_tail_includes_module_field(tmp_path: Path) -> None:
    log = _make_log(tmp_path, _SAMPLE_LINES_PLAIN[:1])
    with patch.object(lar, "_resolve_log_path", return_value=log):
        client = _make_client()
        resp = client.get("/api/admin/logs/tail")
    body = resp.json()
    assert body["lines"][0]["module"] == "src.foo"


def test_html_page_uses_text_content_not_inner_html() -> None:
    # Sanity: JS использует textContent (XSS-safe). Если бы клиентский код
    # передавал e.message прямо в innerHTML — был бы XSS-vulnerability.
    client = _make_client()
    resp = client.get("/admin/logs")
    body = resp.text
    assert "textContent" in body
    # Запрещённый паттерн (динамический инжект log-данных в DOM).
    bad_pattern = "." + "inner" + "HTML = e."
    assert bad_pattern not in body

# -*- coding: utf-8 -*-
"""Тесты для scripts/refresh_claude_md_autotables.py — Wave 29-A.

Покрывает:
  1. grep_handlers() корректно парсит файлы с async def handle_*
  2. fetch_endpoints() обрабатывает сетевую ошибку без исключения
  3. update_claude_md_counts() заменяет счётчики по regex
  4. main(--dry-run) не записывает файлы на диск
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Загружаем модуль напрямую (он не в пакете src/)
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "refresh_claude_md_autotables.py"


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("refresh_claude_md_autotables", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"Не удалось загрузить {SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_mod = _load_script()


# ---------------------------------------------------------------------------
# 1. grep_handlers() — корректный парсинг
# ---------------------------------------------------------------------------


def test_grep_handlers_parses_files(tmp_path: Path) -> None:
    """grep_handlers() должен найти все async def handle_* в переданных файлах."""
    # Патчим ROOT скрипта, чтобы он смотрел во временную директорию
    commands_dir = tmp_path / "src" / "handlers" / "commands"
    commands_dir.mkdir(parents=True)

    # Файл с несколькими обработчиками (и одним не-обработчиком)
    (commands_dir / "test_handlers.py").write_text(
        "async def handle_foo(ctx):\n"
        "    pass\n"
        "\n"
        "async def handle_bar(ctx):\n"
        "    pass\n"
        "\n"
        "def handle_sync(ctx):  # не async — не должна попасть\n"
        "    pass\n"
        "\n"
        "async def not_a_handler(ctx):\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Основной command_handlers.py
    main_handlers = tmp_path / "src" / "handlers" / "command_handlers.py"
    main_handlers.write_text(
        "async def handle_baz(ctx):\n    pass\n",
        encoding="utf-8",
    )

    with patch.object(_mod, "ROOT", tmp_path):
        result = _mod.grep_handlers()

    assert "handle_foo" in result
    assert "handle_bar" in result
    assert "handle_baz" in result
    # Синхронная функция не должна попасть
    assert "handle_sync" not in result
    # Не-handler не должен попасть
    assert "not_a_handler" not in result
    # Список должен быть отсортирован и без дублей
    assert result == sorted(set(result))


def test_grep_handlers_empty_when_no_files(tmp_path: Path) -> None:
    """grep_handlers() возвращает [] если handlers/ не существует."""
    with patch.object(_mod, "ROOT", tmp_path):
        result = _mod.grep_handlers()
    assert result == []


# ---------------------------------------------------------------------------
# 2. fetch_endpoints() — graceful при сетевой ошибке
# ---------------------------------------------------------------------------


def test_fetch_endpoints_handles_network_error(capsys: pytest.CaptureFixture) -> None:
    """fetch_endpoints() возвращает [] и выводит предупреждение при URLError."""
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        result = _mod.fetch_endpoints()

    assert result == []
    captured = capsys.readouterr()
    assert "⚠️" in captured.out or "Не удалось" in captured.out


def test_fetch_endpoints_parses_response() -> None:
    """fetch_endpoints() возвращает список из поля 'endpoints' при корректном ответе."""
    import io

    fake_data = b'{"endpoints": [{"path": "/api/health", "method": "GET"}]}'
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda s: s
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_resp.read.return_value = fake_data

    with patch("urllib.request.urlopen", return_value=fake_resp):
        result = _mod.fetch_endpoints()

    assert len(result) == 1
    assert result[0]["path"] == "/api/health"


# ---------------------------------------------------------------------------
# 3. update_claude_md_counts() — regex замена счётчиков
# ---------------------------------------------------------------------------


def test_update_claude_md_counts_replaces_routes(tmp_path: Path) -> None:
    """update_claude_md_counts() обновляет '(~X routes)' и 'X handle_* функций'."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "### Auto-generated endpoints table (~248 routes; **29 routers**)\n"
        "\n"
        "### Auto-generated handlers (169 handle_* функций)\n"
        "\n"
        "Также (~169 функций) зарегистрировано.\n",
        encoding="utf-8",
    )

    with patch.object(_mod, "CLAUDE_MD", claude_md):
        _mod.update_claude_md_counts(n_endpoints=300, n_handlers=180)

    result = claude_md.read_text(encoding="utf-8")
    # Строка в тесте: "(~248 routes; **29 routers**)" → замена только числа
    assert "~300 routes" in result
    assert "180 handle_* функций" in result
    assert "(~180 функций)" in result
    # Старые значения не должны остаться
    assert "~248" not in result
    assert "169" not in result


def test_update_claude_md_counts_no_change_if_same(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """update_claude_md_counts() не перезаписывает файл если числа уже совпадают."""
    claude_md = tmp_path / "CLAUDE.md"
    original = "### endpoints (~300 routes; routers)\n### handlers (300 handle_* функций)\n"
    claude_md.write_text(original, encoding="utf-8")
    mtime_before = claude_md.stat().st_mtime

    with patch.object(_mod, "CLAUDE_MD", claude_md):
        _mod.update_claude_md_counts(n_endpoints=300, n_handlers=300)

    captured = capsys.readouterr()
    assert "не изменился" in captured.out or "совпадают" in captured.out


# ---------------------------------------------------------------------------
# 4. main(--dry-run) — не записывает файлы
# ---------------------------------------------------------------------------


def test_main_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """main(--dry-run) выводит числа но НЕ изменяет файлы на диске."""
    # Создаём минимальные фиктивные docs/
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    endpoints_doc = docs_dir / "CLAUDE_AUTO_ENDPOINTS.md"
    handlers_doc = docs_dir / "CLAUDE_AUTO_HANDLERS.md"
    prometheus_doc = docs_dir / "CLAUDE_AUTO_PROMETHEUS.md"

    for f in [endpoints_doc, handlers_doc, prometheus_doc]:
        f.write_text("original content\n", encoding="utf-8")

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("(~100 routes)\n", encoding="utf-8")

    with (
        patch.object(_mod, "ROOT", tmp_path),
        patch.object(_mod, "CLAUDE_MD", claude_md),
        patch.object(_mod, "DOCS_DIR", docs_dir),
        patch.object(_mod, "fetch_endpoints", return_value=[{"path": "/api/x", "method": "GET"}]),
        patch.object(_mod, "grep_handlers", return_value=["handle_foo"]),
        patch.object(_mod, "parse_prometheus_rules", return_value=(["AlertA"], ["krab_metric"])),
    ):
        rc = _mod.main(["--dry-run"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "dry-run" in captured.out

    # Файлы не тронуты
    assert endpoints_doc.read_text() == "original content\n"
    assert handlers_doc.read_text() == "original content\n"
    assert prometheus_doc.read_text() == "original content\n"
    assert claude_md.read_text() == "(~100 routes)\n"

# -*- coding: utf-8 -*-
"""Tests для _resolve_hermes_binary() — Wave 19-C.

Покрывает 5 сценариев:
  1. Env override (KRAB_HERMES_BINARY) — валидный executable
  2. PATH fallback через shutil.which
  3. ~/.hermes/bin/hermes fallback (tmp_path симуляция)
  4. Возврат None когда binary нигде нет
  5. Пропуск non-executable файла в env override
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.hermes_acp_bridge import _resolve_hermes_binary

# ---------------------------------------------------------------------------
# 1. env override — валидный executable
# ---------------------------------------------------------------------------


def test_resolve_hermes_binary_env_override(tmp_path: Path) -> None:
    """KRAB_HERMES_BINARY указывает на реальный executable — должен вернуть его."""
    fake_bin = tmp_path / "hermes"
    fake_bin.write_text("#!/bin/bash\necho hermes")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)

    with patch.dict(os.environ, {"KRAB_HERMES_BINARY": str(fake_bin)}):
        result = _resolve_hermes_binary()

    assert result == str(fake_bin)


# ---------------------------------------------------------------------------
# 2. PATH fallback через shutil.which
# ---------------------------------------------------------------------------


def test_resolve_hermes_binary_path_fallback() -> None:
    """Если env не выставлен, используем shutil.which."""
    fake_path = "/usr/local/bin/hermes"

    # Убираем env переменную, мокаем which
    env_without_override = {k: v for k, v in os.environ.items() if k != "KRAB_HERMES_BINARY"}
    with patch.dict(os.environ, env_without_override, clear=True):
        with patch("src.integrations.hermes_acp_bridge.shutil.which", return_value=fake_path):
            # Мокаем Path.home() чтобы исключить ~/.hermes/bin/ случайный хит
            with patch("src.integrations.hermes_acp_bridge.Path") as mock_path_cls:
                # Имитируем что ~/.hermes/bin/hermes НЕ существует
                mock_user_path = mock_path_cls.home.return_value / ".hermes" / "bin" / "hermes"
                mock_user_path.is_file.return_value = False
                result = _resolve_hermes_binary()

    assert result == fake_path


# ---------------------------------------------------------------------------
# 3. ~/.hermes/bin/hermes fallback (tmp_path)
# ---------------------------------------------------------------------------


def test_resolve_hermes_binary_user_dir_fallback(tmp_path: Path) -> None:
    """Если env нет и PATH нет — ищем в ~/.hermes/bin/."""
    # Создаём фейковый executable в tmp_path (симулируем ~/.hermes/bin/hermes)
    hermes_bin = tmp_path / ".hermes" / "bin" / "hermes"
    hermes_bin.parent.mkdir(parents=True)
    hermes_bin.write_text("#!/bin/bash\necho hermes")
    hermes_bin.chmod(hermes_bin.stat().st_mode | stat.S_IEXEC)

    env_without_override = {k: v for k, v in os.environ.items() if k != "KRAB_HERMES_BINARY"}
    with patch.dict(os.environ, env_without_override, clear=True):
        with patch("src.integrations.hermes_acp_bridge.shutil.which", return_value=None):
            with patch("src.integrations.hermes_acp_bridge.Path") as mock_path_cls:
                # Path.home() → tmp_path, поэтому ~/.hermes = tmp_path/.hermes
                mock_path_cls.home.return_value = tmp_path
                # Воссоздаём Path(env_path) поведение для env_path=None ветки
                mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
                mock_path_cls.home.return_value = tmp_path

                result = _resolve_hermes_binary()

    assert result == str(hermes_bin)


# ---------------------------------------------------------------------------
# 4. Возвращает None когда binary нигде нет
# ---------------------------------------------------------------------------


def test_resolve_hermes_binary_returns_none_when_missing() -> None:
    """Если нет ни env, ни PATH, ни ~/.hermes/bin/ — возвращаем None."""
    env_without_override = {k: v for k, v in os.environ.items() if k != "KRAB_HERMES_BINARY"}
    with patch.dict(os.environ, env_without_override, clear=True):
        with patch("src.integrations.hermes_acp_bridge.shutil.which", return_value=None):
            with patch("src.integrations.hermes_acp_bridge.Path") as mock_path_cls:
                mock_user_path = mock_path_cls.home.return_value.__truediv__.return_value
                # Имитируем несуществующий файл
                mock_user_path.__truediv__.return_value.__truediv__.return_value.is_file.return_value = False
                mock_user_path.is_file.return_value = False

                # Проще: мокнуть Path.home() → dir без hermes
                import tempfile

                with tempfile.TemporaryDirectory() as empty_dir:
                    mock_path_cls.home.return_value = Path(empty_dir)
                    mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
                    mock_path_cls.home.return_value = Path(empty_dir)

                    result = _resolve_hermes_binary()

    assert result is None


# ---------------------------------------------------------------------------
# 5. Пропуск non-executable файла в env override
# ---------------------------------------------------------------------------


def test_resolve_hermes_binary_skips_non_executable(tmp_path: Path) -> None:
    """KRAB_HERMES_BINARY указывает на файл без +x — должен пропустить и идти дальше."""
    # Создаём файл БЕЗ прав на выполнение
    fake_bin = tmp_path / "hermes_noexec"
    fake_bin.write_text("#!/bin/bash\necho hermes")
    # Явно убираем execute bit
    fake_bin.chmod(0o644)

    env_without_override = {k: v for k, v in os.environ.items()}
    env_without_override["KRAB_HERMES_BINARY"] = str(fake_bin)

    with patch.dict(os.environ, env_without_override, clear=True):
        with patch("src.integrations.hermes_acp_bridge.shutil.which", return_value=None):
            import tempfile

            with tempfile.TemporaryDirectory() as empty_dir:
                with patch("src.integrations.hermes_acp_bridge.Path") as mock_path_cls:
                    mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
                    mock_path_cls.home.return_value = Path(empty_dir)

                    result = _resolve_hermes_binary()

    # non-executable env path → пропустить → which=None → нет ~/.hermes/bin/ → None
    assert result is None

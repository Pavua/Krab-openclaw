# -*- coding: utf-8 -*-
"""
Юнит-тесты для persistent Chrome profile + stealth_init.js injection.

Покрывают:
- KRAB_CHROME_PROFILE_DIR env переопределяет путь профиля
- Путь раскрывается из ~
- add_init_script вызывается при возврате страницы (mock)
- Если stealth_init.js отсутствует — warn-лог, без краша
- Дефолтный профиль — ~/.openclaw/krab_chrome_profile
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_module(env_overrides: dict[str, str] | None = None):
    """
    Перезагружает browser_bridge с заданными env-переменными и возвращает модуль.

    Необходимо, потому что модульные константы (_PERSISTENT_CHROME_PROFILE_DIR,
    _STEALTH_INIT_JS_PATH) вычисляются один раз при импорте.
    """
    mod_name = "src.integrations.browser_bridge"
    # Удаляем из кеша, чтобы constants пересчитались
    sys.modules.pop(mod_name, None)

    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    with patch.dict(os.environ, env, clear=True):
        mod = importlib.import_module(mod_name)
    return mod


# ---------------------------------------------------------------------------
# Тесты констант модуля
# ---------------------------------------------------------------------------


def test_default_profile_dir_is_openclaw_krab_chrome_profile():
    """Дефолтный профиль должен быть ~/.openclaw/krab_chrome_profile."""
    mod = _reload_module({"KRAB_CHROME_PROFILE_DIR": ""})
    expected = Path.home() / ".openclaw" / "krab_chrome_profile"
    assert mod._PERSISTENT_CHROME_PROFILE_DIR == expected


def test_env_override_changes_profile_dir(tmp_path):
    """KRAB_CHROME_PROFILE_DIR переопределяет путь профиля."""
    custom = str(tmp_path / "my_profile")
    mod = _reload_module({"KRAB_CHROME_PROFILE_DIR": custom})
    assert mod._PERSISTENT_CHROME_PROFILE_DIR == Path(custom)


def test_tilde_in_profile_dir_is_expanded():
    """Путь вида ~/something должен раскрываться в абсолютный."""
    mod = _reload_module({"KRAB_CHROME_PROFILE_DIR": "~/.openclaw/custom_profile"})
    result = mod._PERSISTENT_CHROME_PROFILE_DIR
    # Должен быть абсолютным, не содержать тильду
    assert not str(result).startswith("~")
    assert result.is_absolute()
    assert result == Path.home() / ".openclaw" / "custom_profile"


# ---------------------------------------------------------------------------
# Тесты _inject_stealth_if_available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_init_script_called_when_stealth_js_exists(tmp_path):
    """add_init_script вызывается с путём к файлу, если он существует."""
    stealth_js = tmp_path / "stealth_init.js"
    stealth_js.write_text("// stealth", encoding="utf-8")

    from src.integrations.browser_bridge import BrowserBridge

    bridge = BrowserBridge()

    page = MagicMock()
    page.add_init_script = AsyncMock()

    with patch("src.integrations.browser_bridge._STEALTH_INIT_JS_PATH", stealth_js):
        await bridge._inject_stealth_if_available(page)

    page.add_init_script.assert_awaited_once_with(path=str(stealth_js))


@pytest.mark.asyncio
async def test_no_crash_when_stealth_js_missing(tmp_path, caplog):
    """Если stealth_init.js нет — предупреждение в лог, никакого исключения."""
    import logging

    missing = tmp_path / "nonexistent_stealth_init.js"

    from src.integrations.browser_bridge import BrowserBridge

    bridge = BrowserBridge()
    page = MagicMock()
    page.add_init_script = AsyncMock()

    with patch("src.integrations.browser_bridge._STEALTH_INIT_JS_PATH", missing):
        # Не должно бросить исключение
        await bridge._inject_stealth_if_available(page)

    # add_init_script не должен вызываться
    page.add_init_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_init_script_called_via_active_page(tmp_path):
    """_active_page() вызывает _inject_stealth_if_available на возвращаемой странице."""
    stealth_js = tmp_path / "stealth_init.js"
    stealth_js.write_text("// stealth", encoding="utf-8")

    from src.integrations.browser_bridge import BrowserBridge

    bridge = BrowserBridge()

    # Мокаем page
    mock_page = MagicMock()
    mock_page.add_init_script = AsyncMock()

    # Мокаем browser → context → pages
    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_browser = MagicMock()
    mock_browser.contexts = [mock_ctx]

    with (
        patch.object(bridge, "_get_browser", AsyncMock(return_value=mock_browser)),
        patch("src.integrations.browser_bridge._STEALTH_INIT_JS_PATH", stealth_js),
    ):
        returned_page = await bridge._active_page()

    assert returned_page is mock_page
    mock_page.add_init_script.assert_awaited_once_with(path=str(stealth_js))

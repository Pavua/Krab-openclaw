# -*- coding: utf-8 -*-
"""
Тесты для src/employee_templates.py.

Проверяем: get_role_prompt, list_roles, load_roles, save_role, edge cases.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

# ---------------------------------------------------------------------------
# get_role_prompt
# ---------------------------------------------------------------------------

def test_get_role_prompt_known_role() -> None:
    """get_role_prompt для известной роли возвращает непустую строку."""
    from src.employee_templates import get_role_prompt

    prompt = get_role_prompt("coder")
    assert isinstance(prompt, str)
    assert len(prompt) > 20


def test_get_role_prompt_default_role() -> None:
    """get_role_prompt('default') возвращает дефолтный промпт."""
    from src.employee_templates import get_role_prompt

    prompt = get_role_prompt("default")
    assert isinstance(prompt, str)
    assert len(prompt) > 10


def test_get_role_prompt_unknown_falls_back_to_default() -> None:
    """Несуществующая роль — возвращает промпт 'default', не кидает KeyError."""
    from src.employee_templates import ROLES, get_role_prompt

    default_prompt = ROLES["default"]
    result = get_role_prompt("nonexistent_role_xyz")
    assert result == default_prompt


def test_get_role_prompt_case_insensitive() -> None:
    """Имя роли нечувствительно к регистру."""
    from src.employee_templates import get_role_prompt

    assert get_role_prompt("CODER") == get_role_prompt("coder")
    assert get_role_prompt("Security") == get_role_prompt("security")


def test_get_role_prompt_returns_string_for_all_default_roles() -> None:
    """Каждая дефолтная роль возвращает непустую строку."""
    from src.employee_templates import DEFAULT_ROLES, get_role_prompt

    for role_name in DEFAULT_ROLES:
        result = get_role_prompt(role_name)
        assert isinstance(result, str), f"role={role_name} не вернула str"
        assert result.strip(), f"role={role_name} вернула пустую строку"


# ---------------------------------------------------------------------------
# list_roles
# ---------------------------------------------------------------------------

def test_list_roles_returns_string() -> None:
    """list_roles возвращает строку."""
    from src.employee_templates import list_roles

    result = list_roles()
    assert isinstance(result, str)


def test_list_roles_contains_all_default_keys() -> None:
    """list_roles содержит все имена из DEFAULT_ROLES."""
    from src.employee_templates import DEFAULT_ROLES, list_roles

    result = list_roles()
    for role_name in DEFAULT_ROLES:
        assert role_name in result, f"role={role_name} не найдена в list_roles()"


def test_list_roles_sorted() -> None:
    """Роли перечислены в алфавитном порядке."""
    from src.employee_templates import list_roles

    lines = [line.strip().strip("- `") for line in list_roles().splitlines() if line.strip()]
    # убираем backtick-обёртку: `- \`name\`` -> name
    role_names = [line.strip("`").strip() for line in lines if line]
    assert role_names == sorted(role_names)


# ---------------------------------------------------------------------------
# load_roles
# ---------------------------------------------------------------------------

def test_load_roles_returns_default_when_no_file(tmp_path) -> None:
    """load_roles без кастомного файла возвращает DEFAULT_ROLES."""
    from src.employee_templates import DEFAULT_ROLES

    # Гарантируем, что файл не существует
    fake_path = str(tmp_path / "roles_missing.json")
    with patch("src.employee_templates.ROLES_FILE", fake_path):
        from src.employee_templates import load_roles
        roles = load_roles()

    for key in DEFAULT_ROLES:
        assert key in roles


def test_load_roles_merges_custom_file(tmp_path) -> None:
    """load_roles мёрджит кастомные роли поверх дефолтных."""
    custom = {"custom_agent": "Ты — кастомный агент."}
    roles_file = tmp_path / "roles.json"
    roles_file.write_text(json.dumps(custom), encoding="utf-8")

    with patch("src.employee_templates.ROLES_FILE", str(roles_file)):
        from src.employee_templates import load_roles
        roles = load_roles()

    assert "custom_agent" in roles
    assert roles["custom_agent"] == "Ты — кастомный агент."
    # Дефолтные роли тоже должны быть
    assert "default" in roles


def test_load_roles_survives_corrupt_json(tmp_path) -> None:
    """load_roles при поврежденном JSON файле не падает, возвращает DEFAULT_ROLES."""
    bad_file = tmp_path / "roles.json"
    bad_file.write_text("{{{broken json", encoding="utf-8")

    with patch("src.employee_templates.ROLES_FILE", str(bad_file)):
        from src.employee_templates import load_roles
        roles = load_roles()

    # Должны получить хотя бы дефолтные роли
    assert "default" in roles


# ---------------------------------------------------------------------------
# save_role
# ---------------------------------------------------------------------------

def test_save_role_creates_file_and_persists(tmp_path) -> None:
    """save_role записывает новую роль в JSON и возвращает True."""
    roles_file = str(tmp_path / "roles.json")

    with patch("src.employee_templates.ROLES_FILE", roles_file):
        from src.employee_templates import save_role
        result = save_role("test_role", "Ты тестовый агент.")

    assert result is True
    assert os.path.exists(roles_file)
    with open(roles_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "test_role" in data
    assert data["test_role"] == "Ты тестовый агент."


def test_save_role_normalizes_key_to_lowercase(tmp_path) -> None:
    """save_role приводит имя роли к нижнему регистру."""
    roles_file = str(tmp_path / "roles.json")

    with patch("src.employee_templates.ROLES_FILE", roles_file):
        from src.employee_templates import save_role
        save_role("MyRole", "Промпт.")

    with open(roles_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "myrole" in data
    assert "MyRole" not in data

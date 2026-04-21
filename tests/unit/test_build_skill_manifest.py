# -*- coding: utf-8 -*-
"""
Тесты для scripts/build_skill_manifest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Добавляем scripts/ в путь для импорта
_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "src"))

from build_skill_manifest import (
    collect_capabilities,
    collect_commands,
    collect_skill_modules,
    render_markdown,
)

# ---------------------------------------------------------------------------
# collect_skill_modules
# ---------------------------------------------------------------------------


def test_collect_skill_modules_returns_list():
    result = collect_skill_modules()
    assert isinstance(result, list)
    assert len(result) >= 1


def test_collect_skill_modules_contains_expected():
    result = collect_skill_modules()
    names = {s["name"] for s in result}
    expected = {"crypto", "imessage", "mercadona", "web_search"}
    assert expected.issubset(names), f"Ожидали {expected}, получили {names}"


def test_collect_skill_modules_has_required_keys():
    result = collect_skill_modules()
    for skill in result:
        assert "name" in skill, f"Нет ключа 'name' в {skill}"
        assert "file" in skill, f"Нет ключа 'file' в {skill}"
        assert "summary" in skill, f"Нет ключа 'summary' в {skill}"
        assert skill["file"].startswith("src/skills/"), f"Неверный путь: {skill['file']}"


# ---------------------------------------------------------------------------
# collect_commands
# ---------------------------------------------------------------------------


def test_collect_commands_returns_list():
    result = collect_commands()
    assert isinstance(result, list)
    assert len(result) > 0


def test_collect_commands_stage_field_populated():
    result = collect_commands()
    valid_stages = {"experimental", "beta", "production"}
    for cmd in result:
        assert "stage" in cmd, f"Нет 'stage' в команде {cmd.get('name')}"
        assert cmd["stage"] in valid_stages, (
            f"Невалидный stage={cmd['stage']!r} для {cmd.get('name')}"
        )


def test_collect_commands_has_expected_fields():
    result = collect_commands()
    required = {"name", "category", "description", "usage", "owner_only", "stage"}
    for cmd in result[:5]:  # Проверяем первые 5
        missing = required - cmd.keys()
        assert not missing, f"Отсутствуют поля {missing} в {cmd.get('name')}"


def test_collect_commands_known_commands_present():
    result = collect_commands()
    names = {c["name"] for c in result}
    for expected in ("help", "ask", "search", "stats", "health"):
        assert expected in names, f"Команда !{expected} отсутствует в реестре"


# ---------------------------------------------------------------------------
# collect_capabilities
# ---------------------------------------------------------------------------


def test_collect_capabilities_returns_list():
    result = collect_capabilities()
    assert isinstance(result, list)


def test_collect_capabilities_has_roles():
    result = collect_capabilities()
    assert len(result) >= 1, "Должна быть хотя бы одна роль"
    roles = {r["role"] for r in result}
    # Ожидаем хотя бы owner или full
    assert roles & {"owner", "full"}, f"Не найдены expected роли, есть: {roles}"


def test_collect_capabilities_has_capability_flags():
    result = collect_capabilities()
    for role_data in result:
        assert "role" in role_data
        assert "capabilities" in role_data
        caps = role_data["capabilities"]
        assert isinstance(caps, dict)
        assert len(caps) > 0


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_includes_required_headings():
    skills = [{"name": "test_skill", "file": "src/skills/test_skill.py", "summary": "Test"}]
    commands = [
        {
            "name": "ask",
            "category": "ai",
            "description": "Test desc",
            "usage": "!ask",
            "owner_only": False,
            "aliases": [],
            "stage": "production",
        }
    ]
    capabilities = [
        {
            "role": "owner",
            "capabilities": {"chat": True, "web_search": True},
        }
    ]
    md = render_markdown(skills, commands, capabilities)

    assert "## Skills" in md
    assert "## Commands" in md
    assert "## Capabilities" in md


def test_render_markdown_contains_stage_badge():
    skills: list = []
    commands = [
        {
            "name": "experimental_cmd",
            "category": "dev",
            "description": "Experimental feature",
            "usage": "!experimental_cmd",
            "owner_only": True,
            "aliases": [],
            "stage": "experimental",
        },
        {
            "name": "beta_cmd",
            "category": "dev",
            "description": "Beta feature",
            "usage": "!beta_cmd",
            "owner_only": False,
            "aliases": [],
            "stage": "beta",
        },
        {
            "name": "prod_cmd",
            "category": "dev",
            "description": "Production feature",
            "usage": "!prod_cmd",
            "owner_only": False,
            "aliases": [],
            "stage": "production",
        },
    ]
    capabilities: list = []
    md = render_markdown(skills, commands, capabilities)

    assert "🔴 experimental" in md
    assert "🟡 beta" in md
    assert "🟢 production" in md


def test_render_markdown_contains_regeneration_command():
    md = render_markdown([], [], [])
    assert "venv/bin/python scripts/build_skill_manifest.py" in md


def test_render_markdown_generated_timestamp():
    md = render_markdown([], [], [])
    assert "Generated:" in md
    assert "UTC" in md


# ---------------------------------------------------------------------------
# main() — интеграционный тест записи файла
# ---------------------------------------------------------------------------


def test_main_writes_output_file(tmp_path: Path):
    """main() создаёт файл по указанному --output пути."""
    import importlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_skill_manifest",
        str(_REPO / "scripts" / "build_skill_manifest.py"),
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    out = tmp_path / "TEST_SKILLS.md"
    mod.main.__globals__["__name__"] = "__not_main__"
    # Вызываем main напрямую с патченным sys.argv
    import sys

    old_argv = sys.argv
    try:
        sys.argv = ["build_skill_manifest.py", "--output", str(out)]
        mod.main()
    finally:
        sys.argv = old_argv

    assert out.exists(), f"Файл {out} не был создан"
    content = out.read_text(encoding="utf-8")
    assert "## Skills" in content
    assert "## Commands" in content
    assert len(content) > 500

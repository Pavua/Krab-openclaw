# -*- coding: utf-8 -*-
"""
Юнит-тесты для src/core/command_registry.py.

Покрываем:
  - CommandInfo.to_dict() корректен
  - CommandRegistry.all() — полный список без дублей
  - CommandRegistry.get() — поиск по имени и алиасу, !-префикс, None для неизвестных
  - CommandRegistry.categories() — только присутствующие категории, правильный порядок
  - CommandRegistry.by_category() — фильтрация по категории
  - CommandRegistry.to_api_response() — структура ответа для API
  - Глобальный синглтон registry
  - Конкретные команды присутствуют в реестре
"""

from __future__ import annotations

from src.core.command_registry import _COMMANDS, CommandInfo, CommandRegistry, registry

# ---------------------------------------------------------------------------
# CommandInfo
# ---------------------------------------------------------------------------


class TestCommandInfo:
    def test_to_dict_contains_required_keys(self) -> None:
        cmd = CommandInfo(
            name="test",
            category="basic",
            description="Тест",
            usage="!test",
        )
        d = cmd.to_dict()
        assert "name" in d
        assert "category" in d
        assert "description" in d
        assert "owner_only" in d
        assert "aliases" in d
        assert "usage" in d

    def test_to_dict_values(self) -> None:
        cmd = CommandInfo(
            name="help",
            category="basic",
            description="Справка",
            usage="!help [команда]",
            owner_only=False,
            aliases=["h"],
        )
        d = cmd.to_dict()
        assert d["name"] == "help"
        assert d["category"] == "basic"
        assert d["owner_only"] is False
        assert d["aliases"] == ["h"]
        assert d["usage"] == "!help [команда]"

    def test_to_dict_owner_only_true(self) -> None:
        cmd = CommandInfo(
            name="restart",
            category="dev",
            description="Перезапуск",
            usage="!restart",
            owner_only=True,
        )
        assert cmd.to_dict()["owner_only"] is True

    def test_default_aliases_empty(self) -> None:
        cmd = CommandInfo(name="x", category="basic", description="d", usage="!x")
        assert cmd.to_dict()["aliases"] == []


# ---------------------------------------------------------------------------
# CommandRegistry
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    def test_all_returns_all_commands(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        assert len(reg.all()) == len(_COMMANDS)

    def test_all_no_duplicates(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        names = [c.name for c in reg.all()]
        assert len(names) == len(set(names)), "Дублирующиеся имена команд в реестре"

    def test_get_by_name(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        cmd = reg.get("help")
        assert cmd is not None
        assert cmd.name == "help"

    def test_get_by_name_with_bang_prefix(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        cmd = reg.get("!help")
        assert cmd is not None
        assert cmd.name == "help"

    def test_get_by_alias(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        # bookmark имеет алиас bm
        cmd = reg.get("bm")
        assert cmd is not None
        assert cmd.name == "bookmark"

    def test_get_unknown_returns_none(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        assert reg.get("nonexistent_command_xyz") is None

    def test_categories_only_present(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        present = {cmd.category for cmd in _COMMANDS}
        cats = reg.categories()
        assert set(cats) == present

    def test_categories_ordered(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        cats = reg.categories()
        # Должны идти в определённом порядке (basic первый)
        assert cats[0] == "basic"

    def test_by_category_correct(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        basic_cmds = reg.by_category("basic")
        assert all(c.category == "basic" for c in basic_cmds)
        assert len(basic_cmds) > 0

    def test_by_category_empty_for_unknown(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        assert reg.by_category("nonexistent_category") == []

    def test_to_api_response_structure(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        resp = reg.to_api_response()
        assert resp["ok"] is True
        assert "total" in resp
        assert "commands" in resp
        assert "categories" in resp
        assert resp["total"] == len(reg.all())
        assert isinstance(resp["commands"], list)
        assert isinstance(resp["categories"], list)

    def test_to_api_response_commands_are_dicts(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        resp = reg.to_api_response()
        for item in resp["commands"]:
            assert isinstance(item, dict)
            assert "name" in item
            assert "category" in item

    def test_to_api_response_total_matches_commands_len(self) -> None:
        reg = CommandRegistry(_COMMANDS)
        resp = reg.to_api_response()
        assert resp["total"] == len(resp["commands"])


# ---------------------------------------------------------------------------
# Глобальный синглтон
# ---------------------------------------------------------------------------


class TestGlobalRegistry:
    def test_registry_is_command_registry_instance(self) -> None:
        assert isinstance(registry, CommandRegistry)

    def test_registry_has_commands(self) -> None:
        assert len(registry.all()) > 0

    def test_registry_has_at_least_50_commands(self) -> None:
        # Требование: ~50 команд
        assert len(registry.all()) >= 50

    def test_registry_help_exists(self) -> None:
        assert registry.get("help") is not None

    def test_registry_swarm_exists(self) -> None:
        assert registry.get("swarm") is not None

    def test_registry_translator_exists(self) -> None:
        assert registry.get("translator") is not None

    def test_registry_costs_exists(self) -> None:
        assert registry.get("costs") is not None

    def test_registry_voice_exists(self) -> None:
        assert registry.get("voice") is not None

    def test_registry_search_exists(self) -> None:
        assert registry.get("search") is not None

    def test_registry_remind_exists(self) -> None:
        assert registry.get("remind") is not None

    def test_registry_sysinfo_exists(self) -> None:
        assert registry.get("sysinfo") is not None

    def test_registry_agent_exists(self) -> None:
        assert registry.get("agent") is not None

    def test_registry_model_exists(self) -> None:
        assert registry.get("model") is not None

    def test_registry_budget_exists(self) -> None:
        assert registry.get("budget") is not None

    def test_registry_digest_exists(self) -> None:
        assert registry.get("digest") is not None

    def test_registry_inbox_exists(self) -> None:
        assert registry.get("inbox") is not None

    def test_registry_monitor_exists(self) -> None:
        assert registry.get("monitor") is not None

    def test_registry_has_basic_category(self) -> None:
        assert "basic" in registry.categories()

    def test_registry_has_swarm_category(self) -> None:
        assert "swarm" in registry.categories()

    def test_registry_has_costs_category(self) -> None:
        assert "costs" in registry.categories()

    def test_registry_has_dev_category(self) -> None:
        assert "dev" in registry.categories()

    def test_registry_bookmark_alias_bm(self) -> None:
        cmd = registry.get("bm")
        assert cmd is not None
        assert cmd.name == "bookmark"

    def test_registry_acl_alias_access(self) -> None:
        cmd = registry.get("access")
        assert cmd is not None
        assert cmd.name == "acl"

    def test_registry_search_alias_s(self) -> None:
        cmd = registry.get("s")
        assert cmd is not None
        assert cmd.name == "search"

    def test_registry_help_alias_h(self) -> None:
        cmd = registry.get("h")
        assert cmd is not None
        assert cmd.name == "help"

    def test_owner_only_flag_on_model(self) -> None:
        cmd = registry.get("model")
        assert cmd is not None
        assert cmd.owner_only is True

    def test_owner_only_false_on_help(self) -> None:
        cmd = registry.get("help")
        assert cmd is not None
        assert cmd.owner_only is False

    def test_owner_only_false_on_search(self) -> None:
        cmd = registry.get("search")
        assert cmd is not None
        assert cmd.owner_only is False

    def test_all_commands_have_usage(self) -> None:
        for cmd in registry.all():
            assert cmd.usage, f"Команда '{cmd.name}' не имеет usage"

    def test_all_commands_have_description(self) -> None:
        for cmd in registry.all():
            assert cmd.description, f"Команда '{cmd.name}' не имеет description"

    def test_all_commands_have_category(self) -> None:
        known_cats = set(CommandRegistry.CATEGORY_ORDER)
        for cmd in registry.all():
            assert cmd.category in known_cats, (
                f"Команда '{cmd.name}' имеет неизвестную категорию '{cmd.category}'"
            )

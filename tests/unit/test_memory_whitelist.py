"""
Unit-тесты whitelist-фильтра для Memory Layer.

Покрывают:
  - privacy-by-default (no config → deny all);
  - deny > allow priority;
  - match по id и по title regex;
  - hot-reload по mtime;
  - битый JSON не ломает состояние;
  - `allow_all` при наличии явного deny;
  - round-trip save/load.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.core.memory_whitelist import (
    MemoryWhitelist,
    WhitelistConfig,
    WhitelistDecision,
)


# ---------------------------------------------------------------------------
# Фикстуры.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """Временный путь к конфигу. Файл на диске не создаётся автоматически."""
    return tmp_path / "krab_memory" / "whitelist.json"


def _write_config(path: Path, data: dict) -> None:
    """Утилита — записать dict в JSON-конфиг с созданием директории."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Privacy-by-default.
# ---------------------------------------------------------------------------

class TestPrivacyByDefault:
    def test_no_config_file_denies_all(self, tmp_config_path: Path) -> None:
        wl = MemoryWhitelist(config_path=tmp_config_path)
        decision = wl.is_allowed("123", "Любой чат")
        assert decision.allowed is False
        assert decision.reason == "no_match"

    def test_empty_config_denies_all(self, tmp_config_path: Path) -> None:
        _write_config(tmp_config_path, {})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        decision = wl.is_allowed("123", "Любой чат")
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Allow / deny основная логика.
# ---------------------------------------------------------------------------

class TestAllowDenyLogic:
    def test_allow_by_exact_id(self, tmp_config_path: Path) -> None:
        _write_config(tmp_config_path, {"allow": {"ids": ["-1001234"]}})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        decision = wl.is_allowed("-1001234", "Whatever")
        assert decision.allowed is True
        assert decision.reason == "allow:id:-1001234"

    def test_allow_by_title_regex(self, tmp_config_path: Path) -> None:
        _write_config(
            tmp_config_path,
            {"allow": {"title_regex": [r"Krab Swarm", r"Track [BCD]"]}},
        )
        wl = MemoryWhitelist(config_path=tmp_config_path)

        assert wl.is_allowed("42", "🐝 Krab Swarm").allowed is True
        assert wl.is_allowed("43", "Track B main").allowed is True
        assert wl.is_allowed("44", "Случайный чат").allowed is False

    def test_deny_overrides_allow_id(self, tmp_config_path: Path) -> None:
        _write_config(
            tmp_config_path,
            {
                "allow": {"ids": ["777"]},
                "deny": {"ids": ["777"]},
            },
        )
        wl = MemoryWhitelist(config_path=tmp_config_path)
        decision = wl.is_allowed("777", "Some")
        assert decision.allowed is False
        assert "deny:id:" in decision.reason

    def test_deny_title_regex_overrides_allow_all(
        self, tmp_config_path: Path
    ) -> None:
        _write_config(
            tmp_config_path,
            {
                "allow_all": True,
                "deny": {"title_regex": [r"(?i)family", r"wallet"]},
            },
        )
        wl = MemoryWhitelist(config_path=tmp_config_path)

        assert wl.is_allowed("1", "Family group").allowed is False
        assert wl.is_allowed("2", "My BTC Wallet").allowed is False
        assert wl.is_allowed("3", "How2AI dev").allowed is True

    def test_allow_all_accepts_unknown(self, tmp_config_path: Path) -> None:
        _write_config(tmp_config_path, {"allow_all": True})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        decision = wl.is_allowed("999", "Never heard of it")
        assert decision.allowed is True
        assert decision.reason == "allow_all"

    def test_case_insensitive_title_regex(self, tmp_config_path: Path) -> None:
        _write_config(
            tmp_config_path,
            {"allow": {"title_regex": [r"how2ai"]}},
        )
        wl = MemoryWhitelist(config_path=tmp_config_path)
        assert wl.is_allowed("1", "HOW2AI chat").allowed is True
        assert wl.is_allowed("2", "How2Ai questions").allowed is True


# ---------------------------------------------------------------------------
# Hot-reload.
# ---------------------------------------------------------------------------

class TestHotReload:
    def test_reload_on_mtime_change(self, tmp_config_path: Path) -> None:
        _write_config(tmp_config_path, {"allow": {"ids": ["1"]}})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        assert wl.is_allowed("1").allowed is True
        assert wl.is_allowed("2").allowed is False

        # Меняем конфиг и двигаем mtime чуть вперёд (некоторые FS кладут mtime
        # с точностью до секунд — без явного сдвига тест может быть flaky).
        _write_config(tmp_config_path, {"allow": {"ids": ["1", "2"]}})
        new_mtime = time.time() + 2
        os.utime(tmp_config_path, (new_mtime, new_mtime))

        assert wl.is_allowed("2").allowed is True

    def test_broken_json_keeps_previous_state(
        self, tmp_config_path: Path
    ) -> None:
        _write_config(tmp_config_path, {"allow": {"ids": ["1"]}})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        assert wl.is_allowed("1").allowed is True

        # Записываем битый JSON.
        tmp_config_path.write_text("{ not-json", encoding="utf-8")
        new_mtime = time.time() + 2
        os.utime(tmp_config_path, (new_mtime, new_mtime))

        # Предыдущее состояние должно сохраниться.
        assert wl.is_allowed("1").allowed is True

    def test_config_deleted_reverts_to_deny_all(
        self, tmp_config_path: Path
    ) -> None:
        _write_config(tmp_config_path, {"allow_all": True})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        assert wl.is_allowed("x").allowed is True

        tmp_config_path.unlink()
        # Заставим reload отработать заново (reload проверяет существование).
        decision = wl.is_allowed("x")
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Round-trip save / load.
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_roundtrip(self, tmp_config_path: Path) -> None:
        initial = WhitelistConfig.from_dict(
            {
                "allow_all": False,
                "allow": {
                    "ids": ["-1001234", "42"],
                    "title_regex": [r"Krab"],
                },
                "deny": {"ids": [], "title_regex": [r"family"]},
            }
        )
        wl = MemoryWhitelist(config_path=tmp_config_path, config=initial)
        wl.save()

        # Новый инстанс должен восстановить эквивалентные решения.
        wl2 = MemoryWhitelist(config_path=tmp_config_path)
        assert wl2.is_allowed("42").allowed is True
        assert wl2.is_allowed("unknown").allowed is False
        assert wl2.is_allowed("100", "Family chat").allowed is False
        assert wl2.is_allowed("101", "Krab Swarm").allowed is True


# ---------------------------------------------------------------------------
# Filter batch.
# ---------------------------------------------------------------------------

class TestFilterChats:
    def test_filter_chats_returns_reasons(self, tmp_config_path: Path) -> None:
        _write_config(
            tmp_config_path,
            {
                "allow": {"ids": ["1"], "title_regex": [r"Krab"]},
                "deny": {"title_regex": [r"family"]},
            },
        )
        wl = MemoryWhitelist(config_path=tmp_config_path)

        chats = [
            ("1", "My stuff"),            # allow by id
            ("2", "Krab Swarm"),          # allow by regex
            ("3", "Family"),              # deny by regex (перекрывает regex)
            ("4", "Random"),              # no_match
            ("5", None),                  # no title → только id, no_match
        ]
        results = wl.filter_chats(chats)

        assert len(results) == 5
        decisions = {cid: d for cid, _t, d in results}
        assert decisions["1"].allowed is True
        assert decisions["2"].allowed is True
        assert decisions["3"].allowed is False
        assert decisions["4"].allowed is False
        assert decisions["5"].allowed is False


# ---------------------------------------------------------------------------
# Permissions (chmod 600/700).
# ---------------------------------------------------------------------------

class TestPermissions:
    def test_enforce_permissions(self, tmp_config_path: Path) -> None:
        _write_config(tmp_config_path, {"allow_all": True})
        wl = MemoryWhitelist(config_path=tmp_config_path)
        wl.enforce_permissions()

        file_mode = tmp_config_path.stat().st_mode & 0o777
        dir_mode = tmp_config_path.parent.stat().st_mode & 0o777
        assert file_mode == 0o600, f"expected 600, got {file_mode:o}"
        assert dir_mode == 0o700, f"expected 700, got {dir_mode:o}"


# ---------------------------------------------------------------------------
# WhitelistConfig как датакласс.
# ---------------------------------------------------------------------------

class TestWhitelistConfig:
    def test_from_dict_normalizes_types(self) -> None:
        cfg = WhitelistConfig.from_dict(
            {
                "allow": {"ids": [1, "2", -1003703978531]},
                "deny": {"ids": ["999"]},
            }
        )
        # Все id должны стать строками.
        assert cfg.allow_ids == {"1", "2", "-1003703978531"}
        assert cfg.deny_ids == {"999"}

    def test_decision_dataclass_is_frozen(self) -> None:
        d = WhitelistDecision(True, "allow_all")
        with pytest.raises((AttributeError, Exception)):
            d.allowed = False  # type: ignore[misc]

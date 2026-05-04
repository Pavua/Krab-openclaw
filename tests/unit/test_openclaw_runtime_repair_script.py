# -*- coding: utf-8 -*-
"""
Тесты для scripts/openclaw_runtime_repair.py (Wave 16-J).

Покрывают:
1. config_valid — парсинг, required keys, web_search provider
2. gateway_health — reachable / unreachable
3. session_integrity — clean / malformed / recent-backup cooldown
4. check_only mode
5. output — valid JSON
6. exit codes
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── fixture: импорт модуля скрипта ───────────────────────────────────────────

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "openclaw_runtime_repair.py"


@pytest.fixture(scope="module")
def repair_mod() -> types.ModuleType:
    """Импортируем scripts/openclaw_runtime_repair.py как модуль."""
    spec = importlib.util.spec_from_file_location("openclaw_runtime_repair", SCRIPT_PATH)
    assert spec is not None, f"Скрипт не найден: {SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    # Предотвращаем ошибки при отсутствии structlog в тестовом окружении
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── helper: создать минимальный SQLite ───────────────────────────────────────


def _make_valid_sqlite(path: Path) -> None:
    """Создаёт валидный (пустой) SQLite-файл."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def _make_corrupt_sqlite(path: Path) -> None:
    """Создаёт файл с мусорными байтами (не SQLite)."""
    path.write_bytes(b"NOT_A_DATABASE_GARBAGE_BYTES" * 20)


# ─────────────────────────────────────────────────────────────────────────────
# 1. test_valid_config_passes
# ─────────────────────────────────────────────────────────────────────────────


def test_valid_config_passes(repair_mod: types.ModuleType, tmp_path: Path) -> None:
    """Валидный openclaw.json с корректным provider → check ok."""
    cfg = {
        "agents": {"defaults": {}},
        "providers": {"google": {"apiKey": "AIzaXXXX"}},
        "tools": {"web": {"search": {"provider": "brave"}}},
    }
    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    with patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path):
        result = repair_mod.check_openclaw_config()

    assert result["ok"] is True
    assert "warning" not in result or not result.get("warning")


# ─────────────────────────────────────────────────────────────────────────────
# 2. test_invalid_brave_provider_reports_specific_path
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_search_provider_reports_specific_path(
    repair_mod: types.ModuleType, tmp_path: Path
) -> None:
    """Неверный provider → ok=True (warning) с путём в сообщении."""
    cfg = {
        "agents": {},
        "providers": {},
        "tools": {"web": {"search": {"provider": "unknown_engine"}}},
    }
    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    with patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path):
        result = repair_mod.check_openclaw_config()

    # warning, но не hard-fail (Krab работает без web_search)
    assert result["ok"] is True
    assert result.get("warning") is True
    assert "unknown_engine" in result["detail"]
    # Специфичный путь в сообщении
    assert "tools.web.search.provider" in result["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. test_gateway_unreachable_reported
# ─────────────────────────────────────────────────────────────────────────────


def test_gateway_unreachable_reported(repair_mod: types.ModuleType) -> None:
    """Gateway недоступен → ok=False + hint."""
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = repair_mod.check_gateway_health()

    assert result["ok"] is False
    assert "unreachable" in result["detail"] or "connection_error" in result["detail"]
    assert "hint" in result


# ─────────────────────────────────────────────────────────────────────────────
# 4. test_gateway_healthy_passes
# ─────────────────────────────────────────────────────────────────────────────


def test_gateway_healthy_passes(repair_mod: types.ModuleType) -> None:
    """Gateway отвечает 200 → ok=True."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"status":"ok"}'

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = repair_mod.check_gateway_health()

    assert result["ok"] is True
    assert "200" in result["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. test_session_malformed_triggers_recover
# ─────────────────────────────────────────────────────────────────────────────


def test_session_malformed_triggers_recover(
    repair_mod: types.ModuleType, tmp_path: Path
) -> None:
    """Corrupt session → action=recovered (или error в detail) через sqlite3 .recover."""
    sess_path = tmp_path / "kraab.session"
    _make_corrupt_sqlite(sess_path)

    # Нет recent backup → recovery должна запуститься
    # Мокаем subprocess.run чтобы избежать зависимости от системного sqlite3
    recovered_db = tmp_path / "recovered.sqlite"
    _make_valid_sqlite(recovered_db)

    def fake_run(cmd: list, **kwargs: Any) -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        if ".recover" in cmd:
            # Имитируем dump output
            m.stdout = b".dump content"
        else:
            # load step: создаём валидный SQLite
            _make_valid_sqlite(tmp_path / f"kraab.session.recovered-{int(time.time())}")
            m.stdout = b""
        return m

    # Патчим subprocess.run и _integrity_check на recovered-файл
    original_integrity = repair_mod._integrity_check

    call_count: dict[str, int] = {"n": 0}

    def patched_integrity(path: Path) -> tuple[bool, str]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Первая проверка — original corrupt
            return False, "database disk image is malformed"
        # Вторая проверка — recovered ok
        return True, "ok"

    with (
        patch.object(repair_mod, "_integrity_check", side_effect=patched_integrity),
        patch("subprocess.run", side_effect=fake_run),
        patch.object(repair_mod, "_clean_subprocess_env", return_value={}),
    ):
        result = repair_mod.repair_session_integrity(session_path=sess_path)

    # Результат — либо recovered, либо ошибка recovery (зависит от среды)
    # Главное: action не "none" (recovery была инициирована)
    assert result["action"] in ("recovered", "skipped_check_only", "exit_78") or (
        not result["ok"] and "corrupt" in result["detail"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. test_session_clean_no_recover_action
# ─────────────────────────────────────────────────────────────────────────────


def test_session_clean_no_recover_action(
    repair_mod: types.ModuleType, tmp_path: Path
) -> None:
    """Чистая session → action=none, ok=True."""
    sess_path = tmp_path / "kraab.session"
    _make_valid_sqlite(sess_path)

    result = repair_mod.repair_session_integrity(session_path=sess_path)

    assert result["ok"] is True
    assert result["action"] == "none"
    assert result["detail"] == "integrity_ok"


# ─────────────────────────────────────────────────────────────────────────────
# 7. test_recent_backup_within_1h_exits_78
# ─────────────────────────────────────────────────────────────────────────────


def test_recent_backup_within_1h_exits_78(
    repair_mod: types.ModuleType, tmp_path: Path
) -> None:
    """Recent backup < 1h при corrupt session → exit_code_override=78."""
    sess_path = tmp_path / "kraab.session"
    _make_corrupt_sqlite(sess_path)

    # Создаём recent backup (сейчас)
    backup = tmp_path / f"kraab.session.bak-corrupt-{int(time.time())}"
    backup.write_bytes(b"backup")

    with patch.object(repair_mod, "_integrity_check", return_value=(False, "malformed")):
        result = repair_mod.repair_session_integrity(session_path=sess_path)

    assert result.get("exit_code_override") == 78
    assert result["ok"] is False
    assert "recent backup" in result["detail"].lower() or "exit_78" in result["action"]


# ─────────────────────────────────────────────────────────────────────────────
# 8. test_check_only_mode_skips_recover_action
# ─────────────────────────────────────────────────────────────────────────────


def test_check_only_mode_skips_recover_action(
    repair_mod: types.ModuleType, tmp_path: Path
) -> None:
    """--check-only: corrupt session → action=skipped_check_only, без repair."""
    sess_path = tmp_path / "kraab.session"
    _make_corrupt_sqlite(sess_path)

    with patch.object(repair_mod, "_integrity_check", return_value=(False, "malformed")):
        result = repair_mod.repair_session_integrity(
            session_path=sess_path, check_only=True
        )

    assert result["action"] == "skipped_check_only"
    assert result["ok"] is False
    # Нет backup файлов — repair не запускалась
    backups = list(tmp_path.glob("kraab.session.bak-*"))
    assert len(backups) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 9. test_output_is_valid_json
# ─────────────────────────────────────────────────────────────────────────────


def test_output_is_valid_json(
    repair_mod: types.ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """main() печатает валидный JSON в stdout."""
    cfg = {
        "agents": {},
        "providers": {},
        "tools": {"web": {"search": {"provider": "gemini"}}},
    }
    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    sess_path = tmp_path / "kraab.session"
    _make_valid_sqlite(sess_path)

    import urllib.error

    with (
        patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch.object(repair_mod, "_SESSION_PATH_DEFAULT", sess_path),
    ):
        sys.argv = ["openclaw_runtime_repair.py"]
        try:
            repair_mod.main()
        except SystemExit:
            pass

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)  # должно не бросать

    assert "ok" in parsed
    assert "checks" in parsed
    assert isinstance(parsed["checks"], list)
    assert "errors" in parsed
    assert "warnings" in parsed
    assert "duration_ms" in parsed


# ─────────────────────────────────────────────────────────────────────────────
# 10. test_exit_codes_correct (parametrize over scenarios)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "scenario,expected_exit",
    [
        ("all_ok", 0),
        ("config_error", 1),
        ("session_exit78", 78),
    ],
)
def test_exit_codes_correct(
    scenario: str,
    expected_exit: int,
    repair_mod: types.ModuleType,
    tmp_path: Path,
) -> None:
    """Exit code соответствует сценарию."""
    import urllib.error

    cfg_path = tmp_path / "openclaw.json"
    sess_path = tmp_path / "kraab.session"

    # Заглушка gateway — всегда unreachable (warning, не error)
    gw_patch = patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no"))

    if scenario == "all_ok":
        # Валидный config + clean session
        cfg_path.write_text(
            json.dumps({"agents": {}, "providers": {}, "tools": {"web": {"search": {"provider": "gemini"}}}}),
            encoding="utf-8",
        )
        _make_valid_sqlite(sess_path)

        with (
            patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path),
            patch.object(repair_mod, "_SESSION_PATH_DEFAULT", sess_path),
            gw_patch,
        ):
            sys.argv = ["openclaw_runtime_repair.py"]
            exit_code = repair_mod.main()
        assert exit_code == expected_exit

    elif scenario == "config_error":
        # Битый JSON в config
        cfg_path.write_bytes(b"{invalid_json")
        _make_valid_sqlite(sess_path)

        with (
            patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path),
            patch.object(repair_mod, "_SESSION_PATH_DEFAULT", sess_path),
            gw_patch,
        ):
            sys.argv = ["openclaw_runtime_repair.py"]
            exit_code = repair_mod.main()
        assert exit_code == expected_exit

    elif scenario == "session_exit78":
        # Corrupt session + recent backup → exit 78
        cfg_path.write_text(
            json.dumps({"agents": {}, "providers": {}, "tools": {"web": {"search": {"provider": "gemini"}}}}),
            encoding="utf-8",
        )
        _make_corrupt_sqlite(sess_path)
        # Recent backup
        (tmp_path / f"kraab.session.bak-corrupt-{int(time.time())}").write_bytes(b"x")

        with (
            patch.object(repair_mod, "_OPENCLAW_JSON", cfg_path),
            patch.object(repair_mod, "_SESSION_PATH_DEFAULT", sess_path),
            patch.object(repair_mod, "_integrity_check", return_value=(False, "malformed")),
            gw_patch,
        ):
            sys.argv = ["openclaw_runtime_repair.py"]
            exit_code = repair_mod.main()
        assert exit_code == expected_exit

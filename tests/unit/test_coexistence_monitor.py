"""Wave 25-B: тесты для krab_ear_coexistence_monitor.py.

6 тестов:
1. find_pids() → [] если нет совпадений
2. find_pids() → находит PID по паттерну
3. get_rss_bytes() агрегирует корректно
4. main() → пустой alerts list (все показатели в норме)
5. main() → combined > 12 GB → alert generated
6. main() → записывает JSONL строку
"""
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --------------------------------------------------------------------------- #
# Вспомогательный импорт скрипта без __main__ guard
# --------------------------------------------------------------------------- #

def _import_monitor():
    """Импортировать модуль monitor из scripts/."""
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    # Перезагружаем если уже был импорт (изоляция между тестами)
    if "krab_ear_coexistence_monitor" in sys.modules:
        del sys.modules["krab_ear_coexistence_monitor"]
    return importlib.import_module("krab_ear_coexistence_monitor")


monitor_mod = _import_monitor()
find_pids = monitor_mod.find_pids
get_rss_bytes = monitor_mod.get_rss_bytes


# --------------------------------------------------------------------------- #
# Фиктивные psutil-объекты
# --------------------------------------------------------------------------- #

def _make_proc(pid: int, cmdline: list[str]):
    p = MagicMock()
    p.info = {"pid": pid, "name": cmdline[0] if cmdline else "", "cmdline": cmdline}
    return p


# --------------------------------------------------------------------------- #
# Тест 1: find_pids возвращает [] при отсутствии совпадений
# --------------------------------------------------------------------------- #

def test_find_pids_empty():
    procs = [
        _make_proc(100, ["python", "some_other_script.py"]),
        _make_proc(200, ["/usr/bin/bash"]),
    ]
    with patch("psutil.process_iter", return_value=procs):
        result = find_pids(["userbot_bridge", "src/main.py"])
    assert result == []


# --------------------------------------------------------------------------- #
# Тест 2: find_pids находит PID по паттерну в cmdline
# --------------------------------------------------------------------------- #

def test_find_pids_finds_matching():
    procs = [
        _make_proc(42, ["python", "/opt/krab/src/main.py"]),
        _make_proc(99, ["python", "unrelated.py"]),
        _make_proc(77, ["python", "userbot_bridge.py", "--config", "krab.conf"]),
    ]
    with patch("psutil.process_iter", return_value=procs):
        result = find_pids(["userbot_bridge", "src/main.py"])
    assert set(result) == {42, 77}


# --------------------------------------------------------------------------- #
# Тест 3: get_rss_bytes агрегирует RSS нескольких процессов
# --------------------------------------------------------------------------- #

def test_get_rss_bytes_aggregates():
    def _mock_process(pid):
        proc = MagicMock()
        # 2 GB для PID 10, 1.5 GB для PID 20
        rss_map = {10: 2_000_000_000, 20: 1_500_000_000}
        proc.memory_info.return_value.rss = rss_map[pid]
        return proc

    with patch("psutil.Process", side_effect=_mock_process):
        total = get_rss_bytes([10, 20])
    assert total == 3_500_000_000


# --------------------------------------------------------------------------- #
# Тест 4: main() — все в норме → alerts пустой
# --------------------------------------------------------------------------- #

def test_main_no_alerts(tmp_path):
    mod = _import_monitor()
    log_file = tmp_path / "coexistence_monitor.log"

    # Нормальные значения: 4 GB combined, swap 1 GB, 10 GB free
    _vm = MagicMock()
    _vm.used = 20_000_000_000
    _vm.available = 10_000_000_000

    _sw = MagicMock()
    _sw.used = 1_000_000_000

    def _mock_proc_factory(pid):
        p = MagicMock()
        p.memory_info.return_value.rss = 2_000_000_000  # 2 GB на процесс
        return p

    with (
        patch.object(mod, "LOG_FILE", log_file),
        patch.object(mod, "find_pids", side_effect=lambda patterns: [1] if "userbot" in patterns[0] else [2]),
        patch("psutil.Process", side_effect=_mock_proc_factory),
        patch("psutil.virtual_memory", return_value=_vm),
        patch("psutil.swap_memory", return_value=_sw),
        patch.object(mod, "_send_notify") as mock_notify,
    ):
        rc = mod.main()

    assert rc == 0
    assert log_file.exists()
    line = json.loads(log_file.read_text().strip())
    assert line["alerts"] == []
    mock_notify.assert_not_called()


# --------------------------------------------------------------------------- #
# Тест 5: main() — combined RSS > 12 GB → alert generated + notify вызван
# --------------------------------------------------------------------------- #

def test_main_combined_rss_alert(tmp_path):
    mod = _import_monitor()
    log_file = tmp_path / "coexistence_monitor.log"

    _vm = MagicMock()
    _vm.used = 20_000_000_000
    _vm.available = 10_000_000_000

    _sw = MagicMock()
    _sw.used = 1_000_000_000

    def _big_rss(pid):
        p = MagicMock()
        p.memory_info.return_value.rss = 7_000_000_000  # 7 GB × 2 = 14 GB combined
        return p

    with (
        patch.object(mod, "LOG_FILE", log_file),
        patch.object(mod, "find_pids", side_effect=lambda patterns: [1] if "userbot" in patterns[0] else [2]),
        patch("psutil.Process", side_effect=_big_rss),
        patch("psutil.virtual_memory", return_value=_vm),
        patch("psutil.swap_memory", return_value=_sw),
        patch.object(mod, "_send_notify") as mock_notify,
    ):
        rc = mod.main()

    assert rc == 0
    line = json.loads(log_file.read_text().strip())
    assert any("combined_rss_high" in a for a in line["alerts"])
    mock_notify.assert_called_once()
    call_text = mock_notify.call_args[0][0]
    assert "combined_rss_high" in call_text


# --------------------------------------------------------------------------- #
# Тест 6: main() записывает корректную JSONL строку
# --------------------------------------------------------------------------- #

def test_main_writes_jsonl(tmp_path):
    mod = _import_monitor()
    log_file = tmp_path / "coexistence_monitor.log"

    _vm = MagicMock()
    _vm.used = 8_000_000_000
    _vm.available = 20_000_000_000

    _sw = MagicMock()
    _sw.used = 500_000_000

    def _proc_rss(pid):
        p = MagicMock()
        p.memory_info.return_value.rss = 1_000_000_000
        return p

    with (
        patch.object(mod, "LOG_FILE", log_file),
        patch.object(mod, "find_pids", side_effect=lambda patterns: [10] if "userbot" in patterns[0] else [20]),
        patch("psutil.Process", side_effect=_proc_rss),
        patch("psutil.virtual_memory", return_value=_vm),
        patch("psutil.swap_memory", return_value=_sw),
        patch.object(mod, "_send_notify"),
    ):
        mod.main()
        # Второй запуск — проверим append (должно быть 2 строки)
        mod.main()

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2

    snapshot = json.loads(lines[0])
    # Обязательные ключи
    for key in ("timestamp", "krab_pids", "ear_pids", "krab_rss_gb", "ear_rss_gb",
                 "combined_rss_gb", "system_ram_used_gb", "system_ram_available_gb",
                 "swap_used_gb", "alerts"):
        assert key in snapshot, f"Missing key: {key}"

    assert snapshot["krab_rss_gb"] == 1.0
    assert snapshot["ear_rss_gb"] == 1.0
    assert snapshot["combined_rss_gb"] == 2.0

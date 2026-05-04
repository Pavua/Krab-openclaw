"""Wave 19-B: тесты для memory_baseline_collector.py.

6 тестов: сбор snapshot, фильтрация Krab-процессов,
атомарная запись JSONL, ротация при >50MB,
обнаружение роста памяти, обработка пустого/отсутствующего файла.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Добавляем scripts/ в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from memory_baseline_collector import (
    DEFAULT_TOP_N,
    GROWTH_THRESHOLD,
    ROTATION_SIZE_BYTES,
    _is_krab_relevant,
    analyze,
    append_jsonl,
    collect_snapshot,
)

# ──────────────────────────────────────────────────────────────
# 1. test_collect_snapshot_returns_required_fields
# ──────────────────────────────────────────────────────────────


def test_collect_snapshot_returns_required_fields():
    """Snapshot должен содержать все обязательные поля."""
    snap = collect_snapshot(top_n=5)

    # Верхний уровень
    assert "ts" in snap, "Нет поля ts"
    assert "system" in snap, "Нет поля system"
    assert "processes" in snap, "Нет поля processes"
    assert "krab_procs" in snap, "Нет поля krab_procs"

    # Системные метрики
    sys_data = snap["system"]
    for field in (
        "total_mb",
        "available_mb",
        "used_mb",
        "percent",
        "swap_total_mb",
        "swap_used_mb",
        "swap_percent",
    ):
        assert field in sys_data, f"Нет системного поля: {field}"

    # Метрики процессов
    for proc in snap["processes"]:
        for field in ("pid", "name", "cmd", "rss_mb", "vms_mb", "cpu_pct"):
            assert field in proc, f"Нет поля процесса: {field}"

    # top_n ограничение
    assert len(snap["processes"]) <= 5


def test_collect_snapshot_sorted_by_rss_desc():
    """Процессы должны быть отсортированы по RSS по убыванию."""
    snap = collect_snapshot(top_n=10)
    procs = snap["processes"]
    if len(procs) > 1:
        rss_values = [p["rss_mb"] for p in procs]
        assert rss_values == sorted(rss_values, reverse=True), (
            "Процессы не отсортированы по RSS desc"
        )


# ──────────────────────────────────────────────────────────────
# 2. test_filter_krab_relevant_processes
# ──────────────────────────────────────────────────────────────


def test_filter_krab_relevant_processes():
    """_is_krab_relevant должен корректно фильтровать по имени."""
    # Должны попасть
    assert _is_krab_relevant("python3")
    assert _is_krab_relevant("Python")
    assert _is_krab_relevant("krab")
    assert _is_krab_relevant("Krab_Worker")
    assert _is_krab_relevant("openclaw")
    assert _is_krab_relevant("claude")
    assert _is_krab_relevant("Claude")
    assert _is_krab_relevant("node")
    assert _is_krab_relevant("uvicorn")
    assert _is_krab_relevant("pyrogram")

    # Не должны попасть
    assert not _is_krab_relevant("Finder")
    assert not _is_krab_relevant("Safari")
    assert not _is_krab_relevant("bash")
    assert not _is_krab_relevant("zsh")
    assert not _is_krab_relevant("launchd")
    assert not _is_krab_relevant("")


# ──────────────────────────────────────────────────────────────
# 3. test_jsonl_append_atomic
# ──────────────────────────────────────────────────────────────


def test_jsonl_append_atomic(tmp_path):
    """append_jsonl должен атомарно записывать через tmpfile+rename."""
    output = tmp_path / "test.jsonl"

    snap1 = {
        "ts": "2026-05-04T10:00:00+00:00",
        "system": {"used_mb": 8000},
        "processes": [],
        "krab_procs": [],
    }
    snap2 = {
        "ts": "2026-05-04T10:01:00+00:00",
        "system": {"used_mb": 8100},
        "processes": [],
        "krab_procs": [],
    }

    append_jsonl(output, snap1)
    append_jsonl(output, snap2)

    assert output.exists()
    lines = output.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2, f"Ожидалось 2 строки, получено {len(lines)}"

    loaded1 = json.loads(lines[0])
    loaded2 = json.loads(lines[1])
    assert loaded1["ts"] == snap1["ts"]
    assert loaded2["ts"] == snap2["ts"]
    assert loaded1["system"]["used_mb"] == 8000
    assert loaded2["system"]["used_mb"] == 8100


def test_jsonl_append_creates_parent_dirs(tmp_path):
    """append_jsonl должен создавать родительские директории."""
    output = tmp_path / "nested" / "deep" / "test.jsonl"
    snap = {"ts": "2026-05-04T10:00:00+00:00", "x": 1}
    append_jsonl(output, snap)
    assert output.exists()


# ──────────────────────────────────────────────────────────────
# 4. test_jsonl_rotation_when_over_50mb
# ──────────────────────────────────────────────────────────────


def test_jsonl_rotation_when_over_50mb(tmp_path):
    """_rotate_if_needed должен переименовывать файл при превышении порога.

    Тест напрямую вызывает _rotate_if_needed с патченным ROTATION_SIZE_BYTES.
    """
    import scripts.memory_baseline_collector as mbc

    output = tmp_path / "big.jsonl"
    # Создаём файл с реальным контентом (~140 байт)
    output.write_bytes(b"existing data line\n" * 10)

    assert output.stat().st_size > 0, "Файл должен быть непустым"

    # Сохраняем оригинальный лимит и устанавливаем крошечный порог (1 байт)
    original_limit = mbc.ROTATION_SIZE_BYTES
    mbc.ROTATION_SIZE_BYTES = 1  # Любой непустой файл будет ротирован
    try:
        mbc._rotate_if_needed(output)
    finally:
        mbc.ROTATION_SIZE_BYTES = original_limit

    # После ротации: big.jsonl.1 должен существовать, big.jsonl — нет (переименован)
    rotated = tmp_path / "big.jsonl.1"
    assert rotated.exists(), (
        f"Файл ротации big.jsonl.1 не создан. Files: {list(tmp_path.iterdir())}"
    )
    # Исходный файл должен быть переименован (больше не существует)
    assert not output.exists(), "Оригинальный файл должен быть переименован после ротации"


# ──────────────────────────────────────────────────────────────
# 5. test_analyze_detects_growth
# ──────────────────────────────────────────────────────────────


def test_analyze_detects_growth(tmp_path):
    """analyze должен обнаруживать рост RSS >2x."""
    output = tmp_path / "baseline.jsonl"

    # Генерируем историю: процесс растёт с 100MB → 350MB (3.5x)
    snapshots = []
    rss_values = [100.0, 150.0, 200.0, 280.0, 350.0]
    for i, rss in enumerate(rss_values):
        snap = {
            "ts": f"2026-05-04T10:0{i}:00+00:00",
            "system": {
                "total_mb": 32768.0,
                "available_mb": 20000.0,
                "used_mb": 12000.0 + i * 100,
                "percent": 40.0,
                "swap_total_mb": 8192.0,
                "swap_used_mb": 100.0,
                "swap_percent": 1.0,
            },
            "processes": [
                {
                    "pid": 12345,
                    "name": "krab_ear",
                    "cmd": "python krab_ear_server.py",
                    "rss_mb": rss,
                    "vms_mb": rss * 2,
                    "cpu_pct": 5.0,
                },
                {
                    "pid": 99999,
                    "name": "stable_proc",
                    "cmd": "stable",
                    "rss_mb": 50.0,  # Стабильный процесс
                    "vms_mb": 100.0,
                    "cpu_pct": 0.1,
                },
            ],
            "krab_procs": [],
        }
        snapshots.append(snap)

    with open(output, "w", encoding="utf-8") as f:
        for snap in snapshots:
            f.write(json.dumps(snap) + "\n")

    result = analyze(output)

    assert result["snapshots_count"] == 5
    assert result["memory_growth_detected"] is True, "Должна быть обнаружена утечка"
    assert len(result["leaked_processes"]) > 0

    # krab_ear должен быть в списке утечек
    leaked_names = [p.split(":")[0] for p in result["leaked_processes"]]
    assert "krab_ear" in leaked_names, (
        f"krab_ear не в leaked_processes: {result['leaked_processes']}"
    )

    # stable_proc НЕ должен быть утечкой (1x рост)
    assert "stable_proc" not in leaked_names, "stable_proc ошибочно помечен как утечка"

    # Топ-5 growers должен содержать krab_ear
    assert result["top_growers"], "top_growers пустой"
    top_proc_keys = [g["process"] for g in result["top_growers"]]
    assert any("krab_ear" in k for k in top_proc_keys)

    # Системный тренд
    trend = result["system_trend"]
    assert trend["first_ts"] is not None
    assert trend["last_ts"] is not None


# ──────────────────────────────────────────────────────────────
# 6. test_analyze_handles_empty_or_missing_file
# ──────────────────────────────────────────────────────────────


def test_analyze_handles_missing_file(tmp_path):
    """analyze не должен падать если файл отсутствует."""
    missing = tmp_path / "nonexistent.jsonl"
    result = analyze(missing)

    assert result["snapshots_count"] == 0
    assert result["memory_growth_detected"] is False
    assert "error" in result


def test_analyze_handles_empty_file(tmp_path):
    """analyze не должен падать при пустом файле."""
    output = tmp_path / "empty.jsonl"
    output.write_text("", encoding="utf-8")

    result = analyze(output)

    assert result["snapshots_count"] == 0
    assert result["memory_growth_detected"] is False
    assert "error" in result


def test_analyze_skips_corrupted_lines(tmp_path):
    """analyze должен пропускать повреждённые JSON-строки."""
    output = tmp_path / "partial.jsonl"
    valid_snap = {
        "ts": "2026-05-04T10:00:00+00:00",
        "system": {
            "total_mb": 32768.0,
            "available_mb": 20000.0,
            "used_mb": 12000.0,
            "percent": 36.6,
            "swap_total_mb": 8192.0,
            "swap_used_mb": 0.0,
            "swap_percent": 0.0,
        },
        "processes": [],
        "krab_procs": [],
    }
    with open(output, "w", encoding="utf-8") as f:
        f.write(json.dumps(valid_snap) + "\n")
        f.write("CORRUPTED LINE NOT JSON\n")
        f.write(json.dumps(valid_snap) + "\n")

    result = analyze(output)
    # 2 валидные строки должны быть прочитаны
    assert result["snapshots_count"] == 2
    assert "error" not in result

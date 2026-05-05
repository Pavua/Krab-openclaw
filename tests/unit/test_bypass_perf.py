"""Wave 31-A: тесты для bypass latency profiler.

Покрывает:
1. record_bypass_call() пишет JSONL строку корректно
2. record_bypass_call() graceful если PERF_LOG не writable
3. aggregate_perf() при пустом файле → нули
4. aggregate_perf() фильтрует по window_sec
5. aggregate_perf() p50/p95/p99 корректность
6. aggregate_perf() группировка by_kind
7. aggregate_perf() группировка by_model
8. fail_rate вычислен корректно
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations._bypass_perf import (
    aggregate_perf,
    parse_duration,
    record_bypass_call,
)

# ---------------------------------------------------------------------------
# 1. record_bypass_call() пишет JSONL строку корректно
# ---------------------------------------------------------------------------


def test_record_bypass_call_writes_jsonl(tmp_path: Path) -> None:
    """Запись создаёт файл и добавляет валидный JSON-объект."""
    log_file = tmp_path / "bypass_perf.jsonl"

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        record_bypass_call(
            kind="cli",
            model="codex-cli/gpt-5.5",
            duration_sec=1.234,
            success=True,
            response_len=500,
        )

    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["kind"] == "cli"
    assert record["model"] == "codex-cli/gpt-5.5"
    assert record["duration_sec"] == 1.234
    assert record["success"] is True
    assert record["response_len"] == 500
    assert "ts" in record
    assert isinstance(record["ts"], float)


# ---------------------------------------------------------------------------
# 2. record_bypass_call() graceful если PERF_LOG не writable
# ---------------------------------------------------------------------------


def test_record_bypass_call_graceful_on_error() -> None:
    """Ошибка записи НЕ выбрасывает исключение — graceful swallow."""
    # Направляем в /dev/full-аналог: указываем несуществующую директорию,
    # чтобы mkdir не смог создать (patch mkdir на exception).
    with patch("src.integrations._bypass_perf.PERF_LOG", Path("/nonexistent/path/bypass.jsonl")):
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("no permission")):
            # Не должно кинуть исключение
            record_bypass_call(
                kind="vertex",
                model="google-vertex/gemini-2.5-pro",
                duration_sec=2.5,
                success=False,
                error_type="ConnectionError",
            )


# ---------------------------------------------------------------------------
# 3. aggregate_perf() при пустом файле → нули
# ---------------------------------------------------------------------------


def test_aggregate_perf_empty_file(tmp_path: Path) -> None:
    """Пустой JSONL → все нули, пустые by_kind/by_model."""
    log_file = tmp_path / "bypass_perf.jsonl"
    log_file.write_text("")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    assert result["total_calls"] == 0
    assert result["total_failures"] == 0
    assert result["by_kind"] == {}
    assert result["by_model"] == {}


def test_aggregate_perf_nonexistent_file(tmp_path: Path) -> None:
    """Несуществующий файл → нули без исключения."""
    log_file = tmp_path / "missing_bypass_perf.jsonl"

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    assert result["total_calls"] == 0
    assert result["by_kind"] == {}


# ---------------------------------------------------------------------------
# 4. aggregate_perf() фильтрует по window_sec
# ---------------------------------------------------------------------------


def test_aggregate_perf_filters_by_window(tmp_path: Path) -> None:
    """Записи старше window_sec не учитываются."""
    log_file = tmp_path / "bypass_perf.jsonl"
    now = time.time()

    records = [
        # Свежая запись — попадает в окно
        {"ts": now - 100, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": 1.0, "success": True, "response_len": 100},
        # Старая запись — вне окна
        {"ts": now - 7200, "kind": "vertex", "model": "google-vertex/gemini-2.5-pro",
         "duration_sec": 2.0, "success": True, "response_len": 200},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)  # 1 час

    # Только 1 свежая запись
    assert result["total_calls"] == 1
    assert "cli" in result["by_kind"]
    assert "vertex" not in result["by_kind"]


# ---------------------------------------------------------------------------
# 5. aggregate_perf() p50/p95/p99 корректность
# ---------------------------------------------------------------------------


def test_aggregate_perf_percentiles(tmp_path: Path) -> None:
    """p50/p95/p99 вычислены корректно для известного распределения."""
    log_file = tmp_path / "bypass_perf.jsonl"
    now = time.time()

    # 10 записей: durations 1..10
    records = [
        {"ts": now - i, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": float(i + 1), "success": True, "response_len": 10}
        for i in range(10)
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    cli_stats = result["by_kind"]["cli"]
    assert cli_stats["count"] == 10
    # p50 = значение на 50% индексе sorted([1,2,...,10]) = idx 5 = 6
    assert cli_stats["p50"] == 6.0
    # p95 = idx 9 = 10
    assert cli_stats["p95"] == 10.0
    # p99 = idx 9 = 10
    assert cli_stats["p99"] == 10.0
    # mean = sum(1..10)/10 = 5.5
    assert cli_stats["mean"] == 5.5


# ---------------------------------------------------------------------------
# 6. aggregate_perf() группировка by_kind
# ---------------------------------------------------------------------------


def test_aggregate_perf_by_kind_aggregation(tmp_path: Path) -> None:
    """Записи разных kind агрегируются раздельно."""
    log_file = tmp_path / "bypass_perf.jsonl"
    now = time.time()

    records = [
        {"ts": now - 1, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": 1.0, "success": True, "response_len": 100},
        {"ts": now - 2, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": 3.0, "success": True, "response_len": 200},
        {"ts": now - 3, "kind": "vertex", "model": "google-vertex/gemini-2.5-pro",
         "duration_sec": 5.0, "success": False, "response_len": 0, "error_type": "APIError"},
        {"ts": now - 4, "kind": "google-direct", "model": "google/gemini-3-pro-preview",
         "duration_sec": 2.0, "success": True, "response_len": 300},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    by_kind = result["by_kind"]
    assert set(by_kind.keys()) == {"cli", "vertex", "google-direct"}
    assert by_kind["cli"]["count"] == 2
    assert by_kind["vertex"]["count"] == 1
    assert by_kind["google-direct"]["count"] == 1
    # Vertex — 1 fail из 1 total → fail_rate 1.0
    assert by_kind["vertex"]["fail_rate"] == 1.0
    # CLI — 0 fails из 2 → fail_rate 0.0
    assert by_kind["cli"]["fail_rate"] == 0.0


# ---------------------------------------------------------------------------
# 7. aggregate_perf() группировка by_model
# ---------------------------------------------------------------------------


def test_aggregate_perf_by_model_aggregation(tmp_path: Path) -> None:
    """Записи одной модели агрегируются вместе, разных — раздельно."""
    log_file = tmp_path / "bypass_perf.jsonl"
    now = time.time()

    records = [
        {"ts": now - 1, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": 1.0, "success": True, "response_len": 50},
        {"ts": now - 2, "kind": "cli", "model": "codex-cli/gpt-5.5",
         "duration_sec": 2.0, "success": True, "response_len": 50},
        {"ts": now - 3, "kind": "anthropic-vertex", "model": "anthropic-vertex/claude-opus-4-7",
         "duration_sec": 4.0, "success": True, "response_len": 150},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    by_model = result["by_model"]
    assert "codex-cli/gpt-5.5" in by_model
    assert "anthropic-vertex/claude-opus-4-7" in by_model
    assert by_model["codex-cli/gpt-5.5"]["count"] == 2
    assert by_model["anthropic-vertex/claude-opus-4-7"]["count"] == 1
    # mean для codex = (1+2)/2 = 1.5
    assert by_model["codex-cli/gpt-5.5"]["mean"] == 1.5


# ---------------------------------------------------------------------------
# 8. fail_rate вычислен корректно
# ---------------------------------------------------------------------------


def test_fail_rate_calculation(tmp_path: Path) -> None:
    """fail_rate = failures / total, округлён до 3 знаков."""
    log_file = tmp_path / "bypass_perf.jsonl"
    now = time.time()

    # 3 успешных + 1 провальный → fail_rate = 0.25
    records = [
        {"ts": now - i, "kind": "gemma", "model": "gemma-3-27b-it",
         "duration_sec": 1.0, "success": i != 0, "response_len": 100 if i != 0 else 0}
        for i in range(4)
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    with patch("src.integrations._bypass_perf.PERF_LOG", log_file):
        result = aggregate_perf(window_sec=3600)

    gemma_stats = result["by_kind"]["gemma"]
    assert gemma_stats["count"] == 4
    assert result["total_failures"] == 1
    assert gemma_stats["fail_rate"] == 0.25


# ---------------------------------------------------------------------------
# Дополнительно: parse_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "window,expected",
    [
        ("1h", 3600),
        ("24h", 86400),
        ("5m", 300),
        ("30s", 30),
        ("3600", 3600),
        ("invalid", 3600),  # fallback
        ("", 3600),  # fallback
    ],
)
def test_parse_duration(window: str, expected: int) -> None:
    """parse_duration правильно конвертирует строки в секунды."""
    assert parse_duration(window) == expected

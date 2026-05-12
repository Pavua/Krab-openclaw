# -*- coding: utf-8 -*-
"""Wave 115: тесты cron_history wrapper + CronHistoryLog read interface.

Покрытие:
1. wrapper main() записывает строку с правильными полями (success path).
2. wrapper main() пробрасывает exit_code дочернего процесса.
3. wrapper парсит `--` separator корректно; missing → exit 2.
4. CronHistoryLog.query_recent — DESC по start_ts, фильтр по label.
5. CronHistoryLog.query_recent — limit clamping (отрицательный → []).
6. CronHistoryLog.stats_by_label — корректный fail_pct + last_run.
7. CronHistoryLog.query_recent на несконфигурированном path → [].
8. exit_class классификация в записях query_recent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.core.cron_history_log import CronHistoryLog

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = REPO_ROOT / "scripts" / "krab_cron_wrap.py"


def _load_wrapper_module():
    """Загрузить scripts/krab_cron_wrap.py как module (не пакет)."""
    spec = importlib.util.spec_from_file_location("krab_cron_wrap", WRAPPER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["krab_cron_wrap"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wrapper_mod():
    return _load_wrapper_module()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cron_history.db"


def test_wrapper_records_success_row(wrapper_mod, db_path: Path) -> None:
    """Wrapper запускает `true`, exit=0, строка появляется в БД."""
    rc = wrapper_mod.main(
        ["--label", "test_job", "--db", str(db_path), "--", "/usr/bin/true"]
    )
    assert rc == 0
    assert db_path.exists()

    log = CronHistoryLog(storage_path=db_path)
    rows = log.query_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "test_job"
    assert row["exit_code"] == 0
    assert row["exit_class"] == "ok"
    assert row["duration_sec"] >= 0.0
    assert row["start_ts"] and row["end_ts"]


def test_wrapper_propagates_nonzero_exit(wrapper_mod, db_path: Path) -> None:
    """exit code дочернего процесса пробрасывается + класс fail."""
    rc = wrapper_mod.main(
        ["--label", "failing_job", "--db", str(db_path), "--", "/usr/bin/false"]
    )
    assert rc == 1

    log = CronHistoryLog(storage_path=db_path)
    rows = log.query_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["exit_code"] == 1
    assert rows[0]["exit_class"] == "fail"


def test_wrapper_missing_separator_errors(wrapper_mod, db_path: Path) -> None:
    """Без `--` — argparse завершает с SystemExit(2)."""
    with pytest.raises(SystemExit) as exc:
        wrapper_mod.main(["--label", "x", "--db", str(db_path)])
    assert exc.value.code == 2


def test_query_recent_desc_and_filter(db_path: Path, wrapper_mod) -> None:
    """Три записи: фильтр label возвращает только свои, в DESC порядке."""
    for lbl in ("alpha", "beta", "alpha"):
        rc = wrapper_mod.main(
            ["--label", lbl, "--db", str(db_path), "--", "/usr/bin/true"]
        )
        assert rc == 0

    log = CronHistoryLog(storage_path=db_path)
    rows_all = log.query_recent(limit=10)
    assert len(rows_all) == 3
    # DESC: последний alpha идёт первым
    assert rows_all[0]["label"] == "alpha"

    rows_alpha = log.query_recent(label="alpha", limit=10)
    assert len(rows_alpha) == 2
    assert all(r["label"] == "alpha" for r in rows_alpha)


def test_query_recent_limit_clamping(db_path: Path, wrapper_mod) -> None:
    """Отрицательный limit → пустой результат, гигантский — capped."""
    wrapper_mod.main(["--label", "x", "--db", str(db_path), "--", "/usr/bin/true"])
    log = CronHistoryLog(storage_path=db_path)
    assert log.query_recent(limit=-1) == []
    assert log.query_recent(limit=0) == []
    # Не падает на огромном limit.
    rows = log.query_recent(limit=10_000_000)
    assert len(rows) == 1


def test_stats_by_label(db_path: Path, wrapper_mod) -> None:
    """alpha: 1 ok + 1 fail = fail_pct=50; beta: 1 ok = fail_pct=0."""
    wrapper_mod.main(["--label", "alpha", "--db", str(db_path), "--", "/usr/bin/true"])
    wrapper_mod.main(["--label", "alpha", "--db", str(db_path), "--", "/usr/bin/false"])
    wrapper_mod.main(["--label", "beta", "--db", str(db_path), "--", "/usr/bin/true"])

    log = CronHistoryLog(storage_path=db_path)
    stats = log.stats_by_label()
    by_label = {row["label"]: row for row in stats}
    assert "alpha" in by_label and "beta" in by_label

    a = by_label["alpha"]
    assert a["total"] == 2
    assert a["ok_count"] == 1
    assert a["fail_count"] == 1
    assert a["fail_pct"] == 50.0

    b = by_label["beta"]
    assert b["total"] == 1
    assert b["fail_pct"] == 0.0
    assert b["last_run"] is not None


def test_query_unconfigured_path_returns_empty(tmp_path: Path) -> None:
    """Если БД ещё не создана (path указан, но файла нет) — []."""
    log = CronHistoryLog(storage_path=tmp_path / "absent.db")
    assert log.query_recent(limit=10) == []
    assert log.stats_by_label() == []

    # Без path вообще.
    log2 = CronHistoryLog()
    assert log2.query_recent(limit=10) == []


def test_configure_default_path_idempotent(db_path: Path, wrapper_mod) -> None:
    """configure_default_path можно вызвать повторно, состояние перетирается."""
    wrapper_mod.main(["--label", "x", "--db", str(db_path), "--", "/usr/bin/true"])
    log = CronHistoryLog()
    log.configure_default_path(db_path)
    log.configure_default_path(db_path)  # повтор
    rows = log.query_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["exit_class"] == "ok"

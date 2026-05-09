# -*- coding: utf-8 -*-
"""
Тесты runtime risk audit.

Проверяют чистую аналитику без live-сервисов: секреты не раскрываются, sandbox
не считается down, а шум логов/session-артефактов превращается в ограниченный
список рисков для оператора.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path


def _load_module():
    """Загружает скрипт как модуль, не требуя package import для scripts/."""

    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "krab_runtime_risk_audit.py"
    spec = importlib.util.spec_from_file_location("krab_runtime_risk_audit", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scan_env_risks_redacts_values(tmp_path: Path) -> None:
    """Env-аудит возвращает имена ключей, но не значения секретов."""

    m = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "TELEGRAM_API_ID=123",
                "AUTO_REPLY_CONTEXT_TOKENS=123",
                "AUTO_REPLY_SELF_PRIVATE_ENABLED=true",
                "TELEGRAM_API_HASH=super-secret-hash",
                "GEMINI_API_KEY=[value]",
                "OPENCLAW_TOKEN=sk-real-token",
            ]
        ),
        encoding="utf-8",
    )

    result = m.scan_env_risks(env_path)

    assert result["filled_secret_count"] == 2
    assert result["secret_keys"] == ["OPENCLAW_TOKEN", "TELEGRAM_API_HASH"]
    assert "super-secret-hash" not in str(result)
    assert "sk-real-token" not in str(result)


def test_env_export_syntax_is_treated_as_secret(tmp_path: Path) -> None:
    """Shell-строки export не должны обходить детектор секретов."""

    m = _load_module()
    env_path = tmp_path / ".env"
    template_path = tmp_path / ".env.template"
    env_path.write_text(
        "\n".join(
            [
                "export OPENCLAW_TOKEN=sk-export-token",
                "export AUTO_REPLY_SELF_PRIVATE_ENABLED=true",
                "PRIVATE_KEY=-----BEGIN PRIVATE KEY-----",
            ]
        ),
        encoding="utf-8",
    )

    scan = m.scan_env_risks(env_path)
    result = m.write_env_template(env_path, template_path, dry_run=False)
    text = template_path.read_text(encoding="utf-8")

    assert scan["secret_keys"] == ["OPENCLAW_TOKEN", "PRIVATE_KEY"]
    assert result["written"] is True
    assert "OPENCLAW_TOKEN=" in text
    assert "PRIVATE_KEY=" in text
    assert "AUTO_REPLY_SELF_PRIVATE_ENABLED=true" in text
    assert "sk-export-token" not in text
    assert "BEGIN PRIVATE KEY" not in text


def test_build_risks_marks_sandbox_as_separate_status() -> None:
    """blocked_by_sandbox должен быть warning, а не ложный endpoint down."""

    m = _load_module()
    risks = m.build_risks(
        endpoints=[
            m.EndpointProbe(
                name="gateway",
                url="http://127.0.0.1:18789/health",
                up=True,
                status=200,
            )
        ],
        processes=m.ProcessProbe(status="blocked_by_sandbox", error="Operation not permitted"),
        env_scan={"filled_secret_count": 0, "secret_keys": []},
        log_scan={"large_logs": [], "pattern_hits": []},
        session_scan={"total_artifacts": 0, "categories": {}},
    )

    assert [risk.code for risk in risks] == ["process_probe_blocked"]
    assert risks[0].severity == "medium"


def test_build_risks_marks_endpoint_sandbox_as_blocked_not_down() -> None:
    """Operation not permitted у localhost probe не должен становиться endpoint_down."""

    m = _load_module()
    risks = m.build_risks(
        endpoints=[
            m.EndpointProbe(
                name="panel",
                url="http://127.0.0.1:8080/api/health/lite",
                up=False,
                status=0,
                error="<urlopen error [Errno 1] Operation not permitted>",
            )
        ],
        processes=m.ProcessProbe(status="ok", pids=[123]),
        env_scan={"filled_secret_count": 0, "secret_keys": []},
        log_scan={"large_logs": [], "pattern_hits": []},
        session_scan={"total_artifacts": 0, "categories": {}},
    )

    assert [risk.code for risk in risks] == ["endpoint_probe_blocked"]
    assert risks[0].severity == "medium"


def test_endpoint_urls_are_redacted_in_reports() -> None:
    """Custom endpoint с токеном в query не должен утекать в JSON evidence."""

    m = _load_module()
    probe = m.EndpointProbe(
        name="custom",
        url="http://user:pass@127.0.0.1:18789/health?token=secret&mode=lite",
        up=False,
        status=401,
        error="http_401",
    )

    report_item = m._endpoint_probe_to_report(probe)
    risks = m.build_risks(
        endpoints=[probe],
        processes=m.ProcessProbe(status="ok", pids=[123]),
        env_scan={"filled_secret_count": 0, "secret_keys": []},
        log_scan={"large_logs": [], "pattern_hits": []},
        session_scan={"total_artifacts": 0, "categories": {}},
    )

    assert report_item["url"] == "http://127.0.0.1:18789/health?token=%3Credacted%3E&mode=lite"
    assert "user:pass" not in str(report_item)
    assert "secret" not in str(risks)


def test_scan_logs_detects_large_file_and_tail_patterns(tmp_path: Path) -> None:
    """Лог-аудит видит большой файл и аварийные паттерны в хвосте."""

    m = _load_module()
    log_path = tmp_path / "krab_launchd.err.log"
    log_path.write_text(
        "ok\nresource_tracker: leaked semaphore\nCancelledError\n", encoding="utf-8"
    )

    result = m.scan_logs(tmp_path, large_mb=1, tail_bytes=1024)

    assert result["large_logs"] == []
    codes = {hit["code"] for hit in result["pattern_hits"]}
    assert {"cancelled_error", "leaked_semaphore"} <= codes


def test_scan_session_artifacts_counts_categories(tmp_path: Path) -> None:
    """Session-аудит считает recovery-артефакты и не теряет live-файлы."""

    m = _load_module()
    (tmp_path / "kraab.session").write_text("live", encoding="utf-8")
    (tmp_path / "kraab.session.bak-corrupt-1").write_text("bad", encoding="utf-8")
    (tmp_path / "kraab.session.broken-2").write_text("bad", encoding="utf-8")
    (tmp_path / "kraab.session.malformed-3").write_text("bad", encoding="utf-8")

    result = m.scan_session_artifacts(tmp_path)

    assert result["total_artifacts"] == 3
    assert result["categories"]["bak_corrupt"]["count"] == 1
    assert result["categories"]["broken"]["count"] == 1
    assert result["categories"]["malformed"]["count"] == 1
    assert str(tmp_path / "kraab.session") in result["protected_live_files"]


def test_write_env_template_redacts_only_secret_values(tmp_path: Path) -> None:
    """Template сохраняет несекретные флаги, но очищает значения секретов."""

    m = _load_module()
    env_path = tmp_path / ".env"
    template_path = tmp_path / ".env.template"
    env_path.write_text(
        "\n".join(
            [
                "AUTO_REPLY_SELF_PRIVATE_ENABLED=true",
                "OPENCLAW_TOKEN=sk-real-token",
                "TELEGRAM_API_HASH=real-hash",
            ]
        ),
        encoding="utf-8",
    )

    result = m.write_env_template(env_path, template_path, dry_run=False)
    text = template_path.read_text(encoding="utf-8")

    assert result["written"] is True
    assert "AUTO_REPLY_SELF_PRIVATE_ENABLED=true" in text
    assert "OPENCLAW_TOKEN=" in text
    assert "TELEGRAM_API_HASH=" in text
    assert "sk-real-token" not in text
    assert "real-hash" not in text


def test_rotate_large_logs_dry_run_does_not_touch_file(tmp_path: Path) -> None:
    """Dry-run rotation только сообщает действие и не меняет live-log."""

    m = _load_module()
    log_path = tmp_path / "krab_launchd.out.log"
    log_path.write_bytes(b"x" * 2048)

    result = m.rotate_large_logs(tmp_path, large_mb=1, dry_run=True)

    assert result == []
    assert log_path.stat().st_size == 2048


def test_rotate_large_logs_apply_gzips_and_truncates(tmp_path: Path) -> None:
    """Apply rotation архивирует большой лог и обнуляет исходный inode."""

    m = _load_module()
    log_path = tmp_path / "krab_launchd.out.log"
    log_path.write_bytes(b"x" * 2048)

    result = m.rotate_large_logs(tmp_path, large_mb=1, dry_run=False)

    assert result == []
    assert log_path.stat().st_size == 2048

    result = m.rotate_large_logs(tmp_path, large_mb=1, dry_run=False)
    assert result == []


def test_rotate_large_logs_apply_when_threshold_is_one_mb(tmp_path: Path) -> None:
    """Порог считается в MB, поэтому файл больше 1MB реально ротируется."""

    m = _load_module()
    log_path = tmp_path / "krab_launchd.out.log"
    log_path.write_bytes(b"x" * (1024 * 1024 + 1))

    result = m.rotate_large_logs(tmp_path, large_mb=1, dry_run=False)

    assert len(result) == 1
    assert result[0]["action"] == "rotated_copytruncate"
    assert log_path.stat().st_size == 0
    assert list(tmp_path.glob("krab_launchd.out.log.*.gz"))


def test_cleanup_session_backups_uses_existing_retention(tmp_path: Path) -> None:
    """Wrapper retention удаляет только старые backup-файлы, live session защищена."""

    m = _load_module()
    live = tmp_path / "kraab.session"
    old_backup = tmp_path / "kraab.session.bak-corrupt-old"
    live.write_text("live", encoding="utf-8")
    old_backup.write_text("bad", encoding="utf-8")
    old = time.time() - 30 * 86400
    old_backup.touch()
    import os

    os.utime(old_backup, (old, old))

    result = m.cleanup_session_backups(tmp_path, keep_recent=0, max_age_days=14, dry_run=False)

    assert live.exists()
    assert not old_backup.exists()
    assert str(old_backup) in result["removed"]

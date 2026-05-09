#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аудит рисков текущего Krab runtime.

Зачем нужен:
- собирает независимую картину по HTTP health, процессам, секретам, логам и session-артефактам;
- явно различает реальный down и sandbox-блокировку диагностики;
- пишет JSON-отчёт в `artifacts/ops`, чтобы health-watch, handoff и owner UI могли ссылаться
  на один машинно-читаемый источник.

Связь с проектом:
- дополняет `scripts/krab_core_health_watch.py`, но не заменяет его длительный flap-мониторинг;
- использует retention-логику из `src.bootstrap.session_recovery` только как архитектурный ориентир,
  сам ничего не удаляет.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse
from urllib import error as urlerror
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"
DEFAULT_LOG_DIR = ROOT / "logs"
DEFAULT_SESSION_DIR = ROOT / "data" / "sessions"

# Секреты не печатаем никогда: фиксируем только имя переменной и класс риска.
PLACEHOLDER_RE = re.compile(r"^(\[value\]|changeme|todo|example|none|null|)$", re.IGNORECASE)

# Паттерны подобраны как observability-сигналы, а не как полная классификация ошибок.
LOG_RISK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("cancelled_error", "medium", r"CancelledError"),
    ("leaked_semaphore", "medium", r"resource_tracker:.*leaked semaphore"),
    ("operation_not_permitted", "medium", r"Operation not permitted"),
    ("traceback", "medium", r"Traceback \(most recent call last\)"),
    ("fatal", "high", r"\b(FATAL|CRITICAL)\b"),
)

SESSION_ARTIFACT_PATTERNS: dict[str, str] = {
    "bak_corrupt": "*.bak-corrupt-*",
    "corrupt": "*.corrupt*",
    "broken": "*.broken-*",
    "malformed": "*.malformed-*",
    "pre_recover": "*.pre-recover-*",
    "legacy_bak": "*.bak.*",
}


@dataclass(frozen=True)
class EndpointSpec:
    """Описание HTTP endpoint, который должен отражать отдельную часть runtime."""

    name: str
    url: str


@dataclass
class EndpointProbe:
    """Результат одного HTTP probe без раскрытия тела ответа."""

    name: str
    url: str
    up: bool
    status: int
    error: str = ""
    latency_ms: int = 0


@dataclass
class ProcessProbe:
    """Результат проверки процессов Krab Core через ps."""

    status: str
    pids: list[int] = field(default_factory=list)
    error: str = ""


@dataclass
class RuntimeRisk:
    """Нормализованный риск для JSON-отчёта и CLI-вывода."""

    code: str
    severity: str
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""


@dataclass
class RemediationPlan:
    """План безопасной починки: по умолчанию только preview, без изменения файлов."""

    dry_run: bool
    env_template: dict[str, Any] = field(default_factory=dict)
    log_rotation: list[dict[str, Any]] = field(default_factory=list)
    session_cleanup: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _now_utc_iso() -> str:
    """Возвращает текущее UTC-время в стабильном ISO-формате."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _severity_rank(severity: str) -> int:
    """Сортирует риски от более важных к менее важным."""

    return {"high": 3, "medium": 2, "low": 1}.get(severity, 0)


def _split_env_assignment(raw: str) -> tuple[str, str] | None:
    """
    Аккуратно разбирает строку env.

    Поддерживаем `export KEY=value`: такие строки часто копируют из shell-профилей,
    и именно там нельзя пропустить секрет при построении template.
    """

    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key.removeprefix("export ").strip()
    if not key:
        return None
    return key, value


def _redact_url_for_report(url: str) -> str:
    """Убирает userinfo и secret-like query параметры из URL перед записью отчёта."""

    try:
        parsed = parse.urlsplit(url)
    except ValueError:
        return url

    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    safe_query: list[tuple[str, str]] = []
    for key, value in parse.parse_qsl(parsed.query, keep_blank_values=True):
        safe_query.append((key, "<redacted>" if _is_secret_key_name(key) else value))

    return parse.urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parse.urlencode(safe_query),
            parsed.fragment,
        )
    )


def _endpoint_probe_to_report(probe: EndpointProbe) -> dict[str, Any]:
    """Сериализует endpoint probe без потенциальных токенов в URL."""

    item = asdict(probe)
    item["url"] = _redact_url_for_report(probe.url)
    return item


def _probe_http_sync(spec: EndpointSpec, timeout_sec: float) -> EndpointProbe:
    """Синхронный urllib probe; запускается из async-кода через asyncio.to_thread."""

    started = time.perf_counter()
    req = request.Request(url=spec.url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200) or 200)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return EndpointProbe(
                name=spec.name,
                url=spec.url,
                up=200 <= status < 300,
                status=status,
                latency_ms=latency_ms,
            )
    except urlerror.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return EndpointProbe(
            name=spec.name,
            url=spec.url,
            up=False,
            status=int(exc.code or 0),
            error=f"http_{exc.code}",
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - started) * 1000)
        return EndpointProbe(
            name=spec.name,
            url=spec.url,
            up=False,
            status=0,
            error=str(exc) or exc.__class__.__name__,
            latency_ms=latency_ms,
        )


async def probe_endpoints(
    endpoints: list[EndpointSpec],
    *,
    timeout_sec: float = 3.0,
) -> list[EndpointProbe]:
    """Параллельно проверяет HTTP endpoints стандартной библиотекой Python."""

    tasks = [asyncio.to_thread(_probe_http_sync, endpoint, timeout_sec) for endpoint in endpoints]
    return list(await asyncio.gather(*tasks))


def probe_processes() -> ProcessProbe:
    """
    Ищет core-процессы Krab.

    В sandbox macOS `ps` может вернуть Operation not permitted. Это не down, а
    отдельный статус `blocked_by_sandbox`, чтобы watchdog не врал красным цветом.
    """

    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or exc.__class__.__name__
        status = "blocked_by_sandbox" if _looks_like_sandbox_block(error) else "error"
        return ProcessProbe(status=status, error=error)

    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    if proc.returncode != 0:
        status = "blocked_by_sandbox" if _looks_like_sandbox_block(combined) else "error"
        return ProcessProbe(status=status, error=combined.strip()[:500])

    pids: list[int] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_raw, cmd = parts
        low = cmd.lower()
        if "python" not in low:
            continue
        if "-m src.main" not in cmd and "src/main.py" not in cmd:
            continue
        try:
            pids.append(int(pid_raw))
        except ValueError:
            continue

    return ProcessProbe(status="ok", pids=sorted(set(pids)))


def _looks_like_sandbox_block(text: str) -> bool:
    """Определяет типичные macOS/sandbox отказы без завязки на точный язык ошибки."""

    lowered = text.lower()
    return "operation not permitted" in lowered or "not permitted" in lowered or "denied" in lowered


def scan_env_risks(env_path: Path) -> dict[str, Any]:
    """
    Проверяет наличие заполненных секретов в env-файле.

    Значения не возвращаются в отчёт. Это принципиально: аудит не должен становиться
    ещё одним каналом утечки.
    """

    result: dict[str, Any] = {
        "path": str(env_path),
        "exists": env_path.exists(),
        "secret_keys": [],
        "filled_secret_count": 0,
    }
    if not env_path.exists():
        return result

    keys: list[str] = []
    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        result["error"] = str(exc)
        return result

    for raw in lines:
        assignment = _split_env_assignment(raw)
        if assignment is None:
            continue
        key, value = assignment
        value = value.strip().strip('"').strip("'")
        if not _is_secret_key_name(key):
            continue
        if PLACEHOLDER_RE.match(value):
            continue
        keys.append(key)

    result["secret_keys"] = sorted(set(keys))
    result["filled_secret_count"] = len(set(keys))
    return result


def write_env_template(env_path: Path, template_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """
    Создаёт `.env.template` без значений секретов.

    Хлебная крошка: мы не переносим секреты автоматически в Keychain/1Password,
    потому что это требует выбранного vault/account. Зато сразу убираем главный
    риск accidental disclosure: template можно безопасно коммитить и показывать агентам.
    """

    result: dict[str, Any] = {
        "source": str(env_path),
        "target": str(template_path),
        "dry_run": dry_run,
        "written": False,
    }
    if not env_path.exists():
        result["skipped"] = "env_not_exists"
        return result

    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        result["error"] = str(exc)
        return result

    rendered: list[str] = [
        "# Шаблон env для Krab runtime.",
        "# Реальные значения секретов держим в macOS Keychain/1Password/env-injection, не в git.",
    ]
    for raw in lines:
        assignment = _split_env_assignment(raw)
        if assignment is None:
            rendered.append(raw)
            continue
        key_clean, value = assignment
        if _is_secret_key_name(key_clean):
            rendered.append(f"{key_clean}=")
        else:
            # Несекретные runtime-флаги оставляем как ориентир для запуска.
            rendered.append(f"{key_clean}={value.strip()}")

    text = "\n".join(rendered).rstrip() + "\n"
    result["would_write_bytes"] = len(text.encode("utf-8"))
    if dry_run:
        result["preview_lines"] = rendered[:12]
        return result

    try:
        template_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        result["error"] = str(exc)
        return result
    result["written"] = True
    return result


def _is_secret_key_name(key: str) -> bool:
    """
    Определяет secret-like env name без агрессивных false positive.

    Например, `OPENCLAW_TOKEN` — секрет, а `AUTO_REPLY_CONTEXT_TOKENS` —
    лимит контекста и не должен попадать в security report.
    """

    normalized = key.upper().replace("-", "_")
    parts = normalized.split("_")
    if normalized.endswith(("_API_KEY", "_API_HASH", "_AUTH_TOKEN", "_BOT_TOKEN")):
        return True
    if normalized in {
        "API_KEY",
        "API_HASH",
        "AUTH_TOKEN",
        "BOT_TOKEN",
        "TOKEN",
        "SECRET",
        "DSN",
        "PRIVATE_KEY",
    }:
        return True
    if parts[-1] in {"PASSWORD", "SECRET", "TOKEN", "DSN", "PRIVATEKEY"}:
        return True
    if len(parts) >= 2 and parts[-2:] == ["PRIVATE", "KEY"]:
        return True
    if normalized.endswith("_WEBHOOK_SECRET"):
        return True
    return False


def scan_logs(log_dir: Path, *, large_mb: int = 50, tail_bytes: int = 512_000) -> dict[str, Any]:
    """Сканирует размер логов и последние строки на известные аварийные паттерны."""

    report: dict[str, Any] = {
        "path": str(log_dir),
        "exists": log_dir.exists(),
        "large_logs": [],
        "pattern_hits": [],
        "total_bytes": 0,
    }
    if not log_dir.exists():
        return report

    threshold = max(1, large_mb) * 1024 * 1024
    for path in sorted(log_dir.glob("*.log")):
        try:
            stat = path.stat()
        except OSError:
            continue
        report["total_bytes"] += stat.st_size
        if stat.st_size >= threshold:
            report["large_logs"].append(
                {"path": str(path), "size_bytes": stat.st_size, "size_mb": round(stat.st_size / 1024 / 1024, 1)}
            )

        tail = _read_tail(path, tail_bytes=tail_bytes)
        for code, severity, pattern in LOG_RISK_PATTERNS:
            matches = re.findall(pattern, tail, flags=re.IGNORECASE)
            if matches:
                report["pattern_hits"].append(
                    {
                        "path": str(path),
                        "code": code,
                        "severity": severity,
                        "count_in_tail": len(matches),
                    }
                )

    return report


def _read_tail(path: Path, *, tail_bytes: int) -> str:
    """Читает хвост файла без загрузки больших логов целиком."""

    try:
        with path.open("rb") as fh:
            try:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - tail_bytes), os.SEEK_SET)
            except OSError:
                fh.seek(0)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def scan_session_artifacts(session_dir: Path) -> dict[str, Any]:
    """Считает исторические corrupt/broken/malformed session-артефакты."""

    report: dict[str, Any] = {
        "path": str(session_dir),
        "exists": session_dir.exists(),
        "categories": {},
        "total_artifacts": 0,
        "protected_live_files": [],
    }
    if not session_dir.exists():
        return report

    for live_name in ("kraab.session", "kraab.session-wal", "kraab.session-shm"):
        live_path = session_dir / live_name
        if live_path.exists():
            report["protected_live_files"].append(str(live_path))

    seen: set[Path] = set()
    for category, pattern in SESSION_ARTIFACT_PATTERNS.items():
        files = sorted(path for path in session_dir.glob(pattern) if path.is_file())
        seen.update(files)
        report["categories"][category] = {
            "count": len(files),
            "examples": [str(path) for path in files[:5]],
        }

    report["total_artifacts"] = len(seen)
    return report


def rotate_large_logs(
    log_dir: Path,
    *,
    large_mb: int = 50,
    keep_rotations: int = 5,
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    """
    Gzip-copytruncate rotation для live `.log` файлов.

    Решение именно copytruncate: launchd/долгоживущие процессы часто держат
    file descriptor открытым, rename-rotation может не сработать до рестарта.
    """

    if not log_dir.exists():
        return [{"path": str(log_dir), "skipped": "log_dir_not_exists", "dry_run": dry_run}]

    threshold = max(1, large_mb) * 1024 * 1024
    results: list[dict[str, Any]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

    for path in sorted(log_dir.glob("*.log")):
        try:
            size = path.stat().st_size
        except OSError as exc:
            results.append({"path": str(path), "error": str(exc), "dry_run": dry_run})
            continue

        if size < threshold:
            continue

        gz_path = path.parent / f"{path.name}.{stamp}.gz"
        suffix = 1
        while gz_path.exists():
            gz_path = path.parent / f"{path.name}.{stamp}.{suffix}.gz"
            suffix += 1
        item: dict[str, Any] = {
            "path": str(path),
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 1),
            "target": str(gz_path),
            "dry_run": dry_run,
        }
        if dry_run:
            item["action"] = "would_rotate_copytruncate"
            results.append(item)
            continue

        try:
            with path.open("rb") as src:
                with gzip.open(gz_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            # Не удаляем файл: обнуляем его, сохраняя inode для текущих writer-процессов.
            with path.open("r+b") as live_log:
                live_log.truncate(0)
            item["action"] = "rotated_copytruncate"
        except OSError as exc:
            item["error"] = str(exc)
            results.append(item)
            continue

        removed_old: list[str] = []
        rotations = sorted(
            path.parent.glob(f"{path.name}.*.gz"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        for old in rotations[max(1, keep_rotations) :]:
            try:
                old.unlink()
                removed_old.append(str(old))
            except OSError:
                continue
        item["old_rotations_removed"] = removed_old
        results.append(item)

    return results


def cleanup_session_backups(
    session_dir: Path,
    *,
    keep_recent: int = 3,
    max_age_days: int = 14,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Запускает существующую retention-логику session backup-файлов.

    Не дублируем велосипед: основная политика уже живёт в
    `src.bootstrap.session_recovery.cleanup_old_backups`, этот wrapper только
    делает её частью единого runtime risk audit отчёта.
    """

    # При запуске как `python scripts/...py` корень проекта не всегда в sys.path.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        # Импорт session_recovery поднимает часть runtime bootstrap и может шуметь
        # structlog-сообщениями. Для audit-модуля это не evidence, поэтому глушим.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from src.bootstrap.session_recovery import cleanup_old_backups
    except Exception as exc:  # noqa: BLE001
        return {
            "path": str(session_dir),
            "dry_run": dry_run,
            "error": f"session_recovery_import_failed: {exc}",
        }

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cleanup_old_backups(
                session_dir,
                keep_recent=max(0, keep_recent),
                max_age_days=max(1, max_age_days),
                dry_run=dry_run,
            )
    except Exception as exc:  # noqa: BLE001
        return {"path": str(session_dir), "dry_run": dry_run, "error": str(exc)}


async def run_remediation_plan(
    *,
    env_path: Path,
    log_dir: Path,
    session_dir: Path,
    large_log_mb: int,
    dry_run: bool,
    keep_rotations: int = 5,
    keep_recent_sessions: int = 3,
    session_max_age_days: int = 14,
) -> RemediationPlan:
    """Готовит или применяет безопасные исправления по найденным рискам."""

    template_path = env_path.with_name(".env.template")
    env_task = asyncio.to_thread(write_env_template, env_path, template_path, dry_run=dry_run)
    logs_task = asyncio.to_thread(
        rotate_large_logs,
        log_dir,
        large_mb=large_log_mb,
        keep_rotations=keep_rotations,
        dry_run=dry_run,
    )
    sessions_task = asyncio.to_thread(
        cleanup_session_backups,
        session_dir,
        keep_recent=keep_recent_sessions,
        max_age_days=session_max_age_days,
        dry_run=dry_run,
    )
    env_template, log_rotation, session_cleanup = await asyncio.gather(env_task, logs_task, sessions_task)

    errors: list[str] = []
    if env_template.get("error"):
        errors.append(f"env_template: {env_template['error']}")
    for item in log_rotation:
        if item.get("error"):
            errors.append(f"log_rotation:{item.get('path')}: {item['error']}")
    if session_cleanup.get("error"):
        errors.append(f"session_cleanup: {session_cleanup['error']}")

    return RemediationPlan(
        dry_run=dry_run,
        env_template=env_template,
        log_rotation=log_rotation,
        session_cleanup=session_cleanup,
        errors=errors,
    )


def build_risks(
    *,
    endpoints: list[EndpointProbe],
    processes: ProcessProbe,
    env_scan: dict[str, Any],
    log_scan: dict[str, Any],
    session_scan: dict[str, Any],
) -> list[RuntimeRisk]:
    """Собирает 2-3 главных риска из сырых probe-данных."""

    risks: list[RuntimeRisk] = []

    down_endpoints = [probe for probe in endpoints if not probe.up]
    blocked_endpoints = [probe for probe in down_endpoints if _looks_like_sandbox_block(probe.error)]
    real_down_endpoints = [probe for probe in down_endpoints if probe not in blocked_endpoints]
    if real_down_endpoints:
        risks.append(
            RuntimeRisk(
                code="endpoint_down",
                severity="high",
                title="HTTP health endpoint недоступен",
                detail="Один или несколько runtime endpoint не ответили успешным 2xx статусом.",
                evidence={"endpoints": [_endpoint_probe_to_report(probe) for probe in real_down_endpoints]},
                recommendation="Разделить алерт по panel/gateway и проверять ближайший к пользовательскому симптому endpoint.",
            )
        )
    elif blocked_endpoints:
        risks.append(
            RuntimeRisk(
                code="endpoint_probe_blocked",
                severity="medium",
                title="HTTP probe заблокирован sandbox",
                detail="localhost endpoint не удалось проверить из текущего runtime, но это не доказывает падение сервиса.",
                evidence={"endpoints": [_endpoint_probe_to_report(probe) for probe in blocked_endpoints]},
                recommendation="Показывать blocked_by_sandbox отдельно и сверять с логами launchd или внешним smoke.",
            )
        )

    if processes.status == "blocked_by_sandbox":
        risks.append(
            RuntimeRisk(
                code="process_probe_blocked",
                severity="medium",
                title="Диагностика процессов заблокирована sandbox",
                detail="ps-проверка не может подтвердить PID core-процесса; это нельзя трактовать как down.",
                evidence=asdict(processes),
                recommendation="В отчётах показывать статус blocked_by_sandbox отдельно от down.",
            )
        )
    elif processes.status == "ok" and not processes.pids:
        risks.append(
            RuntimeRisk(
                code="core_process_not_found",
                severity="medium",
                title="Core-процесс не найден через ps",
                detail="HTTP может быть жив за счёт другого entrypoint, но процессный сигнал сейчас пустой.",
                evidence=asdict(processes),
                recommendation="Сверить launchd plist и фактическую команду запуска core.",
            )
        )

    if int(env_scan.get("filled_secret_count") or 0) > 0:
        risks.append(
            RuntimeRisk(
                code="local_env_secrets",
                severity="high",
                title="Заполненные секреты лежат в локальном env-файле",
                detail="В рабочем дереве найден env-файл с непустыми secret-like переменными.",
                evidence={
                    "path": env_scan.get("path"),
                    "filled_secret_count": env_scan.get("filled_secret_count"),
                    "secret_keys": env_scan.get("secret_keys"),
                },
                recommendation="Перенести значения в macOS Keychain/1Password CLI/env-injection, в репозитории оставить только template.",
            )
        )

    large_logs = log_scan.get("large_logs") or []
    pattern_hits = log_scan.get("pattern_hits") or []
    if large_logs or pattern_hits:
        max_pattern_severity = "medium"
        if any(hit.get("severity") == "high" for hit in pattern_hits):
            max_pattern_severity = "high"
        risks.append(
            RuntimeRisk(
                code="observability_noise",
                severity=max_pattern_severity,
                title="Логи зашумлены размером или аварийными паттернами",
                detail="Большие live-логи и повторяющиеся ошибки ухудшают поиск свежего user-facing сбоя.",
                evidence={"large_logs": large_logs[:8], "pattern_hits": pattern_hits[:12]},
                recommendation="Включить rotation/copytruncate для live-логов и вынести повторяющиеся ошибки в отдельные метрики.",
            )
        )

    if int(session_scan.get("total_artifacts") or 0) > 10:
        risks.append(
            RuntimeRisk(
                code="session_artifact_buildup",
                severity="medium",
                title="Накоплены исторические session recovery артефакты",
                detail="В data/sessions много corrupt/broken/malformed/bak файлов; это мешает быстрой диагностике.",
                evidence={
                    "total_artifacts": session_scan.get("total_artifacts"),
                    "categories": session_scan.get("categories"),
                },
                recommendation="Запускать retention cleanup с dry-run отчётом, затем удалять только старые backup-категории.",
            )
        )

    return sorted(risks, key=lambda risk: _severity_rank(risk.severity), reverse=True)[:3]


async def run_audit(
    *,
    endpoints: list[EndpointSpec],
    env_path: Path,
    log_dir: Path,
    session_dir: Path,
    timeout_sec: float,
    large_log_mb: int,
    include_remediation: bool = False,
    apply_remediation: bool = False,
) -> dict[str, Any]:
    """Выполняет полный аудит и возвращает готовый JSON-serializable report."""

    endpoint_results = await probe_endpoints(endpoints, timeout_sec=timeout_sec)
    process_result = await asyncio.to_thread(probe_processes)
    env_scan = await asyncio.to_thread(scan_env_risks, env_path)
    log_scan = await asyncio.to_thread(scan_logs, log_dir, large_mb=large_log_mb)
    session_scan = await asyncio.to_thread(scan_session_artifacts, session_dir)

    risks = build_risks(
        endpoints=endpoint_results,
        processes=process_result,
        env_scan=env_scan,
        log_scan=log_scan,
        session_scan=session_scan,
    )

    report: dict[str, Any] = {
        "ok": not any(risk.severity == "high" for risk in risks),
        "generated_at": _now_utc_iso(),
        "inputs": {
            "env_path": str(env_path),
            "log_dir": str(log_dir),
            "session_dir": str(session_dir),
            "timeout_sec": timeout_sec,
            "large_log_mb": large_log_mb,
            "include_remediation": include_remediation,
            "apply_remediation": apply_remediation,
        },
        "probes": {
            "endpoints": [_endpoint_probe_to_report(probe) for probe in endpoint_results],
            "processes": asdict(process_result),
            "env": env_scan,
            "logs": log_scan,
            "sessions": session_scan,
        },
        "risks": [asdict(risk) for risk in risks],
    }
    if include_remediation:
        remediation = await run_remediation_plan(
            env_path=env_path,
            log_dir=log_dir,
            session_dir=session_dir,
            large_log_mb=large_log_mb,
            dry_run=not apply_remediation,
        )
        report["remediation"] = asdict(remediation)
    return report


def _parse_endpoint(raw: str) -> EndpointSpec:
    """Парсит CLI endpoint в формате name=url."""

    if "=" not in raw:
        raise argparse.ArgumentTypeError("endpoint должен быть в формате name=url")
    name, url = raw.split("=", 1)
    name = name.strip()
    url = url.strip()
    if not name or not url:
        raise argparse.ArgumentTypeError("endpoint должен иметь непустые name и url")
    return EndpointSpec(name=name, url=url)


def _write_report(report: dict[str, Any]) -> Path:
    """Сохраняет timestamped и latest отчёт."""

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    report_path = ARTIFACTS_DIR / f"krab_runtime_risk_audit_{stamp}.json"
    latest_path = ARTIFACTS_DIR / "krab_runtime_risk_audit_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return report_path


def _build_parser() -> argparse.ArgumentParser:
    """Создаёт CLI parser без side effects."""

    parser = argparse.ArgumentParser(description="Аудит рисков текущего Krab runtime.")
    parser.add_argument(
        "--endpoint",
        action="append",
        type=_parse_endpoint,
        default=None,
        help="Endpoint в формате name=url. Можно передать несколько раз.",
    )
    parser.add_argument("--env-path", type=Path, default=ROOT / ".env", help="Путь к env-файлу.")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Директория логов.")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=DEFAULT_SESSION_DIR,
        help="Директория Telegram session-файлов.",
    )
    parser.add_argument("--timeout-sec", type=float, default=3.0, help="Таймаут HTTP probe.")
    parser.add_argument("--large-log-mb", type=int, default=50, help="Порог большого лога в MB.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Вернуть exit code 1, если найден high-risk. Без strict аудит только репортит.",
    )
    parser.add_argument(
        "--plan-remediation",
        action="store_true",
        help="Добавить в отчёт dry-run план: env template, rotation логов, cleanup session backup.",
    )
    parser.add_argument(
        "--apply-remediation",
        action="store_true",
        help="Применить безопасную remediation-фазу: записать .env.template, gzip-copytruncate больших логов, удалить старые backup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint для запуска из Terminal или `.command`."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    endpoints = args.endpoint or [
        EndpointSpec("panel", "http://127.0.0.1:8080/api/health/lite"),
        EndpointSpec("gateway", "http://127.0.0.1:18789/health"),
    ]

    report = asyncio.run(
        run_audit(
            endpoints=endpoints,
            env_path=args.env_path,
            log_dir=args.log_dir,
            session_dir=args.session_dir,
            timeout_sec=max(0.5, float(args.timeout_sec)),
            large_log_mb=max(1, int(args.large_log_mb)),
            include_remediation=bool(args.plan_remediation or args.apply_remediation),
            apply_remediation=bool(args.apply_remediation),
        )
    )
    report_path = _write_report(report)

    print("🦀 Krab Runtime Risk Audit")
    print(f"- endpoints: {len(report['probes']['endpoints'])}")
    print(f"- risks: {len(report['risks'])}")
    for risk in report["risks"]:
        print(f"  [{risk['severity']}] {risk['code']}: {risk['title']}")
    if "remediation" in report:
        remediation = report["remediation"]
        mode = "apply" if args.apply_remediation else "dry-run"
        print(f"- remediation: {mode}")
        print(f"  env_template_written: {remediation['env_template'].get('written', False)}")
        print(f"  log_rotation_items: {len(remediation['log_rotation'])}")
        print(f"  session_removed: {len(remediation['session_cleanup'].get('removed', []))}")
        if remediation.get("errors"):
            print(f"  errors: {len(remediation['errors'])}")
    print(f"- ok: {report['ok']}")
    print(f"- report: {report_path}")

    if args.strict and not bool(report["ok"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

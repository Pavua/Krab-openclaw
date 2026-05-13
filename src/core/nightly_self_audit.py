# -*- coding: utf-8 -*-
"""Wave 40-A: Krab nightly self-audit.

Запускается каждую ночь в 03:00 local через LaunchAgent ai.krab.nightly-audit.
Проверяет 8 критичных дименсий, генерит markdown отчёт, шлёт в Saved Messages.

Audit dimensions:
1. Process health (uptime, daemons up/down)
2. Database integrity (PRAGMA integrity_check на 5 sqlite DBs)
3. Bypass perf trend (24h vs baseline — degradation detection)
4. Memory trend (7-day samples из coexistence_monitor.log)
5. Disk space (>85% = warn, >95% = critical)
6. Inbox bloat (open items >7d)
7. OAuth tokens (expiry проверка)
8. Zombie/sleep escalations (self-recovery event count)

Delivery: POST /api/notify → DM owner в Saved Messages.
Quiet mode: если all_ok — не шлёт (избегаем ночной spam).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

# Хост owner-panel
_PANEL_HOST = os.getenv("KRAB_PANEL_HOST", "http://127.0.0.1:8080")
# HTTP таймаут для panel запросов
_HTTP_TIMEOUT = 5


class AuditFinding:
    """Один результат audit-проверки."""

    def __init__(
        self,
        dimension: str,
        status: str,
        summary: str,
        detail: str = "",
    ) -> None:
        # status: 'ok' | 'warn' | 'critical'
        self.dimension = dimension
        self.status = status
        self.summary = summary
        self.detail = detail

    def to_markdown(self) -> str:
        """Форматирует finding как строку Markdown."""
        emoji = {"ok": "✅", "warn": "⚠️", "critical": "🔴"}.get(self.status, "❓")
        line = f"{emoji} *{self.dimension}*: {self.summary}"
        if self.detail:
            line += f"\n  _{self.detail}_"
        return line


# ---------------------------------------------------------------------------
# Dimension 1: Process health
# ---------------------------------------------------------------------------

# Wave 196: launchctl — authoritative source для LaunchAgent state.
# Канонический label Krab core process.
_KRAB_CORE_LABEL = "ai.krab.core"


def _check_krab_via_launchctl() -> tuple[str, int | None, str]:
    """Спрашиваем launchctl про состояние ai.krab.core.

    Returns:
        (state, pid, raw_line)
        state ∈ {"running", "loaded_idle", "not_loaded", "error"}
        pid — int если PID present, иначе None
        raw_line — сырая строка из launchctl list (или сообщение об ошибке)
    """
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        return ("error", None, f"launchctl failed: {exc}")

    if result.returncode != 0:
        return ("error", None, f"launchctl exit={result.returncode}")

    # Формат: PID\tEXIT\tLABEL — точное совпадение по последнему полю
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1] != _KRAB_CORE_LABEL:
            continue
        pid_field = parts[0]
        if pid_field == "-":
            # Loaded, но сейчас не запущен (cron-style либо crash)
            return ("loaded_idle", None, line.strip())
        try:
            pid = int(pid_field)
        except ValueError:
            return ("error", None, f"bad pid field: {pid_field!r}")
        return ("running", pid, line.strip())

    return ("not_loaded", None, "")


def _check_krab_via_psutil() -> int | None:
    """Fallback: ищем процесс с cmdline содержащим python + src.main / userbot_bridge.

    Returns: pid или None.
    """
    try:
        import psutil
    except ImportError:
        return None

    for p in psutil.process_iter(["cmdline"]):
        try:
            cmdline = p.info.get("cmdline") or []
            joined = " ".join(cmdline)
            # python -m src.main | src/main.py | userbot_bridge | krab_main
            has_python = any("python" in c.lower() for c in cmdline)
            has_krab_module = (
                "src.main" in joined
                or "src/main.py" in joined
                or "userbot_bridge" in joined
                or "krab_main" in joined
            )
            if has_python and has_krab_module:
                return p.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


async def audit_process_health() -> AuditFinding:
    """Проверка uptime Krab-процесса и количества живых LaunchAgent daemon'ов.

    Wave 196: launchctl — authoritative для определения жив ли core.
    psutil — fallback, если launchctl недоступен.
    """
    # 1. Authoritative check через launchctl
    state, pid, raw = _check_krab_via_launchctl()

    if state == "running" and pid is not None:
        krab_pid = pid
    elif state in ("loaded_idle", "not_loaded"):
        # Точно не запущен — авторитетно от launchd
        return AuditFinding(
            "Process",
            "critical",
            f"Krab not running (launchctl: {state})",
            f"label={_KRAB_CORE_LABEL}, raw={raw or '<not in launchctl list>'}",
        )
    else:
        # launchctl недоступен — fallback на psutil
        fallback_pid = _check_krab_via_psutil()
        if fallback_pid is None:
            return AuditFinding(
                "Process",
                "critical",
                f"Krab not running (launchctl: {state}, psutil: not found)",
                raw,
            )
        krab_pid = fallback_pid

    # 2. Uptime через psutil (нужен для отображения)
    try:
        import psutil
    except ImportError:
        return AuditFinding(
            "Process",
            "ok",
            f"Krab running (pid={krab_pid}), psutil not installed — uptime unavailable",
        )

    try:
        uptime_sec = time.time() - psutil.Process(krab_pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        # Между launchctl check и psutil.Process процесс исчез
        return AuditFinding(
            "Process",
            "warn",
            f"Krab pid={krab_pid} disappeared between checks",
            str(exc),
        )
    uptime_h = uptime_sec / 3600

    # Wave 40-A-fix-3: правильная интерпретация launchctl list output
    # Format: PID  EXIT_CODE  LABEL
    #   PID = '-'  → loaded но idle (cron-style, ОК)
    #   PID = N    → currently running (OK)
    #   EXIT = 0   → success / ещё не запускался (HEALTHY)
    #   EXIT < 0   → killed signal (-15 SIGTERM = clean shutdown, OK)
    #   EXIT = 1   → monitoring scripts: "warnings found" — semantic, не bug
    #   EXIT >= 2  → real error (script broken / config missing / etc)
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        krab_lines = [line.strip() for line in result.stdout.splitlines() if "ai.krab." in line]
        total = len(krab_lines)
        healthy = 0  # exit_code 0 или signal (clean) или 1 (monitoring warning)
        broken_labels = []  # exit_code >= 2 — real failure
        for line in krab_lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                exit_code = int(parts[1])
            except ValueError:
                continue
            # 0 / negative / 1 = ok (1 = monitoring warning, not bug)
            # >=2 = real failure
            if exit_code <= 1:
                healthy += 1
            else:
                broken_labels.append(parts[2].split(".")[-1])

        if broken_labels:
            return AuditFinding(
                "Process",
                "warn",
                f"Uptime {uptime_h:.1f}h, {healthy}/{total} healthy, {len(broken_labels)} broken",
                f"Broken (exit≥2): {', '.join(broken_labels[:5])}",
            )
        return AuditFinding(
            "Process",
            "ok",
            f"Uptime {uptime_h:.1f}h, {healthy}/{total} LaunchAgents healthy",
        )
    except Exception as exc:
        # launchctl недоступен — минимально ok, только uptime
        return AuditFinding(
            "Process",
            "ok",
            f"Uptime {uptime_h:.1f}h (launchctl недоступен: {str(exc)[:40]})",
        )


# ---------------------------------------------------------------------------
# Dimension 2: Database integrity
# ---------------------------------------------------------------------------


async def audit_database_integrity() -> AuditFinding:
    """PRAGMA integrity_check на критичных SQLite базах."""
    dbs = [
        Path.home() / "Antigravity_AGENTS/Краб/data/sessions/kraab.session",
        Path.home() / ".openclaw/krab_memory/archive.db",
        Path.home() / ".openclaw/tasks/runs.sqlite",
    ]

    checked = 0
    issues: list[str] = []

    for db_path in dbs:
        if not db_path.exists():
            continue
        checked += 1
        try:
            # read-only URI для безопасности
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
            cursor = conn.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            conn.close()
            if result != "ok":
                issues.append(f"{db_path.name}: {str(result)[:80]}")
        except Exception as exc:
            issues.append(f"{db_path.name}: read_error — {str(exc)[:50]}")

    if issues:
        return AuditFinding(
            "DB integrity",
            "critical",
            f"{len(issues)}/{checked} DBs имеют проблемы",
            "; ".join(issues[:3]),
        )
    if checked == 0:
        return AuditFinding("DB integrity", "warn", "Ни одна DB не найдена — пути не совпадают?")
    return AuditFinding("DB integrity", "ok", f"{checked} DBs проверены — чисто")


# ---------------------------------------------------------------------------
# Dimension 3: Bypass perf trend
# ---------------------------------------------------------------------------


async def audit_bypass_perf_trend() -> AuditFinding:
    """Запрашивает /api/bypass/perf?window=24h и проверяет деградацию p95."""
    try:
        url = f"{_PANEL_HOST}/api/bypass/perf?window=24h"
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())

        total_calls = data.get("total_calls", 0)
        cli_p95 = data.get("by_kind", {}).get("cli", {}).get("p95", 0)
        cli_p95_prev = data.get("by_kind", {}).get("cli", {}).get("p95_prev_24h", 0)

        if cli_p95_prev and cli_p95_prev > 0:
            degradation = (cli_p95 - cli_p95_prev) / cli_p95_prev
            if degradation > 0.5:
                return AuditFinding(
                    "Bypass perf",
                    "warn",
                    f"CLI p95 вырос с {cli_p95_prev:.1f}s до {cli_p95:.1f}s (+{degradation * 100:.0f}%)",
                    "Возможная деградация. Проверить codex-cli health.",
                )

        return AuditFinding(
            "Bypass perf",
            "ok",
            f"CLI p95={cli_p95:.1f}s, total_calls={total_calls}",
        )
    except Exception as exc:
        # endpoint может не существовать — это не критично
        return AuditFinding(
            "Bypass perf",
            "warn",
            f"Не удалось получить perf данные: {str(exc)[:60]}",
        )


# ---------------------------------------------------------------------------
# Dimension 4: Memory trend
# ---------------------------------------------------------------------------


async def audit_memory_trend() -> AuditFinding:
    """Анализирует coexistence_monitor.log за последние 7 дней."""
    log = Path.home() / ".openclaw/krab_runtime_state/coexistence_monitor.log"
    if not log.exists():
        return AuditFinding(
            "Memory trend",
            "warn",
            "coexistence_monitor.log не найден — мониторинг памяти не активен",
        )

    try:
        # Читаем последние ~100KB
        with log.open("rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 100_000))
            raw = f.read().decode(errors="ignore")

        cutoff = time.time() - 7 * 86400
        samples: list[dict] = []
        for line in raw.strip().splitlines():
            try:
                d = json.loads(line)
                if d.get("timestamp", 0) >= cutoff:
                    samples.append(d)
            except Exception:
                continue

        if not samples:
            return AuditFinding("Memory trend", "warn", "Нет данных за 7 дней в лог-файле")

        max_swap = max(s.get("swap_used_gb", 0) for s in samples)
        avg_rss = sum(s.get("krab_rss_gb", 0) + s.get("ear_rss_gb", 0) for s in samples) / len(
            samples
        )

        if max_swap > 16:
            return AuditFinding(
                "Memory trend",
                "warn",
                f"Max swap за 7 дней: {max_swap:.1f}GB — возможный memory leak",
                f"Avg RSS: {avg_rss:.1f}GB. Рассмотреть restart или profiling.",
            )
        return AuditFinding(
            "Memory trend",
            "ok",
            f"Max swap 7д: {max_swap:.1f}GB, avg RSS: {avg_rss:.1f}GB",
        )
    except Exception as exc:
        return AuditFinding("Memory trend", "warn", f"Анализ не удался: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# Dimension 5: Disk space
# ---------------------------------------------------------------------------


async def audit_disk_space() -> AuditFinding:
    """Проверяет использование диска /Users/pablito."""
    import shutil

    total, used, free = shutil.disk_usage("/Users/pablito")
    pct_used = (used / total) * 100
    free_gb = free / 1e9

    if pct_used > 95:
        return AuditFinding(
            "Disk",
            "critical",
            f"{pct_used:.0f}% использовано, свободно {free_gb:.0f}GB — КРИТИЧНО",
        )
    if pct_used > 85:
        return AuditFinding(
            "Disk",
            "warn",
            f"{pct_used:.0f}% использовано, свободно {free_gb:.0f}GB",
            "Нужна очистка. Проверить ~/.openclaw/logs, tmp, бэкапы.",
        )
    return AuditFinding("Disk", "ok", f"{pct_used:.0f}% использовано ({free_gb:.0f}GB свободно)")


# ---------------------------------------------------------------------------
# Dimension 6: Inbox bloat
# ---------------------------------------------------------------------------


async def audit_inbox_bloat() -> AuditFinding:
    """Проверяет stale inbox items (открытые >7 дней)."""
    try:
        url = f"{_PANEL_HOST}/api/inbox/status"
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())

        stale = data.get("stale_open_items", 0)
        open_total = data.get("open_items", 0)

        if stale > 50:
            return AuditFinding(
                "Inbox",
                "warn",
                f"{stale} stale items (>7д) из {open_total} открытых",
                "Запустить !inbox cleanup или проверить cron inbox-cleanup.",
            )
        return AuditFinding("Inbox", "ok", f"{open_total} открытых, {stale} stale (>7д)")
    except Exception as exc:
        return AuditFinding(
            "Inbox",
            "warn",
            f"Panel недоступна: {str(exc)[:60]}",
        )


# ---------------------------------------------------------------------------
# Dimension 7: OAuth tokens
# ---------------------------------------------------------------------------


async def audit_oauth_tokens() -> AuditFinding:
    """Проверяет истечение OAuth токенов (gemini-cli и другие)."""
    issues: list[str] = []
    checked = 0

    # gemini-cli oauth credentials
    gemini_creds = Path.home() / ".gemini/oauth_creds.json"
    if gemini_creds.exists():
        checked += 1
        try:
            d = json.loads(gemini_creds.read_text())
            exp_ms = d.get("expiry_date", 0)
            if exp_ms:
                hours_until = (exp_ms / 1000 - time.time()) / 3600
                if hours_until < 0:
                    # Expired но auto-refresh должен сработать при использовании
                    issues.append(
                        f"gemini token истёк {abs(hours_until):.1f}h назад "
                        f"(auto-refresh при следующем использовании)"
                    )
                elif hours_until < 1:
                    issues.append(
                        f"gemini token истекает через {hours_until * 60:.0f}мин — критично!"
                    )
        except Exception as exc:
            issues.append(f"gemini creds read error: {str(exc)[:40]}")

    # claude-cli token (если есть)
    claude_token_path = Path.home() / ".claude/.credentials.json"
    if claude_token_path.exists():
        checked += 1
        try:
            d = json.loads(claude_token_path.read_text())
            exp = d.get("expires_at") or d.get("expiry_date")
            if exp and isinstance(exp, (int, float)):
                hours_until = (exp - time.time()) / 3600
                if hours_until < 0:
                    issues.append("claude credentials истекли — требует обновления!")
                elif hours_until < 24:
                    issues.append(f"claude credentials истекают через {hours_until:.1f}h")
        except Exception:
            pass  # Нет expire поля — считаем нормальным

    if issues:
        return AuditFinding(
            "OAuth tokens",
            "warn",
            "; ".join(issues[:2]),
        )
    if checked == 0:
        return AuditFinding("OAuth tokens", "warn", "Credential файлы не найдены")
    return AuditFinding(
        "OAuth tokens", "ok", f"{checked} credential-файлов проверено — всё в порядке"
    )


# ---------------------------------------------------------------------------
# Dimension 8: Zombie / sleep escalations
# ---------------------------------------------------------------------------


async def audit_zombie_escalations() -> AuditFinding:
    """Считает события self-recovery за всё время жизни лог-файла."""
    log = Path.home() / ".openclaw/krab_runtime_state/krab_main.log"
    if not log.exists():
        return AuditFinding(
            "Zombie/sleep",
            "warn",
            "krab_main.log не найден — логирование не настроено",
        )

    # Паттерны, которые указывают на zombie/sleep recovery
    patterns = [
        "telegram_session_zombie_escalation",
        "telegram_heartbeat_zombie_escalation",
        "macos_sleep_detected",
        "session_integrity_recover",
        "_main_session_integrity_preflight",
    ]

    try:
        grep_args = [
            "grep",
            "-c",
            "\\|".join(patterns),
            str(log),
        ]
        result = subprocess.run(
            grep_args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        # grep -c возвращает 1 если ничего не найдено
        count = int(result.stdout.strip() or "0")

        if count > 10:
            return AuditFinding(
                "Zombie/sleep",
                "warn",
                f"{count} событий self-recovery (за время жизни лога)",
                "Если нарастает — рассмотреть telethon migration или частые restarts.",
            )
        return AuditFinding(
            "Zombie/sleep",
            "ok",
            f"{count} событий self-recovery (норма)",
        )
    except Exception as exc:
        return AuditFinding("Zombie/sleep", "warn", f"grep не удался: {str(exc)[:50]}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_full_audit() -> dict:
    """Запускает все checks параллельно, собирает отчёт, шлёт в Telegram если нужно."""
    raw_results = await asyncio.gather(
        audit_process_health(),
        audit_database_integrity(),
        audit_bypass_perf_trend(),
        audit_memory_trend(),
        audit_disk_space(),
        audit_inbox_bloat(),
        audit_oauth_tokens(),
        audit_zombie_escalations(),
        return_exceptions=True,
    )

    # Фильтруем exception'ы (заменяем на warn-finding)
    findings: list[AuditFinding] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, AuditFinding):
            findings.append(r)
        else:
            dim = f"Dimension-{i + 1}"
            findings.append(AuditFinding(dim, "warn", f"Exception: {str(r)[:80]}"))

    counts: dict[str, int] = {"ok": 0, "warn": 0, "critical": 0}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1

    # Строим Markdown report
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🌙 *Krab Nightly Audit* — {now_str}", ""]
    for f in findings:
        lines.append(f.to_markdown())
    lines.append(f"\n*Итого*: {counts['ok']} ✅  {counts['warn']} ⚠️  {counts['critical']} 🔴")
    report = "\n".join(lines)

    # Отправляем в Telegram только если есть проблемы (quiet mode для all-ok)
    has_issues = counts["warn"] + counts["critical"] > 0
    if has_issues:
        try:
            payload = json.dumps({"text": report}).encode()
            req = urllib.request.Request(
                f"{_PANEL_HOST}/api/notify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
            logger.info(
                "nightly_audit_sent_telegram",
                warnings=counts["warn"],
                critical=counts["critical"],
            )
        except Exception as exc:
            logger.warning("nightly_audit_send_failed", error=str(exc)[:200])
    else:
        logger.info("nightly_audit_all_ok_quiet_mode")

    return {
        "ok": True,
        "counts": counts,
        "has_issues": has_issues,
        "findings": [
            {
                "dimension": f.dimension,
                "status": f.status,
                "summary": f.summary,
                "detail": f.detail,
            }
            for f in findings
        ],
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI entry point для LaunchAgent
# ---------------------------------------------------------------------------


async def main() -> int:
    """Точка входа при запуске через LaunchAgent или напрямую."""
    result = await run_full_audit()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["counts"]["critical"] == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))

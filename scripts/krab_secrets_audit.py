#!/usr/bin/env python3
"""Wave 100: Secrets audit — сканирует git history и логи на leaked API keys/tokens.

Запускается weekly через LaunchAgent ai.krab.secrets-audit (Sun 09:00).

Цели сканирования:
1. git log --all -p --since='7 days ago' — diff недавних коммитов
2. logs/*.log mtime < 7 days — recent log files

Whitelist:
- .env, .env.*, .env.bak* — ожидаемо содержат ключи (gitignored)
- Любой path в whitelist прерывает scan для конкретного hit

Output JSON: {timestamp, total_scanned, leaks: [...]} в stdout +
~/.openclaw/krab_runtime_state/secrets_audit.json.

Метрика: krab_secrets_audit_leaks{pattern} (Gauge) — публикуется
через POST /api/metric если panel жив (silent skip при отсутствии).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- Regex паттерны секретов: (имя, compiled regex) ---
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "google_api": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "anthropic_api": re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),
    "openai_api": re.compile(r"sk-[a-zA-Z0-9]{40,}"),
    "google_oauth": re.compile(r"ya29\.[a-zA-Z0-9_\-]+"),
    "gitlab_token": re.compile(r"glpat-[a-zA-Z0-9_\-]{20,}"),
    "github_token": re.compile(r"ghp_[a-zA-Z0-9]{36}"),
}

# --- Whitelist: пути, в которых наличие ключей ожидаемо ---
WHITELIST_BASENAMES: tuple[str, ...] = (".env",)
WHITELIST_PREFIXES: tuple[str, ...] = (
    ".env.",  # .env.bak, .env.local, .env.production и т.п.
)


def is_whitelisted(path: str) -> bool:
    """Returns True если path — ожидаемый holder ключей (skip scan)."""
    base = os.path.basename(path.strip())
    if base in WHITELIST_BASENAMES:
        return True
    for prefix in WHITELIST_PREFIXES:
        if base.startswith(prefix):
            return True
    return False


def redact(snippet: str, max_len: int = 120) -> str:
    """Маскирует секрет в snippet'е (оставляем первые 6 символов + '***')."""
    redacted = snippet
    for pattern in SECRET_PATTERNS.values():
        redacted = pattern.sub(lambda m: m.group(0)[:6] + "***REDACTED***", redacted)
    if len(redacted) > max_len:
        redacted = redacted[:max_len] + "…"
    return redacted.strip()


@dataclass
class Leak:
    source: str  # "git" или "log"
    file: str
    line: int
    pattern: str
    redacted_snippet: str


@dataclass
class AuditReport:
    timestamp: str
    total_scanned: int  # количество rows/lines scanned
    leaks: list[Leak] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_scanned": self.total_scanned,
            "leaks": [asdict(leak) for leak in self.leaks],
        }


def scan_text(text: str, source: str, file: str, start_line: int = 1) -> list[Leak]:
    """Сканирует text построчно, возвращает все совпавшие leaks."""
    leaks: list[Leak] = []
    for offset, line in enumerate(text.splitlines()):
        for pattern_name, regex in SECRET_PATTERNS.items():
            if regex.search(line):
                leaks.append(
                    Leak(
                        source=source,
                        file=file,
                        line=start_line + offset,
                        pattern=pattern_name,
                        redacted_snippet=redact(line),
                    )
                )
    return leaks


def run_git_log(
    repo_dir: Path,
    since: str = "7 days ago",
    runner=subprocess.run,
) -> str:
    """Запускает `git log --all -p --since=<since>` в repo_dir."""
    try:
        result = runner(
            ["git", "log", "--all", "-p", f"--since={since}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def scan_git_history(
    repo_dir: Path, since: str = "7 days ago", runner=subprocess.run
) -> tuple[list[Leak], int]:
    """Сканирует git diff diffs и возвращает (leaks, lines_scanned).

    Парсит `diff --git a/<path> b/<path>` headers для отслеживания current file
    и пропускает diffs whitelisted files.
    """
    raw = run_git_log(repo_dir, since=since, runner=runner)
    if not raw:
        return [], 0

    leaks: list[Leak] = []
    current_file: str = "<unknown>"
    skip_current = False
    lines_scanned = 0

    for line in raw.splitlines():
        lines_scanned += 1
        # Обновляем current_file при diff header
        if line.startswith("diff --git "):
            # формат: diff --git a/path/to/file b/path/to/file
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[2][2:] if parts[2].startswith("a/") else parts[2]
                skip_current = is_whitelisted(current_file)
            continue

        if skip_current:
            continue

        # Сканируем только added lines (начинаются с '+', не с '+++')
        if not line.startswith("+") or line.startswith("+++"):
            continue

        content = line[1:]
        for pattern_name, regex in SECRET_PATTERNS.items():
            if regex.search(content):
                leaks.append(
                    Leak(
                        source="git",
                        file=current_file,
                        line=lines_scanned,
                        pattern=pattern_name,
                        redacted_snippet=redact(content),
                    )
                )

    return leaks, lines_scanned


def scan_log_files(logs_dir: Path, max_age_days: int = 7) -> tuple[list[Leak], int]:
    """Сканирует *.log в logs_dir с mtime < max_age_days."""
    if not logs_dir.exists() or not logs_dir.is_dir():
        return [], 0

    cutoff_ts = _dt.datetime.now().timestamp() - max_age_days * 86400
    leaks: list[Leak] = []
    lines_scanned = 0

    for log_path in sorted(logs_dir.glob("*.log")):
        try:
            if log_path.stat().st_mtime < cutoff_ts:
                continue
            if is_whitelisted(str(log_path)):
                continue
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    lines_scanned += 1
                    for pattern_name, regex in SECRET_PATTERNS.items():
                        if regex.search(line):
                            leaks.append(
                                Leak(
                                    source="log",
                                    file=str(log_path),
                                    line=line_no,
                                    pattern=pattern_name,
                                    redacted_snippet=redact(line),
                                )
                            )
        except OSError:
            continue

    return leaks, lines_scanned


def publish_metric(leaks: list[Leak], panel_url: str) -> bool:
    """Публикует krab_secrets_audit_leaks{pattern}=count в panel (best-effort)."""
    counts: dict[str, int] = {name: 0 for name in SECRET_PATTERNS}
    for leak in leaks:
        counts[leak.pattern] = counts.get(leak.pattern, 0) + 1

    payload = {
        "name": "krab_secrets_audit_leaks",
        "type": "gauge",
        "values": counts,
    }
    try:
        req = urllib.request.Request(
            f"{panel_url.rstrip('/')}/api/metric",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def run_audit(
    repo_dir: Path,
    logs_dir: Path,
    since: str = "7 days ago",
    max_log_age_days: int = 7,
    git_runner=subprocess.run,
) -> AuditReport:
    """Главный entrypoint: scan + aggregate в AuditReport."""
    git_leaks, git_scanned = scan_git_history(repo_dir, since=since, runner=git_runner)
    log_leaks, log_scanned = scan_log_files(logs_dir, max_age_days=max_log_age_days)

    report = AuditReport(
        timestamp=_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        total_scanned=git_scanned + log_scanned,
        leaks=git_leaks + log_leaks,
    )
    return report


def _default_state_dir() -> Path:
    return Path(
        os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Krab secrets audit (Wave 100)")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--logs", default=None, help="Logs dir (default <repo>/logs)")
    parser.add_argument("--since", default="7 days ago")
    parser.add_argument("--max-log-age-days", type=int, default=7)
    parser.add_argument("--panel-url", default=os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args(argv)

    repo_dir = Path(args.repo).resolve()
    logs_dir = Path(args.logs).resolve() if args.logs else repo_dir / "logs"

    report = run_audit(
        repo_dir=repo_dir,
        logs_dir=logs_dir,
        since=args.since,
        max_log_age_days=args.max_log_age_days,
    )

    output = report.to_dict()
    print(json.dumps(output, ensure_ascii=False, indent=2))

    # Persist в runtime state
    state_dir = _default_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "secrets_audit.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

    # Опубликовать метрику
    if not args.no_publish:
        publish_metric(report.leaks, args.panel_url)

    # Exit 1 если найдены leaks (для alerting в launchd / CI)
    return 1 if report.leaks else 0


if __name__ == "__main__":
    sys.exit(main())

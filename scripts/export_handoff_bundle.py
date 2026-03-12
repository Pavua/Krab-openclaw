#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Экспортирует Anti-413 handoff-пакет для безопасной миграции в новый чат.

Что делает:
1) Собирает machine-readable срез runtime в JSON.
2) Строит матрицу известных проблем по логам.
3) Копирует ключевые документы миграции в timestamp-бандл.

Зачем:
- При переполнении контекста/ошибке 413 можно продолжить работу в новом окне
  без потери фактов о текущем состоянии системы.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.lm_studio_auth import build_lm_studio_auth_headers

DOCS_DIR = ROOT / "docs"
ARTIFACTS_DIR = ROOT / "artifacts"
NOW = datetime.now(timezone.utc)
STAMP = NOW.strftime("%Y%m%d_%H%M%S")
BUNDLE_DIR = ARTIFACTS_DIR / f"handoff_{STAMP}"
PROJECT_READINESS = "~99%"
RECOVERY_BRANCHES = (
    "codex/live-8080-parallelism-acceptance",
    "codex/release-gate-checklist",
    "codex/pablito-live-parallelism-helper",
    "codex/signal-alert-ssl-hardening",
    "codex/web-runtime-smoke-hardening",
    "codex/handoff-bundle-polish",
)


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _run(cmd: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001 - это диагностический скрипт
        return {"ok": False, "returncode": 1, "stdout": "", "stderr": str(exc)}


def _git_stdout(args: list[str]) -> str:
    """Возвращает stdout git-команды или пустую строку, если ветка/ревизия недоступна."""
    return str(_run(["git", *args]).get("stdout", "") or "").strip()


def _http_json(url: str, *, timeout: float = 4.0) -> dict[str, Any]:
    lm_base = str(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234") or "").strip().rstrip("/")
    headers = {"Accept": "application/json"}
    if lm_base and (url.startswith(f"{lm_base}/api/v1/") or url.startswith(f"{lm_base}/v1/")):
        headers = build_lm_studio_auth_headers(include_json_accept=True)
    req = Request(url, method="GET", headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - локальные health URL
            raw = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    return {"ok": True, "status": resp.status, "json": json.loads(raw), "raw": ""}
                except json.JSONDecodeError:
                    return {"ok": False, "status": resp.status, "json": None, "raw": raw}
            return {"ok": True, "status": resp.status, "json": None, "raw": raw[:1000]}
    except URLError as exc:
        return {"ok": False, "status": None, "json": None, "raw": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "json": None, "raw": str(exc)}


def _tail(path: Path, *, max_lines: int = 400) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


def _tail_recent(path: Path, *, max_lines: int, max_age_hours: float) -> str:
    """
    Возвращает tail файла только если лог действительно свежий.

    Зачем:
    - не подтягивать в handoff матрицу архивные инциденты из старых логов.
    """
    if not path.exists():
        return ""
    try:
        age_sec = NOW.timestamp() - path.stat().st_mtime
        if age_sec > (max_age_hours * 3600):
            return ""
    except OSError:
        return ""
    return _tail(path, max_lines=max_lines)


def _slice_since_last_marker(text: str, *, markers: tuple[str, ...]) -> str:
    """
    Возвращает лог только от последнего runtime-маркера старта.

    Почему:
    - матрица known issues должна отражать текущее состояние спринта,
      а не исторические инциденты прошлых запусков.
    """
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    markers_low = tuple(m.lower() for m in markers if m)
    if not markers_low:
        return str(text or "")

    last_idx = -1
    for idx, line in enumerate(lines):
        low = str(line or "").lower()
        if any(marker in low for marker in markers_low):
            last_idx = idx
    if last_idx < 0:
        return str(text or "")
    return "\n".join(lines[last_idx:])


def _count_pattern(text: str, pattern: str) -> int:
    return text.lower().count(pattern.lower())


def _session_state() -> dict[str, Any]:
    session_name = (os.getenv("TELEGRAM_SESSION_NAME", "kraab") or "kraab").strip()
    session_dir = ROOT / "data" / "sessions"
    session_file = session_dir / f"{session_name}.session"
    wal_file = session_dir / f"{session_name}.session-wal"
    shm_file = session_dir / f"{session_name}.session-shm"

    db_ok = False
    db_error = ""
    if session_file.exists():
        try:
            conn = sqlite3.connect(str(session_file), timeout=1)
            cur = conn.cursor()
            cur.execute("PRAGMA quick_check;")
            result = cur.fetchone()
            db_ok = bool(result and result[0] == "ok")
            conn.close()
        except Exception as exc:  # noqa: BLE001
            db_error = str(exc)

    return {
        "session_name": session_name,
        "session_dir": str(session_dir),
        "session_exists": session_file.exists(),
        "session_size_bytes": session_file.stat().st_size if session_file.exists() else 0,
        "wal_exists": wal_file.exists(),
        "shm_exists": shm_file.exists(),
        "sqlite_quick_check_ok": db_ok,
        "sqlite_error": db_error,
    }


def _openclaw_channels_snapshot() -> dict[str, Any]:
    """
    Снимает channel-матрицу напрямую из ~/.openclaw/openclaw.json.

    Нужна для динамического контроля всех активных каналов без hardcoded-списков.
    """
    openclaw_path = Path.home() / ".openclaw" / "openclaw.json"
    base = {
        "path": str(openclaw_path),
        "configured": [],
        "enabled": [],
        "details": {},
        "error": "",
    }
    if not openclaw_path.exists():
        base["error"] = "openclaw_json_not_found"
        return base
    try:
        payload = json.loads(openclaw_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        base["error"] = str(exc)
        return base

    channels = payload.get("channels", {})
    if not isinstance(channels, dict):
        base["error"] = "channels_not_dict"
        return base

    configured: list[str] = []
    enabled: list[str] = []
    details: dict[str, Any] = {}
    for raw_name, cfg in channels.items():
        name = str(raw_name or "").strip().lower()
        if not name or not isinstance(cfg, dict):
            continue
        configured.append(name)
        enabled_raw = cfg.get("enabled")
        is_enabled = bool(enabled_raw) if isinstance(enabled_raw, bool) else True
        if is_enabled:
            enabled.append(name)
        details[name] = {
            "enabled": is_enabled,
            "dm_policy": str(cfg.get("dmPolicy", "") or "").strip() or None,
            "group_policy": str(cfg.get("groupPolicy", "") or "").strip() or None,
            "streaming": str(cfg.get("streaming", "") or "").strip() or None,
        }

    base["configured"] = configured
    base["enabled"] = enabled
    base["details"] = details
    return base


@dataclass
class IssueRule:
    code: str
    title: str
    pattern: str
    threshold: int = 1


def _build_known_issues_matrix(log_text: str) -> tuple[list[dict[str, Any]], str]:
    rules = [
        IssueRule("telegram_auth_key", "Telegram auth key протухла", "auth key not found"),
        IssueRule("telegram_sqlite_io", "Telegram sqlite disk I/O", "sqlite3.operationalerror: disk i/o error"),
        IssueRule("no_models_loaded", "LM Studio: No models loaded", "no models loaded"),
        IssueRule("lm_empty_message", "LM Studio: EMPTY MESSAGE", "empty message"),
        IssueRule("lm_model_crash", "LM Studio: model crashed", "model has crashed without additional information"),
        IssueRule("cloud_unauthorized", "Cloud auth 401", "unauthorized"),
        IssueRule("tg_message_id_invalid", "Telegram MESSAGE_ID_INVALID", "message_id_invalid"),
        IssueRule("tg_message_empty", "Telegram MESSAGE_EMPTY", "message_empty"),
        IssueRule("tg_not_modified", "Telegram MESSAGE_NOT_MODIFIED", "message_not_modified"),
        IssueRule("photo_stuck", "Фото-путь: зависание на разглядывании", "разглядываю фото"),
    ]
    rows: list[dict[str, Any]] = []
    md_lines = [
        "# Known Issues Matrix",
        "",
        f"- Сгенерировано: `{NOW.isoformat()}`",
        "",
        "| Код | Проблема | Вхождений (tail) | Статус |",
        "|---|---|---:|---|",
    ]
    for rule in rules:
        count = _count_pattern(log_text, rule.pattern)
        status = "ACTIVE" if count >= rule.threshold else "clear"
        rows.append(
            {
                "code": rule.code,
                "title": rule.title,
                "count": count,
                "status": status,
            }
        )
        md_lines.append(f"| `{rule.code}` | {rule.title} | {count} | {status} |")
    return rows, "\n".join(md_lines) + "\n"


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copy2(src, dst)


def _latest_file_by_glob(pattern: str) -> Path | None:
    """Возвращает самый свежий файл по glob-паттерну относительно ROOT."""
    matches = [path for path in ROOT.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _read_json_file(path: Path) -> dict[str, Any]:
    """Безопасно читает JSON-файл; на ошибке возвращает пустой dict."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _acceptance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Нормализует краткое summary acceptance-артефакта."""
    summary: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "generated_at_utc": payload.get("generated_at_utc"),
    }

    checks = payload.get("checks")
    if isinstance(checks, dict):
        failed = [str(name) for name, ok in checks.items() if not bool(ok)]
        summary["failed_checks"] = failed
        summary["checks_total"] = len(checks)

    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        summary["warnings_count"] = len(warnings)

    channels_summary = (payload.get("channels") or {}).get("summary") if isinstance(payload.get("channels"), dict) else None
    if isinstance(channels_summary, dict) and channels_summary.get("success_rate") is not None:
        summary["channels_success_rate"] = channels_summary.get("success_rate")

    kpi = payload.get("kpi")
    if isinstance(kpi, dict):
        summary["kpi"] = kpi

    return summary


def _collect_acceptance_artifacts(bundle_dir: Path) -> dict[str, Any]:
    """
    Подтягивает в bundle последние acceptance-отчёты этапов стабильности/каналов.

    Почему:
    - новый чат должен видеть не только runtime_snapshot, но и факт закрытия KPI
      и readiness следующего этапа без ручного поиска файлов в artifacts.
    """
    patterns = {
        "e1e3_acceptance": "artifacts/e1e3_acceptance_*.json",
        "channels_photo_chrome_acceptance": "artifacts/channels_photo_chrome_acceptance_*.json",
        "channels_photo_chrome_smoke": "artifacts/channels_photo_chrome_smoke_*.json",
    }
    out: dict[str, Any] = {}

    for key, pattern in patterns.items():
        latest = _latest_file_by_glob(pattern)
        record: dict[str, Any] = {
            "source_path": str(latest) if latest else None,
            "bundle_path": None,
            "found": bool(latest),
            "summary": {},
        }
        if latest:
            dst = bundle_dir / latest.name
            shutil.copy2(latest, dst)
            record["bundle_path"] = str(dst)
            record["summary"] = _acceptance_summary(_read_json_file(latest))
        out[key] = record
    return out


def _collect_recovery_branches() -> list[dict[str, str]]:
    """
    Собирает короткий список веток recovery-цикла.

    Зачем:
    - следующему окну нужен не только текущий branch, но и вехи, которые уже
      были запушены как точки восстановления.
    """
    rows: list[dict[str, str]] = []
    for branch in RECOVERY_BRANCHES:
        head_short = _git_stdout(["rev-parse", "--short", branch])
        subject = _git_stdout(["log", "-1", "--pretty=%s", branch])
        if not head_short and not subject:
            continue
        rows.append(
            {
                "name": branch,
                "head_short": head_short,
                "subject": subject,
            }
        )
    return rows


def _ops_artifact_summary(payload: dict[str, Any], *, artifact_name: str) -> dict[str, Any]:
    """Нормализует краткую выжимку ops-артефакта для handoff manifest/summary."""
    summary: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "generated_at": payload.get("generated_at") or payload.get("generated_at_utc"),
    }
    if artifact_name == "pre_release_smoke_latest":
        summary["blocked"] = bool(payload.get("blocked"))
        summary["strict_runtime"] = bool(payload.get("strict_runtime"))
        summary["required_failed"] = len(payload.get("required_failed") or [])
        summary["blocked_required"] = payload.get("blocked_required") or []
        summary["advisory_failed"] = len(payload.get("advisory_failed") or [])
        summary["blocked_advisory"] = payload.get("blocked_advisory") or []
    elif artifact_name == "r20_merge_gate_latest":
        summary["required_failed"] = int(payload.get("required_failed") or 0)
        summary["advisory_failed"] = int(payload.get("advisory_failed") or 0)
        summary["checks_total"] = len(payload.get("checks") or [])
    return summary


def _collect_ops_evidence(bundle_dir: Path) -> dict[str, Any]:
    """
    Подтягивает свежие ops и browser evidence в handoff bundle.

    Почему:
    - attach-папка должна содержать не только narrative docs, но и короткие
      доказательства того, чем именно подтверждено текущее состояние.
    """
    records: dict[str, Any] = {}
    files = {
        "pre_release_smoke_latest": ROOT / "artifacts" / "ops" / "pre_release_smoke_latest.json",
        "r20_merge_gate_latest": ROOT / "artifacts" / "ops" / "r20_merge_gate_latest.json",
        "latest_browser_snapshot": _latest_file_by_glob(".playwright-cli/page-*.yml"),
        "latest_browser_screenshot": _latest_file_by_glob(".playwright-cli/page-*.png"),
    }

    for key, src in files.items():
        record: dict[str, Any] = {
            "source_path": str(src) if src else None,
            "bundle_path": None,
            "found": bool(src and Path(src).exists()),
            "summary": {},
        }
        if src and Path(src).exists():
            src_path = Path(src)
            dst = bundle_dir / src_path.name
            shutil.copy2(src_path, dst)
            record["bundle_path"] = str(dst)
            if src_path.suffix.lower() == ".json":
                record["summary"] = _ops_artifact_summary(
                    _read_json_file(src_path),
                    artifact_name=key,
                )
            else:
                record["summary"] = {
                    "size_bytes": src_path.stat().st_size,
                    "mtime_utc": datetime.fromtimestamp(
                        src_path.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                }
        records[key] = record
    return records


def _build_attach_summary_md(
    *,
    runtime_snapshot: dict[str, Any],
    acceptance: dict[str, Any],
    ops_evidence: dict[str, Any],
) -> str:
    """
    Формирует короткую attach-summary для нового чата и возврата на pablito.

    Почему:
    - следующему окну нужен сжатый operational summary, не требующий читать
      весь checkpoint перед первым действием.
    """
    git = runtime_snapshot.get("git") if isinstance(runtime_snapshot, dict) else {}
    branch = str((git or {}).get("branch") or "").strip() or "unknown"
    head = str((git or {}).get("head") or "").strip() or "unknown"
    health_lite = (
        (((runtime_snapshot.get("health") or {}).get("web_lite") or {}).get("json"))
        if isinstance(runtime_snapshot, dict)
        else {}
    )
    health_lite = health_lite if isinstance(health_lite, dict) else {}
    route = health_lite.get("last_runtime_route") if isinstance(health_lite.get("last_runtime_route"), dict) else {}
    pre_release = (ops_evidence.get("pre_release_smoke_latest") or {}).get("summary") or {}
    merge_gate = (ops_evidence.get("r20_merge_gate_latest") or {}).get("summary") or {}
    operator_workflow = runtime_snapshot.get("operator_workflow") if isinstance(runtime_snapshot, dict) else {}
    operator_workflow = operator_workflow if isinstance(operator_workflow, dict) else {}
    workflow_summary = operator_workflow.get("summary") if isinstance(operator_workflow.get("summary"), dict) else {}
    recent_replies = operator_workflow.get("recent_replied_requests") if isinstance(operator_workflow.get("recent_replied_requests"), list) else []
    recent_activity = operator_workflow.get("recent_activity") if isinstance(operator_workflow.get("recent_activity"), list) else []
    last_reply = recent_replies[0] if recent_replies else {}
    last_reply_meta = last_reply.get("metadata") if isinstance(last_reply, dict) and isinstance(last_reply.get("metadata"), dict) else {}
    last_activity = recent_activity[0] if recent_activity else {}
    active_issues = [
        row.get("code")
        for row in (runtime_snapshot.get("known_issues") or [])
        if isinstance(row, dict) and str(row.get("status") or "").upper() == "ACTIVE"
    ]
    branch_rows = runtime_snapshot.get("recovery_branches") or []

    lines = [
        "# ATTACH SUMMARY RU",
        "",
        "Эту папку можно приложить целиком в новый чат и продолжить работу без пересказа по памяти.",
        "",
        f"- Сгенерировано (UTC): `{NOW.isoformat()}`",
        f"- Текущая ветка: `{branch}`",
        f"- HEAD: `{head}`",
        f"- Ориентировочная готовность проекта: `{PROJECT_READINESS}`",
        "",
        "## Текущее живое состояние",
        f"- `telegram_userbot_state`: `{health_lite.get('telegram_userbot_state', 'unknown')}`",
        f"- `openclaw_auth_state`: `{health_lite.get('openclaw_auth_state', 'unknown')}`",
        f"- Последний runtime route: `{route.get('model') or 'unknown'}` через `{route.get('provider') or 'unknown'}`",
        f"- `pre_release_smoke_latest`: blocked=`{pre_release.get('blocked', False)}`; blocked_required=`{', '.join(pre_release.get('blocked_required') or []) or '-'}`",
        f"- `r20_merge_gate_latest`: required_failed=`{merge_gate.get('required_failed', '-')}`; advisory_failed=`{merge_gate.get('advisory_failed', '-')}`",
        "",
        "## Operator workflow",
        f"- `open_items`: `{workflow_summary.get('open_items', 0)}`",
        f"- `pending_owner_tasks`: `{workflow_summary.get('pending_owner_tasks', 0)}`",
        f"- `pending_owner_requests`: `{workflow_summary.get('pending_owner_requests', 0)}`",
        f"- `pending_owner_mentions`: `{workflow_summary.get('pending_owner_mentions', 0)}`",
        f"- `pending_approvals`: `{workflow_summary.get('pending_approvals', 0)}`",
        f"- `recent_reply_trace`: `{last_reply.get('identity', {}).get('trace_id', 'n/a')}`",
        f"- `recent_reply_excerpt`: `{last_reply_meta.get('reply_excerpt', '-')}`",
        f"- `recent_activity`: `{last_activity.get('action', 'n/a')}` by `{last_activity.get('actor', 'n/a')}`",
        "",
        "## Что уже закрыто на USER2",
        "- truthful блок параллелизма в owner UI реализован и подтверждён unit + browser smoke на изолированном `:18081`",
        "- release gate и runbook доведены до рабочего состояния; merge-gate зелёный",
        "- owner-mismatch в strict runtime smoke классифицируется честно как blocked-среда, а не как code failure",
        "- `signal_alert_route` больше не даёт ложные `CERTIFICATE_VERIFY_FAILED` и умеет truth-fallback через web endpoint",
        "- `live_channel_smoke` и web-based runtime smoke доведены до полезного verdict на временной учётке",
        "- attach-ready handoff bundle теперь генерируется вместе с summary, checklist, manifest и zip-архивом",
        "",
        "## Реальные хвосты перед абсолютным финишем",
        "- переподтвердить новый блок параллелизма на живом `:8080` после restart именно от владельца `pablito`",
        "- при желании закрыть строгий `owner -> userbot -> reply` и полный inbound reserve round-trip как отдельный live E2E этап",
        "- переподтвердить или перелогинить `google-gemini-cli`, потому что OAuth fallback отмечен как хрупкий",
    ]

    if active_issues:
        lines.extend(
            [
                "",
                "## Активные сигналы по свежей матрице проблем",
            ]
        )
        for code in active_issues:
            lines.append(f"- `{code}`")

    lines.extend(
        [
            "",
            "## Ключевые recovery-ветки",
        ]
    )
    for row in branch_rows:
        lines.append(f"- `{row.get('name')}` @ `{row.get('head_short')}`: {row.get('subject')}")

    lines.extend(
        [
            "",
            "## Что открыть в этой папке",
            "1. `START_NEXT_CHAT.md`",
            "2. `PABLITO_RETURN_CHECKLIST.md`",
            "3. `NEXT_CHAT_CHECKPOINT_RU.md`",
            "4. `OPENCLAW_KRAB_ROADMAP.md`",
            "5. `HANDOFF_MANIFEST.json`",
            "",
            "## Что уже лежит как evidence",
        ]
    )
    for key in (
        "pre_release_smoke_latest",
        "r20_merge_gate_latest",
        "latest_browser_snapshot",
        "latest_browser_screenshot",
    ):
        record = ops_evidence.get(key) or {}
        if record.get("bundle_path"):
            lines.append(f"- `{Path(str(record['bundle_path'])).name}`")
    for key in (
        "e1e3_acceptance",
        "channels_photo_chrome_acceptance",
        "channels_photo_chrome_smoke",
    ):
        record = acceptance.get(key) or {}
        if record.get("bundle_path"):
            lines.append(f"- `{Path(str(record['bundle_path'])).name}`")
    return "\n".join(lines) + "\n"


def _build_pablito_return_checklist_md(*, runtime_snapshot: dict[str, Any]) -> str:
    """
    Формирует короткий runbook возврата на основную учётку `pablito`.

    Нужен, чтобы последний live acceptance-хвост можно было закрыть без
    дополнительной реконструкции команд.
    """
    git = runtime_snapshot.get("git") if isinstance(runtime_snapshot, dict) else {}
    branch = str((git or {}).get("branch") or "").strip() or "codex/handoff-bundle-polish"
    lines = [
        "# PABLITO RETURN CHECKLIST",
        "",
        "Этот чеклист нужен для возврата на основную учётку `pablito` и закрытия последнего live acceptance-хвоста.",
        "",
        f"- Рабочая ветка для продолжения: `{branch}`",
        f"- Ориентировочная готовность проекта: `{PROJECT_READINESS}`",
        "",
        "## Самый короткий путь",
        "```bash",
        "cd /Users/pablito/Antigravity_AGENTS/Краб",
        "git fetch origin",
        f"git switch {branch}",
        "git pull --ff-only",
        "./Verify\\ Live\\ Parallelism\\ On\\ Pablito.command",
        "```",
        "",
        "## Что должен сделать helper",
        "- остановить старый runtime",
        "- поднять свежий runtime уже от владельца `pablito`",
        "- дождаться `:8080` и `:18789`",
        "- прочитать `/api/model/catalog`",
        "- сохранить truth в `artifacts/ops/live_parallelism_truth_latest.json`",
        "- открыть `http://127.0.0.1:8080`",
        "",
        "## Если нужен ручной режим",
        "```bash",
        "cd /Users/pablito/Antigravity_AGENTS/Краб",
        "./new\\ Stop\\ Krab.command",
        "nohup ./new\\ start_krab.command > logs/live_parallelism_restart.log 2>&1 &",
        "python3 - <<'PY'",
        "import json, time, urllib.request",
        "for _ in range(80):",
        "    try:",
        "        with urllib.request.urlopen('http://127.0.0.1:8080/api/health/lite', timeout=3) as r:",
        "            if json.loads(r.read().decode()).get('ok'):",
        "                break",
        "    except Exception:",
        "        pass",
        "    time.sleep(1.5)",
        "with urllib.request.urlopen('http://127.0.0.1:8080/api/model/catalog', timeout=10) as r:",
        "    payload = json.loads(r.read().decode())",
        "print(json.dumps(payload.get('catalog', {}).get('parallelism_truth', {}), ensure_ascii=False, indent=2))",
        "PY",
        "```",
        "",
        "## После live verify",
        "1. Сохранить новый handoff bundle уже на `pablito`, если пойдёшь дальше.",
        "2. Если live `:8080` подтвердился, снять этот хвост из roadmap/checkpoint.",
        "3. Дальше решать, нужен ли ещё строгий live E2E owner/reserve Telegram.",
    ]
    return "\n".join(lines) + "\n"


def _build_handoff_manifest(
    *,
    runtime_snapshot: dict[str, Any],
    acceptance: dict[str, Any],
    ops_evidence: dict[str, Any],
    bundle_zip_path: Path,
) -> dict[str, Any]:
    """Собирает machine-readable manifest attach-папки."""
    bundle_files = {
        path.name
        for path in BUNDLE_DIR.iterdir()
        if path.is_file()
    }
    bundle_files.add("HANDOFF_MANIFEST.json")
    return {
        "generated_at_utc": NOW.isoformat(),
        "project_readiness": PROJECT_READINESS,
        "bundle_dir": str(BUNDLE_DIR),
        "bundle_zip": str(bundle_zip_path),
        "entrypoints": {
            "start_next_chat": str(BUNDLE_DIR / "START_NEXT_CHAT.md"),
            "attach_summary": str(BUNDLE_DIR / "ATTACH_SUMMARY_RU.md"),
            "pablito_return_checklist": str(BUNDLE_DIR / "PABLITO_RETURN_CHECKLIST.md"),
            "runtime_snapshot": str(BUNDLE_DIR / "runtime_snapshot.json"),
            "manifest": str(BUNDLE_DIR / "HANDOFF_MANIFEST.json"),
        },
        "git": runtime_snapshot.get("git") or {},
        "recovery_branches": runtime_snapshot.get("recovery_branches") or [],
        "acceptance_artifacts": acceptance,
        "ops_evidence": ops_evidence,
        "known_issues": runtime_snapshot.get("known_issues") or [],
        "operator_workflow": runtime_snapshot.get("operator_workflow") or {},
        "bundle_files": sorted(bundle_files),
        "resume_target": {
            "account": "pablito",
            "preferred_branch": str((runtime_snapshot.get("git") or {}).get("branch") or "").strip()
            or "codex/handoff-bundle-polish",
            "helper_command": "/Users/pablito/Antigravity_AGENTS/Краб/Verify Live Parallelism On Pablito.command",
            "live_truth_artifact": "/Users/pablito/Antigravity_AGENTS/Краб/artifacts/ops/live_parallelism_truth_latest.json",
        },
    }


def _build_start_next_chat_md(
    *,
    bundle_dir: Path,
    runtime_snapshot: dict[str, Any],
    acceptance: dict[str, Any],
    ops_evidence: dict[str, Any],
) -> str:
    """
    Формирует стартовый чеклист для открытия нового чата без потери контекста.
    """
    git = runtime_snapshot.get("git") if isinstance(runtime_snapshot, dict) else {}
    branch = str((git or {}).get("branch") or "").strip() or "unknown"
    head = str((git or {}).get("head") or "").strip() or "unknown"

    required_files = [
        bundle_dir / "ATTACH_SUMMARY_RU.md",
        bundle_dir / "PABLITO_RETURN_CHECKLIST.md",
        bundle_dir / "NEXT_CHAT_CHECKPOINT_RU.md",
        bundle_dir / "OPENCLAW_KRAB_ROADMAP.md",
        bundle_dir / "NEW_CHAT_BOOTSTRAP_PROMPT.md",
        bundle_dir / "runtime_snapshot.json",
        bundle_dir / "HANDOFF_MANIFEST.json",
        bundle_dir / "known_issues_matrix.md",
    ]
    optional_files = [
        acceptance.get("e1e3_acceptance", {}).get("bundle_path"),
        acceptance.get("channels_photo_chrome_acceptance", {}).get("bundle_path"),
        acceptance.get("channels_photo_chrome_smoke", {}).get("bundle_path"),
        ops_evidence.get("pre_release_smoke_latest", {}).get("bundle_path"),
        ops_evidence.get("r20_merge_gate_latest", {}).get("bundle_path"),
        ops_evidence.get("latest_browser_snapshot", {}).get("bundle_path"),
        ops_evidence.get("latest_browser_screenshot", {}).get("bundle_path"),
    ]

    lines = [
        "# START NEXT CHAT",
        "",
        "Ниже готовый пакет для старта нового окна без потери интеграционного контекста.",
        "",
        f"- Сгенерировано (UTC): `{NOW.isoformat()}`",
        f"- Ветка: `{branch}`",
        f"- HEAD: `{head}`",
        f"- Готовность проекта: `{PROJECT_READINESS}`",
        "",
        "## Что открыть первым",
    ]
    for idx, path in enumerate(required_files, start=1):
        lines.append(f"{idx}. `{path}`")

    lines.extend(
        [
            "",
            "## Рекомендуемые acceptance-артефакты",
        ]
    )
    rec_index = 1
    for maybe_path in optional_files:
        if maybe_path:
            lines.append(f"{rec_index}. `{maybe_path}`")
            rec_index += 1
    if rec_index == 1:
        lines.append("1. (не найдены) Сначала прогоните acceptance-скрипты текущего этапа.")

    lines.extend(
        [
            "",
            "## Стартовый prompt для нового чата",
            "1. Сначала прочитай `ATTACH_SUMMARY_RU.md` и `PABLITO_RETURN_CHECKLIST.md`.",
            "2. Затем открой `NEW_CHAT_BOOTSTRAP_PROMPT.md` из этого bundle.",
            "3. Прочитай `NEXT_CHAT_CHECKPOINT_RU.md` и `OPENCLAW_KRAB_ROADMAP.md`.",
            "4. Не доверяй старым процентам готовности из архивных handoff-фраз; текущий truth бери только из этого bundle.",
            "5. Добавь явное требование формата отчёта после каждой итерации:",
            "   - что изменено;",
            "   - как проверено;",
            "   - что осталось.",
            "",
            "## Короткая проверка после старта нового чата",
            "1. Проверить `git status --short --branch`.",
            "2. Прочитать `runtime_snapshot.json`, `HANDOFF_MANIFEST.json` и `known_issues_matrix.md`.",
            "3. Если работа продолжается на `pablito`, запустить `Verify Live Parallelism On Pablito.command`.",
            "4. Продолжить с ближайшего незакрытого пункта roadmap.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    git_branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    git_status = _run(["git", "status", "--short", "--branch"])
    git_head = _run(["git", "rev-parse", "HEAD"])

    web_lite = _http_json("http://127.0.0.1:8080/api/health/lite")
    web_full = _http_json("http://127.0.0.1:8080/api/health")
    runtime_handoff = _http_json("http://127.0.0.1:8080/api/runtime/handoff")
    channels_probe = _http_json("http://127.0.0.1:8080/api/openclaw/channels/status")
    openclaw_health = _http_json("http://127.0.0.1:18789/health")
    lm_models = _http_json(f"{os.getenv('LM_STUDIO_URL', 'http://127.0.0.1:1234').rstrip('/')}/api/v1/models")

    krab_log_tail = _slice_since_last_marker(
        _tail_recent(ROOT / "krab.log", max_lines=2000, max_age_hours=6),
        markers=(
            "krab userbot started",
            "starting_userbot",
        ),
    )
    openclaw_log_tail = _slice_since_last_marker(
        _tail_recent(ROOT / "openclaw.log", max_lines=2000, max_age_hours=6),
        markers=(
            "openclaw started",
            "starting openclaw gateway",
            "gateway reachable",
        ),
    )
    combined_tail = f"{krab_log_tail}\n{openclaw_log_tail}"
    issues_rows, issues_md = _build_known_issues_matrix(combined_tail)

    runtime_snapshot = {
        "generated_at_utc": NOW.isoformat(),
        "bundle_dir": str(BUNDLE_DIR),
        "git": {
            "branch": git_branch.get("stdout", ""),
            "head": git_head.get("stdout", ""),
            "status_short": git_status.get("stdout", ""),
        },
        "health": {
            "web_lite": web_lite,
            "web_full": web_full,
            "runtime_handoff": runtime_handoff,
            "channels_probe": channels_probe,
            "openclaw_health": openclaw_health,
            "lmstudio_models": {
                "ok": lm_models.get("ok", False),
                "status": lm_models.get("status"),
                "models_count": (
                    len((lm_models.get("json") or {}).get("data", []))
                    if isinstance(lm_models.get("json"), dict)
                    else None
                ),
                "error": lm_models.get("raw", ""),
            },
        },
        "channels": _openclaw_channels_snapshot(),
        "telegram_session": _session_state(),
        "operator_workflow": ((runtime_handoff.get("json") or {}).get("operator_workflow") or {}) if isinstance(runtime_handoff.get("json"), dict) else {},
        "secrets_masked": {
            "openclaw_token": _mask_secret(os.getenv("OPENCLAW_TOKEN", "")),
            "gemini_free": _mask_secret(os.getenv("GEMINI_API_KEY_FREE", "")),
            "gemini_paid": _mask_secret(os.getenv("GEMINI_API_KEY_PAID", "")),
            "openai_api_key": _mask_secret(os.getenv("OPENAI_API_KEY", "")),
            "web_api_key": _mask_secret(os.getenv("WEB_API_KEY", "")),
        },
        "known_issues": issues_rows,
        "recovery_branches": _collect_recovery_branches(),
    }

    acceptance_artifacts = _collect_acceptance_artifacts(BUNDLE_DIR)
    ops_evidence = _collect_ops_evidence(BUNDLE_DIR)
    runtime_snapshot["acceptance_artifacts"] = acceptance_artifacts
    runtime_snapshot["ops_evidence"] = ops_evidence

    (BUNDLE_DIR / "runtime_snapshot.json").write_text(
        json.dumps(runtime_snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (BUNDLE_DIR / "known_issues_matrix.md").write_text(issues_md, encoding="utf-8")
    (BUNDLE_DIR / "krab_log_tail.log").write_text(krab_log_tail, encoding="utf-8")
    (BUNDLE_DIR / "openclaw_log_tail.log").write_text(openclaw_log_tail, encoding="utf-8")

    _copy_if_exists(DOCS_DIR / "NEXT_CHAT_CHECKPOINT_RU.md", BUNDLE_DIR / "NEXT_CHAT_CHECKPOINT_RU.md")
    _copy_if_exists(DOCS_DIR / "OPENCLAW_KRAB_ROADMAP.md", BUNDLE_DIR / "OPENCLAW_KRAB_ROADMAP.md")
    _copy_if_exists(DOCS_DIR / "NEW_CHAT_BOOTSTRAP_PROMPT.md", BUNDLE_DIR / "NEW_CHAT_BOOTSTRAP_PROMPT.md")
    (BUNDLE_DIR / "ATTACH_SUMMARY_RU.md").write_text(
        _build_attach_summary_md(
            runtime_snapshot=runtime_snapshot,
            acceptance=acceptance_artifacts,
            ops_evidence=ops_evidence,
        ),
        encoding="utf-8",
    )
    (BUNDLE_DIR / "PABLITO_RETURN_CHECKLIST.md").write_text(
        _build_pablito_return_checklist_md(runtime_snapshot=runtime_snapshot),
        encoding="utf-8",
    )
    (BUNDLE_DIR / "START_NEXT_CHAT.md").write_text(
        _build_start_next_chat_md(
            bundle_dir=BUNDLE_DIR,
            runtime_snapshot=runtime_snapshot,
            acceptance=acceptance_artifacts,
            ops_evidence=ops_evidence,
        ),
        encoding="utf-8",
    )
    bundle_zip_path = ARTIFACTS_DIR / f"{BUNDLE_DIR.name}.zip"
    (BUNDLE_DIR / "HANDOFF_MANIFEST.json").write_text(
        json.dumps(
            _build_handoff_manifest(
                runtime_snapshot=runtime_snapshot,
                acceptance=acceptance_artifacts,
                ops_evidence=ops_evidence,
                bundle_zip_path=bundle_zip_path,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle_zip_path = Path(
        shutil.make_archive(
            str(BUNDLE_DIR),
            "zip",
            root_dir=str(ARTIFACTS_DIR),
            base_dir=BUNDLE_DIR.name,
        )
    )

    print("=== Handoff Bundle Export ===")
    print(f"OK: {BUNDLE_DIR}")
    print(f"bundle_zip: {bundle_zip_path}")
    print(f"runtime_snapshot: {BUNDLE_DIR / 'runtime_snapshot.json'}")
    print(f"known_issues: {BUNDLE_DIR / 'known_issues_matrix.md'}")
    print(f"attach_summary: {BUNDLE_DIR / 'ATTACH_SUMMARY_RU.md'}")
    print(f"pablito_return: {BUNDLE_DIR / 'PABLITO_RETURN_CHECKLIST.md'}")
    print(f"manifest: {BUNDLE_DIR / 'HANDOFF_MANIFEST.json'}")
    print(f"start_packet: {BUNDLE_DIR / 'START_NEXT_CHAT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

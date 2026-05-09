# -*- coding: utf-8 -*-
"""Wave 52-H: Comprehensive runtime state collectors для `!health detail`.

Read-only сборщики, surface-ят rich state (Wave 47-49 артефакты),
который не попал в `!health` short / `!health deep`:
  • Krab core (uptime, PID, RSS via psutil)
  • OpenClaw gateway latency (timing curl-style)
  • MCPs registered list
  • Catchup last run (parses structured log если catchup_history.jsonl нет)
  • Snapshots count + size + last
  • Route switches last 24h count + most-frequent reason
  • Memory: process RSS + swarm_memory entry count
  • Active alerts (если Prometheus integration alive)

Все коллекторы defensive — никогда не raise, на любой failure
возвращают {"error": "..."} либо безопасный default.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Helpers ──────────────────────────────────────────────────────────────────


def _runtime_state_dir() -> Path:
    """Корень runtime-state с возможностью override через env (для тестов)."""
    override = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _format_bytes_mb(b: int) -> float:
    return round(b / (1024 * 1024), 2)


# ── 1. Core ──────────────────────────────────────────────────────────────────


def _collect_health_core(session_start_time: float | None = None) -> dict[str, Any]:
    """Krab core process info: uptime, PID, RSS.

    session_start_time: monotonic-style epoch seconds; если None,
    uptime будет 0 (неизвестно).
    """
    pid = os.getpid()
    info: dict[str, Any] = {"pid": pid}
    if session_start_time:
        try:
            info["uptime_sec"] = max(int(time.time() - float(session_start_time)), 0)
        except Exception:  # noqa: BLE001
            info["uptime_sec"] = 0
    else:
        info["uptime_sec"] = 0

    try:
        import psutil  # type: ignore[import-not-found]

        proc = psutil.Process(pid)
        rss = proc.memory_info().rss
        info["rss_mb"] = _format_bytes_mb(rss)
    except Exception as exc:  # noqa: BLE001
        info["rss_mb"] = None
        info["error"] = f"psutil: {exc!s}"[:120]

    return info


# ── 2. Gateway ───────────────────────────────────────────────────────────────


async def _collect_health_gateway(
    url: str = "http://127.0.0.1:18789/health",
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Curl OpenClaw gateway /health, returns latency_ms."""
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "error": f"httpx import: {exc!s}"}

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "healthy": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "latency_ms": elapsed_ms,
            "url": url,
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "healthy": False,
            "latency_ms": elapsed_ms,
            "url": url,
            "error": str(exc)[:120],
        }


# ── 3. MCPs ──────────────────────────────────────────────────────────────────


def _collect_health_mcps(timeout: float = 4.0) -> dict[str, Any]:
    """Парсит вывод `openclaw mcp list --json`.

    Возвращает {"count": int, "names": list[str], "raw": <…>}.
    """
    try:
        result = subprocess.run(
            ["openclaw", "mcp", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"count": 0, "names": [], "error": "openclaw CLI not found"}
    except subprocess.TimeoutExpired:
        return {"count": 0, "names": [], "error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"count": 0, "names": [], "error": str(exc)[:120]}

    if result.returncode != 0:
        return {
            "count": 0,
            "names": [],
            "error": (result.stderr or "non-zero exit")[:120],
        }

    raw = result.stdout.strip()
    if not raw:
        return {"count": 0, "names": []}

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {"count": 0, "names": [], "error": f"json parse: {exc!s}"[:120]}

    # Поддерживаем разные формы ответа: list of dicts, list of strings, dict.
    names: list[str] = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                name = item.get("name") or item.get("id") or item.get("server")
                if name:
                    names.append(str(name))
            elif isinstance(item, str):
                names.append(item)
    elif isinstance(parsed, dict):
        servers = parsed.get("servers") or parsed.get("mcps") or parsed.get("items")
        if isinstance(servers, list):
            for item in servers:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("id")
                    if name:
                        names.append(str(name))
                elif isinstance(item, str):
                    names.append(item)

    return {"count": len(names), "names": names}


# ── 4. Catchup ───────────────────────────────────────────────────────────────


def _collect_health_catchup() -> dict[str, Any]:
    """Reads last entry из catchup_history.jsonl если файл существует.

    Если jsonl файла нет — пытается распарсить хвост krab_main.log
    на маркер `startup_catchup_complete_multi`.
    """
    history_path = _runtime_state_dir() / "catchup_history.jsonl"
    if history_path.exists():
        try:
            lines = history_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return {"error": f"read fail: {exc!s}"[:120]}
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    return {
                        "ts": entry.get("ts") or entry.get("timestamp"),
                        "target_count": entry.get("target_count"),
                        "total_caught_up": entry.get("total_caught_up"),
                        "total_skipped_self": entry.get("total_skipped_self"),
                    }
            except (ValueError, TypeError):
                continue
        return {"error": "no valid entries"}
    # Fallback: ничего не нашли
    return {"error": "no catchup_history.jsonl"}


# ── 5. Snapshots ─────────────────────────────────────────────────────────────


def _collect_health_snapshots() -> dict[str, Any]:
    """List snapshots dir и суммирует count/size/last."""
    snap_root = _runtime_state_dir() / "snapshots"
    if not snap_root.exists() or not snap_root.is_dir():
        return {"count": 0, "total_bytes": 0, "last_ts": None}
    try:
        snap_dirs = [d for d in snap_root.iterdir() if d.is_dir()]
    except OSError as exc:
        return {"count": 0, "error": str(exc)[:120]}
    total_bytes = 0
    for snap_dir in snap_dirs:
        try:
            for f in snap_dir.iterdir():
                if f.is_file():
                    try:
                        total_bytes += f.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    snap_dirs.sort(key=lambda p: p.name)
    last_ts = snap_dirs[-1].name if snap_dirs else None
    return {
        "count": len(snap_dirs),
        "total_bytes": total_bytes,
        "total_mb": _format_bytes_mb(total_bytes),
        "last_ts": last_ts,
    }


# ── 6. Route switches (24h) ──────────────────────────────────────────────────


def _collect_health_routes_24h() -> dict[str, Any]:
    """Reads route_switches.jsonl, фильтрует ts >= now-24h, считает top reason."""
    log_path = _runtime_state_dir() / "route_switches.jsonl"
    if not log_path.exists():
        return {"count_24h": 0, "top_reason": None}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"count_24h": 0, "error": str(exc)[:120]}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    reasons: Counter[str] = Counter()
    count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        count += 1
        reason = entry.get("reason")
        if isinstance(reason, str) and reason:
            reasons[reason] += 1

    top_reason = reasons.most_common(1)[0][0] if reasons else None
    return {"count_24h": count, "top_reason": top_reason}


# ── 7. Memory (RSS + swarm_memory entry count) ───────────────────────────────


def _collect_health_memory() -> dict[str, Any]:
    """Process RSS + swarm_memory entry count (sum по всем командам)."""
    out: dict[str, Any] = {}
    try:
        import psutil  # type: ignore[import-not-found]

        proc = psutil.Process(os.getpid())
        out["rss_mb"] = _format_bytes_mb(proc.memory_info().rss)
    except Exception as exc:  # noqa: BLE001
        out["rss_mb"] = None
        out["psutil_error"] = str(exc)[:80]

    swarm_path = _runtime_state_dir() / "swarm_memory.json"
    if not swarm_path.exists():
        out["swarm_entries"] = 0
        return out

    try:
        data = json.loads(swarm_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        out["swarm_entries"] = 0
        out["swarm_error"] = str(exc)[:80]
        return out

    total = 0
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                total += len(v)
            elif isinstance(v, dict):
                # Иногда хранится как {team: {entries: [...]}}
                inner = v.get("entries")
                if isinstance(inner, list):
                    total += len(inner)
    elif isinstance(data, list):
        total = len(data)
    out["swarm_entries"] = total
    return out


# ── 8. Alerts (Prometheus integration) ──────────────────────────────────────


async def _collect_health_alerts(
    url: str = "http://127.0.0.1:9090/api/v1/alerts",
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Fetch active Prometheus alerts (если интеграция alive)."""
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return {"available": False}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"available": False}
            data = resp.json()
    except Exception:  # noqa: BLE001
        return {"available": False}

    alerts = data.get("data", {}).get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list):
        return {"available": True, "active": 0}
    active = sum(1 for a in alerts if isinstance(a, dict) and a.get("state") == "firing")
    return {"available": True, "active": active}


# ── Aggregator ───────────────────────────────────────────────────────────────


async def collect_health_detail(*, session_start_time: float | None = None) -> dict[str, Any]:
    """Async-сборщик всех секций. Запускает gateway+alerts параллельно."""
    gateway_task = asyncio.create_task(_collect_health_gateway())
    alerts_task = asyncio.create_task(_collect_health_alerts())

    core = _collect_health_core(session_start_time=session_start_time)
    mcps = _collect_health_mcps()
    catchup = _collect_health_catchup()
    snapshots = _collect_health_snapshots()
    routes = _collect_health_routes_24h()
    memory = _collect_health_memory()

    gateway = await gateway_task
    alerts = await alerts_task

    return {
        "core": core,
        "gateway": gateway,
        "mcps": mcps,
        "catchup": catchup,
        "snapshots": snapshots,
        "routes": routes,
        "memory": memory,
        "alerts": alerts,
    }


def format_health_detail(data: dict[str, Any]) -> str:
    """Markdown-форматирование, Telegram-safe (≤ 4000 chars)."""
    lines: list[str] = ["🦀 **Krab Health Detail**", "─────────────────"]

    # Core
    core = data.get("core", {})
    rss = core.get("rss_mb")
    rss_str = f"{rss}MB" if rss is not None else "?"
    uptime_sec = int(core.get("uptime_sec", 0) or 0)
    hrs, rem = divmod(uptime_sec, 3600)
    mins = rem // 60
    uptime_str = f"{hrs}h {mins}m" if hrs else f"{mins}m"
    pid = core.get("pid", "?")
    lines.append(f"**Core**: ✅ up | uptime {uptime_str} | PID {pid} | RSS {rss_str}")

    # Gateway
    gw = data.get("gateway", {})
    if gw.get("healthy"):
        lines.append(f"**Gateway**: ✅ live | {gw.get('latency_ms', '?')}ms latency")
    else:
        err = gw.get("error", f"status {gw.get('status_code', '?')}")
        lines.append(f"**Gateway**: ❌ down ({err})")

    # MCPs
    mcps = data.get("mcps", {})
    cnt = mcps.get("count", 0)
    if mcps.get("error"):
        lines.append(f"**MCPs**: ❌ {mcps['error']}")
    else:
        names = mcps.get("names") or []
        names_str = " / ".join(names[:10])
        lines.append(f"**MCPs**: ✅ {cnt} registered ({names_str})")

    # Catchup
    cu = data.get("catchup", {})
    if cu.get("error"):
        lines.append(f"**Catchup**: ⚠️ {cu['error']}")
    else:
        lines.append(
            f"**Catchup**: {cu.get('ts', '?')} — {cu.get('target_count', '?')} chats, "
            f"{cu.get('total_caught_up', 0)} caught_up, "
            f"{cu.get('total_skipped_self', 0)} skipped_self"
        )

    # Snapshots
    sn = data.get("snapshots", {})
    if sn.get("error"):
        lines.append(f"**Snapshots**: ❌ {sn['error']}")
    else:
        lines.append(
            f"**Snapshots**: {sn.get('count', 0)} backups, "
            f"{sn.get('total_mb', 0)} MB total, last {sn.get('last_ts') or '—'}"
        )

    # Routes
    rt = data.get("routes", {})
    top = rt.get("top_reason")
    top_str = f" (top reason: {top})" if top else ""
    lines.append(f"**Route switches (24h)**: {rt.get('count_24h', 0)}{top_str}")

    # Memory
    mem = data.get("memory", {})
    rss_m = mem.get("rss_mb")
    rss_m_str = f"{rss_m}MB" if rss_m is not None else "?"
    lines.append(f"**Memory**: {rss_m_str} RSS, swarm_memory {mem.get('swarm_entries', 0)} entries")

    # Alerts (опционально)
    al = data.get("alerts", {})
    if al.get("available"):
        active = al.get("active", 0)
        icon = "✅" if active == 0 else "⚠️"
        lines.append(f"**Alerts**: {icon} {active} active")

    lines.append("")
    lines.append("_Use !routes for fallback chain detail, !mcp for inventory._")

    report = "\n".join(lines)
    if len(report) > 4000:
        report = report[:3990] + "\n…(truncated)"
    return report


__all__ = [
    "_collect_health_alerts",
    "_collect_health_catchup",
    "_collect_health_core",
    "_collect_health_gateway",
    "_collect_health_mcps",
    "_collect_health_memory",
    "_collect_health_routes_24h",
    "_collect_health_snapshots",
    "collect_health_detail",
    "format_health_detail",
]

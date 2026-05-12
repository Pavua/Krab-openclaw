# -*- coding: utf-8 -*-
"""
Wave 79: health probe для Krab Ear backend.
Wave 180: IPC-aware probe + not-installed detection + exponential backoff + env gate.

Дополняет on-scrape проверку из ecosystem_health.py отдельным фоновым loop'ом,
который копит статистику отказов и экспонирует её в Prometheus. Цель — ловить
регрессии тип Session 40 (SingleInstanceGuard deadlock → backend жив, но KE UI
зависал) и алертить когда KE backend перестал отвечать N итераций подряд.

Wave 180 фикс: KE backend = pure IPC (unix socket), не HTTP. Probe Wave 79
ходил на http://127.0.0.1:5005/health которого никогда не существовало — все
probes давали consecutive_failures с reason=connection_error. Wave 180:
- IPC-приоритет: probe сначала проверяет krabear.sock (как KrabEarClient).
- HTTP fallback только если KRAB_EAR_BACKEND_URL задан явно (не дефолт).
- Not-installed detection: если KE app binary, .venv и socket parent dir
  все отсутствуют — probe self-disables (returns "not_installed", не алертит).
- Exponential backoff: после N consecutive_failures interval растёт
  base → base*2 → base*4 ... → max (1h cap), чтобы не спамить лог.
- Env gate: KRAB_EAR_HEALTH_PROBE_ENABLED (новое имя, KRAB_EAR_PROBE_ENABLED
  оставлен для обратной совместимости в userbot_bridge bootstrap).

Метрики (экспонируются через src.core.prometheus_metrics.collect_metrics):
    krab_ear_probe_last_ago_seconds       — секунд с последнего успешного probe
    krab_ear_probe_failures_total{reason} — отказы по причинам (timeout/5xx/connection_error/ipc_*)
    krab_ear_consecutive_failures         — текущая длина streak отказов

Pattern: повторяет launchd_health_monitor (Wave 75) — module-level snapshot,
asyncio background task, env-gate в bootstrap.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Дефолтный backend URL — синхронизирован с ecosystem_health.py.
# Wave 180: HTTP — fallback, основной канал IPC через krabear.sock.
_DEFAULT_BACKEND_URL = "http://127.0.0.1:5005"
_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_TIMEOUT_SEC = 5.0
_DEFAULT_SOCKET_PATH = "~/Library/Application Support/KrabEar/krabear.sock"
# Wave 180: backoff caps.
_BACKOFF_THRESHOLD = 3  # после стольких consecutive failures начинаем тормозить
_BACKOFF_MAX_SEC = 3600  # верхняя граница interval после backoff (1h)
# Wave 180: пути для not-installed detection.
_KE_APP_BINARY = Path(
    "/Users/pablito/Antigravity_AGENTS/Krab Ear/Krab Ear.app/Contents/MacOS/KrabEarAgent"
)
_KE_BACKEND_VENV = Path("/Users/pablito/Antigravity_AGENTS/Krab Ear/.venv_krab_ear/bin/python3")

# Snapshot: module-level, читается on-scrape из prometheus_metrics.collect_metrics().
_SNAPSHOT: dict[str, Any] = {
    "last_probe_ts": 0.0,
    "last_success_ts": 0.0,
    "last_probe_ok": False,
    "consecutive_failures": 0,
    "total_failures": 0,
    "failures_by_reason": {},  # reason → count
    "installed": True,  # Wave 180: false → probe self-disabled
}


def get_snapshot() -> dict[str, Any]:
    """Копия текущего snapshot — для prometheus_metrics.collect_metrics()."""
    snap = dict(_SNAPSHOT)
    snap["failures_by_reason"] = dict(_SNAPSHOT["failures_by_reason"])
    return snap


def _classify_failure(exc: BaseException | None, status_code: int | None) -> str:
    """Маппит исключение/HTTP статус в reason label."""
    if exc is not None:
        if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "connection_error"
        if isinstance(exc, httpx.HTTPError):
            return "http_error"
        return "exception"
    if status_code is not None:
        if 500 <= status_code < 600:
            return "5xx"
        if 400 <= status_code < 500:
            return "4xx"
    return "unknown"


def _record_success(*, now: float) -> None:
    _SNAPSHOT["last_probe_ts"] = now
    _SNAPSHOT["last_success_ts"] = now
    _SNAPSHOT["last_probe_ok"] = True
    _SNAPSHOT["consecutive_failures"] = 0


def _record_failure(*, reason: str, now: float) -> None:
    _SNAPSHOT["last_probe_ts"] = now
    _SNAPSHOT["last_probe_ok"] = False
    _SNAPSHOT["consecutive_failures"] = int(_SNAPSHOT.get("consecutive_failures", 0)) + 1
    _SNAPSHOT["total_failures"] = int(_SNAPSHOT.get("total_failures", 0)) + 1
    bucket: dict[str, int] = _SNAPSHOT.setdefault("failures_by_reason", {})
    bucket[reason] = bucket.get(reason, 0) + 1


def _is_ke_installed(
    *,
    app_binary: Path | None = None,
    backend_venv: Path | None = None,
    socket_parent: Path | None = None,
) -> bool:
    """Wave 180: KE считается installed если хотя бы один из артефактов на месте.

    Параметры опциональны — для тестов с monkeypatched путями.
    """
    binary = app_binary if app_binary is not None else _KE_APP_BINARY
    venv = backend_venv if backend_venv is not None else _KE_BACKEND_VENV
    socket_p = (
        socket_parent
        if socket_parent is not None
        else Path(os.path.expanduser(_DEFAULT_SOCKET_PATH)).parent
    )
    return binary.exists() or venv.exists() or socket_p.exists()


class KrabEarHealthProbe:
    """Фоновый probe Krab Ear backend (IPC + HTTP fallback)."""

    def __init__(
        self,
        *,
        backend_url: str | None = None,
        socket_path: str | None = None,
        interval_sec: int | None = None,
        timeout_sec: float | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._backend_url = (
            backend_url or os.getenv("KRAB_EAR_BACKEND_URL", _DEFAULT_BACKEND_URL)
        ).rstrip("/")
        # Wave 180: HTTP probe выполняется только если URL задан явно (не дефолт).
        # Иначе KE = IPC-only и connection_error к localhost:5005 — false positive.
        self._http_explicit = bool(os.getenv("KRAB_EAR_BACKEND_URL", "").strip())
        self._socket_path = Path(
            (socket_path or os.getenv("KRAB_EAR_SOCKET_PATH", _DEFAULT_SOCKET_PATH))
        ).expanduser()
        self._interval = max(
            5, int(interval_sec or os.getenv("KRAB_EAR_PROBE_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC))
        )
        self._timeout = float(timeout_sec or _DEFAULT_TIMEOUT_SEC)
        self._http_client_factory = http_client_factory
        self._now_fn = now_fn or time.time
        self._task: asyncio.Task | None = None
        # Wave 180: лениво кэшируем installed-флаг (один раз на жизнь процесса).
        self._installed: bool | None = None

    @property
    def health_url(self) -> str:
        return f"{self._backend_url}/health"

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def _make_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=self._timeout)

    def _check_installed(self) -> bool:
        """Wave 180: lazy cached check — KE artifacts present?"""
        if self._installed is None:
            self._installed = _is_ke_installed(socket_parent=self._socket_path.parent)
            _SNAPSHOT["installed"] = self._installed
            if not self._installed:
                logger.info(
                    "krab_ear_probe_not_installed",
                    reason="no_app_binary_no_venv_no_socket_dir",
                    socket_dir=str(self._socket_path.parent),
                )
        return self._installed

    async def _probe_ipc(self) -> tuple[bool, str]:
        """Wave 180: ping IPC backend через unix socket. Контракт — service.py:ping."""
        if not self._socket_path.exists():
            return False, "ipc_socket_missing"
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path=str(self._socket_path)),
                timeout=self._timeout,
            )
            request = {"id": "krab_ear_probe", "method": "ping", "params": {}}
            writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
            if not raw:
                return False, "ipc_empty_response"
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            ok = bool(payload.get("ok"))
            result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
            status = str(result.get("status", "")).strip().lower()
            if ok and status in {"ok", "healthy", "up"}:
                return True, "ok"
            return False, "ipc_not_ok"
        except (asyncio.TimeoutError, TimeoutError):
            return False, "ipc_timeout"
        except (ConnectionRefusedError, FileNotFoundError):
            return False, "ipc_refused"
        except Exception as exc:  # noqa: BLE001
            return False, f"ipc_error:{type(exc).__name__}"
        finally:
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

    async def probe_once(self) -> bool:
        """Одна итерация probe. Обновляет module-level snapshot. True = успех.

        Wave 180: not-installed → no-op (return True, не пишет failure).
        Иначе порядок: IPC → HTTP fallback (если KRAB_EAR_BACKEND_URL задан явно).
        """
        # Wave 180: if KE отсутствует на диске — probe is no-op.
        if not self._check_installed():
            return True

        now = self._now_fn()
        # Wave 180: IPC-приоритет.
        ipc_ok, ipc_reason = await self._probe_ipc()
        if ipc_ok:
            _record_success(now=now)
            return True

        # IPC failed. Если HTTP fallback не настроен — record IPC failure и выход.
        if not self._http_explicit:
            _record_failure(reason=ipc_reason, now=now)
            logger.warning(
                "krab_ear_probe_failed",
                channel="ipc",
                reason=ipc_reason,
                socket_path=str(self._socket_path),
                consecutive_failures=_SNAPSHOT["consecutive_failures"],
            )
            return False

        # HTTP fallback.
        try:
            async with self._make_client() as client:
                response = await client.get(self.health_url)
                status = response.status_code
            if status == 200:
                _record_success(now=now)
                return True
            reason = _classify_failure(None, status)
            _record_failure(reason=reason, now=now)
            logger.warning(
                "krab_ear_probe_bad_status",
                url=self.health_url,
                status=status,
                consecutive_failures=_SNAPSHOT["consecutive_failures"],
            )
            return False
        except Exception as exc:  # noqa: BLE001
            reason = _classify_failure(exc, None)
            _record_failure(reason=reason, now=now)
            logger.warning(
                "krab_ear_probe_failed",
                channel="http",
                url=self.health_url,
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
                consecutive_failures=_SNAPSHOT["consecutive_failures"],
            )
            return False

    def _backoff_interval(self) -> int:
        """Wave 180: после _BACKOFF_THRESHOLD consecutive_failures удваиваем sleep.

        Math: base * 2^(consecutive - threshold), capped at _BACKOFF_MAX_SEC.
        Пример (base=60, threshold=3): fail 3→60s, 4→120s, 5→240s, 6→480s ... → cap.
        """
        consecutive = int(_SNAPSHOT.get("consecutive_failures", 0) or 0)
        if consecutive < _BACKOFF_THRESHOLD:
            return self._interval
        # 2^k где k = consecutive - threshold, но ограничиваем k=20 чтоб избежать overflow.
        k = min(20, consecutive - _BACKOFF_THRESHOLD)
        scaled = self._interval * (1 << k)
        return min(_BACKOFF_MAX_SEC, max(self._interval, int(scaled)))

    def start(self) -> None:
        """Запускает background loop (идемпотентен).

        Wave 180: env gate KRAB_EAR_HEALTH_PROBE_ENABLED (новое) или
        KRAB_EAR_PROBE_ENABLED (legacy, оставляем для bridge bootstrap).
        Если probe self-disabled (KE не установлен) — start всё равно отрабатывает,
        но loop сразу выходит из probe_once → no-op.
        """
        flag = (
            os.getenv("KRAB_EAR_HEALTH_PROBE_ENABLED") or os.getenv("KRAB_EAR_PROBE_ENABLED") or "1"
        )
        if flag.strip().lower() not in ("1", "true", "yes"):
            logger.info("krab_ear_health_probe_disabled_via_env")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="krab_ear_health_probe")
        logger.info(
            "krab_ear_health_probe_started",
            socket_path=str(self._socket_path),
            http_url=self.health_url if self._http_explicit else None,
            interval_sec=self._interval,
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.probe_once()
                sleep_for = self._backoff_interval()
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                logger.info("krab_ear_health_probe_stopped")
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "krab_ear_health_probe_loop_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
                # Не выходим, чтобы регрессия в probe_once не убила фоновый loop.
                await asyncio.sleep(self._interval)


# Module-level singleton — bootstrap из userbot_bridge.start().
krab_ear_health_probe = KrabEarHealthProbe()


def reset_snapshot_for_tests() -> None:
    """Только для тестов: обнуляет module-level snapshot."""
    _SNAPSHOT["last_probe_ts"] = 0.0
    _SNAPSHOT["last_success_ts"] = 0.0
    _SNAPSHOT["last_probe_ok"] = False
    _SNAPSHOT["consecutive_failures"] = 0
    _SNAPSHOT["total_failures"] = 0
    _SNAPSHOT["failures_by_reason"] = {}
    _SNAPSHOT["installed"] = True

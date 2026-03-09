#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog для Krab Ear в составе экосистемы Krab.

Зачем нужен:
1) При высокой нагрузке по памяти (например, тяжёлая локальная LLM) backend Krab Ear
   может потерять IPC-сокет или завершиться.
2) UI Krab Ear в этом состоянии показывает ошибки вида "No such file or directory",
   а ручной "Перезапуск" временно лечит проблему.
3) Этот watchdog автоматизирует лечение: следит за IPC health и при деградации
   перезапускает агент без участия пользователя.

Связь с проектом:
- Используется из `Start Full Ecosystem.command` и `Stop Full Ecosystem.command`.
- Не меняет бизнес-логику Krab Ear, только повышает операционную надёжность.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    print(f"[{_ts()}] {message}", flush=True)


class KrabEarWatchdog:
    """Операционный watchdog для восстановления Krab Ear при падениях backend."""

    def __init__(
        self,
        ear_dir: Path,
        start_script: Path,
        runtime_bin: Path,
        socket_path: Path,
        interval_sec: float,
        fail_threshold: int,
        cooldown_sec: float,
        ping_timeout_sec: float = 2.0,
    ) -> None:
        self.ear_dir = ear_dir
        self.start_script = start_script
        self.runtime_bin = runtime_bin
        self.socket_path = socket_path
        self.interval_sec = max(1.0, interval_sec)
        self.fail_threshold = max(1, fail_threshold)
        self.cooldown_sec = max(1.0, cooldown_sec)
        self.ping_timeout_sec = max(0.5, ping_timeout_sec)
        self._last_restart_at = 0.0
        self._stopping = False

    @property
    def _runtime_pattern(self) -> str:
        return f"{self.runtime_bin} --project-root {self.ear_dir}"

    def _find_pids(self) -> list[int]:
        try:
            proc = subprocess.run(
                ["pgrep", "-f", self._runtime_pattern],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                return []
            pids: list[int] = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids
        except Exception:
            return []

    async def _ping_ipc(self) -> tuple[bool, str]:
        if not self.socket_path.exists():
            return False, "socket_missing"
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(path=str(self.socket_path)),
                timeout=self.ping_timeout_sec,
            )
            payload = {"id": "watchdog-ping", "method": "ping", "params": {}}
            writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=self.ping_timeout_sec)
            if not raw:
                return False, "empty_response"
            data: dict[str, Any] = json.loads(raw.decode("utf-8", errors="replace"))
            ok = bool(data.get("ok"))
            result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
            status = str(result.get("status", "")).strip().lower()
            if ok and status in {"ok", "healthy", "up"}:
                return True, "ok"
            return False, f"ipc_not_ok:{status or 'unknown'}"
        except Exception as exc:  # noqa: BLE001
            return False, f"ipc_error:{exc}"
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass

    def _terminate_pids(self, pids: list[int]) -> None:
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except Exception as exc:  # noqa: BLE001
                _log(f"warn: не удалось отправить SIGTERM pid={pid}: {exc}")
        time.sleep(0.8)
        still_alive = [pid for pid in pids if self._is_alive(pid)]
        if still_alive:
            for pid in still_alive:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    _log(f"warn: не удалось отправить SIGKILL pid={pid}: {exc}")

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _spawn_agent(self) -> bool:
        if not self.start_script.exists():
            _log(f"error: start script не найден: {self.start_script}")
            return False
        try:
            with open(os.devnull, "w", encoding="utf-8") as devnull:
                subprocess.Popen(  # noqa: S603
                    [str(self.start_script), "--launched-by-launchd"],
                    cwd=str(self.ear_dir),
                    stdout=devnull,
                    stderr=devnull,
                    start_new_session=True,
                )
            return True
        except Exception as exc:  # noqa: BLE001
            _log(f"error: не удалось запустить Krab Ear: {exc}")
            return False

    async def _recover(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_restart_at < self.cooldown_sec:
            _log(
                "watchdog: cooldown активен, перезапуск пропущен "
                f"(reason={reason}, remain={self.cooldown_sec - (now - self._last_restart_at):.1f}s)"
            )
            return
        self._last_restart_at = now

        pids = self._find_pids()
        if pids:
            _log(f"watchdog: останавливаю зависший Krab Ear pid={pids} (reason={reason})")
            self._terminate_pids(pids)
        else:
            _log(f"watchdog: Krab Ear process отсутствует, запускаю заново (reason={reason})")

        started = self._spawn_agent()
        if not started:
            return

        await asyncio.sleep(1.2)
        ok, probe_reason = await self._ping_ipc()
        if ok:
            _log("watchdog: Krab Ear успешно восстановлен")
        else:
            _log(f"watchdog: после перезапуска IPC ещё не готов ({probe_reason})")

    async def probe(self) -> dict[str, Any]:
        ok, reason = await self._ping_ipc()
        pids = self._find_pids()
        return {
            "ok": ok,
            "status": "ok" if ok else reason,
            "socket_path": str(self.socket_path),
            "process_running": bool(pids),
            "pids": pids,
        }

    async def loop(self) -> int:
        _log(
            "watchdog: старт "
            f"(socket={self.socket_path}, interval={self.interval_sec}s, threshold={self.fail_threshold})"
        )
        consecutive_failures = 0
        while not self._stopping:
            ok, reason = await self._ping_ipc()
            if ok:
                if consecutive_failures:
                    _log("watchdog: backend снова healthy")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                _log(f"watchdog: health fail #{consecutive_failures}: {reason}")
                if consecutive_failures >= self.fail_threshold:
                    await self._recover(reason=reason)
                    consecutive_failures = 0
            await asyncio.sleep(self.interval_sec)
        return 0

    def request_stop(self) -> None:
        self._stopping = True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watchdog для Krab Ear backend")
    parser.add_argument(
        "--ear-dir",
        default=os.getenv("KRAB_EAR_DIR", str(Path(__file__).resolve().parents[2] / "Krab Ear")),
        help="Путь к папке Krab Ear",
    )
    parser.add_argument(
        "--start-script",
        default="",
        help="Явный путь до scripts/start_agent.command (опционально)",
    )
    parser.add_argument(
        "--runtime-bin",
        default="",
        help="Явный путь до native/runtime/KrabEarAgent (опционально)",
    )
    parser.add_argument(
        "--socket-path",
        default=os.getenv("KRAB_EAR_SOCKET_PATH", "~/Library/Application Support/KrabEar/krabear.sock"),
        help="Путь до IPC сокета",
    )
    parser.add_argument("--interval-sec", type=float, default=float(os.getenv("KRAB_EAR_WATCHDOG_INTERVAL_SEC", "8")))
    parser.add_argument("--fail-threshold", type=int, default=int(os.getenv("KRAB_EAR_WATCHDOG_FAIL_THRESHOLD", "3")))
    parser.add_argument("--cooldown-sec", type=float, default=float(os.getenv("KRAB_EAR_WATCHDOG_COOLDOWN_SEC", "25")))
    parser.add_argument("--probe", action="store_true", help="Сделать один health probe и выйти")
    return parser


async def _main_async() -> int:
    args = _build_parser().parse_args()

    ear_dir = Path(args.ear_dir).expanduser().resolve()
    start_script = Path(args.start_script).expanduser() if args.start_script else (ear_dir / "scripts/start_agent.command")
    runtime_bin = Path(args.runtime_bin).expanduser() if args.runtime_bin else (ear_dir / "native/runtime/KrabEarAgent")
    socket_path = Path(args.socket_path).expanduser()

    watchdog = KrabEarWatchdog(
        ear_dir=ear_dir,
        start_script=start_script.resolve(),
        runtime_bin=runtime_bin.resolve(),
        socket_path=socket_path,
        interval_sec=args.interval_sec,
        fail_threshold=args.fail_threshold,
        cooldown_sec=args.cooldown_sec,
    )

    if args.probe:
        report = await watchdog.probe()
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("ok") else 2

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, watchdog.request_stop)
        except NotImplementedError:
            # На некоторых окружениях (например, Windows) signal handler asyncio может быть недоступен.
            pass

    return await watchdog.loop()


def main() -> int:
    try:
        return asyncio.run(_main_async())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


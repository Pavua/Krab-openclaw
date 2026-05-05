"""Wave 25-B: Combined memory monitor для Krab + Krab Ear.

Использует psutil для детекции:
- Total RSS обоих процессов > 12 GB → log warning
- macOS swap usage > 8 GB → log critical
- Free RAM < 3 GB → log critical

При critical → send Telegram alert через Krab REST API (/api/notify).
Проверяет каждые 60 секунд.

Логирует в ~/.openclaw/krab_runtime_state/coexistence_monitor.log JSONL.
"""
import json
import os
import sys
import time
from pathlib import Path

import psutil

# Пороги детекции (в байтах) — Wave 28-B: macOS-friendly defaults через ENV
# macOS использует compressed swap агрессивно (8-12GB видно регулярно при норм работе),
# поэтому swap > 8GB на macOS НЕ критично. Для Linux 8GB реально означает memory pressure.
_GB = 1_000_000_000
COMBINED_RSS_THRESHOLD = int(float(os.environ.get("KRAB_COEXIST_RSS_THRESHOLD_GB", "12")) * _GB)
SWAP_THRESHOLD = int(float(os.environ.get("KRAB_COEXIST_SWAP_THRESHOLD_GB", "16")) * _GB)
RAM_AVAIL_THRESHOLD = int(float(os.environ.get("KRAB_COEXIST_RAM_AVAIL_THRESHOLD_GB", "3")) * _GB)

# Krab Owner Panel endpoint
KRAB_NOTIFY_URL = os.environ.get("KRAB_NOTIFY_URL", "http://127.0.0.1:8080/api/notify")

LOG_FILE = Path.home() / ".openclaw/krab_runtime_state/coexistence_monitor.log"

# Паттерны командной строки для определения процессов Krab и Krab Ear
KRAB_PATTERNS = ["userbot_bridge", "src/main.py"]
EAR_PATTERNS = ["KrabEar", "krab.ear", "krab_ear"]


def find_pids(name_patterns: list[str]) -> list[int]:
    """Найти PID-ы процессов, чья cmdline содержит любой из паттернов."""
    pids = []
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if any(pat in cmd for pat in name_patterns):
                pids.append(p.info["pid"])
        except Exception:
            continue
    return pids


def get_rss_bytes(pids: list[int]) -> int:
    """Суммарный RSS (bytes) для списка PID-ов. Недоступные процессы пропускаются."""
    total = 0
    for pid in pids:
        try:
            total += psutil.Process(pid).memory_info().rss
        except Exception:
            pass
    return total


def _send_notify(text: str) -> None:
    """Отправить alert через Krab REST API. Ошибки не критичны — подавляем."""
    try:
        import urllib.request

        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            KRAB_NOTIFY_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def main() -> int:
    """Однократный снимок состояния памяти, запись JSONL, отправка alert при нужде."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Найти процессы
    krab_pids = find_pids(KRAB_PATTERNS)
    ear_pids = find_pids(EAR_PATTERNS)

    # RSS каждого контура
    krab_rss = get_rss_bytes(krab_pids)
    ear_rss = get_rss_bytes(ear_pids)
    combined = krab_rss + ear_rss

    # Системная память
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    snapshot: dict = {
        "timestamp": time.time(),
        "krab_pids": krab_pids,
        "ear_pids": ear_pids,
        "krab_rss_gb": round(krab_rss / 1e9, 2),
        "ear_rss_gb": round(ear_rss / 1e9, 2),
        "combined_rss_gb": round(combined / 1e9, 2),
        "system_ram_used_gb": round(vm.used / 1e9, 2),
        "system_ram_available_gb": round(vm.available / 1e9, 2),
        "swap_used_gb": round(sw.used / 1e9, 2),
    }

    # Вычислить alerts
    alerts: list[str] = []
    if combined > COMBINED_RSS_THRESHOLD:
        alerts.append(f"combined_rss_high:{combined / 1e9:.1f}GB")
    if sw.used > SWAP_THRESHOLD:
        alerts.append(f"swap_used_critical:{sw.used / 1e9:.1f}GB")
    if vm.available < RAM_AVAIL_THRESHOLD:
        alerts.append(f"ram_available_low:{vm.available / 1e9:.1f}GB")

    snapshot["alerts"] = alerts

    # Записать строку JSONL
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(snapshot) + "\n")

    # Отправить Telegram alert если есть critical условия
    if alerts:
        alert_text = "⚠️ Krab memory alert: " + ", ".join(alerts)
        _send_notify(alert_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())

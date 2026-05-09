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
# Wave (08.05.2026): bumped 18→28 GB. macOS compressed swap 19-22 GB наблюдается
# в норме под workload (compressor + OrbStack VPN + heavy apps на 36GB M4 Max).
# Threshold 18 давал 216 false-positive alerts за 11h. Реальная panic-зона:
# user verified swap >32 GB → kernel I/O queue overflow → watchdog timeout → reboot.
SWAP_THRESHOLD = int(float(os.environ.get("KRAB_COEXIST_SWAP_THRESHOLD_GB", "28")) * _GB)
RAM_AVAIL_THRESHOLD = int(float(os.environ.get("KRAB_COEXIST_RAM_AVAIL_THRESHOLD_GB", "3")) * _GB)
# Session 40: cooldown между Telegram alerts чтобы не спамить каждую минуту.
# При memory pressure которое держится час — 1-2 уведомления, не 60.
ALERT_COOLDOWN_SEC = int(os.environ.get("KRAB_COEXIST_ALERT_COOLDOWN_SEC", "900"))  # 15 мин

# Krab Owner Panel endpoint
KRAB_NOTIFY_URL = os.environ.get("KRAB_NOTIFY_URL", "http://127.0.0.1:8080/api/notify")

LOG_FILE = Path.home() / ".openclaw/krab_runtime_state/coexistence_monitor.log"
ALERT_STATE_FILE = Path.home() / ".openclaw/krab_runtime_state/coexistence_alert_state.json"

# Паттерны командной строки для определения процессов Krab и Krab Ear
# Wave (08.05.2026): добавлены `src.main` + `-m src.main` — реальный launch использует
# module mode (`python -m src.main`), и `src/main.py` (slash) НИКОГДА не совпадает
# с pgrep output → 11h false-negatives `krab_pids:[]` пока Krab был живой PID 1019.
KRAB_PATTERNS = ["userbot_bridge", "src/main.py", "src.main", "-m src.main"]
EAR_PATTERNS = ["KrabEar", "krab.ear", "krab_ear"]
# Wave 29-C-fix: исключаем Swift compiler (frontend/driver) — он попадает по пути
# "/Users/pablito/Antigravity_AGENTS/Krab Ear/" даже когда Krab Ear не запущен.
# False positive, обнаруженный agent investigation.
EAR_EXCLUDE_PATTERNS = [
    "swift-frontend",
    "swift-driver",
    "xcodebuild",
    "/usr/bin/codesign",
]


def find_pids(name_patterns: list[str], exclude: list[str] | None = None) -> list[int]:
    """Найти PID-ы процессов, чья cmdline содержит любой из паттернов.

    `exclude` (Wave 29-C-fix) — фильтрует false positives (swift-frontend и т.п.).
    """
    pids = []
    excl = exclude or []
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if any(pat in cmd for pat in name_patterns) and not any(ex in cmd for ex in excl):
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
    ear_pids = find_pids(EAR_PATTERNS, exclude=EAR_EXCLUDE_PATTERNS)

    # RSS каждого контура
    krab_rss = get_rss_bytes(krab_pids)
    ear_rss = get_rss_bytes(ear_pids)
    combined = krab_rss + ear_rss

    # Системная память
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    # Wave (08.05.2026): top-5 не-Krab/Ear процессов по RSS — для поиска leaker'ов
    # вне Krab контура (OrbStack VPN, browsers, Codex.app, etc). Без этого root
    # cause panic'ов невозможно найти — Krab сам жрёт <1GB, виновник за периметром.
    krab_ear_pid_set = set(krab_pids) | set(ear_pids)
    top_external: list[dict] = []
    try:
        candidates: list[tuple[int, int, str]] = []
        for p in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
            try:
                pid = p.info["pid"]
                if pid in krab_ear_pid_set:
                    continue
                rss = (p.info.get("memory_info") or psutil.Process(pid).memory_info()).rss
                if rss < 200_000_000:  # 200 MB cutoff — отсекаем мелочь
                    continue
                candidates.append((pid, rss, p.info.get("name") or ""))
            except Exception:
                continue
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_external = [
            {"pid": pid, "rss_gb": round(rss / 1e9, 2), "name": name}
            for pid, rss, name in candidates[:5]
        ]
    except Exception:  # noqa: BLE001
        top_external = []

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
        "top_external_rss": top_external,
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

    # Отправить Telegram alert если есть critical условия + cooldown.
    # Session 40: спам каждую минуту → cooldown 15 мин (default).
    if alerts:
        now_ts = time.time()
        last_ts = 0.0
        try:
            if ALERT_STATE_FILE.exists():
                with ALERT_STATE_FILE.open() as f:
                    last_ts = float(json.load(f).get("last_alert_ts", 0.0))
        except Exception:  # noqa: BLE001
            last_ts = 0.0

        if now_ts - last_ts >= ALERT_COOLDOWN_SEC:
            alert_text = "⚠️ Krab memory alert: " + ", ".join(alerts)
            _send_notify(alert_text)
            try:
                ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                ALERT_STATE_FILE.write_text(json.dumps({"last_alert_ts": now_ts}))
            except Exception:  # noqa: BLE001
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

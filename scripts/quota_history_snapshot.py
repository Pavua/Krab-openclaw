"""Wave 34-A: quota history snapshot — записывает текущий quota state каждый час."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Файл JSONL с историей снимков квот
LOG = Path.home() / ".openclaw/krab_runtime_state/quota_history.jsonl"

# URL owner-panel endpoint (без probe — быстрый ответ)
_URL = "http://127.0.0.1:8080/api/quota?probe=false"

# Максимальный размер лога до ротации (10 МБ)
_MAX_BYTES = 10 * 1024 * 1024


def _rotate_if_needed(log: Path) -> None:
    """Простая ротация: если файл >10 МБ — обрезаем старые строки, оставляем последние 5000."""
    if not log.exists() or log.stat().st_size < _MAX_BYTES:
        return
    lines = log.read_text().splitlines()
    keep = lines[-5000:]
    log.write_text("\n".join(keep) + "\n")


def main() -> int:
    """Снять снимок quota state и добавить строку в JSONL-лог."""
    try:
        with urllib.request.urlopen(_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        # Краб не запущен или недоступен — пишем placeholder
        data = {"ok": False, "error": str(exc)[:120]}
    except Exception as exc:  # noqa: BLE001
        data = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:100]}"}

    snapshot = {"ts": time.time(), **data}

    LOG.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(LOG)

    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

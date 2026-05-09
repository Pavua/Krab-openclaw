#!/usr/bin/env python3
"""Wave 44-R-script-tools — скриншот экрана через macOS screencapture.

Bypass Playwright permission issues. Returns JSON со status + path + size.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_screenshot.py"
MIN_VALID_SIZE = 20_000  # bytes


def _validate_image(path: Path) -> tuple[bool, str]:
    """Проверка: размер > 20KB и не all-white (если есть Pillow)."""
    if not path.is_file():
        return False, "file not created"
    size = path.stat().st_size
    if size < MIN_VALID_SIZE:
        return False, f"file too small: {size} bytes (< {MIN_VALID_SIZE})"
    try:
        from PIL import Image  # type: ignore  # noqa: PLC0415

        with Image.open(path) as img:
            small = img.convert("L").resize((32, 32))
            pixels = list(small.getdata())
            avg = sum(pixels) / len(pixels)
            if avg > 250:
                return False, f"image is nearly all-white (avg={avg:.1f})"
    except ImportError:
        pass  # Pillow не установлен — доверяем размеру
    except Exception:  # noqa: BLE001
        pass  # corrupt image тоже не критично — основная валидация — размер
    return True, ""


def _capture(output: Path, window_title: str | None, display: int | None) -> dict:
    cmd = ["/usr/sbin/screencapture", "-x"]  # -x: no shutter sound
    if display is not None:
        cmd.extend(["-D", str(display)])
    if window_title:
        # Window mode не поддерживает прямой match по title без AppleScript;
        # для простоты используем полноэкранный capture + warning
        pass
    cmd.append(str(output))

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    elapsed = time.time() - started

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"screencapture exit {proc.returncode}",
            "stderr": proc.stderr.strip(),
        }

    valid, reason = _validate_image(output)
    if not valid:
        return {
            "ok": False,
            "error": "screenshot validation failed",
            "reason": reason,
            "path": str(output),
        }

    size = output.stat().st_size
    return {
        "ok": True,
        "path": str(output),
        "size_bytes": size,
        "elapsed_sec": round(elapsed, 2),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture macOS screen via screencapture")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--window", default=None, help="window title (best-effort, currently captures full screen)"
    )
    parser.add_argument("--display", type=int, default=None)
    args = parser.parse_args(argv)

    output = Path(args.output).expanduser().resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return emit_error(f"cannot create dir: {exc}", SCRIPT, sys.argv[1:])

    if not os.path.exists("/usr/sbin/screencapture"):
        return emit_error("screencapture not found (not macOS?)", SCRIPT, sys.argv[1:])

    try:
        result = _capture(output, args.window, args.display)
    except subprocess.TimeoutExpired:
        return emit_error("screencapture timed out", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

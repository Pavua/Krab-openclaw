"""Wave 44-Q: image validation guard.

Защита от отправки blank/corrupt screenshots в Telegram.

Background: 09.05.2026 codex-cli отправил pavua cached "VPN panel screenshot",
оказавшийся 1280×720 blank white PNG (4253 bytes — Playwright captured empty
viewport). Файл прошёл без валидации.

Public API:
    is_blank_image(path, *, min_bytes, near_white_threshold, near_black_threshold)
        -> tuple[bool, str]
    Возвращает (is_blank, reason). reason='ok' если изображение валидное.
"""

from __future__ import annotations

from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)

# Empirical defaults: blank 1280×720 PNG ~4KB, real screenshot 50KB+.
DEFAULT_MIN_BYTES = 20000
DEFAULT_NEAR_WHITE_THRESHOLD = 250.0
DEFAULT_NEAR_BLACK_THRESHOLD = 5.0


def is_blank_image(
    path: str | Path,
    *,
    min_bytes: int = DEFAULT_MIN_BYTES,
    near_white_threshold: float = DEFAULT_NEAR_WHITE_THRESHOLD,
    near_black_threshold: float = DEFAULT_NEAR_BLACK_THRESHOLD,
) -> tuple[bool, str]:
    """Проверяет изображение на blank/corrupt.

    Returns (is_blank, reason):
        - (False, "ok") — реальное изображение, можно отправлять
        - (True, "too_small") — file size < min_bytes
        - (True, "near_white") — средняя яркость RGB > near_white_threshold
        - (True, "near_black") — средняя яркость RGB < near_black_threshold
        - (True, "unreadable: <exc>") — файл не существует/повреждён
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except (FileNotFoundError, OSError) as exc:
        return True, f"unreadable: {exc}"

    if size < min_bytes:
        return True, "too_small"

    try:
        from PIL import Image  # noqa: PLC0415 — lazy import (heavy)
    except ImportError as exc:
        return True, f"unreadable: pillow_missing ({exc})"

    try:
        with Image.open(p) as img:
            img.load()
            # Конвертим в RGB для единообразия (PNG может быть RGBA/L/P).
            rgb = img.convert("RGB")
            # Уменьшаем для скорости — среднее по downscale = среднее по оригиналу.
            rgb.thumbnail((128, 128))
            # NB: getdata() deprecated in Pillow 14 (2027) — заменим позже на get_flattened_data
            pixels = list(rgb.getdata())
    except Exception as exc:  # noqa: BLE001 — Pillow может бросить разное
        return True, f"unreadable: {exc}"

    if not pixels:
        return True, "unreadable: empty pixel data"

    n = len(pixels)
    sum_r = sum(px[0] for px in pixels)
    sum_g = sum(px[1] for px in pixels)
    sum_b = sum(px[2] for px in pixels)
    mean = (sum_r + sum_g + sum_b) / (3.0 * n)

    if mean > near_white_threshold:
        return True, "near_white"
    if mean < near_black_threshold:
        return True, "near_black"

    return False, "ok"

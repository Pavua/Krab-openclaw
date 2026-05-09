"""Wave 44-Q: tests for image_validator (blank/corrupt screenshot guard)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from src.core.image_validator import is_blank_image


def _write_png(path: Path, color: tuple[int, int, int], size: tuple[int, int]) -> int:
    """Создаёт solid-color PNG. Возвращает размер файла."""
    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")
    return path.stat().st_size


def _write_realistic_png(path: Path, size: tuple[int, int] = (400, 300)) -> int:
    """Создаёт PNG с разнообразным контентом — должен пройти валидацию."""
    img = Image.new("RGB", size, (128, 128, 128))
    pixels = img.load()
    assert pixels is not None
    # Рисуем градиент + случайные пятна
    for x in range(size[0]):
        for y in range(size[1]):
            r = (x * 255) // size[0]
            g = (y * 255) // size[1]
            b = ((x + y) * 255) // (size[0] + size[1])
            pixels[x, y] = (r, g, b)
    img.save(path, format="PNG")
    return path.stat().st_size


def test_blank_white_screenshot_rejected(tmp_path: Path) -> None:
    """1280×720 blank white PNG (как в production-инциденте) → near_white."""
    p = tmp_path / "blank.png"
    _write_png(p, (255, 255, 255), (1280, 720))
    # Размер white PNG большой при солид-фоне; обходим too_small чтобы проверить near_white.
    is_blank, reason = is_blank_image(p, min_bytes=100)
    assert is_blank is True
    assert reason == "near_white"


def test_real_screenshot_passes(tmp_path: Path) -> None:
    """Realistic PNG с градиентом → ok."""
    p = tmp_path / "real.png"
    size = _write_realistic_png(p, (400, 300))
    # Понизим min_bytes если PNG-сжатие ужало файл
    is_blank, reason = is_blank_image(p, min_bytes=min(size, 1000))
    assert is_blank is False
    assert reason == "ok"


def test_too_small_rejected(tmp_path: Path) -> None:
    """File size < min_bytes → too_small."""
    p = tmp_path / "tiny.png"
    _write_png(p, (128, 128, 128), (10, 10))
    is_blank, reason = is_blank_image(p, min_bytes=100000)
    assert is_blank is True
    assert reason == "too_small"


def test_missing_file_rejected(tmp_path: Path) -> None:
    """Несуществующий файл → unreadable."""
    p = tmp_path / "nonexistent.png"
    is_blank, reason = is_blank_image(p)
    assert is_blank is True
    assert reason.startswith("unreadable:")


def test_near_black_rejected(tmp_path: Path) -> None:
    """Near-black PNG → near_black."""
    p = tmp_path / "black.png"
    _write_png(p, (1, 1, 1), (1280, 720))
    is_blank, reason = is_blank_image(p, min_bytes=100)
    assert is_blank is True
    assert reason == "near_black"


def test_corrupt_file_rejected(tmp_path: Path) -> None:
    """Файл с не-image содержимым (но достаточного размера) → unreadable."""
    p = tmp_path / "corrupt.png"
    p.write_bytes(b"\x00" * 50000)  # размер ок, но не PNG
    is_blank, reason = is_blank_image(p)
    assert is_blank is True
    assert reason.startswith("unreadable:")


def test_threshold_customizable(tmp_path: Path) -> None:
    """Custom near_white_threshold позволяет lighter изображения."""
    p = tmp_path / "light_gray.png"
    _write_png(p, (240, 240, 240), (1280, 720))
    # При threshold=235 светло-серый отвергается
    is_blank, reason = is_blank_image(p, min_bytes=100, near_white_threshold=235)
    assert is_blank is True
    assert reason == "near_white"
    # При threshold=245 он проходит
    is_blank2, reason2 = is_blank_image(p, min_bytes=100, near_white_threshold=245)
    assert is_blank2 is False
    assert reason2 == "ok"


def test_path_as_string_accepted(tmp_path: Path) -> None:
    """API принимает str path, не только Path."""
    p = tmp_path / "real.png"
    _write_realistic_png(p)
    is_blank, reason = is_blank_image(str(p), min_bytes=100)
    assert is_blank is False
    assert reason == "ok"

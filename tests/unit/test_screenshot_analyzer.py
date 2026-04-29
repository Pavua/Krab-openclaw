# -*- coding: utf-8 -*-
"""
Тесты ScreenshotAnalyzer (Idea 38).

Vision/OCR callable'ы мокаются — никаких реальных моделей.
Покрытие: OCR-fallback, парсинг UI elements, обнаружение error dialog,
LRU-кэш hit, fail-open при vision exception.
"""

from __future__ import annotations

import pytest

from src.core.screenshot_analyzer import (
    ScreenshotAnalyzer,
    UIElement,
)


def _png_bytes(payload: bytes = b"img-1") -> bytes:
    """Псевдо-байты картинки (содержимое не валидируется модулем)."""
    return b"\x89PNG\r\n\x1a\n" + payload


@pytest.mark.asyncio
async def test_ocr_fallback_when_vision_unavailable():
    """Без vision callable: ocr_text заполнен, ui_elements пуст, sentiment эвристикой."""

    async def fake_ocr(_b: bytes) -> str:
        return "Привет, мир. Всё ок."

    analyzer = ScreenshotAnalyzer(ocr_callable=fake_ocr, vision_callable=None)
    result = await analyzer.analyze(_png_bytes())

    assert result.ocr_text == "Привет, мир. Всё ок."
    assert result.ui_elements == ()
    assert result.app_detected is None
    assert result.sentiment == "normal"
    assert result.error_dialog is False


@pytest.mark.asyncio
async def test_ui_elements_parsed_from_vision_json():
    """Vision JSON корректно разбирается в кортеж UIElement + app_detected."""

    async def fake_ocr(_b: bytes) -> str:
        return "Login"

    async def fake_vision(_prompt: str, _b: bytes) -> str:
        return (
            "```json\n"
            "{"
            '"app_detected": "Safari",'
            '"error_dialog": false,'
            '"sentiment": "normal",'
            '"ui_elements": ['
            '{"type": "button", "label": "Sign in", "position_hint": "center"},'
            '{"type": "input",  "label": "Email",   "position_hint": "top"},'
            '{"type": "weird",  "label": "X",       "position_hint": "left"}'
            "]"
            "}\n```"
        )

    analyzer = ScreenshotAnalyzer(ocr_callable=fake_ocr, vision_callable=fake_vision)
    result = await analyzer.analyze(_png_bytes())

    assert result.app_detected == "Safari"
    assert result.sentiment == "normal"
    assert len(result.ui_elements) == 3
    assert result.ui_elements[0] == UIElement(
        type="button", label="Sign in", position_hint="center"
    )
    # Невалидный type схлопывается в "other".
    assert result.ui_elements[2].type == "other"


@pytest.mark.asyncio
async def test_error_dialog_detected_via_vision():
    """sentiment=error из vision → error_dialog принудительно True."""

    async def fake_vision(_prompt: str, _b: bytes) -> str:
        return (
            '{"app_detected": "Xcode", "error_dialog": false, '
            '"sentiment": "error", "ui_elements": '
            '[{"type": "alert", "label": "Build failed", "position_hint": "center"}]}'
        )

    analyzer = ScreenshotAnalyzer(ocr_callable=None, vision_callable=fake_vision)
    result = await analyzer.analyze(_png_bytes())

    assert result.sentiment == "error"
    # Принудительная синхронизация error → error_dialog=True.
    assert result.error_dialog is True
    assert result.app_detected == "Xcode"
    assert result.ui_elements[0].type == "alert"


@pytest.mark.asyncio
async def test_cache_hit_returns_same_instance():
    """Повторный analyze того же image_bytes не дёргает callable'ы."""
    calls = {"vision": 0, "ocr": 0}

    async def fake_ocr(_b: bytes) -> str:
        calls["ocr"] += 1
        return "cached"

    async def fake_vision(_prompt: str, _b: bytes) -> str:
        calls["vision"] += 1
        return '{"sentiment": "normal", "ui_elements": []}'

    analyzer = ScreenshotAnalyzer(ocr_callable=fake_ocr, vision_callable=fake_vision)
    img = _png_bytes(b"same")

    a1 = await analyzer.analyze(img)
    a2 = await analyzer.analyze(img)

    assert a1 is a2
    assert calls["ocr"] == 1
    assert calls["vision"] == 1
    assert analyzer.cache_size() == 1

    # Другие байты — отдельная запись в кэше.
    await analyzer.analyze(_png_bytes(b"other"))
    assert analyzer.cache_size() == 2


@pytest.mark.asyncio
async def test_fail_open_when_vision_raises():
    """Vision raises → graceful fallback в OCR-only с эвристикой sentiment."""

    async def fake_ocr(_b: bytes) -> str:
        return "Critical Error: connection refused"

    async def broken_vision(_prompt: str, _b: bytes) -> str:
        raise RuntimeError("vision endpoint down")

    analyzer = ScreenshotAnalyzer(ocr_callable=fake_ocr, vision_callable=broken_vision)
    result = await analyzer.analyze(_png_bytes(b"err"))

    # Не свалилось, OCR-текст сохранён.
    assert "Critical Error" in result.ocr_text
    assert result.ui_elements == ()
    # Эвристика "error" по тексту.
    assert result.sentiment == "error"
    assert result.error_dialog is True

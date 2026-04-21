"""Тесты для src/integrations/human_like.py (Chado §1 P2)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.human_like import (
    bezier_points,
    human_type,
    random_delay,
    smooth_scroll,
)

# ---------------------------------------------------------------------------
# random_delay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_random_delay_within_bounds() -> None:
    """Все паузы должны укладываться в [min_ms, max_ms]."""
    min_ms, max_ms = 100, 500
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        for _ in range(200):
            await random_delay(min_ms=min_ms, max_ms=max_ms)
        for call in mock_sleep.call_args_list:
            delay_sec: float = call.args[0]
            d_ms = delay_sec * 1000
            assert min_ms <= d_ms <= max_ms, f"Задержка {d_ms} мс вне [{min_ms}, {max_ms}]"


@pytest.mark.asyncio
async def test_random_delay_default_bounds() -> None:
    """Дефолтные значения [200, 800] мс."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        for _ in range(100):
            await random_delay()
        for call in mock_sleep.call_args_list:
            delay_sec: float = call.args[0]
            assert 0.2 <= delay_sec <= 0.8


@pytest.mark.asyncio
async def test_random_delay_equal_min_max() -> None:
    """Если min == max, задержка точно равна этому значению."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await random_delay(min_ms=300, max_ms=300)
    mock_sleep.assert_called_once_with(pytest.approx(0.3, abs=1e-9))


# ---------------------------------------------------------------------------
# bezier_points
# ---------------------------------------------------------------------------

def test_bezier_points_count() -> None:
    """Функция возвращает steps+1 точек."""
    pts = bezier_points((0, 0), (100, 100), steps=20)
    assert len(pts) == 21


def test_bezier_points_start_end() -> None:
    """Первая и последняя точки совпадают со start/end."""
    start = (10.0, 20.0)
    end = (200.0, 300.0)
    pts = bezier_points(start, end, steps=15)
    assert pts[0] == pytest.approx(start, abs=1e-6)
    assert pts[-1] == pytest.approx(end, abs=1e-6)


def test_bezier_points_custom_steps() -> None:
    """Проверяем разные значения steps."""
    for steps in [1, 5, 50]:
        pts = bezier_points((0, 0), (100, 0), steps=steps)
        assert len(pts) == steps + 1


def test_bezier_points_same_start_end() -> None:
    """Если start == end, все точки совпадают."""
    pts = bezier_points((50.0, 50.0), (50.0, 50.0), steps=10)
    for pt in pts:
        assert pt == pytest.approx((50.0, 50.0), abs=1e-5)


def test_bezier_points_horizontal() -> None:
    """Горизонтальное движение — y у start/end равны 0."""
    pts = bezier_points((0.0, 0.0), (100.0, 0.0), steps=10)
    assert pts[0][1] == pytest.approx(0.0, abs=1e-6)
    assert pts[-1][1] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# human_type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_human_type_calls_keyboard_type() -> None:
    """human_type вызывает page.keyboard.type для каждого символа."""
    page = MagicMock()
    element = AsyncMock()
    page.query_selector = AsyncMock(return_value=element)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await human_type(page, "#input", "hello")

    assert page.keyboard.type.call_count == 5
    calls = [c.args[0] for c in page.keyboard.type.call_args_list]
    assert calls == list("hello")


@pytest.mark.asyncio
async def test_human_type_clicks_element() -> None:
    """human_type кликает на элемент перед вводом."""
    page = MagicMock()
    element = AsyncMock()
    page.query_selector = AsyncMock(return_value=element)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await human_type(page, "#input", "ab")

    element.click.assert_called_once()


@pytest.mark.asyncio
async def test_human_type_sleeps_between_chars() -> None:
    """human_type делает паузу между каждым символом."""
    page = MagicMock()
    element = AsyncMock()
    page.query_selector = AsyncMock(return_value=element)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await human_type(page, "#input", "abc", delay_ms_min=50, delay_ms_max=50)

    assert mock_sleep.call_count == 3
    for call in mock_sleep.call_args_list:
        assert call.args[0] == pytest.approx(0.05, abs=1e-9)


@pytest.mark.asyncio
async def test_human_type_element_not_found() -> None:
    """Если элемент не найден, бросаем ValueError."""
    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Элемент не найден"):
        await human_type(page, "#missing", "text")


@pytest.mark.asyncio
async def test_human_type_empty_string() -> None:
    """Пустая строка — нет вызовов keyboard.type."""
    page = MagicMock()
    element = AsyncMock()
    page.query_selector = AsyncMock(return_value=element)
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await human_type(page, "#input", "")

    page.keyboard.type.assert_not_called()


# ---------------------------------------------------------------------------
# smooth_scroll
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smooth_scroll_calls_page_method() -> None:
    """smooth_scroll вызывает page.evaluate хотя бы раз для ненулевого расстояния."""
    page = MagicMock()
    page.evaluate = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await smooth_scroll(page, distance=300, steps=10)

    assert page.evaluate.call_count >= 1


@pytest.mark.asyncio
async def test_smooth_scroll_total_distance() -> None:
    """Сумма всех дельт прокрутки равна distance."""
    page = MagicMock()
    deltas: list[int] = []

    async def capture(js: str) -> None:
        # "window.scrollBy(0, N)" -> extract N
        part = js.split(",")[1].rstrip(")").strip()
        deltas.append(int(part))

    page.evaluate = capture

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await smooth_scroll(page, distance=200, steps=20)

    assert sum(deltas) == 200


@pytest.mark.asyncio
async def test_smooth_scroll_steps_sleep_count() -> None:
    """smooth_scroll делает ровно steps пауз."""
    page = MagicMock()
    page.evaluate = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await smooth_scroll(page, distance=100, steps=5)

    assert mock_sleep.call_count == 5


@pytest.mark.asyncio
async def test_smooth_scroll_single_step() -> None:
    """Работает при steps=1."""
    page = MagicMock()
    page.evaluate = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await smooth_scroll(page, distance=50, steps=1)

    page.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_smooth_scroll_zero_distance() -> None:
    """Нулевое расстояние — page.evaluate не вызывается."""
    page = MagicMock()
    page.evaluate = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await smooth_scroll(page, distance=0, steps=10)

    page.evaluate.assert_not_called()

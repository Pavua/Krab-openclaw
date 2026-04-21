"""Human-like interaction helpers для anti-bot detection слоя 6 (Chado §1).

Playwright-совместимые функции для:
- random_delay: случайная пауза с нормальным распределением
- bezier_move: курсор по кривой Безье между точками
- smooth_scroll: скролл с переменной скоростью
- human_type: печать символов с variable delay

Использование в Mercadona scraper / будущих X/Figma бот-контурах.
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


async def random_delay(min_ms: int = 200, max_ms: int = 800, skew: float = 0.3) -> None:
    """Pause with beta-skewed distribution — realistic thinking time.

    Использует betavariate(2,5) для смещения к нижней части диапазона
    (большинство пауз короткие, иногда длинные).
    """
    span = max_ms - min_ms
    raw = random.betavariate(2, 5) * span + min_ms
    await asyncio.sleep(raw / 1000.0)


def bezier_points(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int = 20,
) -> list[tuple[float, float]]:
    """Return list of (x, y) points along a quadratic Bezier curve.

    Контрольная точка — случайное смещение от середины отрезка,
    перпендикулярно направлению движения.
    """
    x0, y0 = start
    x1, y1 = end

    # Середина отрезка
    mx = (x0 + x1) / 2
    my = (y0 + y1) / 2

    # Перпендикуляр к вектору (dx, dy) — это (-dy, dx)
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy) or 1.0
    perp_x = -dy / length
    perp_y = dx / length

    # Случайное смещение контрольной точки (±20% длины)
    offset = random.uniform(-0.2, 0.2) * length
    cx = mx + perp_x * offset
    cy = my + perp_y * offset

    points: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        # Квадратичная Безье: B(t) = (1-t)^2*P0 + 2(1-t)t*C + t^2*P1
        w0 = (1 - t) ** 2
        w1 = 2 * (1 - t) * t
        w2 = t**2
        x = w0 * x0 + w1 * cx + w2 * x1
        y = w0 * y0 + w1 * cy + w2 * y1
        points.append((x, y))

    return points


async def human_type(
    page: Page,
    selector: str,
    text: str,
    delay_ms_min: int = 50,
    delay_ms_max: int = 150,
) -> None:
    """Type text char-by-char with random inter-key delay."""
    element = await page.query_selector(selector)
    if element is None:
        raise ValueError(f"Элемент не найден: {selector}")

    await element.click()
    for char in text:
        await page.keyboard.type(char)
        delay = random.uniform(delay_ms_min, delay_ms_max) / 1000.0
        await asyncio.sleep(delay)


async def smooth_scroll(page: Page, distance: int, steps: int = 20) -> None:
    """Scroll `distance` pixels over `steps` with eased variable speed.

    Использует синусоидальный easing (ease-in-out) для натурального движения.
    """
    if steps < 1:
        steps = 1

    scrolled = 0
    for i in range(1, steps + 1):
        # ease-in-out: sin(t*pi) даёт 0→1→0, интегрируем кумулятивно
        t = i / steps
        # Целевая позиция по eased кривой
        eased = (1 - math.cos(math.pi * t)) / 2
        target = int(distance * eased)
        delta = target - scrolled
        if delta != 0:
            await page.evaluate(f"window.scrollBy(0, {delta})")
            scrolled = target
        # Небольшая пауза между шагами (5-15 мс)
        await asyncio.sleep(random.uniform(0.005, 0.015))

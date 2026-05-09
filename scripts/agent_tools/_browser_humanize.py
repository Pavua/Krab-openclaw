"""Wave 44-Y-stealth-browser — human-like mouse/keyboard behavior.

- Bezier-curved mouse movements with variable step delays.
- Variable per-character typing speed with occasional pauses.
- Wheel-based scroll with random distances.

All functions are async and operate on a Playwright Page.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Any

logger = logging.getLogger(__name__)


def _bezier_point(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], t: float
) -> tuple[float, float]:
    """Quadratic bezier at t ∈ [0,1]."""
    one_minus = 1 - t
    x = one_minus * one_minus * p0[0] + 2 * one_minus * t * p1[0] + t * t * p2[0]
    y = one_minus * one_minus * p0[1] + 2 * one_minus * t * p1[1] + t * t * p2[1]
    return x, y


async def _move_bezier(
    page: Any, start: tuple[float, float], end: tuple[float, float], *, steps: int | None = None
) -> int:
    """Move mouse from start to end along quadratic bezier. Returns moves emitted."""
    if steps is None:
        steps = random.randint(8, 15)
    # control point — perpendicular offset from midpoint
    mx = (start[0] + end[0]) / 2.0
    my = (start[1] + end[1]) / 2.0
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy) or 1.0
    # perpendicular unit vector
    px, py = -dy / dist, dx / dist
    offset = random.uniform(-dist * 0.25, dist * 0.25)
    control = (mx + px * offset, my + py * offset)

    moves = 0
    for i in range(1, steps + 1):
        t = i / steps
        x, y = _bezier_point(start, control, end, t)
        try:
            await page.mouse.move(x, y, steps=1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mouse.move failed: %s", exc)
            break
        moves += 1
        await asyncio.sleep(random.uniform(0.010, 0.030))
    return moves


async def human_click(page: Any, selector: str, *, timeout_ms: int = 20_000) -> dict[str, Any]:
    """Click selector with bezier mouse path + dwell."""
    el = await page.wait_for_selector(selector, timeout=timeout_ms)
    if el is None:
        return {"ok": False, "error": "selector_not_found", "selector": selector}
    box = await el.bounding_box()
    if not box:
        # fallback
        await el.click()
        return {"ok": True, "selector": selector, "fallback": "no_bbox"}
    # target inside box (avoid exact corner)
    tx = box["x"] + random.uniform(box["width"] * 0.3, box["width"] * 0.7)
    ty = box["y"] + random.uniform(box["height"] * 0.3, box["height"] * 0.7)

    # Use viewport center as approximate current pos (Playwright doesn't expose mouse pos)
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    start = (viewport["width"] / 2.0, viewport["height"] / 2.0)
    moves = await _move_bezier(page, start, (tx, ty))
    # dwell
    await asyncio.sleep(random.uniform(0.050, 0.150))
    await page.mouse.click(tx, ty)
    return {"ok": True, "selector": selector, "moves": moves, "x": tx, "y": ty}


async def human_type(
    page: Any, selector: str, text: str, *, timeout_ms: int = 20_000, typo_rate: float = 0.0
) -> dict[str, Any]:
    """Type into selector with per-char variable delays."""
    el = await page.wait_for_selector(selector, timeout=timeout_ms)
    if el is None:
        return {"ok": False, "error": "selector_not_found", "selector": selector}
    await el.click()
    await asyncio.sleep(random.uniform(0.080, 0.200))

    typos = 0
    for i, ch in enumerate(text):
        # 1-2% typo + correction (default off)
        if typo_rate > 0 and random.random() < typo_rate and ch.isalpha():
            wrong = chr(((ord(ch.lower()) - ord("a") + random.randint(1, 3)) % 26) + ord("a"))
            await page.keyboard.type(wrong)
            await asyncio.sleep(random.uniform(0.080, 0.150))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.060, 0.120))
            typos += 1
        await page.keyboard.type(ch)
        # base char delay
        await asyncio.sleep(random.uniform(0.050, 0.180))
        # occasional thinking pause
        if i > 0 and i % random.randint(8, 20) == 0:
            await asyncio.sleep(random.uniform(0.200, 0.500))
    return {"ok": True, "selector": selector, "chars": len(text), "typos": typos}


async def human_scroll(page: Any, distance: int = 500, direction: str = "down") -> dict[str, Any]:
    """Scroll with multiple wheel events and random distances."""
    sign = 1 if direction == "down" else -1
    total = 0
    events = 0
    while abs(total) < distance:
        step = random.randint(30, 100)
        actual = min(step, distance - abs(total))
        await page.mouse.wheel(0, sign * actual)
        total += actual
        events += 1
        await asyncio.sleep(random.uniform(0.080, 0.250))
    return {"ok": True, "events": events, "distance": total, "direction": direction}


async def random_delay(min_ms: int = 500, max_ms: int = 2000) -> float:
    """Sleep random ms in [min, max]. Returns actual delay (sec)."""
    delay = random.uniform(min_ms / 1000.0, max_ms / 1000.0)
    await asyncio.sleep(delay)
    return delay

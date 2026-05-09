#!/usr/bin/env python3
"""
Генератор анимированного Telegram-аватара Краба.

Скрипт нужен, чтобы из локального репозитория воспроизводимо собрать квадратный
MP4-файл для аватарки Telegram: без браузера, CDN, API-ключей и ручной графики.
Он связан с предыдущим HTML-аватаром концептуально: сохраняет красный 3D-панцирь,
кибер-ядро и мягкую орбиту, но рендерит всё через Pillow в PNG-кадры, а затем
сжимает через ffmpeg в iPhone-friendly H.264.

Почему не экспорт из HTML: в текущем runtime браузерные headless-проверки иногда
упираются в sandbox, а этот пайплайн полностью локальный и повторяемый.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "telegram_avatar"
FRAMES_DIR = OUT_DIR / "frames"
MP4_PATH = OUT_DIR / "krab_telegram_avatar.mp4"
WEBM_PATH = OUT_DIR / "krab_telegram_avatar.webm"
POSTER_PATH = OUT_DIR / "krab_telegram_avatar_poster.png"
TMP_MP4_PATH = Path("/tmp/krab_telegram_avatar.mp4")
SIZE = 720
FPS = 30
DURATION_SECONDS = 4
FRAME_COUNT = FPS * DURATION_SECONDS


def clamp(value: int) -> int:
    """Ограничивает цветовой канал диапазоном 0..255."""
    return max(0, min(255, value))


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Смешивает два RGB-цвета; нужно для псевдо-3D градиентов без внешних библиотек."""
    return (
        clamp(int(a[0] + (b[0] - a[0]) * t)),
        clamp(int(a[1] + (b[1] - a[1]) * t)),
        clamp(int(a[2] + (b[2] - a[2]) * t)),
    )


def ellipse_gradient(
    layer: Image.Image,
    box: tuple[int, int, int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    alpha: int,
) -> None:
    """Рисует эллипс с вертикальным градиентом, чтобы панцирь не выглядел плоским."""
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = grad.load()
    for y in range(height):
        t = y / max(1, height - 1)
        color = mix(top, bottom, t)
        for x in range(width):
            pixels[x, y] = (*color, alpha)

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, width - 1, height - 1), fill=255)
    layer.paste(grad, (x1, y1), mask)


def glow(layer: Image.Image, center: tuple[float, float], radius: float, color: tuple[int, int, int], alpha: int) -> None:
    """Добавляет мягкое свечение вокруг ядра и орбит."""
    cx, cy = center
    glow_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow_layer)
    steps = 18
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius * t
        a = int(alpha * (1 - t) ** 1.8)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, a))
    layer.alpha_composite(glow_layer)


def draw_capsule(
    layer: Image.Image,
    center: tuple[float, float],
    length: float,
    width: float,
    angle: float,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int] | None = None,
) -> None:
    """Рисует повернутую лапу как капсулу, чтобы силуэт оставался читаемым в круглом кропе."""
    cx, cy = center
    local = Image.new("RGBA", (int(length + width * 2), int(width * 3)), (0, 0, 0, 0))
    ld = ImageDraw.Draw(local)
    pad = width
    ld.rounded_rectangle((pad, width, pad + length, width * 2), radius=width / 2, fill=fill, outline=outline, width=2)
    rotated = local.rotate(math.degrees(angle), resample=Image.Resampling.BICUBIC, expand=True)
    layer.alpha_composite(rotated, (int(cx - rotated.width / 2), int(cy - rotated.height / 2)))


def draw_claw(
    base: Image.Image,
    pivot: tuple[float, float],
    side: int,
    phase: float,
    body_shift: float,
) -> None:
    """Рисует клешню; небольшое раскрытие делает аватар живым, но не суетливым."""
    claw_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(claw_layer)
    px, py = pivot
    open_amount = math.sin(phase * math.tau) * 0.08
    arm_angle = side * (0.48 + open_amount)
    arm_center = (px + side * 118, py - 22 + body_shift * 0.25)
    draw_capsule(claw_layer, arm_center, 150, 34, arm_angle, (177, 47, 36, 255), (255, 136, 116, 120))

    cx = px + side * 204
    cy = py - 70 + body_shift * 0.3
    ellipse_gradient(claw_layer, (int(cx - 58), int(cy - 54), int(cx + 54), int(cy + 64)), (255, 103, 83), (119, 27, 22), 255)
    draw.ellipse((cx - 58, cy - 54, cx + 54, cy + 64), outline=(255, 181, 150, 145), width=3)

    # Два "пальца" клешни специально крупные: Telegram обрежет аватар кругом.
    finger_angle = side * (0.36 + open_amount * 1.4)
    draw_capsule(claw_layer, (cx + side * 44, cy - 42), 76, 30, finger_angle, (224, 68, 53, 255), (255, 197, 166, 120))
    draw_capsule(claw_layer, (cx + side * 43, cy + 38), 72, 28, -finger_angle * 0.8, (157, 35, 28, 255), (255, 197, 166, 100))
    base.alpha_composite(claw_layer)


def draw_frame(index: int) -> Image.Image:
    """Создаёт один кадр зацикленной 4-секундной анимации."""
    phase = index / FRAME_COUNT
    pulse = (math.sin(phase * math.tau) + 1) / 2
    breath = math.sin(phase * math.tau * 2)
    body_shift = math.sin(phase * math.tau) * 8
    turn = math.sin(phase * math.tau) * 10

    image = Image.new("RGBA", (SIZE, SIZE), (6, 14, 18, 255))
    bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bg)

    for radius, alpha, color in ((360, 90, (47, 236, 255)), (250, 70, (255, 210, 107)), (150, 42, (98, 255, 155))):
        glow(bg, (SIZE / 2, SIZE / 2 - 36), radius * (0.85 + pulse * 0.08), color, alpha)

    # Звёзды фиксированы математически, чтобы видео было стабильным между сборками.
    for star in range(72):
        sx = (star * 97 + 53) % SIZE
        sy = (star * 181 + 29) % SIZE
        twinkle = (math.sin(phase * math.tau * 2 + star * 0.7) + 1) / 2
        a = int(35 + 100 * twinkle)
        draw.ellipse((sx - 1, sy - 1, sx + 1, sy + 1), fill=(188, 246, 255, a))

    # Орбиты задают технологичный характер без лишнего текста на аватарке.
    orbit_box = (84, 468, 636, 610)
    for offset, color, alpha in ((0, (86, 241, 255), 120), (22, (255, 210, 107), 72)):
        bbox = tuple(v + (offset if i % 2 else -offset) for i, v in enumerate(orbit_box))
        draw.ellipse(bbox, outline=(*color, alpha), width=3)
    image.alpha_composite(bg.filter(ImageFilter.GaussianBlur(0.3)))

    creature = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(creature)

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.ellipse((190, 505, 530, 584), fill=(0, 0, 0, 118))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    for side in (-1, 1):
        draw_claw(creature, (360, 352), side, phase + (0.07 if side > 0 else 0.0), body_shift)

    # Лапы вынесены до панциря, чтобы панцирь визуально сидел поверх.
    for side in (-1, 1):
        for i, y in enumerate((408, 444, 480)):
            leg_phase = math.sin(phase * math.tau * 2 + i * 0.85 + (side > 0) * 0.45) * 0.08
            angle = side * (0.45 + i * 0.12 + leg_phase)
            center = (360 + side * (166 + i * 22), y + body_shift * 0.22)
            draw_capsule(creature, center, 122 - i * 8, 20, angle, (126, 31, 29, 245), (255, 115, 92, 90))

    body_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bx1 = int(186 - turn * 0.9)
    bx2 = int(534 + turn * 0.9)
    by1 = int(244 + body_shift)
    by2 = int(492 + body_shift)
    ellipse_gradient(body_layer, (bx1, by1, bx2, by2), (255, 118, 92), (87, 18, 17), 255)
    bdraw = ImageDraw.Draw(body_layer)
    bdraw.ellipse((bx1, by1, bx2, by2), outline=(255, 190, 157, 136), width=4)
    bdraw.arc((bx1 + 52, by1 + 36, bx2 - 52, by2 - 58), 184, 356, fill=(255, 221, 197, 110), width=5)
    bdraw.arc((bx1 + 80, by1 + 96, bx2 - 80, by2 - 28), 20, 160, fill=(31, 5, 7, 115), width=5)

    for i in range(5):
        x = 238 + i * 61 + math.sin(phase * math.tau + i) * 2
        y = by1 + 105 + abs(i - 2) * 8
        bdraw.arc((x - 28, y - 26, x + 28, y + 42), 210, 330, fill=(92, 18, 18, 105), width=3)

    # Кибер-ядро — узнаваемый маркер AI-ассистента, видно даже в маленькой аватарке.
    core_r = 58 + pulse * 7
    core_center = (360, 374 + body_shift)
    glow(body_layer, core_center, 118 + pulse * 18, (88, 246, 255), 210)
    bdraw.ellipse(
        (core_center[0] - core_r, core_center[1] - core_r, core_center[0] + core_r, core_center[1] + core_r),
        fill=(45, 230, 245, 235),
        outline=(235, 255, 255, 210),
        width=4,
    )
    bdraw.ellipse(
        (core_center[0] - 25, core_center[1] - 25, core_center[0] + 25, core_center[1] + 25),
        outline=(8, 37, 42, 150),
        width=4,
    )
    bdraw.arc(
        (core_center[0] - 43, core_center[1] - 43, core_center[0] + 43, core_center[1] + 43),
        int(phase * 360),
        int(phase * 360 + 230),
        fill=(166, 255, 133, 235),
        width=5,
    )

    # Глаза чуть крупнее реалистичных: в Telegram это работает лучше, чем микродетали.
    for side in (-1, 1):
        ex = 302 + side * 58 + math.sin(phase * math.tau + side) * 2
        ey = 246 + body_shift * 0.65
        cdraw.rounded_rectangle((ex - 16, ey - 52, ex + 16, ey + 12), radius=15, fill=(119, 31, 28, 255))
        cdraw.ellipse((ex - 26, ey - 73, ex + 26, ey - 21), fill=(236, 250, 242, 255), outline=(32, 44, 46, 190), width=3)
        cdraw.ellipse((ex - 9 + side * 3, ey - 57, ex + 10 + side * 3, ey - 35), fill=(6, 19, 22, 255))
        cdraw.ellipse((ex - 2 + side * 3, ey - 54, ex + 4 + side * 3, ey - 48), fill=(255, 255, 255, 210))

    creature.alpha_composite(body_layer)
    creature = creature.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=3))
    image.alpha_composite(creature)

    # Финальная виньетка защищает круглый кроп Telegram и добавляет глубину.
    vignette = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    for i in range(44):
        a = int(i * 2.3)
        vdraw.ellipse((i * -8, i * -8, SIZE - i * -8, SIZE - i * -8), outline=(0, 0, 0, a), width=8)
    image.alpha_composite(vignette.filter(ImageFilter.GaussianBlur(2)))
    return image.convert("RGB")


def run(command: list[str]) -> None:
    """Запускает внешнюю команду и печатает её одной строкой для понятной диагностики."""
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def main() -> None:
    """Собирает PNG-кадры, MP4, WebM и постер."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True)

    for index in range(FRAME_COUNT):
        frame = draw_frame(index)
        frame_path = FRAMES_DIR / f"frame_{index:04d}.png"
        frame.save(frame_path, optimize=True)
        if index == FRAME_COUNT // 4:
            frame.save(POSTER_PATH, optimize=True)

    run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(FRAMES_DIR / "frame_%04d.png"),
            "-vf",
            "format=yuv420p,scale=720:720:flags=lanczos",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-crf",
            "20",
            str(MP4_PATH),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(FRAMES_DIR / "frame_%04d.png"),
            "-vf",
            "scale=720:720:flags=lanczos",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            "34",
            str(WEBM_PATH),
        ]
    )
    shutil.copy2(MP4_PATH, TMP_MP4_PATH)
    print(f"MP4: {MP4_PATH}")
    print(f"WebM: {WEBM_PATH}")
    print(f"Poster: {POSTER_PATH}")
    print(f"Telegram MEDIA path: {TMP_MP4_PATH}")


if __name__ == "__main__":
    main()

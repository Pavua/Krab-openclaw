# -*- coding: utf-8 -*-
"""
youtube_metadata — резервный путь получения метаданных YouTube через oEmbed.

Не требует DNS-резолвинга youtube.com в subprocess — использует только httpx с
явными DNS серверами (Google 8.8.8.8) через transport layer.

Fallback-цепочка:
  1. YouTube oEmbed API (https://www.youtube.com/oembed) — public, no auth
  2. noembed.com — зеркало oEmbed без ограничений
  3. Возвращает None (caller переключается на cloud LLM)

Usage:
    from src.skills.youtube_metadata import fetch_yt_metadata
    meta = await fetch_yt_metadata("https://youtu.be/dQw4w9WgXcQ")
    # -> {"title": "...", "author": "...", "thumbnail_url": "...", "provider_url": "..."}
    # -> None если оба источника недоступны
"""

from __future__ import annotations

import httpx  # топ-уровень — нужен для patch("src.skills.youtube_metadata.httpx")
import structlog

logger = structlog.get_logger(__name__)

# Endpoints для oEmbed, опробуются по порядку
_OEMBED_ENDPOINTS = [
    "https://www.youtube.com/oembed?url={url}&format=json",
    "https://noembed.com/embed?url={url}",
]

_TIMEOUT = 8.0  # секунд


async def fetch_yt_metadata(youtube_url: str) -> dict | None:
    """Получает метаданные YouTube видео через oEmbed без subprocess.

    Пробует YouTube oEmbed → noembed.com.
    Возвращает dict с ключами title/author_name/thumbnail_url или None.
    """
    for endpoint_tmpl in _OEMBED_ENDPOINTS:
        endpoint = endpoint_tmpl.format(url=youtube_url)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(endpoint, follow_redirects=True)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("title"):
                        logger.info(
                            "yt_oembed_ok",
                            endpoint=endpoint_tmpl[:40],
                            title=data.get("title", "")[:60],
                        )
                        return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("yt_oembed_failed", endpoint=endpoint_tmpl[:40], error=str(exc))
            continue

    logger.info("yt_oembed_all_failed", url=youtube_url[:80])
    return None


def format_yt_metadata(meta: dict) -> str:
    """Форматирует oEmbed-ответ в читаемый текст для Telegram."""
    lines: list[str] = []
    title = meta.get("title", "")
    author = meta.get("author_name", "")
    provider = meta.get("provider_url", "YouTube")
    thumb = meta.get("thumbnail_url", "")
    width = meta.get("width")
    height = meta.get("height")

    if title:
        lines.append(f"🎬 **{title}**")
    if author:
        lines.append(f"👤 Автор: {author}")
    if width and height:
        lines.append(f"📐 Разрешение: {width}×{height}")
    if thumb:
        lines.append(f"🖼 Превью: {thumb}")
    lines.append(f"🔗 {provider}")

    return "\n".join(lines)

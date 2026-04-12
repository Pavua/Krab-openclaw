# -*- coding: utf-8 -*-
"""
telegram_buttons.py — вспомогательные функции для создания inline-кнопок в Telegram.

Поддерживаемые схемы callback_data:
  confirm:<action_id>:yes   — подтверждение действия
  confirm:<action_id>:no    — отказ от действия
  page:<prefix>:<page>      — переход к странице (0-based)
  action:<action_id>        — произвольное действие
"""
from __future__ import annotations

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_confirm_buttons(action_id: str) -> InlineKeyboardMarkup:
    """Кнопки подтверждения: ✅ Да / ❌ Нет."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да", callback_data=f"confirm:{action_id}:yes"),
                InlineKeyboardButton("❌ Нет", callback_data=f"confirm:{action_id}:no"),
            ]
        ]
    )


def build_pagination_buttons(
    page: int,
    total_pages: int,
    prefix: str,
) -> InlineKeyboardMarkup:
    """
    Кнопки навигации по страницам.

    page — текущая страница (0-based).
    total_pages — общее количество страниц.
    prefix — произвольный префикс для группировки (например, «results»).
    """
    buttons: list[InlineKeyboardButton] = []

    if page > 0:
        buttons.append(
            InlineKeyboardButton("◀️", callback_data=f"page:{prefix}:{page - 1}")
        )

    # Индикатор текущей позиции
    buttons.append(
        InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data=f"page:{prefix}:noop",
        )
    )

    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton("▶️", callback_data=f"page:{prefix}:{page + 1}")
        )

    return InlineKeyboardMarkup([buttons])


def build_action_buttons(
    actions: list[tuple[str, str]],
    columns: int = 1,
) -> InlineKeyboardMarkup:
    """
    Произвольный набор кнопок.

    actions — список пар (label, callback_data).
    columns — количество кнопок в строке (дефолт 1).
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for label, cb_data in actions:
        row.append(InlineKeyboardButton(label, callback_data=f"action:{cb_data}"))
        if len(row) >= columns:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Предустановленные наборы кнопок
# ---------------------------------------------------------------------------

def build_swarm_team_buttons() -> InlineKeyboardMarkup:
    """Кнопки выбора swarm-команды."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📈 Traders", callback_data="action:swarm_team:traders")],
            [InlineKeyboardButton("💻 Coders", callback_data="action:swarm_team:coders")],
            [InlineKeyboardButton("🔬 Analysts", callback_data="action:swarm_team:analysts")],
            [InlineKeyboardButton("🎨 Creative", callback_data="action:swarm_team:creative")],
        ]
    )


def build_costs_detail_buttons() -> InlineKeyboardMarkup:
    """Кнопка «Подробнее по моделям» для !costs."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔍 Подробнее по моделям",
                    callback_data="action:costs_detail",
                )
            ]
        ]
    )


def build_health_recheck_buttons() -> InlineKeyboardMarkup:
    """Кнопка «Перепроверить» для !health."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Перепроверить",
                    callback_data="action:health_recheck",
                )
            ]
        ]
    )

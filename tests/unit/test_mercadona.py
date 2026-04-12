# -*- coding: utf-8 -*-
"""
Тесты Mercadona skill и Telegram handler `!shop`.

Фиксируем базовый контракт:
- нормализация товара из API не должна дрейфовать незаметно;
- форматированный ответ должен оставаться человекочитаемым;
- `handle_shop` должен корректно отвечать как в happy-path, так и при пустом вводе.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.skills.mercadona as mercadona_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_shop


def test_normalize_product_prefers_price_instructions() -> None:
    """Нормализация должна брать display_name и unit/reference-цену из API-полей Mercadona."""
    product = mercadona_module._normalize_product(
        {
            "display_name": "Leche entera",
            "price_instructions": {
                "unit_price": "1.15",
                "unit_name": "l",
                "reference_price": "1.15",
                "reference_format": "l",
            },
            "thumbnail": "https://img.example/milk.jpg",
        }
    )

    assert product == {
        "name": "Leche entera",
        "price": "1.15",
        "unit": "l",
        "reference_price": "1.15",
        "reference_unit": "l",
        "thumbnail": "https://img.example/milk.jpg",
    }


def test_extract_products_reads_search_results_payload() -> None:
    """Из search payload с `results` должны извлекаться все товары."""
    out: list[dict[str, str]] = []

    mercadona_module._extract_products(
        {
            "results": [
                {"display_name": "Pan", "price": "0.95"},
                {"display_name": "Huevos", "price": "2.40"},
            ]
        },
        out,
    )

    assert [item["name"] for item in out] == ["Pan", "Huevos"]
    assert [item["price"] for item in out] == ["0.95", "2.40"]


def test_format_results_renders_compact_telegram_text() -> None:
    """Форматирование должно оставаться читаемым для Telegram-ответа."""
    rendered = mercadona_module._format_results(
        "leche",
        [
            {
                "name": "Leche entera",
                "price": "1.15",
                "unit": "l",
                "reference_price": "1.15",
                "reference_unit": "l",
                "thumbnail": "",
            },
            {
                "name": "Leche sin lactosa",
                "price": "1.35",
                "unit": "l",
                "reference_price": "",
                "reference_unit": "",
                "thumbnail": "",
            },
        ],
    )

    assert "Mercadona" in rendered
    assert "Leche entera" in rendered
    assert "1.15 € / l" in rendered
    assert "~1.15 €/l" in rendered
    assert "Leche sin lactosa" in rendered


def test_normalize_dom_product_card_prefers_aria_payload() -> None:
    """DOM-карточка search-results должна превращаться в нормализованный товар."""
    product = mercadona_module._normalize_dom_product_card(
        {
            "text": "Leche semidesnatada Hacendado 6 briks x 1 L 5,28 € /pack",
            "aria": "Leche semidesnatada Hacendado, 6 briks x 1 Litro, 5,28€ por Pack",
        }
    )

    assert product == {
        "name": "Leche semidesnatada Hacendado — 6 briks x 1 Litro",
        "price": "5,28",
        "unit": "pack",
        "reference_price": "",
        "reference_unit": "",
        "thumbnail": "",
    }


@pytest.mark.asyncio
async def test_handle_shop_replies_with_skill_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!shop` должен отредактировать временное сообщение результатом поиска."""
    progress_message = SimpleNamespace(edit=AsyncMock())
    message = SimpleNamespace(reply=AsyncMock(return_value=progress_message))
    bot = SimpleNamespace(_get_command_args=lambda _: "молоко")

    monkeypatch.setattr(
        mercadona_module,
        "search_mercadona",
        AsyncMock(return_value="🛒 **Mercadona** — результаты по «молоко»:"),
    )

    await handle_shop(bot, message)

    reply_text = message.reply.await_args.args[0]
    assert "Краб ищет на Mercadona" in reply_text
    progress_message.edit.assert_awaited_once()
    assert "результаты" in progress_message.edit.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_shop_requires_query() -> None:
    """Пустой `!shop` должен отдавать понятную user-facing ошибку."""
    message = SimpleNamespace()
    bot = SimpleNamespace(_get_command_args=lambda _: "")

    with pytest.raises(UserInputError) as exc_info:
        await handle_shop(bot, message)

    assert "!shop <товар>" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_shop_reports_search_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Падение skill должно превращаться в Telegram-friendly ошибку, а не в silent fail."""
    progress_message = SimpleNamespace(edit=AsyncMock())
    message = SimpleNamespace(reply=AsyncMock(return_value=progress_message))
    bot = SimpleNamespace(_get_command_args=lambda _: "масло")

    monkeypatch.setattr(
        mercadona_module,
        "search_mercadona",
        AsyncMock(side_effect=RuntimeError("anti-bot timeout")),
    )

    await handle_shop(bot, message)

    progress_message.edit.assert_awaited_once()
    rendered = progress_message.edit.await_args.args[0]
    assert "Ошибка при поиске на Mercadona" in rendered
    assert "anti-bot timeout" in rendered

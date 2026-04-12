# -*- coding: utf-8 -*-
"""
Тесты для src/skills/mercadona.py.
Playwright не запускается — все браузерные вызовы мокаются.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Импорт тестируемых функций (только чистые, без Playwright)
# ---------------------------------------------------------------------------
from src.skills.mercadona import (
    _HOME_URL,
    _MERCADONA_BASE,
    _SEARCH_RESULTS_URL_RE,
    _STEALTH_SCRIPT,
    _extract_products,
    _format_results,
    _normalize_dom_product_card,
    _normalize_product,
    _resolve_postal_code,
    search_mercadona,
)

# ===========================================================================
# 1. Константы и URL
# ===========================================================================


class TestConstants:
    """Проверяем, что базовые URL и регулярки заданы правильно."""

    def test_base_url(self):
        """Базовый URL указывает на испанский домен Mercadona."""
        assert _MERCADONA_BASE == "https://tienda.mercadona.es"

    def test_home_url(self):
        """Домашний URL строится из базового."""
        assert _HOME_URL.startswith(_MERCADONA_BASE)
        assert _HOME_URL.endswith("/")

    def test_search_results_url_re_matches(self):
        """Regex распознаёт URL страницы результатов поиска."""
        assert _SEARCH_RESULTS_URL_RE.search("/search-results?query=leche")
        assert _SEARCH_RESULTS_URL_RE.search("/search-results")

    def test_search_results_url_re_no_match(self):
        """Regex не срабатывает на обычные страницы."""
        assert not _SEARCH_RESULTS_URL_RE.search("/categories/all")
        assert not _SEARCH_RESULTS_URL_RE.search("/")


# ===========================================================================
# 2. Stealth-скрипт
# ===========================================================================


class TestStealthScript:
    """Антибот JS-патч должен скрывать признаки автоматизации."""

    def test_webdriver_property_hidden(self):
        """Скрипт переопределяет navigator.webdriver."""
        assert "webdriver" in _STEALTH_SCRIPT

    def test_plugins_spoof(self):
        """Скрипт подменяет navigator.plugins."""
        assert "plugins" in _STEALTH_SCRIPT

    def test_chrome_runtime_injected(self):
        """Скрипт добавляет window.chrome.runtime — признак обычного Chrome."""
        assert "window.chrome" in _STEALTH_SCRIPT

    def test_languages_es(self):
        """Скрипт выставляет испанский язык."""
        assert "es-ES" in _STEALTH_SCRIPT


# ===========================================================================
# 3. Почтовый индекс
# ===========================================================================


class TestResolvePostalCode:
    """_resolve_postal_code() читает env или возвращает дефолт."""

    def test_default_postal_code(self, monkeypatch):
        """Без env-переменной возвращается 28001."""
        monkeypatch.delenv("MERCADONA_POSTAL_CODE", raising=False)
        assert _resolve_postal_code() == "28001"

    def test_env_override(self, monkeypatch):
        """MERCADONA_POSTAL_CODE переопределяет дефолт."""
        monkeypatch.setenv("MERCADONA_POSTAL_CODE", "46001")
        assert _resolve_postal_code() == "46001"

    def test_empty_env_falls_back(self, monkeypatch):
        """Пустая env-переменная возвращает 28001."""
        monkeypatch.setenv("MERCADONA_POSTAL_CODE", "   ")
        assert _resolve_postal_code() == "28001"


# ===========================================================================
# 4. Парсинг API-ответа — _normalize_product
# ===========================================================================


class TestNormalizeProduct:
    """_normalize_product() нормализует один товар из API Mercadona."""

    def test_full_item(self):
        """Полный товар: имя, цена, единица, справочная цена."""
        item = {
            "display_name": "Leche entera",
            "price_instructions": {
                "unit_price": "0.89",
                "unit_name": "L",
                "reference_price": "0.89",
                "reference_format": "litro",
            },
            "thumbnail": "https://cdn.example.com/img.jpg",
        }
        result = _normalize_product(item)
        assert result is not None
        assert result["name"] == "Leche entera"
        assert result["price"] == "0.89"
        assert result["unit"] == "L"
        assert result["reference_price"] == "0.89"
        assert result["reference_unit"] == "litro"
        assert result["thumbnail"] == "https://cdn.example.com/img.jpg"

    def test_fallback_name_fields(self):
        """Фолбэк на name/title, если display_name отсутствует."""
        item = {"name": "Pan de molde", "price": "1.20"}
        result = _normalize_product(item)
        assert result is not None
        assert result["name"] == "Pan de molde"

    def test_missing_name_returns_none(self):
        """Товар без имени отбрасывается."""
        assert _normalize_product({"price": "1.00"}) is None

    def test_non_dict_returns_none(self):
        """Не-словарь возвращает None."""
        assert _normalize_product("leche") is None
        assert _normalize_product(None) is None


# ===========================================================================
# 5. Рекурсивный сборщик — _extract_products
# ===========================================================================


class TestExtractProducts:
    """_extract_products() собирает товары из разных форматов API."""

    def test_results_key(self):
        """Формат /api/search: {"results": [...]}."""
        data = {"results": [{"display_name": "Tomate", "price": "0.50"}]}
        out: list = []
        _extract_products(data, out)
        assert len(out) == 1
        assert out[0]["name"] == "Tomate"

    def test_products_key(self):
        """Формат /api/products: {"products": [...]}."""
        data = {"products": [{"display_name": "Queso", "price": "2.00"}]}
        out: list = []
        _extract_products(data, out)
        assert len(out) == 1

    def test_list_input(self):
        """Список товаров обрабатывается рекурсивно."""
        data = [
            {"display_name": "Manzana", "price": "1.00"},
            {"display_name": "Pera", "price": "0.90"},
        ]
        out: list = []
        _extract_products(data, out)
        assert len(out) == 2

    def test_unknown_dict_tries_single_product(self):
        """Нераспознанный словарь пробуется как одиночный товар."""
        data = {"display_name": "Aceite", "price": "3.50"}
        out: list = []
        _extract_products(data, out)
        assert len(out) == 1


# ===========================================================================
# 6. Парсинг DOM-карточек — _normalize_dom_product_card
# ===========================================================================


class TestNormalizeDomProductCard:
    """_normalize_dom_product_card() разбирает карточку из aria-label / innerText."""

    def test_aria_label_full(self):
        """Полный aria-label со всеми полями."""
        card = {
            "aria": "Leche desnatada, 1 litro, 0,75€ por litro",
            "text": "",
        }
        result = _normalize_dom_product_card(card)
        assert result is not None
        assert "Leche desnatada" in result["name"]
        assert result["price"] == "0,75"
        assert result["unit"] == "litro"

    def test_text_fallback(self):
        """Если aria пустой — берём innerText с ценой."""
        card = {
            "aria": "",
            "text": "Yogur natural 0,45 € / unidad",
        }
        result = _normalize_dom_product_card(card)
        assert result is not None
        assert result["price"] == "0,45"

    def test_empty_card_returns_none(self):
        """Пустая карточка — None."""
        assert _normalize_dom_product_card({}) is None

    def test_non_dict_returns_none(self):
        """Не словарь — None."""
        assert _normalize_dom_product_card("string") is None


# ===========================================================================
# 7. Форматирование результатов — _format_results
# ===========================================================================


class TestFormatResults:
    """_format_results() готовит Markdown-строку для вывода в Telegram."""

    def _make_product(self, name="Leche", price="0.89", unit="L", ref_price="", ref_unit=""):
        return {
            "name": name,
            "price": price,
            "unit": unit,
            "reference_price": ref_price,
            "reference_unit": ref_unit,
            "thumbnail": "",
        }

    def test_header_contains_query(self):
        """Заголовок содержит поисковый запрос."""
        result = _format_results("leche", [self._make_product()])
        assert "leche" in result

    def test_product_listed(self):
        """Товар отображается с номером, именем и ценой."""
        result = _format_results("leche", [self._make_product(name="Leche entera", price="0.89")])
        assert "Leche entera" in result
        assert "0.89 €" in result

    def test_unit_included(self):
        """Единица измерения добавляется к цене."""
        result = _format_results("leche", [self._make_product(unit="kg")])
        assert "/ kg" in result

    def test_reference_price_shown(self):
        """Справочная цена отображается в скобках."""
        product = self._make_product(ref_price="0.89", ref_unit="litro")
        result = _format_results("leche", [product])
        assert "0.89" in result
        assert "litro" in result

    def test_multiple_products_numbered(self):
        """Несколько товаров нумеруются последовательно."""
        products = [
            self._make_product(name="Leche"),
            self._make_product(name="Pan"),
        ]
        result = _format_results("test", products)
        assert "1." in result
        assert "2." in result


# ===========================================================================
# 8. search_mercadona — mock Playwright
# ===========================================================================


class TestSearchMercadona:
    """search_mercadona() — интеграция с Playwright через полное мокирование."""

    @pytest.mark.asyncio
    async def test_playwright_not_installed(self):
        """Если playwright не установлен, возвращается сообщение об ошибке."""
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            # Имитируем ImportError при импорте внутри функции
            import builtins

            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "playwright.async_api":
                    raise ImportError("no playwright")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = await search_mercadona("leche")
        assert "playwright" in result.lower() or "❌" in result

    @pytest.mark.asyncio
    async def test_returns_no_results_string(self):
        """Если товары не найдены, возвращается сообщение «не найдены»."""
        # Мокаем весь async_playwright контекст
        mock_page = AsyncMock()
        mock_page.url = "https://tienda.mercadona.es/"
        mock_page.on = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_page.wait_for_url = AsyncMock(side_effect=Exception("timeout"))
        mock_page.keyboard = AsyncMock()
        mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
        mock_page.get_by_text = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
        mock_page.get_by_role = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
        mock_page.get_by_placeholder = MagicMock(
            return_value=AsyncMock(count=AsyncMock(return_value=0))
        )
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=None)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.add_init_script = AsyncMock()
        mock_context.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw.__aexit__ = AsyncMock(return_value=False)

        mock_cfg = MagicMock()
        mock_cfg.TOR_ENABLED = False

        mock_async_playwright_module = MagicMock()
        mock_async_playwright_module.async_playwright = MagicMock(return_value=mock_pw)

        with (
            patch.dict("sys.modules", {"playwright.async_api": mock_async_playwright_module}),
            patch("src.skills.mercadona._submit_search_query", new=AsyncMock(return_value=False)),
        ):
            result = await search_mercadona("leche inexistente")

        assert "Mercadona" in result or "leche" in result

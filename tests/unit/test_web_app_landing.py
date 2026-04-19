"""Тесты для web_app_landing_page.py — HTML генерация, шаблонные переменные, CSS."""

from src.modules.web_app_landing_page import LANDING_PAGE_HTML

# --- Базовая структура HTML ---


def test_html_is_string():
    """Константа должна быть непустой строкой."""
    assert isinstance(LANDING_PAGE_HTML, str)
    assert len(LANDING_PAGE_HTML) > 0


def test_html_doctype_and_lang():
    """Документ начинается с DOCTYPE и имеет lang=ru."""
    assert LANDING_PAGE_HTML.strip().startswith("<!DOCTYPE html>")
    assert 'lang="ru"' in LANDING_PAGE_HTML


def test_html_title():
    """Заголовок страницы содержит 'Krab Control Panel'."""
    assert "<title>Krab Control Panel</title>" in LANDING_PAGE_HTML


def test_html_charset_utf8():
    """Кодировка UTF-8 должна быть объявлена."""
    assert 'charset="UTF-8"' in LANDING_PAGE_HTML


# --- CSS-переменные и стили ---


def test_css_custom_properties():
    """Ключевые CSS-переменные присутствуют в :root."""
    for var in ("--bg", "--card-bg", "--border", "--text", "--text-muted", "--accent"):
        assert var in LANDING_PAGE_HTML, f"CSS-переменная {var} не найдена"


def test_css_accent_color():
    """Акцентный цвет должен быть задан (7dd3fc — голубой)."""
    assert "#7dd3fc" in LANDING_PAGE_HTML


def test_css_fade_in_animation():
    """Анимация fadeIn должна быть определена."""
    assert "@keyframes fadeIn" in LANDING_PAGE_HTML


# --- Навигация и структура ---


def test_nav_links_present():
    """Все навигационные ссылки присутствуют."""
    for href in ('href="/"', 'href="/stats"', 'href="/inbox"', 'href="/costs"', 'href="/swarm"'):
        assert href in LANDING_PAGE_HTML, f"Ссылка {href} не найдена"


def test_nav_active_home():
    """Ссылка на главную должна иметь класс active."""
    assert 'href="/" class="active"' in LANDING_PAGE_HTML


# --- Quick Stats тайлы ---


def test_stat_tiles_ids():
    """Все id для Quick Stats тайлов присутствуют."""
    for stat_id in ("stat-tg", "stat-inbox", "stat-voice", "stat-sched"):
        assert f'id="{stat_id}"' in LANDING_PAGE_HTML, f"id={stat_id} не найден"


def test_stat_labels():
    """Текстовые метки тайлов присутствуют."""
    for label in ("Telegram", "Inbox Open", "Voice", "Scheduler"):
        assert label in LANDING_PAGE_HTML, f"Метка '{label}' не найдена"


# --- Card Grid ---


def test_card_grid_links():
    """Карточки ссылаются на все основные разделы."""
    for href in ("/stats", "/inbox", "/costs", "/swarm"):
        assert f'href="{href}"' in LANDING_PAGE_HTML


def test_card_grid_emojis():
    """Эмодзи карточек присутствуют."""
    for emoji in ("📊", "📥", "💰", "🐝"):
        assert emoji in LANDING_PAGE_HTML, f"Эмодзи {emoji} не найден"


# --- JavaScript ---


def test_js_fetch_api_endpoint():
    """JS обращается к /api/health/lite для получения данных."""
    assert "/api/health/lite" in LANDING_PAGE_HTML


def test_js_clock_update():
    """JS-функция обновления часов присутствует."""
    assert "updateClock" in LANDING_PAGE_HTML


def test_js_polling_interval():
    """Polling интервал задан для периодического обновления данных."""
    assert "setInterval(fetchStats" in LANDING_PAGE_HTML


def test_js_fallback_mock_data():
    """JS содержит fallback mock-данные при недоступности API."""
    assert "Fallback mock data" in LANDING_PAGE_HTML or "fallback" in LANDING_PAGE_HTML.lower()


# --- Footer ---


def test_footer_present():
    """Footer с упоминанием Krab присутствует."""
    assert "<footer>" in LANDING_PAGE_HTML
    assert "Krab" in LANDING_PAGE_HTML


# --- Responsive ---


def test_responsive_media_query():
    """Медиа-запрос для мобильных (max-width: 768px) присутствует."""
    assert "max-width: 768px" in LANDING_PAGE_HTML

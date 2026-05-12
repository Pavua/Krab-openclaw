"""Wave 159: Owner Panel landing page redesign — card grid.

Тестируем что на главной странице:
  * 6 admin-карточек (Models / Routing / Swarm / Costs / Ecosystem / Inbox)
  * Hero section с badges (Model + Uptime + Session)
  * Footer с ссылками на /docs, /metrics, GitHub
  * Все navigation links актуальны и не сломаны
"""

from __future__ import annotations

import re

from src.modules.web_app_landing_page import LANDING_PAGE_HTML  # noqa: I001

# ─── Hero section ────────────────────────────────────────────────────────────


def test_hero_title_renamed_to_owner_panel():
    """Wave 159: переименование 'Control' → 'Owner Panel'."""
    assert "Krab Owner Panel" in LANDING_PAGE_HTML


def test_hero_has_model_uptime_session_badges():
    """Hero показывает 3 badge: Model / Uptime / Session."""
    assert 'id="hero-model"' in LANDING_PAGE_HTML
    assert 'id="hero-uptime"' in LANDING_PAGE_HTML
    # Session badge — статичный, проверяем подпись и наличие badge класса
    assert "Session:" in LANDING_PAGE_HTML
    assert "hero-badges" in LANDING_PAGE_HTML


# ─── Card grid: 6 admin cards ────────────────────────────────────────────────


def test_card_grid_has_six_admin_cards():
    """6 карточек: Models / Routing / Swarm / Costs / Ecosystem / Inbox."""
    # Считаем количество <a class="card"> блоков (animation-delay уникален per card)
    card_matches = re.findall(r'<a [^>]*class="card"', LANDING_PAGE_HTML)
    assert len(card_matches) == 6, f"Ожидалось 6 карточек, найдено {len(card_matches)}"


def test_card_grid_links_to_admin_pages():
    """Каждая admin-card ведёт на правильный admin URL."""
    expected_targets = [
        ("/admin/models", "Models"),
        ("/admin/routing", "Routing"),
        ("/admin/swarm", "Swarm"),
        ("/admin/costs", "Costs"),
        ("/inbox", "Inbox"),
    ]
    for href, title in expected_targets:
        assert f'href="{href}"' in LANDING_PAGE_HTML, f"Card target {href} ({title}) отсутствует"
    # Ecosystem ведёт на read-only API endpoint (нет /admin/ecosystem yet)
    assert 'href="/api/ecosystem/health"' in LANDING_PAGE_HTML


def test_card_grid_uses_three_column_layout():
    """Card grid использует 3-column layout (на desktop)."""
    assert "grid-template-columns: repeat(3, 1fr)" in LANDING_PAGE_HTML


def test_card_emojis_match_admin_pages():
    """Каждая admin-card имеет правильный emoji."""
    # Из спецификации Wave 159: 🎛️ models / 🎯 routing / 🤖 swarm / 💰 costs / 🌐 ecosystem / 📥 inbox
    expected_emojis = ["🎛️", "🎯", "🤖", "💰", "🌐", "📥"]
    for emoji in expected_emojis:
        assert emoji in LANDING_PAGE_HTML, f"Emoji {emoji} не найден"


def test_card_has_status_indicator():
    """Каждая card имеет .indicator div для status."""
    # 6 cards × 1 indicator each = минимум 6 indicator divs
    indicators = re.findall(r'<div class="indicator"[^>]*></div>', LANDING_PAGE_HTML)
    assert len(indicators) >= 6, f"Ожидалось ≥6 indicator div, найдено {len(indicators)}"


# ─── Footer ──────────────────────────────────────────────────────────────────


def test_footer_has_docs_metrics_github_links():
    """Footer содержит ссылки на /docs, /metrics, GitHub."""
    assert 'href="/docs"' in LANDING_PAGE_HTML
    assert 'href="/metrics"' in LANDING_PAGE_HTML
    # GitHub link — внешний с rel="noopener"
    assert "github.com" in LANDING_PAGE_HTML.lower()
    assert 'rel="noopener"' in LANDING_PAGE_HTML


# ─── Backwards-compatibility ─────────────────────────────────────────────────


def test_landing_page_constant_still_exported():
    """LANDING_PAGE_HTML остаётся валидной string-константой."""
    assert isinstance(LANDING_PAGE_HTML, str)
    assert LANDING_PAGE_HTML.strip().startswith("<!DOCTYPE html>")
    assert LANDING_PAGE_HTML.rstrip().endswith("</html>")


def test_no_broken_markup_unclosed_tags():
    """Грубая проверка markup integrity: count открывающих и закрывающих <div>."""
    open_divs = len(re.findall(r"<div\b", LANDING_PAGE_HTML))
    close_divs = len(re.findall(r"</div>", LANDING_PAGE_HTML))
    assert open_divs == close_divs, f"Несбалансированные <div>: {open_divs} open vs {close_divs} close"


def test_health_api_endpoint_unchanged():
    """JS polling endpoint /api/health/lite сохранён (backwards-compat)."""
    assert "/api/health/lite" in LANDING_PAGE_HTML

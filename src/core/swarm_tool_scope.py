# -*- coding: utf-8 -*-
"""
swarm_tool_scope.py — per-team tool scoping для агентного свёрма.

Определяет, какие инструменты доступны каждой команде и в каком порядке
упоминаются в tool-hint prompt'е.

Дизайн:
- Каждая команда имеет base-набор (web_search, peekaboo) + специализированные
- Используется в AgentRoom._run() для формирования tool_hint
- Расширяемо: добавь запись в TEAM_TOOL_SETS и update_tool_hint подхватит

Публичный API:
    get_team_tools(team_name, *, tor_enabled=False) -> list[str]
    format_tool_hint(team_name, *, tor_enabled=False, role_idx=0) -> str
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Базовые инструменты, доступные всем командам
# ---------------------------------------------------------------------------

_BASE_TOOLS: list[str] = [
    "web_search (поиск актуальных данных в интернете)",
    "peekaboo (скриншот экрана macOS)",
]

_TOR_TOOL = "tor_fetch (анонимный HTTP через Tor SOCKS5)"

# ---------------------------------------------------------------------------
# Специализированные инструменты по командам
# ---------------------------------------------------------------------------

# Описание формата: "tool_name (краткое описание)"
# Инструменты упоминаются в LLM prompt — имя должно совпадать с call_tool_unified
TEAM_TOOL_SETS: dict[str, list[str]] = {
    "traders": [
        # Финансовые данные
        "web_search (Coingecko, CoinMarketCap, TradingView, Binance — цены и объёмы)",
        "web_search (DeFi протоколы: TVL, APY, liquidation levels через DefiLlama)",
        "web_search (макроэкономика: ФРС, инфляция, DXY, ставки через Investing.com)",
        # Краб-нативные
        "krab_memory_search (исторический контекст по активам из памяти)",
    ],
    "coders": [
        # Dev-инструменты
        "web_search (документация: PyPI, GitHub, Stack Overflow, MDN)",
        "krab_run_tests (запуск pytest тест-сьюта Краба)",
        "krab_tail_logs (последние строки лога Краба для дебага)",
        "krab_memory_search (архитектурный контекст и прошлые решения)",
    ],
    "analysts": [
        # Исследовательские инструменты
        "web_search (OSINT: новости, отчёты, академические статьи, LinkedIn, Twitter/X)",
        "web_search (RSS-агрегаторы: Feedly, Reuters, Bloomberg — актуальные новости)",
        "krab_memory_search (исторические данные и прошлые исследования из памяти)",
        "telegram_search (поиск по Telegram-каналам и группам)",
    ],
    "creative": [
        # Контентные инструменты
        "web_search (тренды: Google Trends, TikTok, Twitter — что сейчас популярно)",
        "web_search (референсы: Behance, Dribbble, Pinterest, ArtStation)",
        "krab_memory_search (прошлые идеи и одобренные концепции из памяти)",
        "telegram_send_message (публикация готового контента)",
    ],
}

# Инструменты по умолчанию (для неизвестных команд)
_DEFAULT_EXTRA_TOOLS: list[str] = []


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def get_team_tools(team_name: str, *, tor_enabled: bool = False) -> list[str]:
    """Возвращает список описаний инструментов для команды.

    Базовые инструменты всегда включены.
    Специализированные добавляются по команде.
    Tor-инструмент добавляется если tor_enabled=True.

    Args:
        team_name: Название команды (traders/coders/analysts/creative).
        tor_enabled: Включить tor_fetch в список.

    Returns:
        Список строк-описаний инструментов для вставки в prompt.
    """
    # Для traders специализированные инструменты заменяют generic web_search описание
    # (они все равно используют один и тот же tool, просто с разными query)
    team_key = team_name.lower()
    extra = TEAM_TOOL_SETS.get(team_key, _DEFAULT_EXTRA_TOOLS)

    if extra:
        # Базовые без web_search (он переопределён в специализации) + peekaboo
        tools = ["peekaboo (скриншот экрана macOS)"] + extra
    else:
        tools = list(_BASE_TOOLS)

    if tor_enabled:
        tools.append(_TOR_TOOL)

    return tools


def format_tool_hint(
    team_name: str,
    *,
    tor_enabled: bool = False,
    role_idx: int = 0,
) -> str:
    """Форматирует tool_hint для вставки в LLM prompt.

    Args:
        team_name: Название команды.
        tor_enabled: Включить tor_fetch.
        role_idx: Индекс роли в цепочке (0 = первая роль, обязана использовать web_search).

    Returns:
        Строка tool_hint для подстановки в system prompt.
    """
    tools = get_team_tools(team_name, tor_enabled=tor_enabled)
    tools_str = ", ".join(tools)

    if role_idx == 0:
        return (
            f"\n\nУ тебя есть доступ к инструментам: {tools_str}. "
            "ВАЖНО: ты ОБЯЗАН начать с вызова web_search чтобы получить актуальные данные "
            "(цены, курсы, новости, факты). Твои знания устарели — без web_search твой анализ "
            "будет основан на старых данных и бесполезен. Сначала поиск, потом анализ."
        )
    else:
        return (
            f"\n\nУ тебя есть доступ к инструментам: {tools_str}. "
            "Используй web_search если нужны дополнительные актуальные данные."
        )

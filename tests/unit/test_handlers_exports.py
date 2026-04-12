"""
Регрессионные проверки пакетного экспорта Telegram-обработчиков.

Фиксируем контракт `src.handlers`, чтобы новые команды не выпадали из
реэкспорта и не ломали импорт `userbot_bridge` при старте рантайма.
"""

from src import handlers


def test_handlers_exports_handle_shop() -> None:
    """Пакет `src.handlers` должен реэкспортировать `handle_shop`."""
    assert handlers.handle_shop is not None

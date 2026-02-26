# -*- coding: utf-8 -*-
"""
Модуль Error Handler для Krab v2.5.
Единый middleware для обработки ошибок во всех хэндлерах.

Обеспечивает:
- Логирование ошибок с полным traceback
- FloodWait backoff БЕЗ рекурсивного повтора всего хэндлера (!)
- Уведомление владельца о критических ошибках
- Статистику ошибок для !diagnose

ВАЖНО: Предыдущая версия при FloodWait вызывала func повторно,
что в сочетании с вложенными FloodWait приводило к
"maximum recursion depth exceeded". Теперь мы просто ждём и НЕ повторяем.

Фаза 1.3 (Безопасность): автоудаление/переименование config/settings.yaml
при Config/JSONDecodeError убрано — риск потери валидного конфига при
посторонних ошибках. Ошибки только логируются.
"""

import asyncio
import logging
import traceback
import functools
from pyrogram.errors import FloodWait, UserNotParticipant, ChatWriteForbidden, MessageNotModified

logger = logging.getLogger("ErrorHandler")

# Счётчик ошибок для мониторинга
_error_counts = {}


def safe_handler(func):
    """
    Декоратор-middleware для всех хэндлеров Pyrogram.
    Оборачивает обработчик в try/except с умной обработкой ошибок:

    - FloodWait: ждёт указанное Telegram время + 1с буфер, НО НЕ ПОВТОРЯЕТ вызов
      (повторный вызов всего handler'а вызывал рекурсию и крэш)
    - MessageNotModified: тихо игнорирует (не ошибка)
    - ChatWriteForbidden: логирует и пропускает
    - Остальное: логирует полный traceback, уведомляет владельца
    """
    @functools.wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        try:
            return await func(client, update, *args, **kwargs)

        except FloodWait as e:
            # Telegram просит подождать — слушаемся, НО НЕ ПОВТОРЯЕМ handler
            # Повторный вызов func() вызывает рекурсию при каскадных FloodWait
            wait_time = e.value + 1
            logger.warning(
                f"⏳ FloodWait: ждём {wait_time}с ({func.__name__}). "
                f"Handler НЕ будет повторён для предотвращения рекурсии."
            )
            _error_counts["FloodWait"] = _error_counts.get("FloodWait", 0) + 1
            await asyncio.sleep(wait_time)
            # НЕ вызываем func повторно! Пользователь просто отправит команду заново.

        except MessageNotModified:
            # Сообщение не изменилось — не ошибка, просто игнорируем
            pass

        except ChatWriteForbidden:
            logger.warning(f"🚫 Нет прав на запись в чат (handler: {func.__name__})")

        except UserNotParticipant:
            logger.warning(f"👤 Пользователь не участник чата (handler: {func.__name__})")

        except RecursionError:
            # КРИТИЧНО: явно ловим рекурсию, чтобы не положить весь бот
            logger.critical(
                f"🔴 RecursionError в {func.__name__}! "
                f"Прерываем handler, чтобы бот продолжил работу."
            )
            _error_counts["RecursionError"] = _error_counts.get("RecursionError", 0) + 1

        except Exception as e:
            # Общая ошибка — логируем полностью (без автоудаления конфига, см. Фаза 1.3)
            error_name = type(e).__name__
            _error_counts[error_name] = _error_counts.get(error_name, 0) + 1

            tb = traceback.format_exc()
            logger.error(
                f"💥 Необработанная ошибка в {func.__name__}:\n"
                f"   Тип: {error_name}\n"
                f"   Сообщение: {e}\n"
                f"   Traceback:\n{tb}"
            )

            # Попытка уведомить пользователя о проблеме (если update — это Message)
            try:
                if hasattr(update, 'reply_text'):
                    await update.reply_text(
                        f"⚠️ Произошла ошибка: `{error_name}`\n"
                        f"Подробности в логах."
                    )
            except Exception:
                pass  # Если даже ответить не можем — молча логируем

    return wrapper


def get_error_stats() -> dict:
    """Возвращает статистику ошибок для диагностики."""
    return dict(_error_counts)


def reset_error_stats():
    """Сброс счётчиков (вызывается при !diagnose)."""
    _error_counts.clear()

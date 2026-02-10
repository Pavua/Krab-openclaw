# -*- coding: utf-8 -*-
"""
Модуль Error Handler для Krab v2.5.
Единый middleware для обработки ошибок во всех хэндлерах.
Обеспечивает: логирование, FloodWait backoff, уведомление владельца о критических ошибках.
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
    
    - FloodWait: ждёт указанное Telegram время + 1с буфер
    - MessageNotModified: тихо игнорирует (не ошибка)
    - ChatWriteForbidden: логирует и пропускает
    - Остальное: логирует полный traceback, уведомляет владельца
    """
    @functools.wraps(func)
    async def wrapper(client, update, *args, **kwargs):
        try:
            return await func(client, update, *args, **kwargs)
        
        except FloodWait as e:
            # Telegram просит подождать — слушаемся
            wait_time = e.value + 1
            logger.warning(f"⏳ FloodWait: ждём {wait_time}с перед повтором ({func.__name__})")
            await asyncio.sleep(wait_time)
            # Пытаемся ещё раз после ожидания
            try:
                return await func(client, update, *args, **kwargs)
            except Exception as retry_err:
                logger.error(f"❌ Повторная ошибка после FloodWait: {retry_err}")
        
        except MessageNotModified:
            # Сообщение не изменилось — не ошибка, просто игнорируем
            pass
        
        except ChatWriteForbidden:
            logger.warning(f"🚫 Нет прав на запись в чат (handler: {func.__name__})")
        
        except UserNotParticipant:
            logger.warning(f"👤 Пользователь не участник чата (handler: {func.__name__})")
        
        except Exception as e:
            # Общая ошибка — логируем полностью
            error_name = type(e).__name__
            _error_counts[error_name] = _error_counts.get(error_name, 0) + 1
            
            tb = traceback.format_exc()
            # PHASE 10.5: Unstoppable Logic (Self-Healing)
            if "Config" in error_name or "JSONDecodeError" in error_name:
                logger.warning("🩹 Critical data error detected. Attempting Self-Healing...")
                # Если поврежден конфиг — восстанавливаем дефолтный
                if os.path.exists("config/settings.yaml"):
                    os.rename("config/settings.yaml", f"config/settings.yaml.bak_{int(asyncio.get_event_loop().time())}")
                    logger.info("Reverted config to default due to error.")
            
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

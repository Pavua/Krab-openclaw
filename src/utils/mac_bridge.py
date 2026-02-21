# -*- coding: utf-8 -*-
"""
macOS Automation Bridge.
Мост к системным функциям macOS через AppleScript/osascript.

Зачем: Позволяет боту управлять приложениями Mac — открывать сайты,
управлять громкостью, отправлять уведомления, управлять Finder и т.д.
Связь: Вызывается из tool_handler.py как инструмент, доступен через !mac команду.
"""

import asyncio
import subprocess
import logging
from typing import Optional

logger = logging.getLogger("MacBridge")


class MacAutomation:
    """
    Мост к macOS-функциям через osascript (AppleScript).
    Только для владельца бота — все команды выполняются на хост-машине.
    """

    @staticmethod
    async def run_applescript(script: str) -> str:
        """
        Выполняет AppleScript через osascript.
        Асинхронная обёртка для неблокирующего выполнения.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            
            if proc.returncode == 0:
                return stdout.decode().strip() or "✅ Выполнено"
            else:
                error = stderr.decode().strip()
                logger.error(f"AppleScript error: {error}")
                return f"❌ Ошибка: {error}"
        except asyncio.TimeoutError:
            return "❌ Таймаут выполнения AppleScript (>10s)"
        except Exception as e:
            logger.error(f"MacBridge error: {e}")
            return f"❌ Ошибка: {e}"

    # ====== Уведомления ======
    
    @staticmethod
    async def send_notification(title: str, message: str, subtitle: str = "") -> str:
        """Отправляет macOS-уведомление через Notification Center."""
        sub_part = f'subtitle "{subtitle}"' if subtitle else ''
        script = f'display notification "{message}" with title "{title}" {sub_part}'
        return await MacAutomation.run_applescript(script)

    # ====== Управление звуком ======
    
    @staticmethod
    async def set_volume(level: int) -> str:
        """Устанавливает громкость системы (0-100)."""
        level = max(0, min(100, level))
        # macOS volume: 0-7, но osascript поддерживает 0-100
        script = f'set volume output volume {level}'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def get_volume() -> str:
        """Получает текущую громкость."""
        script = 'output volume of (get volume settings)'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def toggle_mute() -> str:
        """Переключает режим 'Без звука'."""
        script = 'set volume with output muted not (output muted of (get volume settings))'
        return await MacAutomation.run_applescript(script)

    # ====== Управление приложениями ======
    
    @staticmethod
    async def open_app(app_name: str) -> str:
        """Открывает приложение по имени."""
        script = f'tell application "{app_name}" to activate'
        return await MacAutomation.run_applescript(script)
    
    @staticmethod
    async def quit_app(app_name: str) -> str:
        """Закрывает приложение."""
        script = f'tell application "{app_name}" to quit'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def list_running_apps() -> str:
        """Список запущенных приложений."""
        script = 'tell application "System Events" to get name of every process whose background only is false'
        return await MacAutomation.run_applescript(script)

    # ====== Яркость и экран ======
    
    @staticmethod
    async def set_brightness(level: float) -> str:
        """Устанавливает яркость экрана (0.0 - 1.0)."""
        level = max(0.0, min(1.0, level))
        script = f'tell application "System Events" to set brightness to {level}'
        # Альтернатива через brightness CLI если установлен
        try:
            proc = await asyncio.create_subprocess_exec(
                "brightness", str(level),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return f"✅ Яркость: {int(level * 100)}%"
        except FileNotFoundError:
            return "⚠️ Утилита brightness не установлена (brew install brightness)"

    # ====== Клипборд ======
    
    @staticmethod
    async def get_clipboard() -> str:
        """Получает содержимое буфера обмена."""
        script = 'the clipboard'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def set_clipboard(text: str) -> str:
        """Устанавливает текст в буфер обмена."""
        # Экранируем кавычки
        escaped = text.replace('"', '\\"')
        script = f'set the clipboard to "{escaped}"'
        return await MacAutomation.run_applescript(script)

    # ====== Файловая система ======
    
    @staticmethod
    async def open_folder(path: str) -> str:
        """Открывает папку в Finder."""
        script = f'tell application "Finder" to open POSIX file "{path}"'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def open_url(url: str) -> str:
        """Открывает URL в браузере по умолчанию."""
        script = f'open location "{url}"'
        return await MacAutomation.run_applescript(script)

    # ====== Музыка ======
    
    @staticmethod
    async def music_play_pause() -> str:
        """Пауза/Воспроизведение в Apple Music (или Spotify)."""
        script = 'tell application "Music" to playpause'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def music_next() -> str:
        """Следующий трек."""
        script = 'tell application "Music" to next track'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def music_current() -> str:
        """Текущий играющий трек."""
        script = '''
        tell application "Music"
            if player state is playing then
                set trackName to name of current track
                set artistName to artist of current track
                return "🎵 " & trackName & " — " & artistName
            else
                return "⏸ Музыка на паузе"
            end if
        end tell
        '''
        return await MacAutomation.run_applescript(script)

    # ====== Диалоги ======
    
    @staticmethod
    async def show_dialog(message: str, title: str = "Krab") -> str:
        """Показывает системный диалог на экране Mac."""
        script = f'display dialog "{message}" with title "{title}" buttons {{"OK"}} default button "OK"'
        return await MacAutomation.run_applescript(script)

    @staticmethod
    async def say_text(text: str, voice: str = "Milena") -> str:
        """Произносит текст вслух через macOS TTS."""
        # Milena — русский голос, Samantha — английский
        script = f'say "{text}" using "{voice}"'
        return await MacAutomation.run_applescript(script)

    # ====== Системные действия ======
    
    @staticmethod
    async def lock_screen() -> str:
        """Блокирует экран Mac."""
        proc = await asyncio.create_subprocess_exec(
            "pmset", "displaysleepnow",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return "🔒 Экран заблокирован"

    @staticmethod
    async def get_wifi_name() -> str:
        """Получает имя текущей Wi-Fi сети."""
        try:
            # macOS Sonoma+ использует другую команду
            proc = await asyncio.create_subprocess_exec(
                "networksetup", "-getairportnetwork", "en0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode().strip()
            if "Current Wi-Fi Network" in output:
                return f"📶 {output.split(':')[1].strip()}"
            return f"📶 {output}"
        except Exception:
            return "❌ Не удалось определить Wi-Fi"

    @staticmethod
    async def get_battery_status() -> str:
        """Получает статус батареи MacBook."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pmset", "-g", "batt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode().strip()
            # Парсим процент
            for line in output.split("\n"):
                if "%" in line:
                    return f"🔋 {line.strip()}"
            return output
        except Exception:
            return "❌ Не удалось получить статус батареи"

    # ====== Агрегатор для AI-driven выбора ======
    
    @classmethod
    async def execute_intent(cls, intent: str, params: dict = None) -> str:
        """
        AI-driven выполнение macOS-действия по описанию намерения.
        Маппинг intent -> метод.
        """
        params = params or {}
        
        intent_map = {
            "notification": lambda: cls.send_notification(
                params.get("title", "Krab"), 
                params.get("message", "")
            ),
            "volume_set": lambda: cls.set_volume(params.get("level", 50)),
            "volume_get": cls.get_volume,
            "mute": cls.toggle_mute,
            "open_app": lambda: cls.open_app(params.get("app", "")),
            "quit_app": lambda: cls.quit_app(params.get("app", "")),
            "list_apps": cls.list_running_apps,
            "clipboard_get": cls.get_clipboard,
            "clipboard_set": lambda: cls.set_clipboard(params.get("text", "")),
            "open_url": lambda: cls.open_url(params.get("url", "")),
            "open_folder": lambda: cls.open_folder(params.get("path", "")),
            "music_toggle": cls.music_play_pause,
            "music_next": cls.music_next,
            "music_current": cls.music_current,
            "lock": cls.lock_screen,
            "wifi": cls.get_wifi_name,
            "battery": cls.get_battery_status,
            "say": lambda: cls.say_text(params.get("text", ""), params.get("voice", "Milena")),
            "dialog": lambda: cls.show_dialog(params.get("message", "")),
        }
        
        handler = intent_map.get(intent)
        if handler:
            return await handler()
        
        return f"❌ Неизвестное намерение: {intent}. Доступные: {', '.join(intent_map.keys())}"

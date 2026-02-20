# -*- coding: utf-8 -*-
"""
Watchdog Module
Мониторинг здоровья системы и автоматическое восстановление.
"""

import logging
import asyncio
import time
import os
import subprocess
from typing import Dict, Any

import aiohttp
import psutil
logger = logging.getLogger(__name__)

class KrabWatchdog:
    def __init__(self, notifier=None):
        self.notifier = notifier
        self.components_pulse: Dict[str, float] = {}
        self.last_recovery_attempt: Dict[str, float] = {}
        self.running = False
        self.check_interval = 30  # секунд
        self.threshold = 120      # секунд до признания "мертвым"
        self.recovery_cooldown_seconds = max(
            10,
            int(str(os.getenv("WATCHDOG_RECOVERY_COOLDOWN_SECONDS", "180")).strip() or "180"),
        )
        self.router = None # Назначается в main.py
        try:
            self.ram_threshold = int(os.getenv("WATCHDOG_RAM_THRESHOLD", "90"))
        except (ValueError, TypeError):
            self.ram_threshold = 90

    def update_pulse(self, component: str):
        """Обновить метку времени работы компонента."""
        self.components_pulse[component] = time.time()
        logger.debug(f"💓 Component {component} is alive.")

    async def start_monitoring(self):
        """Запуск цикла мониторинга."""
        self.running = True
        logger.info("🛡️ Watchdog monitoring started.")
        while self.running:
            await asyncio.sleep(self.check_interval)
            await self._check_health()

    def stop(self):
        self.running = False
        logger.info("🛑 Watchdog stopped.")

    async def _check_health(self):
        """Проверка всех компонентов на зависание."""
        now = time.time()
        
        # 1. Проверка пульса внутренних компонентов
        for component, last_pulse in list(self.components_pulse.items()):
            idle_time = now - last_pulse
            if idle_time > self.threshold:
                logger.critical(f"💀 COMPONENT HANG DETECTED: {component} (Idle for {idle_time:.0f}s)")
                await self._handle_failure(component)

        # 2. Проверка ресурсов (RAM) - Soft Healing
        await self._check_resources()

        # 3. Проверка OpenClaw Gateway (HTTP)
        await self._check_gateway_health()

    async def _check_gateway_health(self):
        """Проверка доступности OpenClaw Gateway."""
        url = os.getenv("OPENCLAW_BASE_URL", "http://localhost:18789") + "/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        logger.debug("🌐 OpenClaw Gateway is healthy.")
                        return True
        except Exception:
            pass
        
        logger.warning("⚠️ OpenClaw Gateway unresponsive. Attempting to lift it...")
        await self._handle_failure("OpenClawGateway")

    async def _handle_failure(self, component: str):
        """Реакция на сбой компонента."""
        now = time.time()
        last_attempt = float(self.last_recovery_attempt.get(component, 0.0) or 0.0)
        cooldown_left = self.recovery_cooldown_seconds - (now - last_attempt)
        if cooldown_left > 0:
            logger.warning(
                "⏳ Watchdog cooldown активен для %s. Пропускаю self-heal еще на %.0fs",
                component,
                cooldown_left,
            )
            return
        # Ставим отметку ДО запуска рестарта, чтобы исключить шторм при частых циклах.
        self.last_recovery_attempt[component] = now

        if self.notifier:
            await self.notifier.notify_system(
                "CRITICAL FAILURE", 
                f"Component `{component}` stopped responding. Attempting self-healing..."
            )
        
        # Логика самовосстановления:
        # Для ядра используем каноничный hard-restart, чтобы не плодить дубликаты процессов.
        ecosystem_script = "/Users/pablito/Antigravity_AGENTS/Краб/Start_Full_Ecosystem.command"
        core_restart_script = "/Users/pablito/Antigravity_AGENTS/Краб/restart_core_hard.command"
        
        if component != "OpenClawGateway" and os.path.exists(core_restart_script):
            logger.info(f"♻️ Executing self-healing via core hard-restart: {core_restart_script}")
            try:
                subprocess.Popen(["/bin/zsh", core_restart_script])
            except Exception as e:
                logger.error(f"Failed to execute self-healing: {e}")
            return

        if os.path.exists(ecosystem_script):
            logger.info(f"♻️ Executing self-healing via Ecosystem Orchestrator: {ecosystem_script}")
            try:
                subprocess.Popen(["/bin/zsh", ecosystem_script, "native", "--force-core-restart"])
            except Exception as e:
                logger.error(f"Failed to execute self-healing: {e}")
        else:
            logger.error(f"Ecosystem script not found at {ecosystem_script}")

    async def _check_resources(self):
        """[R12] Улучшенный мониторинг RAM и многостадийное самоисцеление."""
        try:
            ram_percent = psutil.virtual_memory().percent
        except Exception as e:
            logger.error(f"Failed to get RAM metrics: {e}")
            return

        # Пороги (можно вынести в env)
        SOFT_THRESHOLD = self.ram_threshold  # По умолчанию 90%
        HARD_THRESHOLD = 95.0
        
        if ram_percent > SOFT_THRESHOLD:
            logger.warning(f"🚨 RAM USAGE HIGH: {ram_percent}% (Soft Threshold: {SOFT_THRESHOLD}%)")
            
            if self.router:
                # [Stage 1] Soft Healing: Выгрузка моделей
                logger.info("🧠 RAM [Soft Healing]: Requesting model unload...")
                # unload_models_manual - асинхронный метод
                await self.router.unload_models_manual()
                
                if self.notifier:
                    await self.notifier.notify_system(
                        "SOFT HEALING TRIGGERED",
                        f"Использование RAM: {ram_percent}%. Локальные модели выгружены для освобождения памяти."
                    )
                
                # Даем паузу перед возможным Hard Healing
                await asyncio.sleep(5)
                new_ram = psutil.virtual_memory().percent
                
                if new_ram > HARD_THRESHOLD:
                    logger.critical(f"💀 RAM STILL CRITICAL AFTER SOFT HEAL: {new_ram}% (Hard Threshold: {HARD_THRESHOLD}%)")
                    # [Stage 2] Hard Healing: Рестарт ядра
                    await self._handle_failure("CriticalResourcePressure")
            else:
                # Если роутера нет, сразу идем в хард-рестарт при превышении порога
                if ram_percent > HARD_THRESHOLD:
                    await self._handle_failure("CriticalResourcePressureNoRouter")
        else:
            if ram_percent > 80.0:
                logger.info(f"📊 RAM check: {ram_percent}% - OK (below {SOFT_THRESHOLD}%)")

# Синглтон
krab_watchdog = KrabWatchdog()

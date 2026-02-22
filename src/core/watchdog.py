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
        self.last_soft_heal_attempt: float = 0.0
        self.running = False
        self.check_interval = 30  # секунд
        self.threshold = 120      # секунд до признания "мертвым"
        self.recovery_cooldown_seconds = max(
            10,
            int(str(os.getenv("WATCHDOG_RECOVERY_COOLDOWN_SECONDS", "180")).strip() or "180"),
        )
        self.soft_heal_cooldown_seconds = max(
            15,
            int(str(os.getenv("WATCHDOG_SOFT_HEAL_COOLDOWN_SECONDS", "180")).strip() or "180"),
        )
        # Защита от ложных срабатываний health-check сразу после старта процесса:
        # OpenClaw может подниматься дольше ядра.
        self.started_at = time.time()
        self.gateway_startup_grace_seconds = max(
            0,
            int(str(os.getenv("WATCHDOG_GATEWAY_STARTUP_GRACE_SECONDS", "90")).strip() or "90"),
        )
        # Требуем несколько подряд health-fail, прежде чем запускать self-heal.
        self.gateway_fail_streak = 0
        self.gateway_fail_streak_threshold = max(
            1,
            int(str(os.getenv("WATCHDOG_GATEWAY_FAIL_STREAK_THRESHOLD", "3")).strip() or "3"),
        )
        self.last_gateway_heal_attempt: float = 0.0
        self.gateway_heal_cooldown_seconds = max(
            20,
            int(str(os.getenv("WATCHDOG_GATEWAY_HEAL_COOLDOWN_SECONDS", "180")).strip() or "180"),
        )
        self.router = None # Назначается в main.py
        try:
            self.ram_threshold = int(os.getenv("WATCHDOG_RAM_THRESHOLD", "90"))
        except (ValueError, TypeError):
            self.ram_threshold = 90

        # Anti-storm: предел перезапусков за скользящее временное окно.
        # Если компонент падает чаще N раз за window_seconds — прекращаем перезапуски.
        self.max_recovery_attempts = max(
            1,
            int(str(os.getenv("WATCHDOG_MAX_RECOVERY_ATTEMPTS_PER_WINDOW", "3")).strip() or "3"),
        )
        self.recovery_window_seconds = max(
            60,
            int(str(os.getenv("WATCHDOG_RECOVERY_WINDOW_SECONDS", "1800")).strip() or "1800"),
        )
        # Счётчик перезапусков в текущем окне (сбрасывается по истечении окна).
        self._recovery_counts: Dict[str, int] = {}
        self._recovery_window_start: Dict[str, float] = {}

    def update_pulse(self, component: str):
        """Обновить метку времени работы компонента."""
        self.components_pulse[component] = time.time()
        logger.debug(f"💓 Component {component} is alive.")

    async def start_monitoring(self):
        """Запуск цикла мониторинга."""
        self.started_at = time.time()
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
        now = time.time()
        url = os.getenv("OPENCLAW_BASE_URL", "http://localhost:18789") + "/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        if self.gateway_fail_streak > 0:
                            logger.info(
                                "🌐 OpenClaw Gateway recovered after %s failed checks.",
                                self.gateway_fail_streak,
                            )
                        self.gateway_fail_streak = 0
                        logger.debug("🌐 OpenClaw Gateway is healthy.")
                        return True
        except Exception:
            pass

        since_start = now - float(self.started_at or now)
        if since_start < self.gateway_startup_grace_seconds:
            logger.warning(
                "🕒 OpenClaw health-check failed в startup grace (%.0fs < %.0fs). "
                "Пропускаю self-heal.",
                since_start,
                float(self.gateway_startup_grace_seconds),
            )
            return False

        self.gateway_fail_streak += 1
        if self.gateway_fail_streak < self.gateway_fail_streak_threshold:
            logger.warning(
                "⚠️ OpenClaw health fail streak: %s/%s. Жду перед self-heal.",
                self.gateway_fail_streak,
                self.gateway_fail_streak_threshold,
            )
            return False

        since_heal = now - float(self.last_gateway_heal_attempt or 0.0)
        if since_heal < self.gateway_heal_cooldown_seconds:
            logger.warning(
                "⏳ OpenClaw self-heal cooldown активен (осталось %.0fs).",
                self.gateway_heal_cooldown_seconds - since_heal,
            )
            return False

        self.last_gateway_heal_attempt = now
        logger.warning("⚠️ OpenClaw Gateway unresponsive. Attempting targeted self-heal...")
        await self._handle_failure("OpenClawGateway")
        return False

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

        # Anti-storm guard: если превышен лимит перезапусков за окно — пропускаем.
        if not self._should_allow_recovery(component, now):
            return

        # Все guards пройдены — выполняем heal.
        await self._handle_failure_execute(component)

    def _should_allow_recovery(self, component: str, now: float) -> bool:

        """
        Anti-storm guard: проверяет, не превышен ли лимит перезапусков
        за скользящее временное окно.

        Зачем: без этого при длительной аварии watchdog бесконечно пытается
        запустить heal-скрипты, создавая дублирующие процессы и лишний шум в логах.

        Возвращает True — можно делать recovery.
        Возвращает False — лимит исчерпан, recovery заблокировано.
        """
        window_start = self._recovery_window_start.get(component, 0.0)

        # Если окно истекло — сбрасываем счётчик.
        if (now - window_start) >= self.recovery_window_seconds:
            self._recovery_window_start[component] = now
            self._recovery_counts[component] = 0

        count = self._recovery_counts.get(component, 0)
        if count >= self.max_recovery_attempts:
            logger.critical(
                "🚨 ANTI-STORM: блокируем recovery для %s — %d/%d попыток за %.0fс. "
                "Требуется ручное вмешательство.",
                component,
                count,
                self.max_recovery_attempts,
                self.recovery_window_seconds,
            )
            return False

        # Инкрементируем счётчик и разрешаем recovery.
        self._recovery_counts[component] = count + 1
        return True

    async def _handle_failure_execute(self, component: str) -> None:
        """
        Выполняет фактическую логику heal-скрипта.
        Вынесен из _handle_failure для читаемости и тестируемости.
        """
        if self.notifier:
            await self.notifier.notify_system(
                "CRITICAL FAILURE",
                f"Component `{component}` stopped responding. Attempting self-healing..."
            )

        # Логика самовосстановления:
        # Для ядра используем каноничный hard-restart, чтобы не плодить дубликаты процессов.
        ecosystem_script = "/Users/pablito/Antigravity_AGENTS/Краб/Start_Full_Ecosystem.command"
        openclaw_repair_script = "/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command"
        openclaw_restart_script = "/Users/pablito/Antigravity_AGENTS/Краб/restart_openclaw.command"
        core_restart_script = "/Users/pablito/Antigravity_AGENTS/Краб/restart_core_hard.command"

        if component == "OpenClawGateway":

            # Критично: для проблем OpenClaw не перезапускаем все ядро,
            # чтобы не обрывать активные диалоги и не оставлять «🤔 Думаю...».
            if os.path.exists(openclaw_repair_script):
                logger.info(
                    "♻️ Executing targeted OpenClaw self-heal: %s",
                    openclaw_repair_script,
                )
                try:
                    subprocess.Popen(["/bin/zsh", openclaw_repair_script])
                except Exception as e:
                    logger.error(f"Failed to execute OpenClaw self-heal: {e}")
                return

            if os.path.exists(openclaw_restart_script):
                logger.info(
                    "♻️ Executing OpenClaw restart script: %s",
                    openclaw_restart_script,
                )
                try:
                    subprocess.Popen(["/bin/zsh", openclaw_restart_script])
                except Exception as e:
                    logger.error(f"Failed to execute OpenClaw restart: {e}")
                return

            logger.error(
                "OpenClaw heal scripts not found (repair/restart). Falling back to ecosystem restart."
            )
            if os.path.exists(ecosystem_script):
                try:
                    subprocess.Popen(["/bin/zsh", ecosystem_script, "native", "--force-core-restart"])
                except Exception as e:
                    logger.error(f"Failed to execute ecosystem fallback recovery: {e}")
            return
        
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
        now = time.time()
        
        if ram_percent > SOFT_THRESHOLD:
            logger.warning(f"🚨 RAM USAGE HIGH: {ram_percent}% (Soft Threshold: {SOFT_THRESHOLD}%)")
            
            if self.router:
                since_soft_heal = now - float(self.last_soft_heal_attempt or 0.0)
                cooldown_left = self.soft_heal_cooldown_seconds - since_soft_heal
                if cooldown_left > 0:
                    logger.warning(
                        "🧠 Soft-heal cooldown активен: пропускаю выгрузку моделей еще на %.0fs",
                        cooldown_left,
                    )
                    if ram_percent > HARD_THRESHOLD:
                        logger.critical(
                            "💀 RAM CRITICAL во время soft-heal cooldown: %.1f%%",
                            ram_percent,
                        )
                        await self._handle_failure("CriticalResourcePressure")
                    return

                # [Stage 1] Soft Healing: Выгрузка моделей
                logger.info("🧠 RAM [Soft Healing]: Requesting model unload...")
                self.last_soft_heal_attempt = now
                try:
                    # unload_models_manual - асинхронный метод
                    await self.router.unload_models_manual()
                except Exception as unload_error:
                    logger.error("Soft healing unload failed: %s", unload_error)
                    if ram_percent > HARD_THRESHOLD:
                        await self._handle_failure("CriticalResourcePressure")
                    return
                
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

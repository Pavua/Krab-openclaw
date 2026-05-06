# -*- coding: utf-8 -*-
"""
NetworkWatchdog mixin для `KraabUserbot`.

Wave 31-C: извлечён из `src/userbot_bridge.py` (2026-05-05).
Содержит TCP-probe к Telegram DC и петлю мониторинга offline-состояния сети.

Wave 36-A: добавлен MTProto session probe + zombie-escalation с process restart.

Зависимости через self.*:
- `self._last_telegram_event_ts` — float, обновляется в _process_message
- `self._send_proactive_watch_alert` — метод из proactive_loops (или bridge)
- `self.client` — Pyrogram Client (может быть None до start())
"""

from __future__ import annotations

import asyncio
import os
import time

from ..config import config
from ..core.logger import get_logger

logger = get_logger(__name__)

# Wave 36-A: сколько consecutive session probe failures до process restart
_ZOMBIE_ESCALATION_THRESHOLD = int(os.environ.get("KRAB_ZOMBIE_ESCALATION_THRESHOLD", "3"))
# Wave 36-A: тишина должна быть дольше этого чтобы вообще делать session probe
_ZOMBIE_DOUBLE_SILENCE_SEC = int(os.environ.get("KRAB_ZOMBIE_DOUBLE_SILENCE_SEC", "600"))


async def _probe_telegram_session_alive(client: object, *, timeout_sec: float = 5.0) -> bool:
    """Wave 36-A: верифицирует что MTProto session реально работает.

    TCP-only probe (Wave 27-A) даёт false positive когда session stale —
    TCP жив, но Telegram updates не идут. Реальный probe — invoke
    GetUsers([InputUserSelf()]) = лёгкий API-вызов без side-effects.
    """
    try:
        from pyrogram.raw.functions.users import GetUsers  # noqa: PLC0415
        from pyrogram.raw.types import InputUserSelf  # noqa: PLC0415

        result = await asyncio.wait_for(
            client.invoke(GetUsers(id=[InputUserSelf()])),  # type: ignore[union-attr]
            timeout=timeout_sec,
        )
        return bool(result)
    except asyncio.TimeoutError:
        logger.warning("telegram_session_probe_timeout", timeout_sec=timeout_sec)
        return False
    except Exception as _e:  # noqa: BLE001
        logger.warning("telegram_session_probe_failed", error=str(_e)[:200])
        return False


class NetworkWatchdogMixin:
    """Wave 31-C: TCP-probe + петля мониторинга offline сети.
    Wave 36-A: MTProto session probe + zombie-escalation."""

    @staticmethod
    async def _probe_telegram_dc(timeout: float = 5.0) -> bool:
        """
        Активный TCP-probe к Telegram DC1 (149.154.167.50:443).
        Возвращает True если соединение успешно установлено.
        Не шлёт никаких данных — только проверяет TCP reachability.
        """
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection("149.154.167.50", 443),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _send_zombie_alert_to_owner(self, silence_sec: float, consecutive: int) -> None:
        """Wave 36-A: алерт владельцу перед zombie process restart."""
        silence_min = int(silence_sec // 60)
        silence_s = int(silence_sec % 60)
        msg = (
            f"🧟 **Krab: zombie session — принудительный перезапуск**\n"
            f"DC доступен, но MTProto session не отвечает {silence_min}м {silence_s}с.\n"
            f"Session probe failures: {consecutive}/{_ZOMBIE_ESCALATION_THRESHOLD}.\n"
            f"Выполняю `os._exit(78)` → launchd respawn."
        )
        try:
            await self._send_proactive_watch_alert(msg)
        except Exception as _e:  # noqa: BLE001
            logger.warning("zombie_alert_send_failed", error=str(_e))

    async def _network_offline_monitor_loop(self) -> None:
        """
        Мониторинг сетевого offline: если Krab не получал Telegram-событий
        дольше KRAB_NETWORK_OFFLINE_ALERT_SEC секунд — отправляет алерт владельцу.

        Wave 27-A улучшения:
        - Threshold увеличен до 180s default (60s был слишком агрессивным, pyrofork heartbeat ~30-60s)
        - Active TCP probe к DC перед алертом — false-alarm filter (quiet hour)
        - Auto-reconnect при реальном offline: disconnect + start, при успехе — не алертим
        - Alert debounce увеличен до 1800s (30 мин)
        - ENV: KRAB_NETWORK_SILENCE_THRESHOLD_SEC, KRAB_NETWORK_ALERT_DEBOUNCE_SEC

        Wave 36-A: zombie session escalation:
        - DC reachable + тишина > KRAB_ZOMBIE_DOUBLE_SILENCE_SEC → session probe
        - ≥ KRAB_ZOMBIE_ESCALATION_THRESHOLD consecutive probe failures → os._exit(78)
        - ENV: KRAB_ZOMBIE_ESCALATION_ENABLED (default=1), KRAB_ZOMBIE_ESCALATION_THRESHOLD,
                KRAB_ZOMBIE_DOUBLE_SILENCE_SEC

        Дебаунс: не более 1 алерта каждые 30 минут (1800 сек).
        Логика: отслеживаем _last_telegram_event_ts, обновляемый в _process_message.
        Не считаем offline, если userbot только что стартовал (grace period = threshold).
        """
        _raw_threshold = int(getattr(config, "KRAB_NETWORK_OFFLINE_ALERT_SEC", 60) or 60)
        if _raw_threshold == 0:
            return  # мониторинг отключён через env

        # Wave 27-A: threshold из нового env или legacy, минимум 60s
        _env_threshold = os.environ.get("KRAB_NETWORK_SILENCE_THRESHOLD_SEC")
        if _env_threshold is not None:
            threshold_sec = max(60, int(_env_threshold))
        else:
            # Если legacy KRAB_NETWORK_OFFLINE_ALERT_SEC == 60 (default), поднимаем до 180
            threshold_sec = _raw_threshold if _raw_threshold > 60 else 180

        # Wave 27-A: debounce из env или 30 минут default
        _env_debounce = os.environ.get("KRAB_NETWORK_ALERT_DEBOUNCE_SEC")
        debounce_sec = int(_env_debounce) if _env_debounce is not None else 1800  # 30 мин

        # Wave 36-A: zombie escalation enabled by default
        _zombie_enabled = os.environ.get("KRAB_ZOMBIE_ESCALATION_ENABLED", "1") != "0"

        check_interval = max(15, threshold_sec // 6)
        _last_alert_ts: float = 0.0
        _alert_active: bool = False  # флаг «алерт уже был отправлен»
        _consecutive_zombie_failures: int = 0  # Wave 36-A: счётчик zombie probe failures

        # Grace period: даём время на прогрев после запуска
        await asyncio.sleep(threshold_sec)

        try:
            while True:
                await asyncio.sleep(check_interval)
                now = time.time()
                silence_sec = now - self._last_telegram_event_ts

                if silence_sec >= threshold_sec:
                    # Проверяем: Pyrogram вообще connected?
                    client_connected = bool(self.client and self.client.is_connected)
                    if not client_connected:
                        # Уже ловит watchdog → не дублируем
                        _alert_active = False
                        _consecutive_zombie_failures = 0
                        continue

                    # Wave 27-A: Active TCP probe — если DC НЕ reachable → reconnect + alert
                    dc_reachable = await self._probe_telegram_dc()
                    if not dc_reachable:
                        # DC недоступен — пробуем auto-reconnect перед алертом
                        _consecutive_zombie_failures = 0  # сеть упала, не zombie
                        if now - _last_alert_ts >= debounce_sec:
                            logger.warning(
                                "network_silence_dc_unreachable_attempting_reconnect",
                                silence_sec=round(silence_sec, 1),
                            )
                            try:
                                if self.client:
                                    await self.client.disconnect()
                                await asyncio.sleep(2)
                                if self.client:
                                    await self.client.start()
                                # Reconnect удался — сбрасываем ts и не алертим
                                self._last_telegram_event_ts = time.time()
                                logger.info(
                                    "network_silence_auto_reconnected",
                                    silence_sec=round(silence_sec, 1),
                                )
                                _alert_active = False
                                continue
                            except Exception as _re:  # noqa: BLE001
                                logger.warning(
                                    "network_silence_reconnect_failed",
                                    error=str(_re),
                                    silence_sec=round(silence_sec, 1),
                                )

                            # Reconnect не помог — отправляем алерт
                            _last_alert_ts = now
                            _alert_active = True
                            silence_min = int(silence_sec // 60)
                            silence_s = int(silence_sec % 60)
                            msg = (
                                f"⚠️ **Krab: нет Telegram-событий {silence_min}м {silence_s}с**\n"
                                f"MTProto подключён, но входящих сообщений не было >{threshold_sec}s.\n"
                                f"DC probe: недоступен. Auto-reconnect не помог.\n"
                                f"Используй `!health` для диагностики."
                            )
                            logger.warning(
                                "network_offline_alert_sent",
                                silence_sec=round(silence_sec, 1),
                                threshold_sec=threshold_sec,
                            )
                            try:
                                await self._send_proactive_watch_alert(msg)
                            except Exception as _e:  # noqa: BLE001
                                logger.warning("network_offline_alert_send_failed", error=str(_e))
                        continue

                    # Wave 27-A: DC reachable + тишина → раньше просто continue (false-alarm)
                    # Wave 36-A: если тишина достаточно долгая — делаем session probe
                    if _zombie_enabled and silence_sec > _ZOMBIE_DOUBLE_SILENCE_SEC:
                        session_alive = await _probe_telegram_session_alive(self.client)
                        if not session_alive:
                            _consecutive_zombie_failures += 1
                            logger.warning(
                                "telegram_session_zombie_detected",
                                silence_sec=round(silence_sec, 1),
                                consecutive_failures=_consecutive_zombie_failures,
                                threshold=_ZOMBIE_ESCALATION_THRESHOLD,
                            )
                            if _consecutive_zombie_failures >= _ZOMBIE_ESCALATION_THRESHOLD:
                                # Zombie подтверждён — уведомляем и перезапускаем
                                logger.error(
                                    "telegram_session_zombie_escalation",
                                    action="process_exit_for_launchd_respawn",
                                    consecutive_failures=_consecutive_zombie_failures,
                                    silence_sec=round(silence_sec, 1),
                                )
                                await self._send_zombie_alert_to_owner(
                                    silence_sec, _consecutive_zombie_failures
                                )
                                # os._exit обходит asyncio cleanup — launchd respawn'нет через exit 78
                                os._exit(78)  # noqa: SIM905 — намеренно, нет safe shutdown
                        else:
                            # Session живая — просто тихий час
                            _consecutive_zombie_failures = 0
                            logger.debug(
                                "network_silence_but_dc_reachable_session_alive",
                                silence_sec=round(silence_sec, 1),
                                threshold_sec=threshold_sec,
                            )
                    else:
                        # Тишина меньше zombie-порога — DC reachable, считаем тихим часом
                        _consecutive_zombie_failures = 0
                        logger.debug(
                            "network_silence_but_dc_reachable",
                            silence_sec=round(silence_sec, 1),
                            threshold_sec=threshold_sec,
                        )
                else:
                    # Событие было — сбрасываем zombie счётчик + recovery алерт
                    _consecutive_zombie_failures = 0
                    if _alert_active:
                        _alert_active = False
                        try:
                            await self._send_proactive_watch_alert(
                                "✅ **Krab: Telegram-события восстановлены** — сеть в норме."
                            )
                        except Exception as _e:  # noqa: BLE001
                            logger.warning("network_recovery_alert_send_failed", error=str(_e))
        except asyncio.CancelledError:
            logger.info("network_offline_monitor_cancelled")

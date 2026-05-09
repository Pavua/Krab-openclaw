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
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import config
from ..core.logger import get_logger

if TYPE_CHECKING:
    from pyrogram import Client  # noqa: F401 — для "Client" type annotation

logger = get_logger(__name__)


def _launchd_exit_78() -> None:
    """Trigger launchd-respawn через `os._exit(78)` (EX_CONFIG).

    Session 39: вынесено в helper чтобы добавить test-process guard. Если
    модуль исполняется внутри pytest worker (PYTEST_CURRENT_TEST установлен
    pytest перед каждым тестом) — НЕ убиваем интерпретатор, а raise'аем
    SystemExit. Это позволяет xdist worker корректно завершиться, а
    тесты — assert'ить что watchdog хотел escalation.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # pytest-xdist worker умирает при os._exit → "node down" + hang.
        # SystemExit ловится в caller (или в pytest framework) и тест
        # видит явное завершение, без crash worker process.
        raise SystemExit(78)
    os._exit(78)  # noqa: SIM905 — production: launchd respawn через EX_CONFIG


# Wave 36-A: сколько consecutive session probe failures до process restart
_ZOMBIE_ESCALATION_THRESHOLD = int(os.environ.get("KRAB_ZOMBIE_ESCALATION_THRESHOLD", "3"))
# Wave 36-A: тишина должна быть дольше этого чтобы вообще делать session probe
_ZOMBIE_DOUBLE_SILENCE_SEC = int(os.environ.get("KRAB_ZOMBIE_DOUBLE_SILENCE_SEC", "600"))

# Wave 36-C: fail-loud если zombie escalation повторяется > N раз за 24h.
# Указывает на architectural-уровень problem (не одиночный glitch).
_ZOMBIE_HISTORY_FILE: Path = (
    Path.home() / ".openclaw" / "krab_runtime_state" / "zombie_escalation_history.json"
)
_ZOMBIE_FAIL_LOUD_THRESHOLD = int(os.environ.get("KRAB_ZOMBIE_FAIL_LOUD_THRESHOLD", "3"))
_ZOMBIE_FAIL_LOUD_WINDOW_SEC = int(
    os.environ.get("KRAB_ZOMBIE_FAIL_LOUD_WINDOW_SEC", str(24 * 3600))
)


def _read_zombie_history() -> list[float]:
    """Читает persistent JSON-историю zombie escalation timestamps.

    Возвращает список UNIX timestamps. Невалидный/отсутствующий файл = []
    (fail-open: lost history лучше чем crash при boot).
    """
    try:
        if not _ZOMBIE_HISTORY_FILE.exists():
            return []
        data = json.loads(_ZOMBIE_HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [float(x) for x in data if isinstance(x, (int, float))]
    except Exception as exc:  # noqa: BLE001
        logger.warning("zombie_history_read_failed", error=str(exc))
        return []


def _record_zombie_escalation() -> int:
    """Записывает текущий timestamp в zombie history + возвращает recent count.

    Хранит rolling window — старше _ZOMBIE_FAIL_LOUD_WINDOW_SEC обрезается.
    Atomic write через .tmp + rename. Returns: число escalation-ов в окне
    включая текущий (для решения о fail-loud).
    """
    now = time.time()
    cutoff = now - _ZOMBIE_FAIL_LOUD_WINDOW_SEC
    history = [ts for ts in _read_zombie_history() if ts >= cutoff]
    history.append(now)
    try:
        _ZOMBIE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ZOMBIE_HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(history), encoding="utf-8")
        tmp.replace(_ZOMBIE_HISTORY_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("zombie_history_write_failed", error=str(exc))
    return len(history)


def _count_recent_escalations() -> int:
    """Считает escalations за окно — для startup-диагностики (без записи)."""
    now = time.time()
    cutoff = now - _ZOMBIE_FAIL_LOUD_WINDOW_SEC
    return sum(1 for ts in _read_zombie_history() if ts >= cutoff)


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


async def _probe_updates_flow_alive(
    owner: object,
    *,
    settle_sec: float = 30.0,
) -> bool:
    """Wave 39-D: True если update_id двигался за settle_sec.

    Детектит split-brain: invoke alive + updates_subscriber dead.
    GetUsers работает (heartbeat success), но реальные Telegram updates
    не приходят → _last_seen_update_id заморожен.

    ``owner`` должен иметь атрибут ``_last_seen_update_id`` (int).
    Вынесено на уровень модуля (не метод класса) для удобства тестирования.
    """
    baseline = getattr(owner, "_last_seen_update_id", 0)
    await asyncio.sleep(settle_sec)
    current = getattr(owner, "_last_seen_update_id", 0)
    alive = current > baseline
    logger.debug(
        "updates_flow_probe",
        baseline=baseline,
        current=current,
        alive=alive,
        settle_sec=settle_sec,
    )
    return alive


class NetworkWatchdogMixin:
    """Wave 31-C: TCP-probe + петля мониторинга offline сети.
    Wave 36-A: MTProto session probe + zombie-escalation.
    Wave 39-D: true split-brain detection через update_id tracking."""

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

    async def _send_zombie_alert_to_owner(
        self,
        silence_sec: float,
        consecutive: int,
        *,
        recent_escalations: int = 1,
    ) -> None:
        """Wave 36-A+C: алерт владельцу перед zombie process restart.

        Wave 36-C: при ``recent_escalations >= _ZOMBIE_FAIL_LOUD_THRESHOLD``
        переключается в fail-loud режим — отдельный critical-prefix alert,
        чтобы владелец заметил architectural-уровень проблему (не одиночный
        glitch). Stack trace или architectural review требуется.
        """
        silence_min = int(silence_sec // 60)
        silence_s = int(silence_sec % 60)
        if recent_escalations >= _ZOMBIE_FAIL_LOUD_THRESHOLD:
            window_h = _ZOMBIE_FAIL_LOUD_WINDOW_SEC // 3600
            msg = (
                f"🚨 **КРИТИЧНО: повторяющиеся zombie restart'ы**\n\n"
                f"Krab сделал **{recent_escalations}** zombie escalation за {window_h}h "
                f"(threshold: {_ZOMBIE_FAIL_LOUD_THRESHOLD}).\n"
                f"Текущий: тишина {silence_min}м {silence_s}с, probe failures "
                f"{consecutive}/{_ZOMBIE_ESCALATION_THRESHOLD}.\n\n"
                f"⚠️ Это указывает на **архитектурную проблему**, а не одиночный glitch:\n"
                f"• MTProto session corruption (попробуй `sqlite3 .recover`)\n"
                f"• Telegram-side rate limiting / FloodWait в фоне\n"
                f"• pyrofork → telethon migration может потребоваться\n\n"
                f"Выполняю `os._exit(78)` → launchd respawn, но **посмотри Sentry / логи**."
            )
        else:
            msg = (
                f"🧟 **Krab: zombie session — принудительный перезапуск**\n"
                f"DC доступен, но MTProto session не отвечает {silence_min}м {silence_s}с.\n"
                f"Session probe failures: {consecutive}/{_ZOMBIE_ESCALATION_THRESHOLD}.\n"
                f"Recent zombie escalations: {recent_escalations}/{_ZOMBIE_FAIL_LOUD_THRESHOLD} "
                f"(окно {_ZOMBIE_FAIL_LOUD_WINDOW_SEC // 3600}h).\n"
                f"Выполняю `os._exit(78)` → launchd respawn."
            )
        try:
            await self._send_proactive_watch_alert(msg)
        except Exception as _e:  # noqa: BLE001
            logger.warning("zombie_alert_send_failed", error=str(_e))

    async def _try_reconnect_pyrofork(self, client: "Client") -> bool:
        """Wave 33-A: корректный reconnect для pyrofork 2.3.69.

        Стратегия 1: если client.is_connected → stop(block=True) затем start().
        Pyrogram Client.disconnect() raises ConnectionError когда клиент
        инициализирован (connected state). Client.stop() — правильный shutdown.

        Стратегия 2 (fallback): Ping invoke без disconnect — проверяет
        живость сессии без teardown. Если Ping прошёл — сессия жива.

        Returns True если reconnect / ping успешен.
        """
        import secrets

        # Стратегия 1: graceful stop + start
        try:
            is_conn = getattr(client, "is_connected", False)
            if is_conn:
                await client.stop(block=True)
            await client.start()
            return True
        except (ConnectionError, RuntimeError, OSError) as _e:
            logger.warning("pyrofork_reconnect_strategy1_failed", error=str(_e))

        # Стратегия 2: Ping invoke (без teardown)
        try:
            from pyrogram.raw.functions import Ping  # noqa: PLC0415

            await client.invoke(Ping(ping_id=secrets.randbits(63)))
            return True
        except Exception as _e:  # noqa: BLE001
            logger.warning("pyrofork_reconnect_strategy2_failed", error=str(_e))
            return False

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
        - DC reachable + тишина > KRAB_ZOMBIE_DOUBLE_SILENCE_SEC → MTProto session probe
        - ≥ KRAB_ZOMBIE_ESCALATION_THRESHOLD consecutive probe failures → os._exit(78)
        - launchd respawn'нет автоматически (KeepAlive + ThrottleInterval настроены)
        - ENV: KRAB_ZOMBIE_ESCALATION_ENABLED (default=1), KRAB_ZOMBIE_ESCALATION_THRESHOLD,
                KRAB_ZOMBIE_DOUBLE_SILENCE_SEC

        Дебаунс: не более 1 алерта каждые 30 минут (1800 сек).
        Логика: отслеживаем _last_telegram_event_ts, обновляемый в _process_message.
        Не считаем offline, если userbot только что стартовал (grace period = threshold).
        """
        # Константы и helper уже определены в этом модуле (см. верх файла) —
        # никакого import не нужно. Wave 31-O: fix unresolved import после
        # консолидации (раньше копия жила в bridge.py с другим relative path).

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
                            # Wave 33-A: используем _try_reconnect_pyrofork вместо
                            # прямого disconnect()+start() — исправляет
                            # "Can't disconnect an initialized client" от pyrofork.
                            reconnected = False
                            if self.client:
                                await asyncio.sleep(2)
                                reconnected = await self._try_reconnect_pyrofork(self.client)
                            if reconnected:
                                # Reconnect удался — сбрасываем ts и не алертим
                                self._last_telegram_event_ts = time.time()
                                logger.info(
                                    "network_silence_auto_reconnected",
                                    silence_sec=round(silence_sec, 1),
                                )
                                _alert_active = False
                                continue
                            else:
                                logger.warning(
                                    "network_silence_reconnect_failed",
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

                    # Wave 36-A: DC reachable + тишина достаточно долгая → session probe
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
                                # Zombie подтверждён — уведомляем и перезапускаем процесс
                                logger.error(
                                    "telegram_session_zombie_escalation",
                                    action="process_exit_for_launchd_respawn",
                                    consecutive_failures=_consecutive_zombie_failures,
                                    silence_sec=round(silence_sec, 1),
                                )
                                await self._send_zombie_alert_to_owner(
                                    silence_sec, _consecutive_zombie_failures
                                )
                                # os._exit обходит asyncio cleanup — launchd respawn через exit 78
                                _launchd_exit_78()
                        else:
                            # Wave 39-D: invoke alive — но проверяем updates flow.
                            # GetUsers прошёл (session_alive=True), но возможен
                            # split-brain: invoke API работает, updates_subscriber мёртв.
                            # Измеряем движение _last_seen_update_id за ~30с.
                            # Settle < check_interval, чтобы не блокировать петлю надолго.
                            _split_settle = min(30.0, check_interval * 0.5)
                            updates_alive = await _probe_updates_flow_alive(
                                self, settle_sec=_split_settle
                            )
                            if not updates_alive:
                                logger.warning(
                                    "telegram_split_brain_detected",
                                    silence_sec=round(silence_sec, 1),
                                    settle_sec=_split_settle,
                                    last_seen_update_id=getattr(self, "_last_seen_update_id", 0),
                                )
                                # Пробуем graceful reconnect первым.
                                reconnected = False
                                if self.client:
                                    reconnected = await self._try_reconnect_pyrofork(self.client)
                                if reconnected:
                                    self._last_telegram_event_ts = time.time()
                                    logger.info("split_brain_resolved_via_reconnect")
                                    _consecutive_zombie_failures = 0
                                else:
                                    logger.error(
                                        "split_brain_escalation",
                                        action="process_exit_for_launchd_respawn",
                                    )
                                    try:
                                        await self._send_proactive_watch_alert(
                                            "🧠 **Krab: split-brain сессия** — invoke alive, "
                                            "но updates_subscriber мёртв.\n"
                                            "Graceful reconnect не помог → перезапуск процесса."
                                        )
                                    except Exception:  # noqa: BLE001
                                        pass
                                    _launchd_exit_78()
                    else:
                        # Тишина меньше zombie-порога — просто тихий час
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

    async def _telegram_heartbeat_loop(self) -> None:
        """Wave 36-B: проактивный MTProto heartbeat.

        Каждые HEARTBEAT_INTERVAL_SEC отправляет lightweight API call
        (`GetUsers([InputUserSelf()])`). Это:
        1. Детектит zombie session за <5 мин (вместо 10-15 у Wave 36-A)
        2. Side effect: keepalive — TG видит активную session
        3. При consecutive failures → trigger escalation (os._exit(78) → launchd respawn)

        Работает ПАРАЛЛЕЛЬНО с Wave 36-A (_network_offline_monitor_loop):
        - Wave 36-B — PROACTIVE: не ждёт тишины, сам зондирует каждые 4 мин
        - Wave 36-A — REACTIVE: детектит silence > 10 мин, потом зондирует
        Вместе — defense in depth для zombie session detection.

        ENV:
          KRAB_TELEGRAM_HEARTBEAT_ENABLED          — 1/true/yes (default=1)
          KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC     — интервал (default=240)
          KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD   — порог consecutive failures (default=3)
          KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC      — таймаут одного probe (default=10.0)
        """
        # Читаем конфигурацию из ENV
        _enabled = os.environ.get("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1").strip().lower()
        if _enabled not in {"1", "true", "yes"}:
            logger.info("telegram_heartbeat_disabled")
            return

        _interval = int(os.environ.get("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "240"))
        _fail_threshold = int(os.environ.get("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3"))
        _timeout = float(os.environ.get("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "10.0"))

        logger.info(
            "telegram_heartbeat_started",
            interval_sec=_interval,
            fail_threshold=_fail_threshold,
            timeout_sec=_timeout,
        )

        consecutive_failures = 0
        # Wave 37-A: флаг "graceful reconnect attempt уже был в текущем
        # failure window". Сбрасывается на success. Не позволяет спамить
        # _try_reconnect_pyrofork каждую failed итерацию.
        heartbeat_restart_attempted = False

        while True:
            try:
                await asyncio.sleep(_interval)
            except asyncio.CancelledError:
                logger.info("telegram_heartbeat_cancelled")
                break

            # Не зондируем, если клиент не подключён
            if not (self.client and self.client.is_connected):
                logger.debug("telegram_heartbeat_skip_disconnected")
                continue

            try:
                from pyrogram.raw.functions.users import GetUsers  # noqa: PLC0415
                from pyrogram.raw.types import InputUserSelf  # noqa: PLC0415

                await asyncio.wait_for(
                    self.client.invoke(GetUsers(id=[InputUserSelf()])),
                    timeout=_timeout,
                )

                # Успех: ресетим счётчик сбоев + restart-флаг.
                # Wave 37-A: НЕ обновляем _last_telegram_event_ts — это маскирует
                # split-brain detection в _network_offline_monitor_loop. Вместо
                # этого пишем в отдельное поле _last_heartbeat_ok_ts.
                self._last_heartbeat_ok_ts = time.time()
                consecutive_failures = 0
                heartbeat_restart_attempted = False
                logger.debug("telegram_heartbeat_ok")
                continue

            except asyncio.TimeoutError:
                consecutive_failures += 1
                logger.warning(
                    "telegram_heartbeat_timeout",
                    consecutive_failures=consecutive_failures,
                    threshold=_fail_threshold,
                )
            except asyncio.CancelledError:
                logger.info("telegram_heartbeat_cancelled")
                break
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                logger.warning(
                    "telegram_heartbeat_failed",
                    error=str(exc)[:200],
                    consecutive_failures=consecutive_failures,
                )

            # Wave 37-A: на первый fail в текущем failure window — попытка
            # graceful pyrofork reconnect ПЕРЕД ожиданием threshold. Это
            # быстрее чем ждать N×interval секунд (=12 мин при default).
            if consecutive_failures == 1 and not heartbeat_restart_attempted:
                heartbeat_restart_attempted = True
                logger.warning(
                    "telegram_heartbeat_attempting_graceful_restart",
                    consecutive_failures=consecutive_failures,
                )
                try:
                    restart_ok = await self._try_reconnect_pyrofork(self.client)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "telegram_heartbeat_graceful_restart_exception",
                        error=str(exc)[:200],
                    )
                    restart_ok = False

                if restart_ok:
                    logger.info("telegram_heartbeat_graceful_restart_success")
                    consecutive_failures = 0
                    heartbeat_restart_attempted = False
                    # После reconnect мы знаем session re-handshake'нулась —
                    # имеет смысл сбросить silence timer (даём recovery период
                    # на receive первого update).
                    self._last_telegram_event_ts = time.time()
                    continue
                else:
                    logger.warning("telegram_heartbeat_graceful_restart_failed")
                    # Не escalate сразу — даём counter дойти до threshold,
                    # это safety net на случай transient glitch.

            if consecutive_failures >= _fail_threshold:
                # Zombie подтверждён — уведомляем и перезапускаем через launchd
                logger.error(
                    "telegram_heartbeat_zombie_escalation",
                    action="process_exit_for_launchd_respawn",
                    consecutive_failures=consecutive_failures,
                )
                try:
                    await self._send_zombie_alert_to_owner(
                        float(_interval * consecutive_failures), consecutive_failures
                    )
                except Exception:  # noqa: BLE001
                    pass
                _launchd_exit_78()  # EX_CONFIG → launchd respawn (test-safe wrapper)

    async def _macos_sleep_detect_loop(self) -> None:
        """Wave 36-D: детект macOS sleep через monotonic time jump.

        Алгоритм: каждые KRAB_SLEEP_DETECT_INTERVAL_SEC секунд сравниваем
        ожидаемый интервал с фактическим. Если фактический delta > ожидаемого +
        KRAB_SLEEP_DETECT_THRESHOLD_SEC — mac находился в sleep.

        При детекте: принудительный _force_pyrofork_session_reinit().

        Почему monotonic: time.monotonic() не прыгает во время sleep (в отличие от
        wall clock). asyncio.sleep() тоже заморожен во время sleep. Разница между
        ожидаемым и фактическим прошедшим временем и есть длительность sleep.

        Работает ПАРАЛЛЕЛЬНО с Wave 36-A и Wave 36-B (defense in depth):
        - Wave 36-A — REACTIVE: детектит TG-silence, TCP probe, zombie escalation
        - Wave 36-B — PROACTIVE: heartbeat каждые 4 мин, детектит zombie session
        - Wave 36-D — WAKE: детектит macOS sleep/wake, немедленно reinit session

        ENV:
          KRAB_SLEEP_DETECT_ENABLED        — 1/true/yes (default=1)
          KRAB_SLEEP_DETECT_INTERVAL_SEC   — интервал проверки (default=30)
          KRAB_SLEEP_DETECT_THRESHOLD_SEC  — порог детекта sleep (default=60)
        """
        _enabled = os.environ.get("KRAB_SLEEP_DETECT_ENABLED", "1").strip().lower()
        if _enabled not in {"1", "true", "yes"}:
            logger.info("macos_sleep_detect_disabled")
            return

        _interval = float(os.environ.get("KRAB_SLEEP_DETECT_INTERVAL_SEC", "30"))
        _threshold = float(os.environ.get("KRAB_SLEEP_DETECT_THRESHOLD_SEC", "60"))

        logger.info(
            "macos_sleep_detect_started",
            interval_sec=_interval,
            threshold_sec=_threshold,
        )

        last_check = time.monotonic()

        while True:
            try:
                await asyncio.sleep(_interval)
            except asyncio.CancelledError:
                logger.info("macos_sleep_detect_cancelled")
                break

            now = time.monotonic()
            actual_delta = now - last_check
            last_check = now

            # Если фактический delta сильно больше ожидаемого — был sleep
            if actual_delta > _interval + _threshold:
                sleep_duration = actual_delta - _interval
                logger.warning(
                    "macos_sleep_detected",
                    sleep_duration_sec=round(sleep_duration, 1),
                    actual_delta_sec=round(actual_delta, 1),
                    expected_sec=_interval,
                )
                try:
                    await self._force_pyrofork_session_reinit()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "macos_post_sleep_reinit_failed",
                        error=str(exc)[:200],
                    )

    async def _force_pyrofork_session_reinit(self) -> None:
        """Wave 36-D: принудительный reinit pyrofork session после macOS sleep.

        Стратегия:
        1. client.stop(block=True) — ждёт graceful disconnect (TTL freeze закончен)
        2. await asyncio.sleep(2) — даём сети устояться после пробуждения
        3. client.start() — re-handshake, fresh MTProto session

        При failure → логируем и поднимаем исключение (caller решает что делать).
        Fall-through на Wave 36-A escalation logic происходит автоматически
        через _network_offline_monitor_loop / heartbeat.
        """
        logger.info("forced_pyrofork_reinit_starting")
        try:
            if self.client and hasattr(self.client, "stop"):
                await self.client.stop(block=True)
            await asyncio.sleep(2)
            if self.client:
                await self.client.start()
            # Сбрасываем таймер тишины: reconnect = событие активности
            self._last_telegram_event_ts = time.time()
            logger.info("forced_pyrofork_reinit_success")
        except Exception as exc:  # noqa: BLE001
            logger.warning("forced_pyrofork_reinit_failed", error=str(exc)[:200])
            raise

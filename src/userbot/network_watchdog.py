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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import config
from ..core.logger import get_logger

if TYPE_CHECKING:
    from pyrogram import Client  # noqa: F401 — для "Client" type annotation

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Wave 63-A Step 1: GetState pts probe — detection state container
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GetStateProbeResult:
    """Wave 63-A Step 1: outcome `_probe_updates_via_get_state`.

    Поля:
      alive               — есть ли уверенность что updates dispatch loop жив
      split_brain_suspected — server pts двинулся, update_id заморожен
      server_pts          — последний server pts (или 0 если не получен)
      server_pts_delta    — насколько pts вырос с предыдущего snapshot
      error               — текст ошибки (None если probe прошёл)
    """

    alive: bool
    split_brain_suspected: bool = False
    server_pts: int = 0
    server_pts_delta: int = 0
    error: str | None = None


async def _probe_updates_via_get_state(
    owner: object,
    *,
    timeout_sec: float = 8.0,
    update_id_baseline: int | None = None,
) -> GetStateProbeResult:
    """Wave 63-A Step 1: split-brain detector через `updates.GetState`.

    Алгоритм:
      1. Делаем `client.invoke(GetState())` (легковесный, нет side-effects).
      2. Сравниваем server `pts` с предыдущим snapshot `owner._last_server_pts`.
      3. Если server `pts` advanced (delta >= 1), но `_last_seen_update_id` НЕ
         двинулся с указанного baseline → flag split-brain.

    Параметры:
      ``owner``               — duck-type с `_last_server_pts`, `_last_seen_update_id`,
                                `client`.
      ``update_id_baseline``  — против чего сравнивать update_id. По умолчанию
                                равен текущему `_last_seen_update_id`
                                (т.е. "со времени последнего вызова движений не было").

    Возвращает `GetStateProbeResult`. Snapshot записывается в
    ``owner._last_server_pts`` всегда когда GetState вернулся успешно — это
    позволяет следующему probe сравнивать с актуальным значением.
    """
    client = getattr(owner, "client", None)
    if client is None:
        return GetStateProbeResult(alive=False, error="no_client")

    prev_pts = int(getattr(owner, "_last_server_pts", 0) or 0)
    current_uid = int(getattr(owner, "_last_seen_update_id", 0) or 0)
    baseline_uid = current_uid if update_id_baseline is None else int(update_id_baseline)

    try:
        from pyrogram.raw.functions.updates import GetState  # noqa: PLC0415

        state = await asyncio.wait_for(
            client.invoke(GetState()),  # type: ignore[union-attr]
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("get_state_probe_timeout", timeout_sec=timeout_sec)
        return GetStateProbeResult(alive=False, error="timeout")
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_state_probe_failed", error=str(exc)[:200])
        return GetStateProbeResult(alive=False, error=str(exc)[:200])

    server_pts = int(getattr(state, "pts", 0) or 0)
    # Всегда обновляем snapshot — снимает дрейф между probe и следующим.
    try:
        owner._last_server_pts = server_pts  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    if prev_pts <= 0:
        # Первая инициализация: нет previous, не можем судить о split-brain.
        return GetStateProbeResult(
            alive=True,
            split_brain_suspected=False,
            server_pts=server_pts,
            server_pts_delta=0,
        )

    delta = server_pts - prev_pts
    if delta <= 0:
        # Server не двигался — quiet window, не split-brain.
        return GetStateProbeResult(
            alive=True,
            split_brain_suspected=False,
            server_pts=server_pts,
            server_pts_delta=delta,
        )

    # Server pts advanced. Двинулся ли update_id?
    if current_uid > baseline_uid:
        return GetStateProbeResult(
            alive=True,
            split_brain_suspected=False,
            server_pts=server_pts,
            server_pts_delta=delta,
        )

    # Split-brain: server активен (pts +N), а dispatch loop стоит.
    logger.warning(
        "updates_pts_split_brain",
        server_pts=server_pts,
        server_pts_delta=delta,
        last_seen_update_id=current_uid,
        prev_server_pts=prev_pts,
    )
    return GetStateProbeResult(
        alive=False,
        split_brain_suspected=True,
        server_pts=server_pts,
        server_pts_delta=delta,
    )


# ---------------------------------------------------------------------------
# Wave 63-C: dispatcher tick staleness check
# ---------------------------------------------------------------------------

# Сколько секунд без вызова _process_message считаем "starved" dispatcher.
# 10 минут — два heartbeat-цикла (4 мин × 2 + buffer); ловит ситуации когда
# pts probe alive, но handler chain не работает (Pyrogram dispatcher dead,
# updates parser стопится, или message loop wedged).
_DISPATCHER_TICK_STALENESS_SEC: float = float(
    os.environ.get("KRAB_DISPATCHER_TICK_STALENESS_SEC", "600")
)

# Wave 63-D: surgical recovery для main kraab при dispatcher_starved.
# Default OFF — observability-only mode для сбора production data о
# false-positive rate ПЕРЕД auto-recovery. User включает = 1 в .env когда
# уверен. Throttle — минимальный интервал между попытками recovery (10 мин
# default), защищает от tight loop при persistent starvation.
_DISPATCHER_RECOVERY_ENABLED: bool = (
    os.environ.get("KRAB_DISPATCHER_RECOVERY_ENABLED", "0").strip() == "1"
)
_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC: float = float(
    os.environ.get("KRAB_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC", "600")
)


def _check_dispatcher_starved(
    owner: object,
    *,
    now: float | None = None,
    staleness_sec: float | None = None,
) -> bool:
    """Wave 63-C: True если последний dispatcher tick старше staleness_sec.

    Возвращает True (starved) только если ``_last_dispatcher_tick_ts`` явно
    устарел. Если у owner нет атрибута (не bridge) — False (fail-open: probe
    в swarm clients не должен фолзить из-за отсутствия инфраструктуры).

    Cross-reference signal: вызывать после того как pts probe сказал "alive".
    Stale tick + alive pts = новый split-brain pattern.
    """
    last_ts = getattr(owner, "_last_dispatcher_tick_ts", None)
    if last_ts is None:
        return False
    threshold = _DISPATCHER_TICK_STALENESS_SEC if staleness_sec is None else staleness_sec
    current = time.time() if now is None else now
    return (current - float(last_ts)) >= threshold


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


async def _active_probe_updates_subscriber(
    client: object,
    *,
    timeout_sec: float = 8.0,
) -> bool:
    """Wave 44-I: активный probe updates_subscriber через GetDialogs.

    Проблема Wave 44-C: passive `_probe_updates_flow_alive` ждёт движения
    `_last_seen_update_id`. В genuinely quiet windows (3am-7am, 0 incoming
    traffic) update_id заморожен → false-failure → exit_78 → respawn loop.

    Решение: `client.invoke(GetDialogs(limit=1))` идёт через Pyrogram
    dispatcher (НЕ bypass его) — если updates_subscriber реально мёртв,
    invoke зависнет/упадёт; если жив — вернётся быстро. Никаких видимых
    side-effects (read-only).

    Returns True если probe прошёл за timeout_sec, False иначе.
    """
    try:
        from pyrogram.raw.functions.messages import GetDialogs  # noqa: PLC0415
        from pyrogram.raw.types import InputPeerEmpty  # noqa: PLC0415

        await asyncio.wait_for(
            client.invoke(  # type: ignore[union-attr]
                GetDialogs(
                    offset_date=0,
                    offset_id=0,
                    offset_peer=InputPeerEmpty(),
                    limit=1,
                    hash=0,
                )
            ),
            timeout=timeout_sec,
        )
        return True
    except asyncio.TimeoutError:
        logger.warning("active_probe_updates_subscriber_timeout", timeout_sec=timeout_sec)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("active_probe_updates_subscriber_failed", error=str(exc)[:200])
        return False


async def _probe_updates_flow_alive(
    owner: object,
    *,
    settle_sec: float = 30.0,
    active_probe_on_silence: bool = True,
) -> bool:
    """Wave 39-D + Wave 44-I: True если update_id двигался ИЛИ активный probe жив.

    Детектит split-brain: invoke alive + updates_subscriber dead.
    GetUsers работает (heartbeat success), но реальные Telegram updates
    не приходят → _last_seen_update_id заморожен.

    Wave 44-I (hybrid): если passive probe (settle_sec/2) не увидел
    движения update_id, делаем active probe через GetDialogs(limit=1).
    Это устраняет false-positive в quiet windows (ночь, 0 traffic):
    - probe OK = "quiet but alive" → return True
    - probe fail = real split-brain → продолжаем passive ожидание + return False

    ``owner`` должен иметь атрибут ``_last_seen_update_id`` (int) и
    опционально ``client`` (Pyrogram Client) для active probe.
    """
    baseline = getattr(owner, "_last_seen_update_id", 0)

    # Hybrid: первая половина пассивного ожидания
    first_half = settle_sec / 2.0 if active_probe_on_silence else settle_sec
    await asyncio.sleep(first_half)
    current = getattr(owner, "_last_seen_update_id", 0)

    if current > baseline:
        logger.debug(
            "updates_flow_probe",
            baseline=baseline,
            current=current,
            alive=True,
            settle_sec=settle_sec,
            phase="passive_first_half",
        )
        return True

    # Passive frozen: пробуем active probe (если разрешено и есть client)
    client = getattr(owner, "client", None)
    if active_probe_on_silence and client is not None:
        probe_ok = await _active_probe_updates_subscriber(client)
        if probe_ok:
            logger.debug(
                "updates_flow_probe",
                baseline=baseline,
                current=current,
                alive=True,
                settle_sec=settle_sec,
                phase="active_probe_quiet_window",
            )
            return True
        # Active probe failed → продолжаем passive ожидание (вторая половина)
        await asyncio.sleep(settle_sec - first_half)
        current = getattr(owner, "_last_seen_update_id", 0)
        alive = current > baseline
        logger.debug(
            "updates_flow_probe",
            baseline=baseline,
            current=current,
            alive=alive,
            settle_sec=settle_sec,
            phase="active_probe_failed_passive_second_half",
        )
        return alive

    # active_probe_on_silence=False or no client: legacy passive-only behaviour
    # Досыпаем оставшееся время чтобы соблюсти полный settle_sec contract.
    remaining = settle_sec - first_half
    if remaining > 0:
        await asyncio.sleep(remaining)
        current = getattr(owner, "_last_seen_update_id", 0)
    alive = current > baseline
    logger.debug(
        "updates_flow_probe",
        baseline=baseline,
        current=current,
        alive=alive,
        settle_sec=settle_sec,
        phase="passive_only",
    )
    return alive


# Wave 57-A: throttle — не чаще одного catchup каждые 5 минут,
# чтобы избежать catchup storm при heartbeat flapping.
_GRACEFUL_RESTART_CATCHUP_THROTTLE_SEC: float = float(
    os.environ.get("KRAB_GRACEFUL_RESTART_CATCHUP_THROTTLE_SEC", "300")
)
# Wave 57-A: минимальный uptime процесса перед тем как catchup допустим
# после graceful restart (60s). Если process только стартовал, startup
# catchup уже запускается из userbot_started — дублировать не нужно.
_GRACEFUL_RESTART_CATCHUP_MIN_UPTIME_SEC: float = float(
    os.environ.get("KRAB_GRACEFUL_RESTART_CATCHUP_MIN_UPTIME_SEC", "60")
)


class NetworkWatchdogMixin:
    """Wave 31-C: TCP-probe + петля мониторинга offline сети.
    Wave 36-A: MTProto session probe + zombie-escalation.
    Wave 39-D: true split-brain detection через update_id tracking.
    Wave 57-A: catchup trigger после graceful Pyrogram restart."""

    def _schedule_catchup_after_graceful_restart(self) -> None:
        """Wave 57-A: fire-and-forget catchup task после graceful Pyrogram restart.

        Вызывается из _telegram_heartbeat_loop сразу после успешного
        _try_reconnect_pyrofork. Не блокирует watchdog loop.

        Throttle: не чаще одного catchup за 5 минут (_GRACEFUL_RESTART_CATCHUP_THROTTLE_SEC).
        Uptime guard: если процесс только стартовал (<60s), startup catchup
        из userbot_started уже идёт — не дублируем.

        Production bug 2026-05-10 21:12-21:31: после graceful restart (21:12:50
        telegram_heartbeat_graceful_restart_success) пользователь написал
        "Проверка связи" в 21:31:43 — сообщение не было ingested в inbox
        т.к. catchup не был triggered (Wave 46-A срабатывает только при
        полном process restart через userbot_started hook).
        """
        now = time.time()

        # Uptime guard: если process только стартовал, startup catchup уже работает
        session_start = getattr(self, "_session_start_time", now)
        uptime_sec = now - session_start
        if uptime_sec < _GRACEFUL_RESTART_CATCHUP_MIN_UPTIME_SEC:
            logger.debug(
                "graceful_restart_catchup_skipped_startup",
                uptime_sec=round(uptime_sec, 1),
                min_uptime_sec=_GRACEFUL_RESTART_CATCHUP_MIN_UPTIME_SEC,
            )
            return

        # Throttle: избегаем catchup storm при heartbeat flapping
        last_catchup = getattr(self, "_last_catchup_triggered_at", 0.0)
        elapsed_since_last = now - last_catchup
        if elapsed_since_last < _GRACEFUL_RESTART_CATCHUP_THROTTLE_SEC:
            logger.debug(
                "graceful_restart_catchup_throttled",
                elapsed_since_last_sec=round(elapsed_since_last, 1),
                throttle_sec=_GRACEFUL_RESTART_CATCHUP_THROTTLE_SEC,
            )
            return

        # Проверяем что метод catchup доступен (mixin-совместимость)
        catchup_fn = getattr(self, "_run_startup_catchup_safe", None)
        if catchup_fn is None:
            logger.warning("graceful_restart_catchup_method_missing")
            return

        # Записываем timestamp до create_task — защита от race condition
        self._last_catchup_triggered_at = now

        logger.info(
            "graceful_restart_triggering_catchup",
            uptime_sec=round(uptime_sec, 1),
            elapsed_since_last_catchup_sec=round(elapsed_since_last, 1),
        )

        try:
            asyncio.create_task(catchup_fn(), name="graceful_restart_catchup")
        except Exception as exc:  # noqa: BLE001
            # Rollback timestamp чтобы следующий restart мог попробовать снова
            self._last_catchup_triggered_at = last_catchup
            logger.warning("graceful_restart_catchup_schedule_failed", error=str(exc)[:200])

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

    async def _attempt_dispatcher_recovery(self) -> None:
        """Wave 63-D: surgical recovery main kraab client при dispatcher_starved.

        Гейт `KRAB_DISPATCHER_RECOVERY_ENABLED` (default 0): пока observability-
        only mode. При =1 — invoke `_try_reconnect_pyrofork(self.client)` с
        throttle `KRAB_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC` (default 600s).

        Swarm clients и openclaw остаются нетронутыми → zero-downtime для
        остальных компонентов.
        """
        if not _DISPATCHER_RECOVERY_ENABLED:
            logger.warning(
                "dispatcher_starved_recovery_skipped",
                reason="disabled",
            )
            return

        now = time.time()
        last_ts = getattr(self, "_last_dispatcher_recovery_ts", 0.0) or 0.0
        elapsed = now - float(last_ts)
        if elapsed < _DISPATCHER_RECOVERY_MIN_INTERVAL_SEC:
            logger.warning(
                "dispatcher_starved_recovery_skipped",
                reason="throttled",
                elapsed_sec=round(elapsed, 1),
                min_interval_sec=_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC,
            )
            return

        self._last_dispatcher_recovery_ts = now
        client = getattr(self, "client", None)
        if client is None:
            logger.warning(
                "dispatcher_starved_recovery_skipped",
                reason="no_client",
            )
            return

        logger.warning(
            "dispatcher_starved_recovery_attempt",
            min_interval_sec=_DISPATCHER_RECOVERY_MIN_INTERVAL_SEC,
        )
        try:
            ok = await self._try_reconnect_pyrofork(client)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dispatcher_starved_recovery_exception",
                error=str(exc)[:200],
                error_type=type(exc).__name__,
            )
            return

        if ok:
            logger.info("dispatcher_starved_recovery_ok")
            # Сбрасываем silence-timer чтобы heartbeat не считал tick немедленно
            # stale (свежий reconnect → reset event budget).
            self._last_telegram_event_ts = time.time()
        else:
            logger.warning("dispatcher_starved_recovery_failed")

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

                    # Wave 63-A Step 2: DROP 10-минутный gate для split-brain probe.
                    # Раньше (Wave 36-A): `silence_sec > _ZOMBIE_DOUBLE_SILENCE_SEC=600`
                    # — split-brain probe запускался только после 10+ минут тишины.
                    # Это давало 93+ минут до detection в инциденте 2026-05-11
                    # 22:07→23:42. Теперь probe запускается сразу при достижении
                    # threshold_sec (180s default), gated только на dc_reachable +
                    # zombie_enabled. Константа `_ZOMBIE_DOUBLE_SILENCE_SEC`
                    # сохранена для других путей (например future reconnect
                    # cooldown), но больше не контролирует probe.
                    if _zombie_enabled:
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
                                    # Wave 44-C: post-reconnect verification.
                                    # Wave 39-D обнаруживал split-brain, но
                                    # `_try_reconnect_pyrofork` мог вернуть True
                                    # на TCP-уровне, оставив updates_subscriber мёртвым.
                                    # Production observed twice 2026-05-09 (06:58, 18:46):
                                    # log "split_brain_resolved_via_reconnect" есть,
                                    # а incoming messages не обрабатываются.
                                    # Re-probe update_id за post_reconnect_verify_sec —
                                    # если всё ещё frozen → false-success → escalate.
                                    _post_reconnect_verify_sec = max(
                                        10.0, min(15.0, check_interval * 0.3)
                                    )
                                    updates_after_reconnect = await _probe_updates_flow_alive(
                                        self, settle_sec=_post_reconnect_verify_sec
                                    )
                                    if updates_after_reconnect:
                                        logger.info(
                                            "split_brain_resolved_via_reconnect",
                                            verified=True,
                                            verify_settle_sec=_post_reconnect_verify_sec,
                                        )
                                        _consecutive_zombie_failures = 0
                                    else:
                                        logger.error(
                                            "split_brain_reconnect_did_not_restore_updates",
                                            action="process_exit_for_launchd_respawn",
                                            verify_settle_sec=_post_reconnect_verify_sec,
                                            last_seen_update_id=getattr(
                                                self, "_last_seen_update_id", 0
                                            ),
                                        )
                                        try:
                                            await self._send_proactive_watch_alert(
                                                "🧠 **Krab: false-recovery split-brain** — "
                                                "reconnect succeeded, но updates_subscriber "
                                                "всё ещё мёртв.\n"
                                                "→ Эскалация: launchd respawn (Wave 44-C)."
                                            )
                                        except Exception:  # noqa: BLE001
                                            pass
                                        _launchd_exit_78()
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

        # Wave 63-A Step 1: GetState pts probe — детектит split-brain за 4 мин
        # вместо 93. ENV gate для safe rollout (default ON).
        _get_state_probe_enabled = os.environ.get(
            "KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED", "1"
        ).strip().lower() in {"1", "true", "yes"}
        _get_state_probe_timeout = float(
            os.environ.get("KRAB_HEARTBEAT_GET_STATE_TIMEOUT_SEC", "8.0")
        )

        logger.info(
            "telegram_heartbeat_started",
            interval_sec=_interval,
            fail_threshold=_fail_threshold,
            timeout_sec=_timeout,
            get_state_probe_enabled=_get_state_probe_enabled,
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

                # Wave 63-A Step 1: GetState pts probe для split-brain detection.
                # Сравниваем server `pts` с предыдущим snapshot. Если server pts
                # вырос, а `_last_seen_update_id` не двинулся — dispatch loop
                # мёртв (invoke работает, updates_subscriber — нет).
                # Detect за heartbeat-цикл (~4 мин) вместо silence+probe (~93 мин).
                if _get_state_probe_enabled:
                    try:
                        pre_probe_uid = int(getattr(self, "_last_seen_update_id", 0) or 0)
                        probe = await _probe_updates_via_get_state(
                            self,
                            timeout_sec=_get_state_probe_timeout,
                            update_id_baseline=pre_probe_uid,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "heartbeat_get_state_probe_exception",
                            error=str(exc)[:200],
                        )
                        probe = None

                    # Wave 63-C: cross-reference сигнал. pts probe увидел
                    # движение server pts, но _process_message не вызывался
                    # давно → handler chain мёртв (network OK, dispatcher dead).
                    # Логируем отдельно для ops-видимости; реакция остаётся за
                    # split_brain_suspected ниже (consistent escalation).
                    if (
                        probe is not None
                        and probe.alive
                        and probe.server_pts_delta > 0
                        and _check_dispatcher_starved(self)
                    ):
                        last_tick_ts = getattr(self, "_last_dispatcher_tick_ts", 0.0)
                        logger.warning(
                            "dispatcher_starved_detected",
                            server_pts=probe.server_pts,
                            server_pts_delta=probe.server_pts_delta,
                            last_dispatcher_tick_ago_sec=round(
                                time.time() - float(last_tick_ts), 1
                            ),
                            dispatcher_tick_count=getattr(self, "_dispatcher_tick_count", 0),
                            staleness_threshold_sec=_DISPATCHER_TICK_STALENESS_SEC,
                        )
                        # Wave 63-D: surgical recovery (main kraab only).
                        await self._attempt_dispatcher_recovery()

                    if probe is not None and probe.split_brain_suspected:
                        logger.warning(
                            "split_brain_via_get_state",
                            server_pts=probe.server_pts,
                            server_pts_delta=probe.server_pts_delta,
                            last_seen_update_id=pre_probe_uid,
                            action="immediate_reconnect",
                        )
                        try:
                            reconnect_ok = await self._try_reconnect_pyrofork(self.client)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "split_brain_via_get_state_reconnect_exception",
                                error=str(exc)[:200],
                            )
                            reconnect_ok = False
                        if reconnect_ok:
                            logger.info(
                                "split_brain_via_get_state_reconnect_ok",
                                server_pts=probe.server_pts,
                            )
                            # После reconnect сбрасываем silence-timer и триггерим
                            # catchup (Wave 57-A) для пропущенных сообщений.
                            self._last_telegram_event_ts = time.time()
                            self._schedule_catchup_after_graceful_restart()
                        else:
                            logger.warning(
                                "split_brain_via_get_state_reconnect_failed",
                                server_pts=probe.server_pts,
                            )
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
                    # Wave 57-A: trigger catchup для missed messages за время
                    # пока heartbeat был недоступен. Production bug 2026-05-10
                    # 21:12-21:31: msg 16890 "Проверка связи" не попал в inbox.
                    self._schedule_catchup_after_graceful_restart()
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
                    # Wave 50-A + 41-O hygiene: WARNING (не ERROR) для
                    # ожидаемых post-sleep transient failures. Network
                    # offline monitor / zombie escalation подхватит если
                    # реально zombie session.
                    logger.warning(
                        "macos_post_sleep_reinit_failed",
                        error=str(exc)[:200],
                    )

    @staticmethod
    async def _safe_client_disconnect(client: "Client | None") -> bool:
        """Wave 50-A: idempotent client teardown для post-sleep reinit.

        Pyrogram raises `ConnectionError("Client is already disconnected")`
        когда client.stop() вызывается на уже отключённом клиенте — это
        ожидаемое состояние после macOS sleep, когда система сама порвала
        socket. Глотаем ConnectionError + проверяем `is_connected` чтобы
        не дёргать stop() впустую.

        Returns: True если stop отработал или клиент уже отключён (no-op
        success), False если stop поднял неожиданную ошибку.
        """
        if client is None:
            return True
        # Defensive: getattr — pyrofork может изменить API stability
        is_conn = getattr(client, "is_connected", False)
        if not is_conn:
            logger.debug("safe_disconnect_skip_not_connected")
            return True
        if not hasattr(client, "stop"):
            return True
        try:
            await client.stop(block=True)
            return True
        except ConnectionError as exc:
            # Pyrofork: "Client is already disconnected" — ok после sleep
            logger.debug("safe_disconnect_already_disconnected", error=str(exc)[:120])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("safe_disconnect_unexpected_error", error=str(exc)[:200])
            return False

    async def _force_pyrofork_session_reinit(self) -> None:
        """Wave 36-D: принудительный reinit pyrofork session после macOS sleep.

        Стратегия:
        1. _safe_client_disconnect — idempotent stop (Wave 50-A: глотает
           "Client is already disconnected" ConnectionError, которая
           возникает когда macOS sleep уже разорвал socket до wake).
        2. await asyncio.sleep(2) — даём сети устояться после пробуждения
        3. client.start() — re-handshake, fresh MTProto session

        Wave 50-A: failure → log WARNING (не ERROR, Wave 41-O hygiene),
        исключение поднимаем для caller-loop телеметрии.
        Fall-through на Wave 36-A escalation logic происходит автоматически
        через _network_offline_monitor_loop / heartbeat.
        """
        logger.info("forced_pyrofork_reinit_starting")
        try:
            await self._safe_client_disconnect(self.client)
            await asyncio.sleep(2)
            if self.client:
                await self.client.start()
            # Сбрасываем таймер тишины: reconnect = событие активности
            self._last_telegram_event_ts = time.time()
            logger.info("forced_pyrofork_reinit_success")
        except Exception as exc:  # noqa: BLE001
            logger.warning("forced_pyrofork_reinit_failed", error=str(exc)[:200])
            raise

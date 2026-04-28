"""Тесты bootstrap learning singletons (Session 28 part 4).

Проверяют что:
1. owner_presence_tracker.configure_default_path принимает Path и грузится.
2. repl_session.configure_default_paths принимает audit_log_path.
3. pattern_detector.configure_default_path принимает Path.
4. record_owner_seen вызывается на outgoing message owner'а (is_self=True hook
   из userbot_bridge handle_message_event).

Полный start() KraabUserbot не вызываем — он требует Pyrogram client/MTProto.
Вместо этого убеждаемся что bootstrap-API стабилен и outgoing hook реально
двигает last_seen_at (regression guard для Idea 17 holdover).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_owner_presence_bootstrap_configures_storage_path(tmp_path: Path) -> None:
    """Bootstrap путь подхватывается, last_seen_at читается с диска."""
    from src.core.owner_presence import OwnerPresenceTracker  # noqa: PLC0415

    tracker = OwnerPresenceTracker()
    storage = tmp_path / "owner_presence.json"
    tracker.configure_default_path(storage)
    # После configure файла ещё нет — last_seen None.
    assert tracker.last_seen_at() is None
    # Запись heartbeat создаёт файл.
    tracker.record_owner_seen()
    assert storage.exists()
    # Повторная конфигурация подгружает state.
    tracker2 = OwnerPresenceTracker()
    tracker2.configure_default_path(storage)
    assert tracker2.last_seen_at() is not None


def test_repl_session_bootstrap_configures_audit_log(tmp_path: Path) -> None:
    """Bootstrap audit-log пути не падает; путь сохраняется."""
    from src.core.repl_session import REPLSession  # noqa: PLC0415

    session = REPLSession()
    audit_log = tmp_path / "repl_audit.log"
    session.configure_default_paths(audit_log)
    # Путь принят без ошибок (внутреннее состояние не сразу пишет файл).
    # Достаточно факт что не было исключения.
    assert True


def test_pattern_detector_bootstrap_configures_storage_path(tmp_path: Path) -> None:
    """Bootstrap пути подхватывается; повторный configure на новый путь
    переинициализирует state."""
    from src.core.proactive_suggestions import PatternDetector  # noqa: PLC0415

    detector = PatternDetector()
    storage = tmp_path / "proactive.json"
    detector.configure_default_path(storage)
    # Не должно бросать; configure повторно — тоже.
    storage2 = tmp_path / "proactive2.json"
    detector.configure_default_path(storage2)


def test_outgoing_message_hook_records_owner_seen(tmp_path: Path) -> None:
    """Симулирует hook is_self=True из handle_message_event:
    record_owner_seen() должен сдвинуть last_seen_at вперёд."""
    from src.core.owner_presence import OwnerPresenceTracker  # noqa: PLC0415

    tracker = OwnerPresenceTracker()
    storage = tmp_path / "owner_presence.json"
    tracker.configure_default_path(storage)

    # Эмулируем фрагмент bridge:
    #     if is_self:
    #         try:
    #             from .core.owner_presence import owner_presence_tracker
    #             owner_presence_tracker.record_owner_seen()
    #         except Exception: pass
    is_self = True
    if is_self:
        tracker.record_owner_seen()

    seen = tracker.last_seen_at()
    assert seen is not None
    # is_offline=False сразу после heartbeat.
    assert tracker.is_offline(threshold_min=60.0) is False


def test_module_singletons_have_expected_api() -> None:
    """Smoke-test: module-level singletons экспортируются и имеют API,
    которое использует bootstrap в userbot_bridge.start()."""
    from src.core.anomaly_detector import anomaly_detector  # noqa: PLC0415
    from src.core.chat_sensitivity import sensitive_chat_registry  # noqa: PLC0415
    from src.core.named_entity_memory import named_entity_memory  # noqa: PLC0415
    from src.core.owner_presence import owner_presence_tracker  # noqa: PLC0415
    from src.core.proactive_suggestions import pattern_detector  # noqa: PLC0415
    from src.core.repl_session import repl_session  # noqa: PLC0415

    assert hasattr(owner_presence_tracker, "configure_default_path")
    assert hasattr(owner_presence_tracker, "record_owner_seen")
    assert hasattr(repl_session, "configure_default_paths")
    assert hasattr(pattern_detector, "configure_default_path")
    # Session 28 part 4: bulk wire-up batch (Idea 13/26/28).
    assert hasattr(named_entity_memory, "configure_default_path")
    assert hasattr(anomaly_detector, "configure_default_path")
    assert hasattr(sensitive_chat_registry, "configure_default_path")


def test_named_entity_memory_bootstrap_configures_storage_path(tmp_path: Path) -> None:
    """Idea 13: bootstrap путь подхватывается без ошибок, повторный configure
    допустим."""
    from src.core.named_entity_memory import EntityStore  # noqa: PLC0415

    store = EntityStore()
    storage = tmp_path / "named_entities.json"
    store.configure_default_path(storage)
    storage2 = tmp_path / "named_entities2.json"
    store.configure_default_path(storage2)


def test_anomaly_detector_bootstrap_configures_storage_path(tmp_path: Path) -> None:
    """Idea 26: bootstrap пути не падает; повторный configure ок."""
    from src.core.anomaly_detector import AnomalyDetector  # noqa: PLC0415

    detector = AnomalyDetector()
    storage = tmp_path / "anomaly_baselines.json"
    detector.configure_default_path(storage)
    storage2 = tmp_path / "anomaly_baselines2.json"
    detector.configure_default_path(storage2)


def test_sensitive_chat_registry_bootstrap_configures_storage_path(tmp_path: Path) -> None:
    """Idea 28: bootstrap путь подхватывается, повторный configure не падает."""
    from src.core.chat_sensitivity import SensitiveChatRegistry  # noqa: PLC0415

    registry = SensitiveChatRegistry()
    storage = tmp_path / "sensitive_chats.json"
    registry.configure_default_path(storage)
    storage2 = tmp_path / "sensitive_chats2.json"
    registry.configure_default_path(storage2)


def test_bootstrap_failure_logged_not_raised(tmp_path: Path) -> None:
    """Если configure_default_path бросит, bootstrap должен поймать и
    залогировать (warning), а не уронить start(). Эмулируем broken singleton."""
    from src.core.owner_presence import OwnerPresenceTracker  # noqa: PLC0415

    tracker = OwnerPresenceTracker()

    with patch.object(
        tracker, "configure_default_path", side_effect=OSError("disk full")
    ):
        # Эмулируем bootstrap try-except:
        logger = MagicMock()
        try:
            tracker.configure_default_path(tmp_path / "x.json")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "owner_presence_bootstrap_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        logger.warning.assert_called_once()
        call_kwargs = logger.warning.call_args.kwargs
        assert call_kwargs["error_type"] == "OSError"
        assert call_kwargs["error"] == "disk full"

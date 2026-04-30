# -*- coding: utf-8 -*-
"""
Тесты авто-старта proactive_watch:
- PROACTIVE_WATCH_ENABLED включён по умолчанию
- отключается через env=0
- интервал по умолчанию 900 сек
- get_status() отражает значение enabled из конфига
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestProactiveWatchConfig:
    """Тесты конфигурации proactive_watch в Config."""

    def test_enabled_by_default(self):
        """PROACTIVE_WATCH_ENABLED=True без явной env-переменной."""
        with patch.dict("os.environ", {}, clear=False):
            # Убираем возможный overriding env
            import os

            env_backup = os.environ.pop("PROACTIVE_WATCH_ENABLED", None)
            try:
                # Перезагружаем Config с «чистым» env
                import importlib

                import src.config as cfg_mod

                importlib.reload(cfg_mod)
                from src.config import Config

                assert Config.PROACTIVE_WATCH_ENABLED is True
            finally:
                if env_backup is not None:
                    os.environ["PROACTIVE_WATCH_ENABLED"] = env_backup
                # Восстанавливаем модуль в исходное состояние
                importlib.reload(cfg_mod)

    def test_disabled_via_env(self):
        """PROACTIVE_WATCH_ENABLED=0 → Config.PROACTIVE_WATCH_ENABLED is False."""
        import importlib
        import os

        orig = os.environ.get("PROACTIVE_WATCH_ENABLED")
        os.environ["PROACTIVE_WATCH_ENABLED"] = "0"
        try:
            import src.config as cfg_mod

            importlib.reload(cfg_mod)
            from src.config import Config

            assert Config.PROACTIVE_WATCH_ENABLED is False
        finally:
            if orig is None:
                os.environ.pop("PROACTIVE_WATCH_ENABLED", None)
            else:
                os.environ["PROACTIVE_WATCH_ENABLED"] = orig
            importlib.reload(cfg_mod)

    def test_interval_default(self):
        """PROACTIVE_WATCH_INTERVAL_SEC по умолчанию = 900 секунд."""
        import importlib
        import os

        orig = os.environ.get("PROACTIVE_WATCH_INTERVAL_SEC")
        os.environ.pop("PROACTIVE_WATCH_INTERVAL_SEC", None)
        try:
            import src.config as cfg_mod

            importlib.reload(cfg_mod)
            from src.config import Config

            assert Config.PROACTIVE_WATCH_INTERVAL_SEC == 900
        finally:
            if orig is not None:
                os.environ["PROACTIVE_WATCH_INTERVAL_SEC"] = orig
            importlib.reload(cfg_mod)


class TestProactiveWatchGetStatus:
    """Тесты метода ProactiveWatch.get_status()."""

    def _make_watcher(self, enabled: bool = True, interval: int = 900) -> object:
        """
        Создаёт экземпляр ProactiveWatch с замокированными тяжёлыми зависимостями.
        """
        heavy_mocks = {
            "src.core.proactive_watch.macos_automation": MagicMock(),
            "src.core.proactive_watch.memory_manager": MagicMock(),
            "src.core.proactive_watch.openclaw_client": MagicMock(),
            "src.core.proactive_watch.anomaly_detector": MagicMock(),
            "src.core.proactive_watch.auto_restart_manager": MagicMock(),
            "src.core.proactive_watch.inbox_service": MagicMock(),
            "src.core.proactive_watch.krab_scheduler": MagicMock(),
            "src.core.proactive_watch.append_workspace_memory_entry": MagicMock(),
            "src.core.proactive_watch.get_runtime_primary_model": MagicMock(
                return_value="test/model"
            ),
        }
        with patch.multiple("src.core.proactive_watch", **{k.split(".")[-1]: v for k, v in heavy_mocks.items()}):
            # Патчим конфиг прямо в модуле
            mock_config = MagicMock()
            mock_config.PROACTIVE_WATCH_ENABLED = enabled
            mock_config.PROACTIVE_WATCH_INTERVAL_SEC = interval
            mock_config.PROACTIVE_WATCH_ALERT_COOLDOWN_SEC = 1800
            mock_config.BASE_DIR = MagicMock()

            with patch("src.core.proactive_watch.config", mock_config):
                from src.core.proactive_watch import ProactiveWatch

                watcher = ProactiveWatch.__new__(ProactiveWatch)
                # Минимальная инициализация без вызова __init__
                watcher.alert_cooldown_sec = 1800
                watcher._state_path = MagicMock()
                watcher._config = mock_config

                # Патчим _load_state чтобы не читать файл
                watcher._load_state = MagicMock(return_value={})
                # Подменяем config прямо на атрибут экземпляра через closure
                # get_status читает module-level config, поэтому патчим там
                return watcher, mock_config

    def test_get_status_reflects_enabled(self):
        """get_status() → dict с enabled=True когда конфиг включён."""
        mock_config = MagicMock()
        mock_config.PROACTIVE_WATCH_ENABLED = True
        mock_config.PROACTIVE_WATCH_INTERVAL_SEC = 900
        mock_config.PROACTIVE_WATCH_ALERT_COOLDOWN_SEC = 1800
        mock_config.BASE_DIR = MagicMock()

        with patch("src.core.proactive_watch.config", mock_config):
            with patch("src.core.proactive_watch.krab_scheduler", MagicMock()):
                with patch("src.core.proactive_watch.macos_automation", MagicMock()):
                    with patch("src.core.proactive_watch.memory_manager", MagicMock()):
                        with patch("src.core.proactive_watch.openclaw_client", MagicMock()):
                            with patch("src.core.proactive_watch.anomaly_detector", MagicMock()):
                                with patch(
                                    "src.core.proactive_watch.auto_restart_manager", MagicMock()
                                ):
                                    with patch(
                                        "src.core.proactive_watch.inbox_service", MagicMock()
                                    ):
                                        from src.core.proactive_watch import ProactiveWatchService

                                        watcher = ProactiveWatchService.__new__(ProactiveWatchService)
                                        watcher.alert_cooldown_sec = 1800
                                        watcher._load_state = MagicMock(return_value={})

                                        status = watcher.get_status()

        assert isinstance(status, dict)
        assert status["enabled"] is True
        assert status["interval_sec"] == 900

    def test_get_status_reflects_disabled(self):
        """get_status() → dict с enabled=False когда конфиг выключен."""
        mock_config = MagicMock()
        mock_config.PROACTIVE_WATCH_ENABLED = False
        mock_config.PROACTIVE_WATCH_INTERVAL_SEC = 900
        mock_config.PROACTIVE_WATCH_ALERT_COOLDOWN_SEC = 1800
        mock_config.BASE_DIR = MagicMock()

        with patch("src.core.proactive_watch.config", mock_config):
            with patch("src.core.proactive_watch.krab_scheduler", MagicMock()):
                with patch("src.core.proactive_watch.macos_automation", MagicMock()):
                    with patch("src.core.proactive_watch.memory_manager", MagicMock()):
                        with patch("src.core.proactive_watch.openclaw_client", MagicMock()):
                            with patch("src.core.proactive_watch.anomaly_detector", MagicMock()):
                                with patch(
                                    "src.core.proactive_watch.auto_restart_manager", MagicMock()
                                ):
                                    with patch(
                                        "src.core.proactive_watch.inbox_service", MagicMock()
                                    ):
                                        from src.core.proactive_watch import ProactiveWatchService

                                        watcher = ProactiveWatchService.__new__(ProactiveWatchService)
                                        watcher.alert_cooldown_sec = 1800
                                        watcher._load_state = MagicMock(return_value={})

                                        status = watcher.get_status()

        assert status["enabled"] is False

    def test_get_status_contains_required_keys(self):
        """get_status() возвращает все обязательные ключи."""
        mock_config = MagicMock()
        mock_config.PROACTIVE_WATCH_ENABLED = True
        mock_config.PROACTIVE_WATCH_INTERVAL_SEC = 900
        mock_config.PROACTIVE_WATCH_ALERT_COOLDOWN_SEC = 1800
        mock_config.BASE_DIR = MagicMock()

        with patch("src.core.proactive_watch.config", mock_config):
            with patch("src.core.proactive_watch.krab_scheduler", MagicMock()):
                with patch("src.core.proactive_watch.macos_automation", MagicMock()):
                    with patch("src.core.proactive_watch.memory_manager", MagicMock()):
                        with patch("src.core.proactive_watch.openclaw_client", MagicMock()):
                            with patch("src.core.proactive_watch.anomaly_detector", MagicMock()):
                                with patch(
                                    "src.core.proactive_watch.auto_restart_manager", MagicMock()
                                ):
                                    with patch(
                                        "src.core.proactive_watch.inbox_service", MagicMock()
                                    ):
                                        from src.core.proactive_watch import ProactiveWatchService

                                        watcher = ProactiveWatchService.__new__(ProactiveWatchService)
                                        watcher.alert_cooldown_sec = 1800
                                        watcher._load_state = MagicMock(return_value={})

                                        status = watcher.get_status()

        required_keys = {
            "enabled",
            "interval_sec",
            "alert_cooldown_sec",
            "last_reason",
            "last_digest_ts",
            "last_alert_ts",
            "last_alerted_reason",
            "last_snapshot",
        }
        assert required_keys.issubset(status.keys())

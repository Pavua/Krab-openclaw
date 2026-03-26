# -*- coding: utf-8 -*-
"""
Регрессионные проверки voice-настроек central Config.

Зачем:
- после рестарта userbot обязан подняться с тем voice-профилем, который лежит в `.env`;
- если typed-поля voice пропадают из `Config`, runtime молча стартует с выключенным TTS.
"""

from __future__ import annotations

import importlib

import src.config as config_module


def test_config_reads_voice_defaults_from_env(monkeypatch) -> None:
    """Voice-поля должны читаться из env и оставаться доступными как typed-атрибуты."""
    with monkeypatch.context() as mp:
        mp.setenv("VOICE_MODE_DEFAULT", "1")
        mp.setenv("VOICE_REPLY_SPEED", "1.25")
        mp.setenv("VOICE_REPLY_VOICE", "ru-RU-SvetlanaNeural")
        mp.setenv("VOICE_REPLY_DELIVERY", "voice-only")

        reloaded = importlib.reload(config_module)
        assert reloaded.config.VOICE_MODE_DEFAULT is True
        assert reloaded.config.VOICE_REPLY_SPEED == 1.25
        assert reloaded.config.VOICE_REPLY_VOICE == "ru-RU-SvetlanaNeural"
        assert reloaded.config.VOICE_REPLY_DELIVERY == "voice-only"

    importlib.reload(config_module)


def test_update_setting_updates_voice_fields_and_env(tmp_path, monkeypatch) -> None:
    """`update_setting()` должен синхронно обновлять и память, и `.env` для voice-ключей."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "VOICE_MODE_DEFAULT=1",
                "VOICE_REPLY_SPEED=1.5",
                "VOICE_REPLY_VOICE=ru-RU-DmitryNeural",
                "VOICE_REPLY_DELIVERY=text+voice",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with monkeypatch.context() as mp:
        mp.setattr(config_module.Config, "BASE_DIR", tmp_path)
        mp.setattr(config_module.Config, "VOICE_MODE_DEFAULT", True, raising=False)
        mp.setattr(config_module.Config, "VOICE_REPLY_SPEED", 1.5, raising=False)
        mp.setattr(config_module.Config, "VOICE_REPLY_VOICE", "ru-RU-DmitryNeural", raising=False)
        mp.setattr(config_module.Config, "VOICE_REPLY_DELIVERY", "text+voice", raising=False)

        assert config_module.Config.update_setting("VOICE_MODE_DEFAULT", "0") is True
        assert config_module.Config.update_setting("VOICE_REPLY_SPEED", "1.2") is True
        assert config_module.Config.update_setting("VOICE_REPLY_VOICE", "ru-RU-SvetlanaNeural") is True
        assert config_module.Config.update_setting("VOICE_REPLY_DELIVERY", "voice-only") is True

        assert config_module.Config.VOICE_MODE_DEFAULT is False
        assert config_module.Config.VOICE_REPLY_SPEED == 1.2
        assert config_module.Config.VOICE_REPLY_VOICE == "ru-RU-SvetlanaNeural"
        assert config_module.Config.VOICE_REPLY_DELIVERY == "voice-only"

    env_text = env_path.read_text(encoding="utf-8")
    assert "VOICE_MODE_DEFAULT=0" in env_text
    assert "VOICE_REPLY_SPEED=1.2" in env_text
    assert "VOICE_REPLY_VOICE=ru-RU-SvetlanaNeural" in env_text
    assert "VOICE_REPLY_DELIVERY=voice-only" in env_text

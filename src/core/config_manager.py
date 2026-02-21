# -*- coding: utf-8 -*-
"""
Config Manager для Krab v2.5.
Горячая перезагрузка настроек без перезапуска бота.
Читает/пишет YAML-конфиг, доступен через команду !config.
"""

import copy
import os
import yaml
import logging

logger = logging.getLogger("ConfigManager")

# Путь к конфигу
CONFIG_PATH = "config.yaml"

# Дефолтные значения
DEFAULTS = {
    "ai": {
        "default_model": "gemini-2.0-flash",
        "reasoning_model": "gemini-2.0-pro-exp",
        "temperature": 0.7,
        "max_tokens": 4096,
        "use_rag": True,
    },
    "tts": {
        "voice": "ru-RU-DmitryNeural",
        "speed": "+0%",
    },
    "security": {
        "rate_limit": 10,
        "rate_window_sec": 60,
    },
    "scheduler": {
        "digest_hour": 9,
        "backup_hour": 3,
    },
    "personality": {
        "emoji_style": "moderate",
    },
    "group_chat": {
        "allow_replies": True,
    }
}


class ConfigManager:
    """
    Менеджер конфигурации с горячей перезагрузкой.
    
    Использование:
        cfg = ConfigManager()
        model = cfg.get("ai.default_model")        # → "gemini-2.0-flash"
        cfg.set("ai.temperature", 0.9)              # → сохраняет в YAML
    """
    
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.data = {}
        self._load()
    
    def _load(self):
        """Загрузка конфига из файла или создание нового."""
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    self.data = yaml.safe_load(f) or {}
                logger.info(f"✅ Config loaded from {self.path}")
            except Exception as e:
                logger.error(f"❌ Failed to load config: {e}")
                self.data = {}
        else:
            # Создаём конфиг с дефолтами
            self.data = DEFAULTS.copy()
            self._save()
            logger.info(f"📝 Default config created at {self.path}")
    
    def _save(self):
        """Сохранение текущего конфига в файл."""
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                yaml.dump(self.data, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            logger.error(f"❌ Failed to save config: {e}")
    
    def get(self, key: str, default=None):
        """
        Получить значение по dot-нотации.
        Пример: cfg.get("ai.temperature") → 0.7
        """
        keys = key.split(".")
        value = self.data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                # Попробуем вернуть из дефолтов
                def_value = DEFAULTS
                for dk in keys:
                    if isinstance(def_value, dict):
                        def_value = def_value.get(dk)
                    else:
                        return default
                return def_value if def_value is not None else default
        return value
    
    def set(self, key: str, value) -> bool:
        """
        Установить значение по dot-нотации и сохранить.
        Пример: cfg.set("ai.temperature", 0.9)
        """
        keys = key.split(".")
        d = self.data
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        
        d[keys[-1]] = value
        self._save()
        logger.info(f"🔄 Config updated: {key} = {value}")
        return True
    
    def reload(self):
        """Перечитать конфиг с диска."""
        self._load()

    def to_display(self) -> str:
        """Форматированный вывод для показа в чате."""
        lines = ["**⚙️ Krab Config:**\n"]
        for section, values in self.data.items():
            lines.append(f"**[{section}]**")
            if isinstance(values, dict):
                for k, v in values.items():
                    lines.append(f"  `{k}`: `{v}`")
            else:
                lines.append(f"  `{values}`")
            lines.append("")
        return "\n".join(lines)

    def get_all(self) -> dict:
        """Возвращает всё загруженное конфигурационное дерево (copy)."""
        return copy.deepcopy(self.data)

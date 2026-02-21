# -*- coding: utf-8 -*-
"""
Plugin Manager v1.0 (Phase 13).
Динамическая загрузка модулей из папки plugins/.
"""

import os
import importlib.util
import structlog
from typing import Dict, Any

logger = structlog.get_logger("PluginManager")

class PluginManager:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = plugins_dir
        self.plugins: Dict[str, Any] = {}
        os.makedirs(self.plugins_dir, exist_ok=True)

    async def load_all(self, app, deps: Dict[str, Any]):
        """Загружает все плагины из директории."""
        if not os.path.exists(self.plugins_dir):
            return

        for filename in os.listdir(self.plugins_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                plugin_name = filename[:-3]
                await self.load_plugin(plugin_name, app, deps)

    async def load_plugin(self, name: str, app, deps: Dict[str, Any]) -> bool:
        """Загружает конкретный плагин по имени."""
        path = os.path.join(self.plugins_dir, f"{name}.py")
        if not os.path.exists(path):
            logger.error(f"Plugin {name} not found at {path}")
            return False

        try:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Если в плагине есть функция register_handlers, вызываем её
            if hasattr(module, "register_handlers"):
                module.register_handlers(app, deps)
                logger.info(f"✅ Plugin '{name}' registered handlers")
            
            # Если есть setup_plugin, вызываем
            if hasattr(module, "setup_plugin"):
                await module.setup_plugin(deps)
                logger.info(f"✅ Plugin '{name}' setup completed")

            self.plugins[name] = module
            return True
        except Exception as e:
            logger.error(f"❌ Failed to load plugin '{name}': {e}")
            return False

    async def unload_plugin(self, name: str):
        """Отключает плагин (упрощенно: удаляет из списка)."""
        if name in self.plugins:
            # Предупреждение: Pyrogram не поддерживает легкую отмену декораторов 
            # без перезапуска или костылей. Поэтому "выгрузка" требует осторожности.
            del self.plugins[name]
            logger.info(f"🔌 Plugin '{name}' unloaded (registry only)")
            return True
        return False

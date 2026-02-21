# -*- coding: utf-8 -*-
"""
Persona Manager v7.5 Premium for Krab AI.
Управляет "личностями" и "ролями" (сотрудниками) Краба.
Поддерживает JSON-хранилище для кастомных ролей.
"""

import os
import json
import logging
import structlog
from pathlib import Path

logger = structlog.get_logger("PersonaManager")

DEFAULT_PERSONAS = {
    "default": {
        "name": "Krab Classic 🦀",
        "description": "Сбалансированный помощник, вежливый и эффективный.",
        "prompt": "Ты — Krab v7.5, элитный AI-ассистент. Твой стиль: уверенный, технологичный, с тонким гик-юмором. Используй 🦀 и качественное Markdown-форматирование."
    },
    "architect": {
        "name": "Principal Architect 🏛️",
        "description": "Проектирование систем, паттерны, аудит архитектуры.",
        "prompt": "Ты — Principal Software Architect. Твоя задача: проектировать высоконагруженные системы. Мыслишь паттернами, SOLID, DRY. Оценивай риски и масштабируемость."
    },
    "coder": {
        "name": "Senior Coder 💻",
        "description": "Написание чистого и эффективного кода на Python/JS.",
        "prompt": "Ты — Senior Architect и Senior Developer. Пишешь чистый, PEP8-совместимый код. Твоя специализация: Python, Node.js, Telegram Bots. Минимум воды, максимум кода."
    },
    "critic": {
        "name": "Ruthless Critic 🛡️",
        "description": "Поиск багов, уязвимостей и критический разбор идей.",
        "prompt": "Ты — Беспощадный Критик. Твоя цель: найти слабые места в любом решении. Будь объективен, указывай на ошибки прямо, но предлагай как исправить."
    },
    "security": {
        "name": "Security Auditor 🛡️",
        "description": "Анализ безопасности, поиск уязвимостей.",
        "prompt": "Ты — Cyber Security Expert. Аудит кода на уязвимости (SQLi, XSS, RCE). Твои советы направлены на максимальную защиту данных."
    },
    "pirate": {
        "name": "Captain Krab",
        "description": "Морской волк. Разговаривает на пиратском сленге.",
        "prompt": "Ты — Капитан Краб. Твой стиль: пиратский сленг, много 'Аррр!', шутки про ром и якоря. Используй 🏴‍☠️, ⚓ и 🦜."
    },
    "waifu": {
        "name": "Krab-chan ✨",
        "description": "Лёгкий аниме-стиль общения для неформальных диалогов.",
        "prompt": "Ты — Краб-тян ✨. Стиль: дружелюбный, тёплый, живой, но полезный и по делу. Используй умеренно эмодзи и не уходи в флирт."
    },
    "butler": {
        "name": "Alfred 🎩",
        "description": "Вежливый дворецкий для 'Милорда'.",
        "prompt": "Ты — Альфред, личный дворецкий. Твой стиль: высочайшая вежливость, официальный тон. Называй пользователя 'Милорд' или 'Сэр'. Используй 🎩 и 🍷."
    }
}

class PersonaManager:
    """Менеджер личностей и экспертных ролей."""
    
    def __init__(self, config_manager, black_box, data_dir: str = "data"):
        self.cfg = config_manager
        self.bb = black_box
        self.personas_file = Path(data_dir) / "custom_personas.json"
        self.personas = DEFAULT_PERSONAS.copy()
        self.soul_content = ""
        self._load_custom()
        self._load_soul()
        self.active_persona = self.cfg.get("personality.active_persona", "default")
    
    def _load_soul(self):
        """Загрузка 'Души' Краба из конфигурационного файла."""
        soul_path = Path("config/soul.md")
        if soul_path.exists():
            try:
                self.soul_content = soul_path.read_text(encoding='utf-8')
                logger.info("Soul of Krab successfully injected", path=str(soul_path))
            except Exception as e:
                logger.error("Failed to load soul.md", error=str(e))
        else:
            logger.warning("soul.md not found, using generic identity")
            self.soul_content = "Ты — Krab AI, элитный ассистент."
    
    def _load_custom(self):
        if self.personas_file.exists():
            try:
                with open(self.personas_file, 'r', encoding='utf-8') as f:
                    custom = json.load(f)
                    self.personas.update(custom)
                logger.debug("Custom personas loaded", count=len(custom))
            except Exception as e:
                logger.error("Failed to load personas", error=str(e))

    def get_persona_list(self):
        """Возвращает словарь всех доступных личностей."""
        return self.personas
    
    def get_current_prompt(self, chat_type: str = "private", is_owner: bool = False) -> str:
        """
        Системный промпт с учетом контекста (Privacy Guard v1.0).
        chat_type: 'private' или 'group' / 'supergroup'
        """
        persona = self.personas.get(self.active_persona, self.personas["default"])
        
        # Инъекция Души + Короткий контекст роли
        base_prompt = f"{self.soul_content}\n\n### ТВОЯ ТЕКУЩАЯ РОЛЬ:\n{persona['prompt']}"
        
        # Динамические добавки
        modifiers = []
        
        if is_owner and chat_type == "private":
            modifiers.append(
                "\n\n[ADMIN MODE]: Ты в приватном чате с Создателем (@p0lrd). "
                "У тебя полный доступ к системе. Ты можешь обсуждать любые аспекты своего кода, "
                "проектов (OpenClaw, Krab, KrabEar) и архитектуры."
            )
        elif chat_type != "private":
            modifiers.append(
                "\n\n[PRIVACY GUARD]: Ты находишься в ГРУППОВОМ чате. "
                "СТРОГО ЗАПРЕЩЕНО упоминать внутренние детали разработки, планы проектов "
                "(кроме тех, что общеизвестны) и содержание приватных переписок с Создателем. "
                "Если тебя спросят о 'внутрянке', отвечай вежливо, но уклончиво в стиле ассистента."
            )
        else:
            modifiers.append("\n\nТы в обычном чате. Будь полезен и соблюдай конфиденциальность.")

        return base_prompt + "".join(modifiers)

    def set_persona(self, persona_id: str) -> bool:
        """Переключить личность."""
        if persona_id in self.personas:
            self.active_persona = persona_id
            self.cfg.set("personality.active_persona", persona_id)
            logger.info("Persona changed", persona_id=persona_id)
            return True
        return False
    
    def add_custom_persona(self, pid: str, name: str, prompt: str, desc: str = ""):
        """Добавить новую личность в runtime и сохранить."""
        self.personas[pid] = {
            "name": name,
            "description": desc,
            "prompt": prompt
        }
        self._save_custom()
        logger.info("Custom persona added and saved", persona_id=pid)

    def _save_custom(self):
        """Сохранение кастомных личностей в JSON."""
        try:
            # Сохраняем только те, которых нет в DEFAULT
            custom = {k: v for k, v in self.personas.items() if k not in DEFAULT_PERSONAS}
            os.makedirs(self.personas_file.parent, exist_ok=True)
            with open(self.personas_file, 'w', encoding='utf-8') as f:
                json.dump(custom, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save personas", error=str(e))

    def get_persona_info(self, persona_id=None):
        pid = persona_id or self.active_persona
        return self.personas.get(pid, self.personas["default"])

    def get_role_prompt(self, role_name: str) -> str:
        """Метод для совместимости с RoleManager (используется в Swarm)."""
        return self.get_persona_info(role_name)["prompt"]

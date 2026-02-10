# -*- coding: utf-8 -*-
"""
Self-Refactoring Module (Phase 9).
Бот анализирует собственный код и предлагает/применяет улучшения.

Зачем: Позволяет боту эволюционировать, исправлять баги в своём коде
и оптимизировать производительность по команде владельца.
Связь: Вызывается командой !refactor в main.py.
"""

import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("SelfRefactor")


class SelfRefactor:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.ignored_dirs = {".git", ".venv", "__pycache__", "artifacts", "backups", "node_modules"}

    def get_project_structure(self) -> str:
        """Собирает структуру проекта для контекста AI."""
        structure = []
        for root, dirs, files in os.walk(self.project_root):
            # Фильтруем игнорируемые директории
            dirs[:] = [d for d in dirs if d not in self.ignored_dirs]
            
            level = root.replace(str(self.project_root), '').count(os.sep)
            indent = ' ' * 4 * level
            structure.append(f"{indent}{os.path.basename(root)}/")
            sub_indent = ' ' * 4 * (level + 1)
            for f in files:
                if f.endswith(".py") or f.endswith(".md"):
                    structure.append(f"{sub_indent}{f}")
        
        return "\n".join(structure)

    def read_file(self, relative_path: str) -> str:
        """Читает файл проекта."""
        file_path = self.project_root / relative_path
        if not file_path.exists():
            return f"❌ Файл {relative_path} не найден."
        
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ Ошибка чтения {relative_path}: {e}"

    async def analyze_and_propose(self, router, target_file: str, instructions: str = "") -> str:
        """
        Использует AI (Router) для анализа файла и предложений по рефакторингу.
        """
        code = self.read_file(target_file)
        if code.startswith("❌"):
            return code

        prompt = f"""
        Ты — Сеньор-Архитектор проекта Krab. Проведи ревизию кода в файле `{target_file}`.
        
        ИНСТРУКЦИИ ПО РЕФАКТОРИНГУ:
        {instructions or "Найди потенциальные баги, неоптимальные решения или места, требующие улучшения читаемости."}
        
        ТЕКУЩИЙ КОД ФАЙЛА:
        ```python
        {code}
        ```
        
        ОТВЕТЬ В ФОРМАТЕ:
        1. **Анализ**: Кратко, что не так.
        2. **Предложение**: Что именно нужно изменить.
        3. **Новый Код**: Полный исправленный код файла внутри блока ```python ... ```.
        """

        logger.info(f"👨‍🔬 Analyzing file for refactoring: {target_file}")
        response = await router.route_query(prompt, task_type='reasoning')
        
        return response

    async def apply_refactor(self, target_file: str, new_content: str) -> str:
        """Применяет изменения (перезаписывает файл)."""
        file_path = self.project_root / target_file
        if not file_path.exists():
            return f"❌ Файл {target_file} не найден."
        
        try:
            # Делаем бекап перед записью
            backup_path = file_path.with_suffix(f".py.bak_{int(time.time())}")
            file_path.rename(backup_path)
            
            # Записываем новый контент
            file_path.write_text(new_content, encoding="utf-8")
            return f"✅ Файл `{target_file}` успешно обновлён. Бекап: `{backup_path.name}`"
        except Exception as e:
            return f"❌ Ошибка при записи файла: {e}"

    async def find_vulnerabilities(self, router) -> str:
        """Сканирует проект на уязвимости (безопасность)."""
        structure = self.get_project_structure()
        
        prompt = f"""
        Ты — Хацкер-безопасник. Проанализируй структуру проекта Krab и найди потенциальные дыры.
        
        СТРУКТУРА:
        ```
        {structure}
        ```
        
        На что обратить внимание:
        - Хранение ключей/токенов в коде.
        - Shell injection в subprocess.
        - Небезопасные exec/eval.
        - Проблемы с изоляцией контекста.
        
        Выдай отчет в стиле 'Bug Bounty'.
        """
        
        return await router.route_query(prompt, task_type='reasoning')

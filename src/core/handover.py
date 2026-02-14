# -*- coding: utf-8 -*-
"""
Krab Project Handover Engine v1.0.
Автоматически генерирует документацию (HANDOVER.md) для завершенных проектов.
"""

import os
import json
from datetime import datetime
import structlog
from typing import Dict, Any

logger = structlog.get_logger("HandoverEngine")

class HandoverEngine:
    def __init__(self, router):
        self.router = router

    async def generate_report(self, state: Any, projects_dir: str) -> str:
        """
        Генерирует Markdown-отчет на основе состояния проекта.
        """
        project_id = state.project_id
        project_path = os.path.join(projects_dir, project_id)
        os.makedirs(project_path, exist_ok=True)
        
        report_path = os.path.join(project_path, "HANDOVER.md")
        
        logger.info("📄 Generating Handover Report", project_id=project_id)

        # Подготовка данных для LLM
        tasks_summary = ""
        for task in state.plan:
            status_icon = "✅" if task.get("status") == "completed" else "❌"
            tasks_summary += f"- {status_icon} **{task.get('title')}**: {task.get('description')}\n"
            if task.get("result"):
                tasks_summary += f"  - *Результат:* {str(task.get('result'))[:200]}...\n"

        prompt = f"""
Ты — Senior Project Manager. Твоя задача — составить итоговый отчет (HANDOVER.md) для завершенного автономного проекта.

ЦЕЛЬ ПРОЕКТА: {state.goal}

ВЫПОЛНЕННЫЕ ЗАДАЧИ:
{tasks_summary}

СОЗДАННЫЕ ФАЙЛЫ:
{", ".join(state.files_created) if state.files_created else "Нет данных о новых файлах"}

ИНСТРУКЦИИ ДЛЯ ОТЧЕТА:
1. Используй СТРОГО РУССКИЙ ЯЗЫК.
2. Сделай отчет профессиональным, структурированным и вдохновляющим.
3. Добавь разделы: # [Название проекта], ## Итоги, ## Технические решения, ## Рекомендации.
4. Выдели ключевые достижения.

ВЕРНИ ТОЛЬКО ТЕКСТ MARKDOWN.
"""
        
        report_content = await self.router.route_query(prompt, task_type="creative")
        
        # Очистка markdown блоков
        if "```markdown" in report_content:
            report_content = report_content.split("```markdown")[1].split("```")[0].strip()
        elif "```" in report_content:
            report_content = report_content.split("```")[1].split("```")[0].strip()

        # Сохраняем отчет в директорию проекта
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)
            f.write("\n\n---\n*Отчет сгенерирован автоматически Krab Handover Engine*")

        logger.info("✅ Handover Report Saved", path=report_path)
        return report_path

# -*- coding: utf-8 -*-
"""
Verification script for Handover Engine.
Simulates a completed project and checks for report generation.
"""

import asyncio
import os
import json
from unittest.mock import MagicMock
from src.core.agent_loop import ProjectAgent, ProjectState
from src.core.handover import HandoverEngine

class MockRouter:
    async def route_query(self, prompt, task_type=None):
        return """
# Project Achievement Report
## Итоги
Проект успешно завершен. Все задачи выполнены на 100%.
## Технические решения
Использованы современные паттерны и чистая архитектура.
## Рекомендации
Продолжайте в том же духе!
"""

async def test_handover():
    router = MockRouter()
    tools = MagicMock()
    memory = MagicMock()
    
    projects_dir = "data/test_projects"
    os.makedirs(projects_dir, exist_ok=True)
    
    agent = ProjectAgent(router, tools, memory, projects_dir=projects_dir)
    
    # Создаем фиктивное состояние проекта
    project_id = "test_handover_proj"
    state = ProjectState(project_id, "Тестовая цель проекта")
    state.plan = [
        {"id": 1, "title": "Задача 1", "description": "Описание 1", "status": "completed", "result": "Успех"},
        {"id": 2, "title": "Задача 2", "description": "Описание 2", "status": "completed", "result": "Готово"}
    ]
    state.files_created = ["test_file.py", "config.json"]
    state.status = "completed"
    
    # Сохраняем состояние (нужно для HandoverEngine, так как он ищет папку)
    project_path = os.path.join(projects_dir, project_id)
    os.makedirs(project_path, exist_ok=True)
    
    print(f"🚀 Running Handover Engine for {project_id}...")
    report_path = await agent.handover.generate_report(state, projects_dir)
    
    print(f"✅ Report generated at: {report_path}")
    
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
            print("--- REPORT CONTENT ---")
            print(content)
            print("--- END OF REPORT ---")
            if "Итоги" in content and "Krab Handover Engine" in content:
                print("✨ VERIFICATION SUCCESSFUL!")
            else:
                print("❌ Verification failed: Content mismatch.")
    else:
        print("❌ Verification failed: Report file not found.")

if __name__ == "__main__":
    asyncio.run(test_handover())

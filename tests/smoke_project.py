# -*- coding: utf-8 -*-
"""
Smoke Test for ProjectAgent.
Проверка создания проекта, генерации плана и выполнения шагов.
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.append(str(Path(__file__).parent.parent))

from src.core.agent_loop import ProjectAgent
from src.core.model_manager import ModelRouter
from src.core.tool_handler import ToolHandler
from src.core.context_manager import ContextKeeper

async def smoke_test():
    print("🚀 Starting ProjectAgent Smoke Test...")
    
    # Инициализация минимальных зависимостей
    router = ModelRouter(config=os.environ)
    memory = ContextKeeper()
    tools = ToolHandler(router, None, None) # RAG и OpenClaw не важны для базы
    
    agent = ProjectAgent(router, tools, memory)
    
    goal = "Напиши 'Hello World' в лог и завершись."
    chat_id = 12345
    
    print(f"1. Creating project with goal: {goal}")
    project_id = await agent.create_project(goal, chat_id)
    print(f"✅ Project Created: {project_id}")
    
    print("2. Running Step 1 (Planning)...")
    result1 = await agent.run_step(project_id)
    print(f"✅ Step 1 Result: {result1['status']}")
    
    if result1['status'] == 'planned':
        print("Plan generated:")
        for t in result1['plan']:
            print(f"  - {t['id']}: {t['title']}")
    else:
        print(f"❌ Planning failed: {result1}")
        return

    print("3. Running Step 2 (Execution of Task 1)...")
    result2 = await agent.run_step(project_id)
    print(f"✅ Step 2 Result: {result2['status']}, Task: {result2.get('task')}")
    
    print("4. Running remaining steps...")
    max_loops = 5
    while max_loops > 0:
        res = await agent.run_step(project_id)
        print(f"   Status: {res['status']}")
        if res['status'] == 'completed':
            print("✅ Project Completed Experimentally!")
            break
        max_loops -= 1
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(smoke_test())

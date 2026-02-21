# -*- coding: utf-8 -*-
"""
Krab Project Agent Loop v1.0 (Phase 16).
Управляет жизненным циклом автономного проекта: планирование, выполнение, отчетность.
Поддерживает персистентность состояния проекта.
"""

import os
import json
import asyncio
import structlog
from datetime import datetime
from typing import List, Dict, Any, Optional
from src.core.handover import HandoverEngine

logger = structlog.get_logger("ProjectAgent")

class ProjectState:
    """Хранилище состояния проекта."""
    def __init__(self, project_id: str, goal: str):
        self.project_id = project_id
        self.goal = goal
        self.status = "initializing"
        self.plan: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.files_created: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectState':
        state = cls(data['project_id'], data['goal'])
        state.__dict__.update(data)
        return state

class ProjectAgent:
    """
    Автономный агент для выполнения многошаговых проектов.
    """
    def __init__(self, router, tools, memory, projects_dir: str = "data/projects"):
        self.router = router
        self.tools = tools
        self.memory = memory
        self.projects_dir = projects_dir
        os.makedirs(self.projects_dir, exist_ok=True)
        self.active_projects: Dict[str, ProjectState] = {}
        self.handover = HandoverEngine(self.router)

    def _get_project_path(self, project_id: str) -> str:
        return os.path.join(self.projects_dir, f"{project_id}.json")

    async def create_project(self, goal: str, chat_id: int) -> str:
        """Создает новый проект и возвращает его ID."""
        project_id = f"proj_{int(datetime.now().timestamp())}_{chat_id}"
        state = ProjectState(project_id, goal)
        self.active_projects[project_id] = state
        self._save_state(state)
        
        logger.info("🆕 Project Created", project_id=project_id, goal=goal)
        return project_id

    def _save_state(self, state: ProjectState):
        """Сохраняет состояние проекта в файл."""
        path = self._get_project_path(state.project_id)
        state.updated_at = datetime.now().isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)

    async def run_step(self, project_id: str) -> Dict[str, Any]:
        """
        Выполняет один "шаг" проекта (Размышление или Действие).
        """
        if project_id not in self.active_projects:
            # Попробуем загрузить из файла
            path = self._get_project_path(project_id)
            if os.path.exists(path):
                with open(path, 'r') as f:
                    self.active_projects[project_id] = ProjectState.from_dict(json.load(f))
            else:
                return {"error": "Project not found"}

        state = self.active_projects[project_id]
        
        # 1. Если плана еще нет — планируем
        if not state.plan:
            await self._generate_plan(state)
            return {"status": "planned", "plan": state.plan}

        # 2. Ищем невыполненную задачу
        for task in state.plan:
            if task.get("status") == "pending":
                result = await self._execute_task(state, task)
                return {"status": "executing", "task": task["title"], "result": result}

        state.status = "completed"
        self._save_state(state)
        
        # Generate Handover Report
        try:
            report_path = await self.handover.generate_report(state, self.projects_dir)
            return {"status": "completed", "summary": "Все задачи выполнены. Информационный отчет создан.", "report_path": report_path}
        except Exception as e:
            logger.error("❌ Failed to generate handover report", error=str(e))
            return {"status": "completed", "summary": "Все задачи выполнены. Ошибка создания отчета."}

    async def _generate_plan(self, state: ProjectState):
        """Генерирует план проекта через LLM."""
        state.status = "planning"
        self._save_state(state)

        prompt = f"""
Ты — Старший Архитектор. Разбей следующую цель на последовательность конкретных задач для автономного агента.
ЦЕЛЬ: {state.goal}

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{self.tools.get_tool_registry()}

ВЕРНИ ОТВЕТ СТРОГО В ПРЕДЛОЖЕННОМ JSON (МАССИВ ОБЪЕКТОВ):
[
  {{ "id": 1, "title": "Название задачи", "description": "Что именно сделать", "depends_on": [] }},
  ...
]
"""
        response = await self.router.route_query(prompt, task_type="coding")
        
        # Auto-Fallback: Если облако вернуло ошибку (401/500/Network), форсируем локальную модель
        if not response or response.startswith("⚠️") or response.startswith("❌"):
            logger.warning(f"⚠️ Cloud Plan Gen failed: {response}. Auto-Switching to FORCE LOCAL...")
            
            # Сохраняем текущий режим и форсируем локальный
            original_mode = self.router.force_mode
            self.router.force_mode = "force_local"
            try:
                response = await self.router.route_query(prompt, task_type="coding")
            finally:
                self.router.force_mode = original_mode

        try:
            # Очистка JSON
            clean_json = response.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            
            plan = json.loads(clean_json)
            for task in plan:
                task["status"] = "pending"
            
            state.plan = plan
            state.status = "execution"
            self._save_state(state)
            logger.info("📋 Plan Generated", project_id=state.project_id, task_count=len(plan))
        except Exception as e:
            logger.error("❌ Failed to parse plan", error=str(e), raw=response)
            state.status = "error"
            state.logs.append({"type": "error", "message": f"Planning failed: {e}"})
            self._save_state(state)

    async def _execute_task(self, state: ProjectState, task: Dict[str, Any]) -> str:
        """Выполняет конкретную задачу из плана."""
        task["status"] = "in_progress"
        self._save_state(state)
        
        logger.info("🛠 Executing Task", project_id=state.project_id, task=task['title'])
        
        # Здесь мы можем использовать ReAct Executor для решения подзадачи
        from src.core.agent_executor import AgentExecutor
        executor = AgentExecutor(self.router, self.tools, self.memory)
        
        result = await executor.run(f"Выполни задачу: {task['title']}. Контекст: {task['description']}", int(state.project_id.split('_')[-1]))
        
        task["status"] = "completed"
        task["result"] = result
        state.logs.append({"task_id": task["id"], "result": result, "timestamp": datetime.now().isoformat()})
        self._save_state(state)
        
        return result

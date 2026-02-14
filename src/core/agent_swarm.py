"""
src/core/agent_swarm.py
~~~~~~~~~~~~~~~~~~~~~~~
Нативная реализация мульти-агентного взаимодействия (Swarm Intelligence) для Krab.
Обеспечивает параллельное и последовательное выполнение задач специализированными командами.
"""

import asyncio
from typing import List, Dict, Any, Optional
import structlog

logger = structlog.get_logger(__name__)

class SwarmAgent:
    """Представление отдельного агента в рое."""
    def __init__(self, name: str, role: str, goal: str, instructions: str):
        self.name = name
        self.role = role
        self.goal = goal
        self.instructions = instructions

class SwarmManager:
    """
    Нативный оркестратор для Agent Swarm.
    Использует ModelRouter для вызова LLM.
    """
    
    def __init__(self, model_router=None):
        self.router = model_router
        logger.info("Native SwarmManager initialized")

    async def execute_task(self, 
                           task_description: str, 
                           agents: List[SwarmAgent],
                           context: Optional[List[Dict[str, str]]] = None,
                           mode: str = "sequential") -> Dict[str, str]:
        """
        Выполняет задачу силами команды агентов.
        mode: 'sequential' (передача результата по цепочке) или 'parallel' (независимо).
        """
        logger.info(f"🚀 Starting Swarm Task: {task_description[:50]}...", mode=mode)
        
        results = {}
        
        if mode == "sequential":
            current_data = task_description
            for agent in agents:
                logger.info(f"🤖 Agent {agent.name} ({agent.role}) is working...")
                
                prompt = (
                    f"Твоя роль: {agent.role}\n"
                    f"Твоя цель: {agent.goal}\n"
                    f"Инструкции: {agent.instructions}\n\n"
                    f"Данные для обработки:\n{current_data}"
                )
                
                response = await self.router.route_query(
                    prompt=prompt,
                    task_type="chat",
                    context=context
                )
                
                results[agent.name] = response
                # Обновляем данные для следующего агента
                current_data = f"Результат от {agent.name}:\n{response}\n\nИсходная задача:\n{task_description}"
        
        else: # parallel
            async def _run_agent(agent):
                prompt = (
                    f"Твоя роль: {agent.role}\n"
                    f"Твоя цель: {agent.goal}\n"
                    f"Инструкции: {agent.instructions}\n\n"
                    f"Задача:\n{task_description}"
                )
                response = await self.router.route_query(prompt=prompt, task_type="chat", context=context)
                return agent.name, response

            tasks = [_run_agent(a) for a in agents]
            completed = await asyncio.gather(*tasks)
            results = dict(completed)
            
        return results

    # --- Команды (Teams) ---

    def get_osint_team(self) -> List[SwarmAgent]:
        """Команда глубокого поиска (OSINT)."""
        return [
            SwarmAgent("Planner", "Intelligence Planner", "Разбить задачу на векторы поиска.", 
                       "Составь список неочевидных поисковых запросов и ресурсов (PDF, архивы, форумы)."),
            SwarmAgent("Researcher", "Deep Web Researcher", "Найти факты по плану.", 
                       "Собери ключевые факты и ссылки по предложенному плану."),
            SwarmAgent("Analyst", "Intelligence Analyst", "Собрать финальный отчет.", 
                       "Сведи факты в единую картину. Выдели Executive Summary и главные инсайты.")
        ]

    def get_trading_team(self) -> List[SwarmAgent]:
        """Команда торговых экспертов (Manus-style)."""
        return [
            SwarmAgent("Analyst", "Senior Data Analyst", "Собрать и структурировать объективные данные о текущем состоянии рынка (технический анализ и сентимент).", 
                       "Ты хладнокровный аналитик. Твоя задача — без эмоций собирать факты. Анализируй тренды, RSI, MACD. Не давай советов, только чистые цифры."),
            SwarmAgent("Strategist", "Quant Trading Strategist", "Сгенерировать торговую гипотезу (Long/Short/Hold) на основе данных.", 
                       "Ты гениальный стратег хедж-фонда. Находишь неэффективности. Формат ответа: Направление, Точка входа, Обоснование."),
            SwarmAgent("RiskManager", "Strict Risk Manager", "Оценить риски. Отклонить сделку или рассчитать Stop-Loss/Take-Profit.", 
                       "Ты параноидальный риск-менеджер. Макс риск 2% на сделку, R/R 1:3. Если рынок непонятен — ВЕТО (HOLD). Если одобряешь: Entry, SL, TP, Size."),
            SwarmAgent("Executor", "Paper Trading Executor", "Зафиксировать сделку в формате JSON.", 
                       "Сформируй финальный JSON объект сделки или причину отказа. Только JSON.")
        ]

    def get_content_team(self) -> List[SwarmAgent]:
        """Завод по производству контента."""
        return [
            SwarmAgent("SEO", "SEO & Trend Analyst", "Поиск ключевых слов и болей.", 
                       "Составь семантическое ядро и структуру заголовков для темы."),
            SwarmAgent("Copywriter", "Creative Copywriter", "Написание вовлекающего текста.", 
                       "Напиши текст без воды, используя сторителлинг, на основе SEO-плана."),
            SwarmAgent("Editor", "Chief Editor", "Финальная полировка.", 
                       "Проверь факты и стиль. Отформатируй в идеальный Markdown.")
        ]

    def get_dev_team(self) -> List[SwarmAgent]:
        """Команда разработки (Dev Squad)."""
        return [
            SwarmAgent("Architect", "Senior System Architect", "Проектирование архитектуры.", 
                       "Преврати идею в технический план (стек, БД, API, микросервисы)."),
            SwarmAgent("Coder", "Lead Fullstack Developer", "Написание чистого кода.", 
                       "Реализуй основную логику по спецификации архитектора (Python/JS)."),
            SwarmAgent("Critic", "Senior QA & Security Auditor", "Аудит и поиск багов.", 
                       "Найди уязвимости и дыры. Выдай разгромный отзыв или 'ОДОБРЕНО'.")
        ]

    def get_summary_team(self) -> List[SwarmAgent]:
        """Команда резюмирования документов и текстов."""
        return [
            SwarmAgent("Researcher", "Information Extractor", "Извлечь ключевые факты из текста.",
                       "Выдели все ключевые тезисы, факты и цифры. Структурируй по категориям."),
            SwarmAgent("Analyst", "Critical Analyst", "Оценить значимость и контекст.",
                       "Оцени каждый факт по важности. Убери шум. Добавь контекст и связи."),
            SwarmAgent("Editor", "Executive Summary Writer", "Создать финальное резюме.",
                       "Сведи всё в лаконичное Executive Summary (3-5 пунктов). Добавь вывод и рекомендации.")
        ]

    def get_email_team(self) -> List[SwarmAgent]:
        """Команда составления деловых писем и коммуникаций."""
        return [
            SwarmAgent("Copywriter", "Business Communication Expert", "Написать черновик письма.",
                       "Напиши профессиональное письмо по теме. Учти тон, аудиторию и цель. Без воды."),
            SwarmAgent("Stylist", "Tone & Style Advisor", "Подобрать идеальный тон.",
                       "Проверь тон письма. Если нужно — сделай формальнее/неформальнее. Убери канцеляризмы."),
            SwarmAgent("Proofreader", "Senior Proofreader", "Финальная корректура.",
                       "Исправь грамматику, пунктуацию, логику. Проверь что письмо достигает цели. Выдай финальную версию.")
        ]

    def get_audit_team(self) -> List[SwarmAgent]:
        """Команда security-аудита кода и систем."""
        return [
            SwarmAgent("Pentester", "Offensive Security Engineer", "Найти уязвимости.",
                       "Ищи SQL-инъекции, XSS, SSRF, утечки ключей, небезопасные зависимости. Думай как хакер."),
            SwarmAgent("Analyst", "Security Analyst", "Оценить риски и приоритеты.",
                       "Классифицируй найденные уязвимости по CVSS. Определи что критично, а что можно отложить."),
            SwarmAgent("Advisor", "Security Remediation Advisor", "Предложить исправления.",
                       "Для каждой уязвимости предложи конкретный fix с примером кода. Формат: Проблема → Решение → Код.")
        ]

    def get_planning_team(self) -> List[SwarmAgent]:
        """Команда бизнес-планирования и стратегии."""
        return [
            SwarmAgent("Strategist", "Business Strategist", "Разработать стратегию.",
                       "Проанализируй идею: рынок, конкуренты, целевая аудитория. Предложи бизнес-модель и MVP."),
            SwarmAgent("Financier", "Financial Analyst", "Оценить финансовую модель.",
                       "Рассчитай unit-экономику: CAC, LTV, break-even. Предложи бюджет на 3-6-12 месяцев."),
            SwarmAgent("Critic", "Devil's Advocate", "Разнести план в пух и прах.",
                       "Найди все слабости плана. Задай 5 самых неудобных вопросов. Если план выдержит — ОДОБРЕНО.")
        ]

    async def run_team(self, team_type: str, task: str) -> str:
        """Метод-фабрика для запуска конкретной команды."""
        teams = {
            "osint": self.get_osint_team(),
            "trading": self.get_trading_team(),
            "content": self.get_content_team(),
            "dev": self.get_dev_team(),
            "summary": self.get_summary_team(),
            "email": self.get_email_team(),
            "audit": self.get_audit_team(),
            "planning": self.get_planning_team(),
        }
        
        if team_type not in teams:
            return f"❌ Неизвестная команда: {team_type}"
            
        team = teams[team_type]
        results = await self.execute_task(task, team, mode="sequential")
        
        # Красивое форматирование вывода
        output = [f"### 🌊 Swarm Report: {team_type.upper()} TEAM"]
        for name, res in results.items():
            output.append(f"\n#### 🤖 {name}")
            output.append(res)
            
        return "\n".join(output)

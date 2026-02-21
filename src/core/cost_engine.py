# -*- coding: utf-8 -*-
"""
Cost Engine Module
Отвечает за расчет стоимости запросов, отслеживание бюджета и переключение режимов экономии.
"""

import logging
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger("CostEngine")

class CostEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.report_path = Path(config.get("MODEL_USAGE_REPORT_PATH", "artifacts/model_usage_report.json"))
        
        # Настройки бюджета
        try:
            self.monthly_budget_usd = float(config.get("CLOUD_MONTHLY_BUDGET_USD", 25.0))
        except (ValueError, TypeError):
            self.monthly_budget_usd = 25.0
            
        # Цены (дефолты)
        self.pricing = {
            "gemini-2.0-flash-lite": float(config.get("MODEL_COST_FLASH_LITE_USD", 0.0001)), # Очень дешево
            "gemini-2.0-flash": float(config.get("MODEL_COST_FLASH_USD", 0.0005)),
            "gemini-2.0-pro-exp": float(config.get("MODEL_COST_PRO_USD", 0.005)),
            "default": float(config.get("CLOUD_COST_PER_CALL_USD", 0.001))
        }

    def _get_usage_data(self) -> Dict[str, Any]:
        """Загрузка данных об использовании из общего отчета."""
        try:
            if self.report_path.exists():
                with self.report_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load usage report: {e}")
        return {"total_spent_usd": 0.0, "monthly_spent_usd": 0.0, "last_reset": ""}

    def get_budget_status(self) -> Dict[str, Any]:
        """
        Возвращает детальный статус бюджета и рекомендации.
        """
        usage = self._get_usage_data()
        spent = usage.get("monthly_spent_usd", 0.0)
        
        # Расчет процента выполнения месяца
        now = datetime.now()
        day_of_month = now.day
        days_in_month = 30 # Упрощенно
        month_progress = day_of_month / days_in_month
        
        budget_usage_ratio = spent / self.monthly_budget_usd if self.monthly_budget_usd > 0 else 1.0
        
        # Режим экономии: если тратим быстрее, чем идет месяц
        # Или если осталось меньше 20% бюджета
        is_economy_mode = (budget_usage_ratio > month_progress * 1.2) or (budget_usage_ratio > 0.8)
        
        return {
            "monthly_budget": self.monthly_budget_usd,
            "monthly_spent": round(spent, 4),
            "usage_percent": round(budget_usage_ratio * 100, 1),
            "is_economy_mode": is_economy_mode,
            "month_progress_percent": round(month_progress * 100, 1),
            "runway_days": round((self.monthly_budget_usd - spent) / (spent / day_of_month), 1) if spent > 0 else 30
        }

    def get_recommended_model(self, task_profile: str, original_model: str) -> str:
        """
        Корректирует выбор модели в зависимости от бюджета.
        """
        status = self.get_budget_status()
        if not status["is_economy_mode"]:
            return original_model
            
        # В режиме экономии:
        # Если задача не критичная (chat), форсируем Lite
        if task_profile in ["chat", "communication"]:
            logger.info(f"💰 Economy mode: Downgrading {original_model} -> gemini-2.0-flash-lite")
            return "gemini-2.0-flash-lite-preview-02-05"
            
        return original_model

    def record_call(self, model_id: str, tokens_in: int = 0, tokens_out: int = 0):
        """
        Записывает факт звонка и примерную стоимость.
        (Вызывается из ModelRouter после успешного ответа).
        """
        # В данной реализации мы просто инкрементируем счетчик в файле, 
        # который читает ModelRouter. Но для автономности CostEngine 
        # может сам обновлять свои локальные метрики.
        pass

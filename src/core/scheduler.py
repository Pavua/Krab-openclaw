# -*- coding: utf-8 -*-
"""
Krab Task Scheduler v2.0.
Управляет периодическими задачами: дайджесты, бекапы, очистка RAG, мониторинг.

Что нового в v2.0:
- RAG Cleanup: автоматическая очистка устаревших документов (еженедельно)
- System Health: мониторинг RAM/CPU/Диск с уведомлениями при критических значениях
- Улучшенный дайджест с информацией о системе

Связь: Запускается из main.py при старте бота.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import os
from datetime import datetime, timedelta
from src.core.memory_archiver import MemoryArchiver # Added this import based on the instruction

logger = logging.getLogger("Scheduler")


class KrabScheduler:
    def __init__(self, client, router, black_box, archiver=None):
        self.client = client
        self.router = router
        self.bb = black_box
        self.archiver = archiver
        self.scheduler = AsyncIOScheduler()
        self.owner_id = None  # Определим при старте

    async def _resolve_owner_id(self):
        """Ленивое определение owner_id по username."""
        if self.owner_id:
            return self.owner_id
        
        owner_username = os.getenv("OWNER_USERNAME", "").replace("@", "")
        if owner_username:
            try:
                user = await self.client.get_users(owner_username)
                self.owner_id = user.id
            except Exception as e:
                logger.error(f"Failed to get owner ID: {e}")
        
        return self.owner_id

    async def send_daily_digest(self):
        """Отправка ежедневного дайджеста владельцу."""
        owner_id = await self._resolve_owner_id()
        if not owner_id:
            return

        logger.info("Generating daily digest...")
        
        # Статистика за 24 часа
        stats = self.bb.get_stats()
        
        # Статистика RAG
        rag_stats = self.router.rag.get_stats()
        
        # Системный мониторинг
        system_info = ""
        try:
            from src.utils.system_monitor import SystemMonitor
            snapshot = SystemMonitor.get_snapshot()
            system_info = (
                f"RAM: {snapshot.ram_used_gb:.1f}/{snapshot.ram_total_gb:.1f}GB ({snapshot.ram_percent:.0f}%), "
                f"CPU: {snapshot.cpu_percent:.0f}%, "
                f"Disk: {snapshot.disk_percent:.0f}%"
            )
        except Exception:
            system_info = "Мониторинг недоступен"

        prompt = f"""
        Ты — Исполнительный Ассистент Краб. 
        Подготовь краткий утренний отчет для владельца.
        
        Статистика: {stats['total']} сообщений в Black Box.
        RAG: {rag_stats.get('count', 0)} документов, {rag_stats.get('expired', 0)} устаревших.
        Система: {system_info}
        
        Напиши бодрое приветствие, пожелай продуктивного дня и сообщи, что все системы работают штатно.
        Язык: РУССКИЙ. Стиль: Премиальный, лаконичный.
        """
        
        report = await self.router.route_query(prompt, task_type='chat')
        
        try:
            await self.client.send_message(owner_id, f"🌅 **Daily Report**\n\n{report}")
        except Exception as e:
            logger.error(f"Failed to send digest: {e}")

    async def backup_db(self):
        """Ежедневный бекап базы данных Black Box."""
        import shutil
        target_dir = "backups/db"
        os.makedirs(target_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        backup_path = f"{target_dir}/black_box_{timestamp}.db"
        
        try:
            shutil.copy2(self.bb.db_path, backup_path)
            logger.info(f"💾 Black Box Backup Created: {backup_path}")
            
            # Чистим старые бекапы (оставляем последние 7)
            backups = sorted(
                [f for f in os.listdir(target_dir) if f.startswith("black_box_")],
                reverse=True
            )
            for old_backup in backups[7:]:
                os.remove(os.path.join(target_dir, old_backup))
                logger.info(f"🗑️ Удалён старый бекап: {old_backup}")
                
        except Exception as e:
            logger.error(f"Failed to backup DB: {e}")

    async def cleanup_rag(self):
        """Еженедельная очистка устаревших документов из RAG."""
        try:
            removed = self.router.rag.cleanup_expired()
            logger.info(f"🧹 RAG Weekly Cleanup: удалено {removed} устаревших документов")
            
            # Уведомляем владельца если удалено много
            if removed > 10:
                owner_id = await self._resolve_owner_id()
                if owner_id:
                    await self.client.send_message(
                        owner_id,
                        f"🧹 **RAG Cleanup:** удалено {removed} устаревших документов из базы знаний."
                    )
        except Exception as e:
            logger.error(f"RAG cleanup error: {e}")

    async def run_archival(self):
        """Запуск архивации памяти (Infinite Memory)."""
        if self.archiver:
            await self.archiver.archive_old_chats()
            logger.info("📦 Scheduled Memory Archival Completed")

    async def system_health_check(self):
        try:
            from src.utils.system_monitor import SystemMonitor
            
            snapshot = SystemMonitor.get_snapshot()
            
            # Логируем
            logger.info(
                f"📊 Health Check: RAM {snapshot.ram_percent:.0f}%, "
                f"CPU {snapshot.cpu_percent:.0f}%, "
                f"Disk {snapshot.disk_percent:.0f}%"
            )
            
            # Уведомляем при критических значениях
            alerts = []
            if snapshot.is_ram_critical(85):
                alerts.append(f"⚠️ RAM: {snapshot.ram_percent:.0f}% (свободно {snapshot.ram_available_gb:.1f}GB)")
            if snapshot.is_disk_critical(90):
                alerts.append(f"⚠️ Диск: {snapshot.disk_percent:.0f}% (свободно {snapshot.disk_free_gb:.0f}GB)")
            
            if alerts:
                owner_id = await self._resolve_owner_id()
                if owner_id:
                    alert_text = "**🚨 Krab System Alert:**\n\n" + "\n".join(alerts)
                    await self.client.send_message(owner_id, alert_text)
                    
        except ImportError:
            pass  # psutil не установлен — пропускаем
        except Exception as e:
            logger.warning(f"Health check error: {e}")

    def start(self):
        """Запуск всех периодических задач."""
        # Ежедневный отчет в 09:00
        self.scheduler.add_job(
            self.send_daily_digest, 
            CronTrigger(hour=9, minute=0),
            id='daily_digest'
        )
        
        # Бекап базы в 03:00 ночи
        self.scheduler.add_job(
            self.backup_db,
            CronTrigger(hour=3, minute=0),
            id='db_backup'
        )
        
        # RAG Cleanup — каждое воскресенье в 04:00
        self.scheduler.add_job(
            self.cleanup_rag,
            CronTrigger(day_of_week='sun', hour=4, minute=0),
            id='rag_cleanup'
        )
        
        # System Health Check — каждые 2 часа
        self.scheduler.add_job(
            self.system_health_check,
            'interval',
            hours=2,
            id='health_check'
        )
        
        # Heartbeat в логи — каждые 6 часов
        self.scheduler.add_job(
            lambda: logger.info("📢 Scheduler Heartbeat: Systems Nominal"),
            'interval',
            hours=6,
            id='heartbeat'
        )
        
        # Infinite Memory Archival — каждую ночь в 03:30
        self.scheduler.add_job(
            self.run_archival,
            CronTrigger(hour=3, minute=30),
            id='memory_archival'
        )
        
        self.scheduler.start()
        logger.info("✅ Krab Scheduler v2.0 Started (5 jobs)")

    def shutdown(self):
        self.scheduler.shutdown()

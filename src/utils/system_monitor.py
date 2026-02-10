# -*- coding: utf-8 -*-
"""
Системный Монитор (System Monitor).
Мониторинг ресурсов macOS: RAM, CPU, диск, GPU, температура.

Зачем: Отслеживание нагрузки на систему, чтобы бот не крашнул MacBook 
при загрузке тяжёлых моделей (Flux, Whisper, etc.).
Связь: Используется в model_manager.py для проверки RAM перед загрузкой
моделей, в scheduler.py для периодического мониторинга, в dashboard для отображения.
"""

import psutil
import platform
import subprocess
import logging
from dataclasses import dataclass, asdict
from typing import Optional

logger = logging.getLogger("SystemMonitor")


@dataclass
class SystemSnapshot:
    """Снапшот состояния системы в текущий момент."""
    # RAM
    ram_total_gb: float
    ram_used_gb: float
    ram_available_gb: float
    ram_percent: float
    
    # CPU
    cpu_percent: float
    cpu_count: int
    
    # Диск
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent: float
    
    # Система
    os_name: str
    os_version: str
    hostname: str
    uptime_hours: float
    
    # Опционально (macOS-specific)
    gpu_info: Optional[str] = None
    thermal_state: Optional[str] = None
    
    def to_dict(self):
        """Конверсия в словарь для JSON/логирования."""
        return asdict(self)
    
    def is_ram_critical(self, threshold_percent: float = 85.0) -> bool:
        """Проверяет, превышает ли использование RAM критический порог."""
        return self.ram_percent >= threshold_percent
    
    def is_disk_critical(self, threshold_percent: float = 90.0) -> bool:
        """Проверяет, заполнен ли диск до критической отметки."""
        return self.disk_percent >= threshold_percent
    
    def format_report(self) -> str:
        """Форматированный отчёт для Telegram (Markdown)."""
        # Цветовые индикаторы
        ram_icon = "🔴" if self.ram_percent > 85 else ("🟡" if self.ram_percent > 65 else "🟢")
        cpu_icon = "🔴" if self.cpu_percent > 85 else ("🟡" if self.cpu_percent > 50 else "🟢")
        disk_icon = "🔴" if self.disk_percent > 90 else ("🟡" if self.disk_percent > 70 else "🟢")
        
        report = (
            f"**🖥️ Системный Монитор ({self.hostname})**\n\n"
            f"{ram_icon} **RAM:** {self.ram_used_gb:.1f} / {self.ram_total_gb:.1f} GB "
            f"({self.ram_percent:.0f}%) | Свободно: {self.ram_available_gb:.1f} GB\n"
            f"{cpu_icon} **CPU:** {self.cpu_percent:.0f}% ({self.cpu_count} ядер)\n"
            f"{disk_icon} **Диск:** {self.disk_used_gb:.0f} / {self.disk_total_gb:.0f} GB "
            f"({self.disk_percent:.0f}%) | Свободно: {self.disk_free_gb:.0f} GB\n"
            f"📱 **ОС:** {self.os_name} {self.os_version}\n"
            f"⏰ **Uptime:** {self.uptime_hours:.1f}ч"
        )
        
        if self.gpu_info:
            report += f"\n🎮 **GPU:** {self.gpu_info}"
        if self.thermal_state:
            report += f"\n🌡️ **Термальное состояние:** {self.thermal_state}"
            
        return report


class SystemMonitor:
    """Класс для мониторинга ресурсов системы."""
    
    @staticmethod
    def get_snapshot() -> SystemSnapshot:
        """Собирает полный снапшот системы."""
        # RAM
        ram = psutil.virtual_memory()
        
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.5)
        
        # Диск (корневой раздел)
        disk = psutil.disk_usage('/')
        
        # Uptime
        import time
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_hours = uptime_seconds / 3600
        
        # GPU info (macOS-specific через system_profiler)
        gpu_info = None
        thermal_state = None
        
        if platform.system() == "Darwin":
            gpu_info = SystemMonitor._get_macos_gpu()
            thermal_state = SystemMonitor._get_macos_thermal()
        
        return SystemSnapshot(
            ram_total_gb=ram.total / (1024**3),
            ram_used_gb=ram.used / (1024**3),
            ram_available_gb=ram.available / (1024**3),
            ram_percent=ram.percent,
            cpu_percent=cpu_percent,
            cpu_count=psutil.cpu_count(),
            disk_total_gb=disk.total / (1024**3),
            disk_used_gb=disk.used / (1024**3),
            disk_free_gb=disk.free / (1024**3),
            disk_percent=disk.percent,
            os_name=platform.system(),
            os_version=platform.mac_ver()[0] if platform.system() == "Darwin" else platform.version(),
            hostname=platform.node(),
            uptime_hours=uptime_hours,
            gpu_info=gpu_info,
            thermal_state=thermal_state
        )
    
    @staticmethod
    def _get_macos_gpu() -> Optional[str]:
        """Получает информацию о GPU на macOS через system_profiler."""
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
                capture_output=True, text=True, timeout=5
            )
            # Парсим вывод — ищем Chipset Model
            for line in result.stdout.split("\n"):
                if "Chipset Model" in line:
                    return line.split(":")[1].strip()
        except Exception as e:
            logger.debug(f"Не удалось получить GPU info: {e}")
        return None
    
    @staticmethod
    def _get_macos_thermal() -> Optional[str]:
        """Получает термальное состояние macOS через pmset."""
        try:
            result = subprocess.run(
                ["pmset", "-g", "therm"],
                capture_output=True, text=True, timeout=5
            )
            # Парсим строку "CPU_Scheduler_Limit = 100"
            for line in result.stdout.split("\n"):
                if "CPU_Speed_Limit" in line:
                    limit = line.split("=")[1].strip()
                    if int(limit) == 100:
                        return "✅ Норма (без тротлинга)"
                    else:
                        return f"⚠️ Тротлинг: CPU на {limit}%"
        except Exception as e:
            logger.debug(f"Не удалось получить thermal info: {e}")
        return None
    
    @staticmethod
    def can_load_heavy_model(min_free_gb: float = 4.0) -> bool:
        """
        Проверяет, достаточно ли свободной RAM для загрузки тяжёлой модели.
        Используется перед загрузкой Flux, Whisper Large, etc.
        
        Порог: min_free_gb (по умолчанию 4 GB).
        """
        try:
            ram = psutil.virtual_memory()
            available_gb = ram.available / (1024**3)
            can_load = available_gb >= min_free_gb
            
            if not can_load:
                logger.warning(
                    f"⚠️ Недостаточно RAM для тяжёлой модели: "
                    f"{available_gb:.1f}GB свободно, нужно {min_free_gb:.1f}GB"
                )
            
            return can_load
        except Exception:
            return True  # В случае ошибки — разрешаем (лучше попытаться)
    
    @staticmethod
    def get_process_info() -> dict:
        """Информация о текущем процессе бота."""
        proc = psutil.Process()
        return {
            "pid": proc.pid,
            "ram_mb": proc.memory_info().rss / (1024**2),
            "cpu_percent": proc.cpu_percent(),
            "threads": proc.num_threads(),
            "open_files": len(proc.open_files()),
        }

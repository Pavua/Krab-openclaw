# -*- coding: utf-8 -*-
"""
Supervisor для Krab v2.5.
Скрипт мониторинга, который перезапускает бота при падении.
Создает исполняемый .command файл для macOS.
"""

import os
import sys
import time
import subprocess
import signal

# Путь к основному файлу бота
BOT_SCRIPT = "src/main.py"
VENV_PYTHON = os.path.join(".venv", "bin", "python3")

# Если нет venv, используем системный
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = "python3"

def run_supervisor():
    """Запуск бота в цикле с авто-перезапуском."""
    print(f"🔄 Starting Krab Supervisor (Python: {VENV_PYTHON})...")
    
    while True:
        try:
            print("\n🚀 Starting Krab Unit...")
            # Запускаем процесс
            process = subprocess.Popen([VENV_PYTHON, "-m", "src.main"])
            
            # Ждем завершения
            process.wait()
            
            if process.returncode == 0:
                print("✅ Krab stopped normally. Supervisor exiting.")
                break
            else:
                print(f"⚠️ Krab crashed (exit code: {process.returncode}). Restarting in 5s...")
                time.sleep(5)
        
        except KeyboardInterrupt:
            print("\n🛑 Supervisor interrupted by user. Stopping...")
            # Пытаемся корректно убить дочерний процесс
            if 'process' in locals():
                process.terminate()
            break
        except Exception as e:
            print(f"💥 Supervisor Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_supervisor()

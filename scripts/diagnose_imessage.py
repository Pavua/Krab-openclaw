#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диагностика iMessage RPC для Krab/OpenClaw.
Проверяет доступ к chat.db и возможность запуска imsg.
"""
import os
import subprocess
import sys

DB_PATH = "/Users/pablito/Library/Messages/chat.db"
CLI_PATH = "/opt/homebrew/bin/imsg"

def check():
    print(f"--- Диагностика iMessage ---")
    
    # 1. Проверка файла базы
    if os.path.exists(DB_PATH):
        print(f"✅ Файл базы найден: {DB_PATH}")
        try:
            with open(DB_PATH, 'rb') as f:
                f.read(100)
            print("✅ Доступ на чтение БД: OK")
        except Exception as e:
            print(f"❌ Ошибка доступа к БД: {e}")
            print("💡 Убедитесь, что Terminal/Python имеет 'Full Disk Access' в системных настройках macOS.")
    else:
        print(f"❌ Файл базы НЕ найден: {DB_PATH}")

    # 2. Проверка CLI
    if os.path.exists(CLI_PATH):
        print(f"✅ CLI найден: {CLI_PATH}")
        try:
            res = subprocess.run([CLI_PATH, "--version"], capture_output=True, text=True)
            print(f"✅ CLI версия: {res.stdout.strip()}")
        except Exception as e:
            print(f"❌ Ошибка запуска CLI: {e}")
    else:
        print(f"❌ CLI НЕ найден: {CLI_PATH}")

    # 3. Попытка запустить rpc вручную (кратко)
    print("\n--- Проверка RPC (нажмите Ctrl+C через 2 секунды, если застрянет) ---")
    try:
        # Запускаем imsg в режиме rpc на короткое время
        proc = subprocess.Popen([CLI_PATH, "rpc"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            outs, errs = proc.communicate(timeout=2)
            print(f"RPC вывод: {outs.decode()}")
            print(f"RPC ошибка: {errs.decode()}")
        except subprocess.TimeoutExpired:
            proc.kill()
            print("✅ RPC запустился и ожидает (Timeout Expired - это нормально для теста)")
    except Exception as e:
        print(f"❌ Критическая ошибка RPC: {e}")

if __name__ == "__main__":
    check()

import os
import json
from pathlib import Path

# Конфигурация
TARGET_PHRASES = [
    "Я готов к вашему следующему запросу",
    "Я готов к вашему следующему запросу. 🦀",
    "Я готов к вашему следующему запросу.🦀"
]
MEMORY_DIR = Path("artifacts/memory")

def cleanup_history():
    print(f"🚀 Начинаю очистку истории от мусорных фраз в {MEMORY_DIR}...")
    
    deleted_count = 0
    files_processed = 0
    
    if not MEMORY_DIR.exists():
        print("❌ Директория памяти не найдена.")
        return

    # Рекурсивный обход всех history.jsonl
    for history_file in MEMORY_DIR.glob("**/history.jsonl"):
        files_processed += 1
        temp_file = history_file.with_suffix(".tmp")
        
        needed_lines = []
        file_deleted_count = 0
        
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        text = data.get("text", "")
                        
                        # Если фраза есть в тексте - пропускаем эту строку
                        if any(phrase in text for phrase in TARGET_PHRASES):
                            file_deleted_count += 1
                            continue
                        
                        needed_lines.append(line)
                    except json.JSONDecodeError:
                        needed_lines.append(line)
            
            if file_deleted_count > 0:
                with open(history_file, "w", encoding="utf-8") as f:
                    f.writelines(needed_lines)
                print(f"✅ {history_file.relative_to(MEMORY_DIR)}: Удалено {file_deleted_count} строк.")
                deleted_count += file_deleted_count
            
        except Exception as e:
            print(f"⚠️ Ошибка при обработке {history_file}: {e}")

    print(f"\n✨ Итог: Обработано файлов: {files_processed}. Всего удалено 'залипших' ответов: {deleted_count}.")

if __name__ == "__main__":
    cleanup_history()

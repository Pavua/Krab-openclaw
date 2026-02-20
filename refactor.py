import os
import re

file_path = "src/handlers/tools.py"
with open(file_path, "r", encoding="utf-8") as f:
    text = f.read()

replacement1 = r"""if not voice_gateway:
            await message.reply_text(
                "❌ **Ошибка:** Voice Gateway недоступен.\n\n"
                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."
            )
            return"""
text = text.replace(
    'if not voice_gateway:\n            await message.reply_text("❌ Voice Gateway client не инициализирован.")\n            return',
    replacement1
)

replacement2 = r"""session_id = active_call_sessions.get(message.chat.id)
        if not session_id:
            await message.reply_text(
                "⚠️ **Ошибка:** Нет активной voice-сессии.\n\n"
                "💡 **Подсказка:** Используйте `!callstart` для начала новой сессии."
            )
            return"""
text = re.sub(
    r'session_id = active_call_sessions\.get\(message\.chat\.id\)\n\s+if not session_id:\n\s+await message\.reply_text\("(?:⚠️ Нет активной сессии\. Сначала `!callstart`\.|⚠️ Нет активной сессии\. Используй `!callstart`\.|ℹ️ Активной сессии нет\.)"\)\n\s+return',
    replacement2,
    text
)

def repl3(m):
    err = m.group(1)
    return f"""if not result.get("ok"):
            await message.reply_text(
                f"❌ **Ошибка:** {err}\\n"
                f"🛡️ Описание: `{{result.get('error', 'unknown')}}`\\n\\n"
                "💡 **Подсказка:** Проверьте соединение с Voice Gateway (`!calldiag`) или логи сервиса."
            )
            return"""

text = re.sub(
    r"if not result\.get\(\"ok\"\):\n\s+await message\.reply_text\(f\"❌ ([^\"]+): \{result\.get\('error', 'unknown'\)\}\"\)\n\s+return",
    repl3,
    text
)

repl4 = r"""if not result.get("ok"):
            await notification.edit_text(
                f"❌ **Ошибка:** Не удалось запустить сессию.\n"
                f"🛡️ Детали: `{result.get('error', 'unknown')}`\n\n"
                "💡 **Подсказка:** Проверьте логи Voice Gateway. Сервис может быть offline."
            )
            return"""

text = re.sub(
    r"if not result\.get\(\"ok\"\):\n\s+await notification\.edit_text\(f\"❌ Не удалось запустить сессию: \{result\.get\('error', 'unknown'\)\}\"\)\n\s+return",
    repl4,
    text
)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(text)

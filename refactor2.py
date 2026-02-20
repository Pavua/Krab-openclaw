import re

file_path = "src/handlers/tools.py"
with open(file_path, "r", encoding="utf-8") as f:
    text = f.read()

# 1. replace voice_gateway check
gateway_orig = 'if not voice_gateway:\n            await message.reply_text("❌ Voice Gateway client не инициализирован.")\n            return'
gateway_new = 'if not voice_gateway:\n            await message.reply_text(\n                "❌ **Ошибка:** Voice Gateway недоступен.\\n\\n"\n                "💡 **Подсказка:** Убедитесь, что сервис voice-gateway запущен."\n            )\n            return'
text = text.replace(gateway_orig, gateway_new)

# 2. replace active_session check
for pat in [
    'await message.reply_text("⚠️ Нет активной сессии. Сначала `!callstart`.")',
    'await message.reply_text("⚠️ Нет активной сессии. Используй `!callstart`.")',
    'await message.reply_text("ℹ️ Активной сессии нет.")'
]:
    new_pat = 'await message.reply_text(\n                "⚠️ **Ошибка:** Нет активной voice-сессии.\\n\\n"\n                "💡 **Подсказка:** Используйте `!callstart` для начала новой сессии."\n            )'
    text = text.replace(pat, new_pat)

# 3. replace generic errors
# We can't easily use .replace() for all generic errors because they vary.
# We will use re.sub but with a literal string return, so escaped bytes remain escaped.

def repl_generic_err(match):
    err_msg = match.group(1)
    return (
        'if not result.get("ok"):\n'
        '            await message.reply_text(\n'
        f'                "❌ **Ошибка:** {err_msg}\\n"\n'
        '                f"🛡️ Детали: `{result.get(\'error\', \'unknown\')}`\\n\\n"\n'
        '                "💡 **Подсказка:** Проверьте логи сервиса Voice Gateway."\n'
        '            )\n'
        '            return'
    )

text = re.sub(
    r'if not result\.get\("ok"\):\n\s+await message\.reply_text\(f"❌ ([^"]+): \{result\.get\(\'error\', \'unknown\'\)\}"\)\n\s+return',
    repl_generic_err,
    text
)

# 4. replace callstart specific err
callstart_err = 'if not result.get("ok"):\n            await notification.edit_text(f"❌ Не удалось запустить сессию: {result.get(\'error\', \'unknown\')}")\n            return'
callstart_new = 'if not result.get("ok"):\n            await notification.edit_text(\n                "❌ **Ошибка:** Не удалось запустить сессию.\\n"\n                f"🛡️ Детали: `{result.get(\'error\', \'unknown\')}`\\n\\n"\n                "💡 **Подсказка:** Проверьте логи Voice Gateway. Сервис может быть offline."\n            )\n            return'
text = text.replace(callstart_err, callstart_new)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(text)

# E2E MCP Smoke Test Results

**Run:** 2026-04-24 07:00:00  
**Total:** 6/8 passed  
**Elapsed:** 143.2s  
**Transport:** MCP SSE `http://127.0.0.1:8011/sse`  
**Krab status:** `up` / userbot=`running`

| Test | Status | Elapsed | Chat | Snippet | Reason |
|------|--------|---------|------|---------|--------|
| `version_cmd` | FAIL | 41.9s | `312322764` | — | timeout (40.0s) — ответа нет |
| `uptime_cmd` | PASS | 6.0s | `312322764` | ⏱️ Uptime ───────────── macOS: 2д 10ч 13м Краб: 1ч 1м OpenCl… | — |
| `proactivity_status` | PASS | 2.9s | `312322764` | ⚡ Proactivity Level: 2 (attentive) 🤖 Autonomy mode: normal 📊… | — |
| `silence_status` | FAIL | 41.6s | `312322764` | — | timeout (40.0s) — ответа нет |
| `model_cmd` | PASS | 3.1s | `312322764` | 🧭 Маршрутизация моделей - Режим: ☁️ cloud (принудительно) Ак… | — |
| `dialog_no_gospodin` | PASS | 10.2s | `312322764` | ━━━━━━━━━━━━━ 💰 $0.0027 · 35.7k↓ / 0.1k↑ · gpt-5.4 · 6.6s | — |
| `phantom_action_guard` | PASS | 18.6s | `312322764` | ━━━━━━━━━━━━━ 💰 $0.0077 · 101.7k↓ / 0.2k↑ · gpt-5.4 · 11.4s | — |
| `how2ai_blocklist_silence` | PASS | 2.9s | `-1001587432709` | 👤 Yung Nagato (@yung_nagato)  🏅 Ранг: Ветеран ❤️ Репутация: … | — |

## Details

### `version_cmd` — FAIL
- **Описание:** !version возвращает версию
- **Chat:** `312322764`
- **Отправлено:** `!version`
- **Expect no reply:** False
- **Ответ:**
```
(пусто)
```
- **Failure:** timeout (40.0s) — ответа нет

### `uptime_cmd` — PASS
- **Описание:** !uptime показывает аптайм
- **Chat:** `312322764`
- **Отправлено:** `!uptime`
- **Expect no reply:** False
- **Ответ:**
```
⏱️ Uptime
─────────────
macOS: 2д 10ч 13м
Краб: 1ч 1м
OpenClaw: ✅ Online
LM Studio: ⚠️ Status 401
Archive: 506.2 MB (last write 2м ago)
```

### `proactivity_status` — PASS
- **Описание:** !proactivity показывает текущий уровень
- **Chat:** `312322764`
- **Отправлено:** `!proactivity`
- **Expect no reply:** False
- **Ответ:**
```
⚡ Proactivity Level: 2 (attentive)
🤖 Autonomy mode: normal
📊 Trigger threshold: 0.7
💬 Reactions mode: contextual
💡 Unsolicited thoughts: off

Уровни: silent reactive attentive engaged proactive
```

### `silence_status` — FAIL
- **Описание:** !silence status отдаёт состояние
- **Chat:** `312322764`
- **Отправлено:** `!silence status`
- **Expect no reply:** False
- **Ответ:**
```
(пусто)
```
- **Failure:** timeout (40.0s) — ответа нет

### `model_cmd` — PASS
- **Описание:** !model показывает текущую модель
- **Chat:** `312322764`
- **Отправлено:** `!model`
- **Expect no reply:** False
- **Ответ:**
```
🧭 Маршрутизация моделей
-
Режим: ☁️ cloud (принудительно)
Активная модель: нет
Облачная модель: google/gemini-3-pro-preview
LM Studio URL: http://192.168.0.171:1234
FORCE_CLOUD: True

_Подкоманды: info, local, cloud, auto, set , load , unload, scan_
```

### `dialog_no_gospodin` — PASS
- **Описание:** W31 regression: нет дефолтного «Мой Господин»
- **Chat:** `312322764`
- **Отправлено:** `привет, как ты сегодня?`
- **Expect no reply:** False
- **Ответ:**
```
━━━━━━━━━━━━━
💰 $0.0027 · 35.7k↓ / 0.1k↑ · gpt-5.4 · 6.6s
```

### `phantom_action_guard` — PASS
- **Описание:** Phantom-action guard — Краб не врёт что отправил
- **Chat:** `312322764`
- **Отправлено:** `передай Чадо привет от меня`
- **Expect no reply:** False
- **Ответ:**
```
━━━━━━━━━━━━━
💰 $0.0077 · 101.7k↓ / 0.2k↑ · gpt-5.4 · 11.4s
```

### `how2ai_blocklist_silence` — PASS
- **Описание:** W26.1: в чате How2AI Краб не отвечает (blocklist)
- **Chat:** `-1001587432709`
- **Отправлено:** `!status`
- **Expect no reply:** True
- **Ответ:**
```
👤 Yung Nagato (@yung_nagato)

🏅 Ранг: Ветеран
❤️ Репутация: 104
👑 Максимальный ранг достигнут

🔓 Разрешено:
- текст
- изображения
- видео
- аудио
- голосовые сообщения
- видеосообщения
- документы
- опросы
- стикеры/GIF
- ссылки
```

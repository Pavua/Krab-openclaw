# OPEN ISSUES CHECKLIST

## Статус на 2026-03-07

### Закрыто по acceptance
1. E1→E3 acceptance:
- Базовый acceptance зелёный.
- Restart-resilience прогнан на 2 полных цикла stop/start без деградации.

2. Каналы / фото / Chrome relay:
- Каналы проходят smoke с success rate 100%.
- Фото/Chrome acceptance зелёный.
- BlueBubbles подключён и после рестартов определяется как `works`.

3. Cloud truth / cloud smoke:
- Строгий cloud swarm smoke зелёный.
- Неивазивная cloud-проверка больше не дёргает локальную модель без явного deep probe.

4. Chrome DevTools MCP:
- Конфиг Codex подготовлен для подключения к реальному Chrome-профилю через `--autoConnect`.
- Вспомогательный launcher создан: `new Enable Chrome Remote Debugging.command`.
- Для активации в самой среде Codex нужен перезапуск приложения или новое окно/чат после перечитывания MCP-конфига.

### Новый активный этап
1. Long-context local hardening:
- Снизить деградацию на длинных диалогах (`TruncateMiddle`, cache clear, timeout, empty stream).

2. Channel runtime hardening:
- Добить исторически плавающие websocket/pathology-кейсы для Discord/Slack, даже если текущий smoke зелёный.

3. Runtime truth / observability polish:
- Довести UI/runtime badges и диагностику до состояния, где маршрут, auth и реальный recovery видны без двусмысленности.

4. Восстановление полезных функций после рефакторинга:
- Рой агентов и другие реально рабочие куски восстанавливать точечно, только после проверки полезности и совместимости с текущим runtime.

## Каналы и ожидаемое поведение
1. Telegram Userbot:
- Ожидание: стабильный старт без постоянного relogin.
- Проблемы: `auth key not found`, `disk I/O error` при stop.

2. Telegram Bot:
- Ожидание: автозагрузка локальной модели при `No models loaded`.
- Проблемы: периодические `No models loaded`, иногда пустые/ошибочные ответы.

3. iMessage:
- Ожидание: такой же recovery-путь, как у Telegram Bot.
- Проблемы: `No models loaded`, нерегулярные ответы.

4. OpenClaw dashboard chat:
- Ожидание: детерминированный ответ или явная ошибка.
- Проблемы: ложный preflight OK при последующем auth fail/empty flow.

5. Фото/медиа:
- Ожидание: ответ или явная ошибка в разумный timeout.
- Проблемы: зависание на `👀 Разглядываю фото...`.

6. Голосовые в группах:
- Ожидание: корректный триггер по имени/упоминанию и обработка voice.
- Проблемы: частые пропуски отклика, нестабильный voice path.

7. Chrome Relay:
- Ожидание: ясный статус attached/not attached.
- Проблемы: «желтое» зависшее состояние без ясной причины.

8. Krab Ear:
- Ожидание: watchdog поднимает backend без ручных кликов.
- Проблемы: периодические падения backend/agent при памяти под давлением.

## Техдолг по стабильности
1. Telegram lifecycle hardening:
- Safe wrapper вокруг `client.stop()` и save-session ошибок.
- Non-fatal поведение на sqlite I/O в shutdown.

2. Единый recovery-контракт по каналам:
- Userbot/Bot/iMessage/dashboard должны использовать одинаковый local autoload guard.

3. Empty stream handling:
- `EMPTY MESSAGE` и `model crashed` должны классифицироваться и обрабатываться fallback’ом.

4. Cloud auth truth:
- `configured but unauthorized` вместо ложного «все ок».

5. UI диагностика:
- `health/lite` и runtime badges должны отражать фактический маршрут и auth-состояние.

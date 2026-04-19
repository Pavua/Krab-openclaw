# Krab Telegram Commands Cheatsheet

Generated: 2026-04-18 21:33 · Total: 147 commands

## Categories

| Category | Count |
|----------|-------|
| ai | 17 |
| basic | 21 |
| costs | 3 |
| dev | 15 |
| files | 1 |
| management | 22 |
| models | 3 |
| modes | 9 |
| notes | 13 |
| scheduler | 9 |
| swarm | 1 |
| system | 21 |
| translator | 1 |
| users | 11 |

## Ai

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!ask` | `!ask [вопрос]` | Спросить AI о сообщении (reply → AI отвечает) | — |
| `!catchup` | `!catchup` | Кратко о пропущенном с момента последнего визита | — |
| `!define` | `!define <слово>` | Определение слова из словаря (через AI) | — |
| `!explain` | `!explain <код>  или reply на сообщение с кодом` | Объяснение кода простым языком через AI | — |
| `!img` | `!img [вопрос]` | Описание фото через AI vision (reply на фото) | ✓ |
| `!news` | `!news [тема|ru|en]` | Топ-5 новостей через AI — тема или язык (ru/en) | — |
| `!ocr` | `!ocr [подсказка]` | Извлечение текста из изображения через AI vision (reply на фото) | ✓ |
| `!poll` | `!poll <вопрос> | <вариант1> | <вариант2> ...` | Создать опрос в чате | — |
| `!quiz` | `!quiz <тема>` | AI-генерированная викторина по теме | ✓ |
| `!rate` | `!rate <тикер> [тикер2 ...]` | Курс криптовалюты или акции через AI (цена, 24h%, капитализация) | — |
| `!report` | `!report <тема>` | Расширенный исследовательский отчёт | — |
| `!search` | `!search <запрос>` | Веб-поиск Brave | — |
| `!summary` | `!summary [N]` | Суммаризация последних N сообщений | — |
| `!translate` | `!translate [язык]` | Перевод текста (reply или аргумент) | — |
| `!tts` | `!tts <текст>  или reply на сообщение` | Text-to-speech: преобразовать текст в голосовое сообщение | ✓ |
| `!urban` | `!urban <слово>` | Определение слова из Urban Dictionary через AI + web_search | — |
| `!weather` | `!weather [город]` | Текущая погода через web_search | — |

## Basic

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!b64` | `!b64 encode|decode <текст>` | Base64 кодирование/декодирование | — |
| `!calc` | `!calc <выражение>` | Калькулятор: математические выражения (поддерживает функции) | — |
| `!clear` | `!clear` | Очистить историю диалога | — |
| `!context` | `!context [clear|save]` | Управление контекстом чата | — |
| `!convert` | `!convert <число> <из> <в>  (напр. !convert 100 km mi)` | Конвертер единиц: длина, масса, объём, скорость, температура | — |
| `!currency` | `!currency <сумма> <из> <в>  (напр. !currency 100 USD EUR)` | Конвертация валют через AI | — |
| `!diagnose` | `!diagnose` | Детальная диагностика подключений | — |
| `!dice` | `!dice [NdM]` | Бросок кубика(ов): стандартные нотации (2d6, d20) | — |
| `!dns` | `!dns <домен> [тип]` | DNS-запрос: A, AAAA, MX, TXT, CNAME записи | — |
| `!hash` | `!hash [алгоритм] <текст>  или reply на файл` | Хэш текста или файла (MD5, SHA1, SHA256, SHA512) | — |
| `!health` | `!health` | Диагностика всех подсистем | — |
| `!help` | `!help [команда]` | Справка по всем командам | — |
| `!ip` | `!ip <IP-адрес>` | Геолокация IP-адреса и ASN-информация | — |
| `!len` | `!len <текст>  или reply на сообщение` | Длина текста в символах, словах и байтах | — |
| `!panel` | `!panel` | Owner panel (:8080) | ✓ |
| `!ping` | `!ping <host> [порт]` | Ping хоста (ICMP или TCP) | — |
| `!qr` | `!qr <текст|URL>  или ответь на сообщение` | Генерация QR-кода из текста или URL | ✓ |
| `!rand` | `!rand [N] | !rand <a> <b> | !rand pick item1 item2 ...` | Случайное число, выбор из списка или перемешивание | — |
| `!stats` | `!stats [ecosystem|eco|health|basic]` | Статистика сессии или ecosystem health | — |
| `!status` | `!status` | Статус всех подсистем Краба | — |
| `!time` | `!time [город|timezone]` | Текущее время в разных часовых поясах | — |

## Costs

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!budget` | `!budget [сумма]` | Просмотр/установка дневного бюджета | ✓ |
| `!costs` | `!costs [detail]` | Отчёт расходов по провайдерам | ✓ |
| `!digest` | `!digest` | Weekly digest активности | ✓ |

## Dev

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!agent` | `!agent new <name> <prompt>|list|swarm [loop N] <тема>` | Управление агентами | ✓ |
| `!backup` | `!backup [list]` | Экспорт всех persistent данных Краба в ZIP-архив | ✓ |
| `!claude_cli` | `!claude_cli <задача>` | Claude Code CLI | ✓ |
| `!codex` | `!codex <задача>` | OpenAI Codex CLI | ✓ |
| `!config` | `!config|!set <KEY> <VAL>` | Просмотр/установка настроек | ✓ |
| `!debug` | `!debug [sessions|tasks|gc]` | Отладочная сводка: tasks, sessions, GC, last error (owner-only) | ✓ |
| `!eval` | `!eval <python код>` | Выполнить произвольный Python-код (owner-only) | ✓ |
| `!gemini` | `!gemini <задача>` | Gemini CLI | ✓ |
| `!grep` | `!grep <паттерн> [текст]  или reply на сообщение` | Поиск паттерна по тексту или reply (regex поддерживается) | ✓ |
| `!json` | `!json <json-строка>  или reply на сообщение` | Форматировать/валидировать JSON | — |
| `!opencode` | `!opencode <задача>` | OpenCode CLI | ✓ |
| `!restart` | `!restart` | Перезапуск бота | ✓ |
| `!run` | `!run <команда>` | Запустить shell-скрипт или команду (owner-only) | ✓ |
| `!shop` | `!shop <url>` | Mercadona Playwright scraper | ✓ |
| `!yt` | `!yt <url|id>` | Информация о YouTube-видео или плейлисте | — |

## Files

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!media` | `!media [save|info]` | Скачивание медиафайлов (фото/видео/документ/аудио). Reply на медиа. | ✓ |

## Management

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!archive` | `!archive [list]` | Архивировать текущий чат (list — список архива) | ✓ |
| `!autodel` | `!autodel <сек>` | Автоудаление через N секунд (0 = выключить) | ✓ |
| `!chatmute` | `!chatmute [мин]` | Замутить уведомления чата на N минут (0 = снять мут) | ✓ |
| `!collect` | `!collect [N]` | Собрать N сообщений чата в один текст | — |
| `!del` | `!del [N]` | Удалить N последних сообщений (default 1) | ✓ |
| `!diff` | `!diff <текст1> --- <текст2>  или reply + аргумент` | Diff двух текстов: unified-формат | — |
| `!fwd` | `!fwd <chat_id>` | Переслать сообщение (reply) | ✓ |
| `!link` | `!link [сообщение]  (reply на сообщение для permalink)` | Создать invite-link чата или получить ссылку на сообщение | ✓ |
| `!mark` | `!mark [tag]  (reply на сообщение)` | Пометить сообщение тегом для быстрого поиска | ✓ |
| `!pin` | `!pin` | Закрепить сообщение (reply) | ✓ |
| `!purge` | `!purge` | Очистить историю бота в чате | ✓ |
| `!react` | `!react <эмодзи>` | Поставить реакцию (reply) | — |
| `!regex` | `!regex <паттерн> <текст>  или reply на сообщение` | Проверить регулярное выражение против текста | — |
| `!say` | `!say <текст>              — отправить в текущий чат
!say <chat_id> <текст>   — отправить в другой чат` | Тихая отправка сообщения от имени юзербота (команда удаляется) | ✓ |
| `!schedule` | `!schedule [list|cancel|add]` | Отложенные сообщения | ✓ |
| `!sed` | `!sed s/старое/новое/  (reply на сообщение)` | Замена текста по паттерну s/старое/новое/ (reply на сообщение) | — |
| `!slowmode` | `!slowmode <сек>  (0 = выключить)` | Включить slow mode в группе (задержка в секундах) | ✓ |
| `!tag` | `!tag [all|admins|<user1> <user2>]` | Тегировать участников группы (упомянуть @всех или список) | ✓ |
| `!top` | `!top [N] | !top week | !top all` | Лидерборд активности чата по количеству сообщений | — |
| `!unarchive` | `!unarchive` | Разархивировать текущий чат | ✓ |
| `!unpin` | `!unpin` | Открепить сообщение (reply) | ✓ |
| `!welcome` | `!welcome <текст> | !welcome off | !welcome status` | Настройка приветственного сообщения для новых участников | ✓ |

## Models

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!model` | `!model [info|local|cloud|auto|set <id>|load <name>|unload|scan]` | Управление маршрутизацией модели | ✓ |
| `!reasoning` | `!reasoning [show|clear]` | Просмотр/очистка reasoning-trace | ✓ |
| `!role` | `!role [name|list]` | Смена системного ролевого промпта | ✓ |

## Modes

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!afk` | `!afk [сообщение] | !afk off` | Режим Away From Keyboard: авто-ответ при упоминании | ✓ |
| `!cap` | `!cap [name on|off|reset]` | Матрица capabilities чатов | ✓ |
| `!chatban` | `!chatban [chat_id]` | Заблокировать обработку чата | ✓ |
| `!listen` | `!listen [active|mention-only|muted|reset|list|stats]` | Режим ответов Краба в текущем чате | ✓ |
| `!notify` | `!notify on|off` | Tool narrations (🔍 Ищу... 📸 Скриншот...) | ✓ |
| `!spam` | `!spam block <@user|id> | !spam unblock <id> | !spam list` | Антиспам: блокировать/разблокировать пользователя за спам | ✓ |
| `!typing` | `!typing [сек]` | Имитировать typing action в чате | ✓ |
| `!voice` | `!voice on|off|toggle|block|unblock|speed <0.75..2.5>|voice <edge-tts-id>` | Управление голосовыми ответами и TTS | ✓ |
| `!тишина` | `!тишина [мин|стоп|глобально|расписание HH:MM-HH:MM|статус]` | Режим тишины (без AI-ответов) | ✓ |

## Notes

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!bookmark` | `!bookmark` | Закладка на сообщение | — |
| `!confirm` | `!confirm <hash>` | Подтвердить persistent-запись памяти (owner-only) | ✓ |
| `!export` | `!export [N]` | Экспорт N последних сообщений чата | — |
| `!memo` | `!memo <текст>` | Заметка в Obsidian (reply или аргумент) | — |
| `!memory` | `!memory recent [source_filter] | !memory stats` | Память: recent записи workspace / stats по Memory Layer | — |
| `!note` | `!note` | Голосовая заметка (reply на голосовое сообщение) | — |
| `!paste` | `!paste <текст>  или reply на сообщение` | Отправить длинный текст как файл-документ (>4096 символов) | — |
| `!quote` | `!quote [save|list|random|del <id>]` | Сохранить/показать цитату (reply на сообщение) | — |
| `!recall` | `!recall <запрос>` | Вспомнить факт из памяти | — |
| `!remember` | `!remember <текст>` | Запомнить факт в память | — |
| `!snippet` | `!snippet save <name> <lang> <code> | !snippet <name> | !snippet list` | Сниппеты кода: сохранить и отправить с подсветкой синтаксиса | ✓ |
| `!template` | `!template save <name> <text>  — сохранить
!template <name> [val1 val2]  — отправить (с подстановкой)
!template list                — список
!template del <name>          — удалить` | Шаблоны сообщений с подстановкой переменных | ✓ |
| `!todo` | `!todo [add <текст>|list|done <N>|clear]` | Список задач: добавить, показать, отметить выполненными | — |

## Scheduler

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!cron` | `!cron [list|enable|disable|run|status] [<name>] | !cron quick "<время>" "<промпт>"` | Управление OpenClaw cron jobs (list/enable/disable/run/status/quick) | ✓ |
| `!cronstatus` | `!cronstatus` | Статус cron scheduler | ✓ |
| `!monitor` | `!monitor [add|del|list|status]` | Мониторинг чатов | ✓ |
| `!remind` | `!remind <2h|17:30|tomorrow 9:00|when X then Y> <текст> / list / cancel <id>` | Поставить напоминание (time/event) или list/cancel | — |
| `!reminders` | `!reminders` | Список активных напоминаний | — |
| `!rm_remind` | `!rm_remind <id>` | Удалить напоминание по id | ✓ |
| `!stopwatch` | `!stopwatch start|stop|lap|reset` | Секундомер: start, stop, lap, reset | — |
| `!timer` | `!timer <время> [метка] | !timer list | !timer cancel [id]` | Таймер с уведомлением по истечении: list, cancel | — |
| `!watch` | `!watch status|now` | Proactive watch / owner-digest | ✓ |

## Swarm

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!swarm` | `!swarm <team> <задача>|research <тема>|summary|teams|schedule|memory|jobs|task|artifacts|listen|channels|setup` | Мультиагентный рой: research (+ self-reflection → follow-up tasks), summary, sch | ✓ |

## System

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!browser` | `!browser [cdp|status]` | CDP browser bridge | ✓ |
| `!chatinfo` | `!chatinfo [chat_id|@username]` | Подробная информация о чате | — |
| `!color` | `!color <#HEX|rgb(r,g,b)|название>` | Конвертация и просмотр цвета: HEX, RGB, HSL | — |
| `!decrypt` | `!decrypt <пароль> <зашифрованный текст>` | Расшифровать текст паролем (AES-256) | ✓ |
| `!emoji` | `!emoji <эмодзи>  или !emoji search <название>` | Информация об эмодзи: код, название, категория | — |
| `!encrypt` | `!encrypt <пароль> <текст>` | Зашифровать текст паролем (AES-256) | ✓ |
| `!history` | `!history` | Статистика чата (последние 1000 сообщений) | — |
| `!hs` | `!hs <команда>` | Hammerspoon bridge | ✓ |
| `!log` | `!log [N] [error|warn|info]` | Последние N строк лог-файла Краба | ✓ |
| `!loglevel` | `!loglevel [TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL]` | Runtime лог-уровень: показать или сменить (owner-only) | ✓ |
| `!ls` | `!ls [path]` | Список файлов | ✓ |
| `!mac` | `!mac clipboard|notify|apps|finder|notes|reminders|calendar` | macOS автоматизация (clipboard/notify/apps/finder/notes/reminders/calendar) | ✓ |
| `!read` | `!read <path>` | Чтение файла | ✓ |
| `!reset` | `!reset [--all] [--layer=krab|openclaw|gemini|archive] [--dry-run] [--force]` | Агрессивный reset истории: Krab cache + OpenClaw + Gemini + Archive | ✓ |
| `!screenshot` | `!screenshot [ocr [lang]|health]` | Снимок Chrome / OCR / статус CDP | ✓ |
| `!sticker` | `!sticker  (reply на стикер)` | Информация о стикере (reply): pack, emoji, file_id | — |
| `!sysinfo` | `!sysinfo` | Информация о хосте (CPU/RAM/диск) | ✓ |
| `!uptime` | `!uptime` | Аптайм Краба и системный uptime macOS | — |
| `!version` | `!version` | Версия Краба: git commit, branch, Python, Pyrogram, OpenClaw | — |
| `!web` | `!web [status|open|close]` | Управление браузером | ✓ |
| `!write` | `!write <file> <content>` | Запись файла | ✓ |

## Translator

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!translator` | `!translator on|off|status|history|lang <from>-<to>|mode <mode>|session start|stop|pause` | Управление автопереводчиком | ✓ |

## Users

| Command | Usage | Description | Owner |
|---------|-------|-------------|-------|
| `!acl` | `!acl` | Управление full/partial доступом | ✓ |
| `!alias` | `!alias [add|del|list]` | Алиасы команд | ✓ |
| `!blocked` | `!blocked [list|add|remove]` | Управление заблокированными пользователями | ✓ |
| `!contacts` | `!contacts [поиск]` | Список контактов Telegram (поиск, статистика) | ✓ |
| `!inbox` | `!inbox [list|ack|done|approve|reject|task]` | Owner inbox / escalation | ✓ |
| `!invite` | `!invite <@user|id> [chat_id]` | Пригласить пользователя в группу/канал | ✓ |
| `!members` | `!members [N] [admins|bots|recent]` | Список участников чата с фильтрацией | ✓ |
| `!profile` | `!profile [@user|reply]` | Профиль пользователя Telegram: аватар, статистика, биография | — |
| `!scope` | `!scope | !scope grant <user_id> full|partial | !scope revoke <user_id> | !scope list` | Управление ACL-правами: просмотр уровня доступа, grant/revoke | — |
| `!who` | `!who [@user|reply]` | Информация о пользователе или чате | — |
| `!whois` | `!whois <domain|IP>` | WHOIS-поиск домена или IP-адреса через AI | — |

## Owner-only quick ref

| Command | Usage |
|---------|-------|
| `!acl` | `!acl` |
| `!afk` | `!afk [сообщение] | !afk off` |
| `!agent` | `!agent new <name> <prompt>|list|swarm [loop N] <тема>` |
| `!alias` | `!alias [add|del|list]` |
| `!archive` | `!archive [list]` |
| `!autodel` | `!autodel <сек>` |
| `!backup` | `!backup [list]` |
| `!blocked` | `!blocked [list|add|remove]` |
| `!browser` | `!browser [cdp|status]` |
| `!budget` | `!budget [сумма]` |
| `!cap` | `!cap [name on|off|reset]` |
| `!chatban` | `!chatban [chat_id]` |
| `!chatmute` | `!chatmute [мин]` |
| `!claude_cli` | `!claude_cli <задача>` |
| `!codex` | `!codex <задача>` |
| `!config` | `!config|!set <KEY> <VAL>` |
| `!confirm` | `!confirm <hash>` |
| `!contacts` | `!contacts [поиск]` |
| `!costs` | `!costs [detail]` |
| `!cron` | `!cron [list|enable|disable|run|status] [<name>] | !cron quick "<время>" "<промпт>"` |
| `!cronstatus` | `!cronstatus` |
| `!debug` | `!debug [sessions|tasks|gc]` |
| `!decrypt` | `!decrypt <пароль> <зашифрованный текст>` |
| `!del` | `!del [N]` |
| `!digest` | `!digest` |
| `!encrypt` | `!encrypt <пароль> <текст>` |
| `!eval` | `!eval <python код>` |
| `!fwd` | `!fwd <chat_id>` |
| `!gemini` | `!gemini <задача>` |
| `!grep` | `!grep <паттерн> [текст]  или reply на сообщение` |
| `!hs` | `!hs <команда>` |
| `!img` | `!img [вопрос]` |
| `!inbox` | `!inbox [list|ack|done|approve|reject|task]` |
| `!invite` | `!invite <@user|id> [chat_id]` |
| `!link` | `!link [сообщение]  (reply на сообщение для permalink)` |
| `!listen` | `!listen [active|mention-only|muted|reset|list|stats]` |
| `!log` | `!log [N] [error|warn|info]` |
| `!loglevel` | `!loglevel [TRACE|DEBUG|INFO|WARNING|ERROR|CRITICAL]` |
| `!ls` | `!ls [path]` |
| `!mac` | `!mac clipboard|notify|apps|finder|notes|reminders|calendar` |
| `!mark` | `!mark [tag]  (reply на сообщение)` |
| `!media` | `!media [save|info]` |
| `!members` | `!members [N] [admins|bots|recent]` |
| `!model` | `!model [info|local|cloud|auto|set <id>|load <name>|unload|scan]` |
| `!monitor` | `!monitor [add|del|list|status]` |
| `!notify` | `!notify on|off` |
| `!ocr` | `!ocr [подсказка]` |
| `!opencode` | `!opencode <задача>` |
| `!panel` | `!panel` |
| `!pin` | `!pin` |
| `!purge` | `!purge` |
| `!qr` | `!qr <текст|URL>  или ответь на сообщение` |
| `!quiz` | `!quiz <тема>` |
| `!read` | `!read <path>` |
| `!reasoning` | `!reasoning [show|clear]` |
| `!reset` | `!reset [--all] [--layer=krab|openclaw|gemini|archive] [--dry-run] [--force]` |
| `!restart` | `!restart` |
| `!rm_remind` | `!rm_remind <id>` |
| `!role` | `!role [name|list]` |
| `!run` | `!run <команда>` |
| `!say` | `!say <текст>              — отправить в текущий чат
!say <chat_id> <текст>   — отправить в другой чат` |
| `!schedule` | `!schedule [list|cancel|add]` |
| `!screenshot` | `!screenshot [ocr [lang]|health]` |
| `!shop` | `!shop <url>` |
| `!slowmode` | `!slowmode <сек>  (0 = выключить)` |
| `!snippet` | `!snippet save <name> <lang> <code> | !snippet <name> | !snippet list` |
| `!spam` | `!spam block <@user|id> | !spam unblock <id> | !spam list` |
| `!swarm` | `!swarm <team> <задача>|research <тема>|summary|teams|schedule|memory|jobs|task|artifacts|listen|channels|setup` |
| `!sysinfo` | `!sysinfo` |
| `!tag` | `!tag [all|admins|<user1> <user2>]` |
| `!template` | `!template save <name> <text>  — сохранить
!template <name> [val1 val2]  — отправить (с подстановкой)
!template list                — список
!template del <name>          — удалить` |
| `!translator` | `!translator on|off|status|history|lang <from>-<to>|mode <mode>|session start|stop|pause` |
| `!tts` | `!tts <текст>  или reply на сообщение` |
| `!typing` | `!typing [сек]` |
| `!unarchive` | `!unarchive` |
| `!unpin` | `!unpin` |
| `!voice` | `!voice on|off|toggle|block|unblock|speed <0.75..2.5>|voice <edge-tts-id>` |
| `!watch` | `!watch status|now` |
| `!web` | `!web [status|open|close]` |
| `!welcome` | `!welcome <текст> | !welcome off | !welcome status` |
| `!write` | `!write <file> <content>` |
| `!тишина` | `!тишина [мин|стоп|глобально|расписание HH:MM-HH:MM|статус]` |
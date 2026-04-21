<!-- AUTO-GENERATED — do not edit manually. Regenerate: venv/bin/python scripts/build_skill_manifest.py -->
# Krab Skill Manifest

> Generated: **2026-04-21 02:33 UTC**

## Skills

Модули в `src/skills/` — специализированные интеграции.

| Module | File | Description |
|--------|------|-------------|
| `crypto` | `src/skills/crypto.py` | Crypto Skill - Получение курсов криптовалют |
| `imessage` | `src/skills/imessage.py` | iMessage Integration - Отправка сообщений через AppleScript |
| `mercadona` | `src/skills/mercadona.py` | Mercadona Skill — поиск товаров и цен на актуальном web-flow Mercadona. |
| `web_search` | `src/skills/web_search.py` | Web Search Skill - Поиск информации через Brave Search API |

## Commands

Команды из `CommandRegistry` (`src/core/command_registry.py`).

### ai

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!ask` | 🟢 production |  | Спросить AI о сообщении (reply → AI отвечает) | `!ask [вопрос]` |
| `!catchup` | 🟢 production |  | Кратко о пропущенном с момента последнего визита | `!catchup` |
| `!define` | 🟢 production |  | Определение слова из словаря (через AI) | `!define <слово>` |
| `!explain` | 🟢 production |  | Объяснение кода простым языком через AI | `!explain <код>  или reply на сообщение с кодом` |
| `!img` | 🟢 production | ✓ | Описание фото через AI vision (reply на фото) | `!img [вопрос]` |
| `!news` | 🟢 production |  | Топ-5 новостей через AI — тема или язык (ru/en) | `!news [тема\|ru\|en]` |
| `!ocr` | 🟢 production | ✓ | Извлечение текста из изображения через AI vision (reply на фото) | `!ocr [подсказка]` |
| `!poll` | 🟢 production |  | Создать опрос в чате | `!poll <вопрос> \| <вариант1> \| <вариант2> ...` |
| `!quiz` | 🟢 production | ✓ | AI-генерированная викторина по теме | `!quiz <тема>` |
| `!rate` | 🟢 production |  | Курс криптовалюты или акции через AI (цена, 24h%, капитализация) | `!rate <тикер> [тикер2 ...]` |
| `!report` | 🟢 production |  | Расширенный исследовательский отчёт | `!report <тема>` |
| `!search` | 🟢 production |  | Веб-поиск Brave | `!search <запрос>` |
| `!summary` | 🟢 production |  | Суммаризация последних N сообщений | `!summary [N]` |
| `!translate` | 🟢 production |  | Перевод текста (reply или аргумент) | `!translate [язык]` |
| `!tts` | 🟢 production | ✓ | Text-to-speech: преобразовать текст в голосовое сообщение | `!tts <текст>  или reply на сообщение` |
| `!urban` | 🟢 production |  | Определение слова из Urban Dictionary через AI + web_search | `!urban <слово>` |
| `!weather` | 🟢 production |  | Текущая погода через web_search | `!weather [город]` |

### basic

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!b64` | 🟢 production |  | Base64 кодирование/декодирование | `!b64 encode\|decode <текст>` |
| `!calc` | 🟢 production |  | Калькулятор: математические выражения (поддерживает функции) | `!calc <выражение>` |
| `!clear` | 🟢 production |  | Очистить историю диалога | `!clear` |
| `!context` | 🟢 production |  | Управление контекстом чата | `!context [clear\|save]` |
| `!convert` | 🟢 production |  | Конвертер единиц: длина, масса, объём, скорость, температура | `!convert <число> <из> <в>  (напр. !convert 100 km mi)` |
| `!currency` | 🟢 production |  | Конвертация валют через AI | `!currency <сумма> <из> <в>  (напр. !currency 100 USD EUR)` |
| `!diagnose` | 🟢 production |  | Детальная диагностика подключений | `!diagnose` |
| `!dice` | 🟢 production |  | Бросок кубика(ов): стандартные нотации (2d6, d20) | `!dice [NdM]` |
| `!dns` | 🟢 production |  | DNS-запрос: A, AAAA, MX, TXT, CNAME записи | `!dns <домен> [тип]` |
| `!hash` | 🟢 production |  | Хэш текста или файла (MD5, SHA1, SHA256, SHA512) | `!hash [алгоритм] <текст>  или reply на файл` |
| `!health` | 🟢 production |  | Диагностика всех подсистем | `!health` |
| `!help` | 🟢 production |  | Справка по всем командам | `!help [команда]` |
| `!ip` | 🟢 production |  | Геолокация IP-адреса и ASN-информация | `!ip <IP-адрес>` |
| `!len` | 🟢 production |  | Длина текста в символах, словах и байтах | `!len <текст>  или reply на сообщение` |
| `!panel` | 🟢 production | ✓ | Owner panel (:8080) | `!panel` |
| `!ping` | 🟢 production |  | Ping хоста (ICMP или TCP) | `!ping <host> [порт]` |
| `!qr` | 🟢 production | ✓ | Генерация QR-кода из текста или URL | `!qr <текст\|URL>  или ответь на сообщение` |
| `!rand` | 🟢 production |  | Случайное число, выбор из списка или перемешивание | `!rand [N] \| !rand <a> <b> \| !rand pick item1 item2 ...` |
| `!stats` | 🟢 production |  | Статистика сессии или ecosystem health | `!stats [ecosystem\|eco\|health\|basic]` |
| `!status` | 🟢 production |  | Статус всех подсистем Краба | `!status` |
| `!time` | 🟢 production |  | Текущее время в разных часовых поясах | `!time [город\|timezone]` |

### costs

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!budget` | 🟢 production | ✓ | Просмотр/установка дневного бюджета | `!budget [сумма]` |
| `!costs` | 🟢 production | ✓ | Отчёт расходов по провайдерам | `!costs [detail]` |
| `!digest` | 🟢 production | ✓ | Weekly digest активности | `!digest` |

### dev

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!agent` | 🟢 production | ✓ | Управление агентами | `!agent new <name> <prompt>\|list\|swarm [loop N] <тема>` |
| `!backup` | 🟢 production | ✓ | Экспорт всех persistent данных Краба в ZIP-архив | `!backup [list]` |
| `!bench` | 🟢 production | ✓ | Бенчмарк перфоманса Memory Layer (fast/full/fts/semantic) | `!bench [fast\|full\|fts\|semantic]` |
| `!claude_cli` | 🟢 production | ✓ | Claude Code CLI | `!claude_cli <задача>` |
| `!codex` | 🟢 production | ✓ | OpenAI Codex CLI | `!codex <задача>` |
| `!config` | 🟢 production | ✓ | Просмотр/установка настроек | `!config\|!set <KEY> <VAL>` |
| `!debug` | 🟢 production | ✓ | Отладочная сводка: tasks, sessions, GC, last error (owner-only) | `!debug [sessions\|tasks\|gc]` |
| `!eval` | 🟢 production | ✓ | Выполнить произвольный Python-код (owner-only) | `!eval <python код>` |
| `!fix` | 🟢 production | ✓ | AI-предложения по исправлению кода (reply на сообщение с кодом) | `!fix [язык] \| !fix [--lang=py\|js\|go]` |
| `!gemini` | 🟢 production | ✓ | Gemini CLI | `!gemini <задача>` |
| `!grep` | 🟢 production | ✓ | Поиск паттерна по тексту или reply (regex поддерживается) | `!grep <паттерн> [текст]  или reply на сообщение` |
| `!json` | 🟢 production |  | Форматировать/валидировать JSON | `!json <json-строка>  или reply на сообщение` |
| `!opencode` | 🟢 production | ✓ | OpenCode CLI | `!opencode <задача>` |
| `!restart` | 🟢 production | ✓ | Перезапуск бота | `!restart` |
| `!rewrite` | 🟢 production | ✓ | Переписать текст/код в другом стиле (reply или аргумент) | `!rewrite <стиль> \| !rewrite [style=concise\|verbose\|formal\|casual]` |
| `!run` | 🟢 production | ✓ | Запустить shell-скрипт или команду (owner-only) | `!run <команда>` |
| `!shop` | 🟢 production | ✓ | Mercadona Playwright scraper | `!shop <url>` |
| `!yt` | 🟢 production |  | Информация о YouTube-видео или плейлисте | `!yt <url\|id>` |

### files

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!media` | 🟢 production | ✓ | Скачивание медиафайлов (фото/видео/документ/аудио). Reply на медиа. | `!media [save\|info]` |

### management

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!archive` | 🟢 production | ✓ | Архивировать текущий чат (list — список архива) | `!archive [list]` |
| `!autodel` | 🟢 production | ✓ | Автоудаление через N секунд (0 = выключить) | `!autodel <сек>` |
| `!chatmute` | 🟢 production | ✓ | Замутить уведомления чата на N минут (0 = снять мут) | `!chatmute [мин]` |
| `!collect` | 🟢 production |  | Собрать N сообщений чата в один текст | `!collect [N]` |
| `!del` | 🟢 production | ✓ | Удалить N последних сообщений (default 1) | `!del [N]` |
| `!diff` | 🟢 production |  | Diff двух текстов: unified-формат | `!diff <текст1> --- <текст2>  или reply + аргумент` |
| `!fwd` | 🟢 production | ✓ | Переслать сообщение (reply) | `!fwd <chat_id>` |
| `!link` | 🟢 production | ✓ | Создать invite-link чата или получить ссылку на сообщение | `!link [сообщение]  (reply на сообщение для permalink)` |
| `!mark` | 🟢 production | ✓ | Пометить сообщение тегом для быстрого поиска | `!mark [tag]  (reply на сообщение)` |
| `!pin` | 🟢 production | ✓ | Закрепить сообщение (reply) | `!pin` |
| `!purge` | 🟢 production | ✓ | Очистить историю бота в чате | `!purge` |
| `!react` | 🟢 production |  | Поставить реакцию (reply) | `!react <эмодзи>` |
| `!regex` | 🟢 production |  | Проверить регулярное выражение против текста | `!regex <паттерн> <текст>  или reply на сообщение` |
| `!say` | 🟢 production | ✓ | Тихая отправка сообщения от имени юзербота (команда удаляется) | `!say <текст>              — отправить в текущий чат
!say <chat_id> <текст>   — отправить в другой чат` |
| `!schedule` | 🟢 production | ✓ | Отложенные сообщения | `!schedule [list\|cancel\|add]` |
| `!sed` | 🟢 production |  | Замена текста по паттерну s/старое/новое/ (reply на сообщение) | `!sed s/старое/новое/  (reply на сообщение)` |
| `!slowmode` | 🟢 production | ✓ | Включить slow mode в группе (задержка в секундах) | `!slowmode <сек>  (0 = выключить)` |
| `!tag` | 🟢 production | ✓ | Тегировать участников группы (упомянуть @всех или список) | `!tag [all\|admins\|<user1> <user2>]` |
| `!top` | 🟢 production |  | Лидерборд активности чата по количеству сообщений | `!top [N] \| !top week \| !top all` |
| `!unarchive` | 🟢 production | ✓ | Разархивировать текущий чат | `!unarchive` |
| `!unpin` | 🟢 production | ✓ | Открепить сообщение (reply) | `!unpin` |
| `!welcome` | 🟢 production | ✓ | Настройка приветственного сообщения для новых участников | `!welcome <текст> \| !welcome off \| !welcome status` |

### models

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!model` | 🟢 production | ✓ | Управление маршрутизацией модели | `!model [info\|local\|cloud\|auto\|set <id>\|load <name>\|unload\|scan]` |
| `!reasoning` | 🟢 production | ✓ | Просмотр/очистка reasoning-trace | `!reasoning [show\|clear]` |
| `!role` | 🟢 production | ✓ | Смена системного ролевого промпта | `!role [name\|list]` |

### modes

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!afk` | 🟢 production | ✓ | Режим Away From Keyboard: авто-ответ при упоминании | `!afk [сообщение] \| !afk off` |
| `!cap` | 🟢 production | ✓ | Матрица capabilities чатов | `!cap [name on\|off\|reset]` |
| `!chatban` | 🟢 production | ✓ | Заблокировать обработку чата | `!chatban [chat_id]` |
| `!listen` | 🟢 production | ✓ | Режим ответов Краба в текущем чате | `!listen [active\|mention-only\|muted\|reset\|list\|stats]` |
| `!notify` | 🟢 production | ✓ | Tool narrations (🔍 Ищу... 📸 Скриншот...) | `!notify on\|off` |
| `!spam` | 🟢 production | ✓ | Антиспам: блокировать/разблокировать пользователя за спам | `!spam block <@user\|id> \| !spam unblock <id> \| !spam list` |
| `!typing` | 🟢 production | ✓ | Имитировать typing action в чате | `!typing [сек]` |
| `!voice` | 🟢 production | ✓ | Управление голосовыми ответами и TTS | `!voice on\|off\|toggle\|block\|unblock\|speed <0.75..2.5>\|voice <edge-tts-id>` |
| `!тишина` | 🟢 production | ✓ | Режим тишины (без AI-ответов) | `!тишина [мин\|стоп\|глобально\|расписание HH:MM-HH:MM\|статус]` |

### notes

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!bookmark` | 🟢 production |  | Закладка на сообщение | `!bookmark` |
| `!confirm` | 🟢 production | ✓ | Подтвердить persistent-запись памяти (owner-only) | `!confirm <hash>` |
| `!export` | 🟢 production |  | Экспорт N последних сообщений чата | `!export [N]` |
| `!memo` | 🟢 production |  | Заметка в Obsidian (reply или аргумент) | `!memo <текст>` |
| `!memory` | 🟢 production |  | Память: recent / stats / clear / rebuild в Memory Layer | `!memory recent [source_filter] \| !memory stats \| !memory clear \| !memory rebuild` |
| `!note` | 🟢 production |  | Голосовая заметка (reply на голосовое сообщение) | `!note` |
| `!paste` | 🟢 production |  | Отправить длинный текст как файл-документ (>4096 символов) | `!paste <текст>  или reply на сообщение` |
| `!quote` | 🟢 production |  | Сохранить/показать цитату (reply на сообщение) | `!quote [save\|list\|random\|del <id>]` |
| `!recall` | 🟢 production |  | Вспомнить факт из памяти | `!recall <запрос>` |
| `!remember` | 🟢 production |  | Запомнить факт в память | `!remember <текст>` |
| `!snippet` | 🟢 production | ✓ | Сниппеты кода: сохранить и отправить с подсветкой синтаксиса | `!snippet save <name> <lang> <code> \| !snippet <name> \| !snippet list` |
| `!template` | 🟢 production | ✓ | Шаблоны сообщений с подстановкой переменных | `!template save <name> <text>  — сохранить
!template <name> [val1 val2]  — отправить (с подстановкой)
!template list                — список
!template del <name>          — удалить` |
| `!todo` | 🟢 production |  | Список задач: добавить, показать, отметить выполненными | `!todo [add <текст>\|list\|done <N>\|clear]` |

### scheduler

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!cron` | 🟢 production | ✓ | Управление OpenClaw cron jobs (list/enable/disable/run/status/quick) | `!cron [list\|enable\|disable\|run\|status] [<name>] \| !cron quick "<время>" "<промпт>"` |
| `!cronstatus` | 🟢 production | ✓ | Статус cron scheduler | `!cronstatus` |
| `!monitor` | 🟢 production | ✓ | Мониторинг чатов | `!monitor [add\|del\|list\|status]` |
| `!remind` | 🟢 production |  | Поставить напоминание (time/event) или list/cancel | `!remind <2h\|17:30\|tomorrow 9:00\|when X then Y> <текст> / list / cancel <id>` |
| `!reminders` | 🟢 production |  | Список активных напоминаний | `!reminders` |
| `!rm_remind` | 🟢 production | ✓ | Удалить напоминание по id | `!rm_remind <id>` |
| `!stopwatch` | 🟢 production |  | Секундомер: start, stop, lap, reset | `!stopwatch start\|stop\|lap\|reset` |
| `!timer` | 🟢 production |  | Таймер с уведомлением по истечении: list, cancel | `!timer <время> [метка] \| !timer list \| !timer cancel [id]` |
| `!watch` | 🟢 production | ✓ | Proactive watch / owner-digest | `!watch status\|now` |

### swarm

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!swarm` | 🟢 production | ✓ | Мультиагентный рой: research (+ self-reflection → follow-up tasks), summary, schedule, teams, memory | `!swarm <team> <задача>\|research <тема>\|summary\|teams\|schedule\|memory\|jobs\|task\|artifacts\|listen\|channels\|setup` |

### system

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!browser` | 🟢 production | ✓ | CDP browser bridge | `!browser [cdp\|status]` |
| `!chatinfo` | 🟢 production |  | Подробная информация о чате | `!chatinfo [chat_id\|@username]` |
| `!color` | 🟢 production |  | Конвертация и просмотр цвета: HEX, RGB, HSL | `!color <#HEX\|rgb(r,g,b)\|название>` |
| `!decrypt` | 🟢 production | ✓ | Расшифровать текст паролем (AES-256) | `!decrypt <пароль> <зашифрованный текст>` |
| `!emoji` | 🟢 production |  | Информация об эмодзи: код, название, категория | `!emoji <эмодзи>  или !emoji search <название>` |
| `!encrypt` | 🟢 production | ✓ | Зашифровать текст паролем (AES-256) | `!encrypt <пароль> <текст>` |
| `!history` | 🟢 production |  | Статистика чата (последние 1000 сообщений) | `!history` |
| `!hs` | 🟢 production | ✓ | Hammerspoon bridge | `!hs <команда>` |
| `!log` | 🟢 production | ✓ | Последние N строк лог-файла Краба | `!log [N] [error\|warn\|info]` |
| `!loglevel` | 🟢 production | ✓ | Runtime лог-уровень: показать или сменить (owner-only) | `!loglevel [TRACE\|DEBUG\|INFO\|WARNING\|ERROR\|CRITICAL]` |
| `!ls` | 🟢 production | ✓ | Список файлов | `!ls [path]` |
| `!mac` | 🟢 production | ✓ | macOS автоматизация (clipboard/notify/apps/finder/notes/reminders/calendar) | `!mac clipboard\|notify\|apps\|finder\|notes\|reminders\|calendar` |
| `!read` | 🟢 production | ✓ | Чтение файла | `!read <path>` |
| `!reset` | 🟢 production | ✓ | Агрессивный reset истории: Krab cache + OpenClaw + Gemini + Archive | `!reset [--all] [--layer=krab\|openclaw\|gemini\|archive] [--dry-run] [--force]` |
| `!screenshot` | 🟢 production | ✓ | Снимок Chrome / OCR / статус CDP | `!screenshot [ocr [lang]\|health]` |
| `!sticker` | 🟢 production |  | Информация о стикере (reply): pack, emoji, file_id | `!sticker  (reply на стикер)` |
| `!sysinfo` | 🟢 production | ✓ | Информация о хосте (CPU/RAM/диск) | `!sysinfo` |
| `!uptime` | 🟢 production |  | Аптайм Краба и системный uptime macOS | `!uptime` |
| `!version` | 🟢 production |  | Версия Краба: git commit, branch, Python, Pyrogram, OpenClaw | `!version` |
| `!web` | 🟢 production | ✓ | Управление браузером | `!web [status\|open\|close]` |
| `!write` | 🟢 production | ✓ | Запись файла | `!write <file> <content>` |

### translator

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!translator` | 🟢 production | ✓ | Управление автопереводчиком | `!translator on\|off\|status\|history\|lang <from>-<to>\|mode <mode>\|session start\|stop\|pause` |

### users

| Command | Stage | Owner-only | Description | Usage |
|---------|-------|------------|-------------|-------|
| `!acl` | 🟢 production | ✓ | Управление full/partial доступом | `!acl` |
| `!alias` | 🟢 production | ✓ | Алиасы команд | `!alias [add\|del\|list]` |
| `!blocked` | 🟢 production | ✓ | Управление заблокированными пользователями | `!blocked [list\|add\|remove]` |
| `!contacts` | 🟢 production | ✓ | Список контактов Telegram (поиск, статистика) | `!contacts [поиск]` |
| `!inbox` | 🟢 production | ✓ | Owner inbox / escalation | `!inbox [list\|ack\|done\|approve\|reject\|task]` |
| `!invite` | 🟢 production | ✓ | Пригласить пользователя в группу/канал | `!invite <@user\|id> [chat_id]` |
| `!members` | 🟢 production | ✓ | Список участников чата с фильтрацией | `!members [N] [admins\|bots\|recent]` |
| `!profile` | 🟢 production |  | Профиль пользователя Telegram: аватар, статистика, биография | `!profile [@user\|reply]` |
| `!scope` | 🟢 production |  | Управление ACL-правами: просмотр уровня доступа, grant/revoke | `!scope \| !scope grant <user_id> full\|partial \| !scope revoke <user_id> \| !scope list` |
| `!who` | 🟢 production |  | Информация о пользователе или чате | `!who [@user\|reply]` |
| `!whois` | 🟢 production |  | WHOIS-поиск домена или IP-адреса через AI | `!whois <domain\|IP>` |

## Capabilities

Role × capability matrix из `src/core/capability_registry.py`.

| Capability | `owner` | `full` | `partial` | `guest` |
|------------|-----|-----|-----|-----|
| `acl_admin` | ✓ | ✗ | ✗ | ✗ |
| `approvals` | ✓ | ✓ | ✗ | ✗ |
| `browser_control` | ✓ | ✓ | ✗ | ✗ |
| `chat` | ✓ | ✓ | ✓ | ✓ |
| `clipboard_read` | ✓ | ✓ | ✗ | ✗ |
| `clipboard_write` | ✓ | ✓ | ✗ | ✗ |
| `file_ops` | ✓ | ✓ | ✗ | ✗ |
| `inbox` | ✓ | ✓ | ✗ | ✗ |
| `macos_control` | ✓ | ✓ | ✗ | ✗ |
| `memory` | ✓ | ✓ | ✗ | ✗ |
| `model_routing` | ✓ | ✓ | ✗ | ✗ |
| `ocr` | ✓ | ✓ | ✗ | ✗ |
| `runtime_mutation` | ✓ | ✓ | ✗ | ✗ |
| `runtime_truth` | ✓ | ✓ | ✓ | ✗ |
| `screenshots` | ✓ | ✓ | ✗ | ✗ |
| `tor_proxy` | ✗ | ✗ | ✗ | ✗ |
| `ui_automation` | ✓ | ✗ | ✗ | ✗ |
| `voice_runtime` | ✓ | ✓ | ✗ | ✗ |
| `web_search` | ✓ | ✓ | ✓ | ✗ |

---

*Сгенерировано 2026-04-21 02:33 UTC. Команда регенерации:*

```bash
venv/bin/python scripts/build_skill_manifest.py
```

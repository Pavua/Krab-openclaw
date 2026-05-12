# Команды Краба (162 registered, 172+ с алиасами)

Полный справочник команд. Обновлено: Session 48 (13.05.2026, Wave 168).
Источник истины: `src/core/command_registry.py::_COMMANDS` (162 `CommandInfo`).

```
# AI и контент
!ask <вопрос>                — AI ответ в текущем чате
!search <запрос>             — AI поиск + источники
!search --raw <запрос>       — сырые результаты Brave
!translate <текст>           — перевести текст
!summary [N]                 — суммарный recap N сообщений
!catchup                     — алиас !summary 100
!report [daily|weekly]       — AI отчёт по активности
!weather <город>             — прогноз погоды
!define <слово>              — определение слова
!urban <слово>               — Urban Dictionary
!img <prompt>                — генерация изображения
!ocr                         — распознать текст из изображения
!yt <url>                    — транскрипция YouTube
!news [тема]                 — актуальные новости
!rate <текст>                — оценить текст/идею

# Costs & FinOps
!costs                       — cost report прямо в Telegram
!budget [сумма]              — показать или установить бюджет
!digest                      — немедленный weekly digest
!quota                       — статус квоты (Vertex/Codex)

# Заметки и хранилище
!memo [текст]                — заметка в текущем чате
!memo list                   — список заметок
!memo del <n>                — удалить заметку
!note <текст>                — быстрая заметка
!bookmark / !bm [url]        — закладка (из reply или URL)
!bm list                     — список закладок
!bm del <n>                  — удалить закладку
!export [формат]             — экспорт заметок/закладок
!snippet [lang] <код>        — сохранить code snippet
!paste [текст]               — вставить clipboard/текст
!quote                       — цитата из reply
!template <name> [text]      — шаблон сообщения
!tag <name>                  — пометить сообщение тегом

# Анализ чата
!grep <паттерн>              — поиск по истории чата
!context [N]                 — контекст чата (N сообщений)
!monitor on/off              — мониторинг активности
!who [N]                     — топ активных участников
!fwd <chat_id>               — переслать сообщение
!collect [N]                 — собрать N последних
!top [N]                     — топ сообщений по реакциям
!history [N]                 — история чата
!chatinfo                    — информация о чате
!whois <user>                — информация о пользователе

# Сообщения и управление
!pin [тихо]                  — закрепить reply-сообщение
!unpin [all]                 — открепить сообщение
!del [N]                     — удалить N последних своих
!purge [N]                   — удалить N от любого (reply)
!autodel <sec>               — автоудаление через N сек
!schedule <time> <текст>     — отложить сообщение
!remind <time> <текст>       — напоминание
!remind list                 — список напоминаний
!remind cancel <n>           — отменить напоминание
!poll <вопрос> | <opt1> | …  — голосование
!quiz <вопрос> | <ответ>     — викторина
!dice [N]                    — бросить кубик
!typing [сек]                — эффект "печатает..."
!say <текст>                 — отправить от имени бота

# Текстовые утилиты
!calc <выражение>            — калькулятор
!b64 [enc|dec] <текст>       — Base64 кодирование
!hash [algo] <текст>         — хэш (md5/sha1/sha256)
!len / !count <текст>        — длина и количество слов
!json [pretty|compact]       — форматировать JSON
!sed s/from/to               — замена в тексте (reply)
!diff                        — diff двух текстов
!regex <паттерн> <текст>     — проверить regex
!rand [N] / !rand <a> <b>    — случайное число
!qr <текст>                  — QR-код
!convert <val> <from> <to>   — конвертация единиц
!color <hex|rgb|name>        — информация о цвете
!emoji <name|unicode>        — информация об эмодзи

# Время и сеть
!timer <время>               — таймер (1m30s, etc.)
!stopwatch start/stop/lap    — секундомер
!time [timezone]             — текущее время
!currency <сумма> <from> <to> — курс валют
!ip [адрес]                  — информация об IP
!dns <домен>                 — DNS lookup
!ping <хост>                 — ping хоста
!link <url>                  — short link / info
!uptime                      — аптайм Краба

# Социальное и модерация
!react <emoji>               — реакция на reply
!afk [причина]               — режим отсутствия
!afk off / !back             — вернуться
!afk status                  — статус AFK
!welcome on/off              — приветствие новых участников
!sticker                     — инфо о стикере
!alias <cmd> <команда>       — создать алиас команды
!alias list                  — список алиасов
!chatmute <user> [dur]       — заглушить пользователя
!slowmode [сек]              — слоумод в группе
!spam status/add/remove      — антиспам
!archive / !unarchive        — архивировать чат
!mark <read|unread>          — пометить прочитанным
!blocked                     — список заблокированных
!invite <user>               — пригласить в группу
!profile [bio|photo|name]    — управление профилем
!contacts [search]           — управление контактами
!members [search]            — участники группы
!log [N]                     — лог активности
!tts <текст>                 — text-to-speech

# Программирование и утилиты
!run <lang> <код>            — выполнить код
!eval <python>               — eval Python (owner-only)
!grep <паттерн>              — regex поиск
!encrypt / !decrypt <текст>  — шифрование текста
!report spam                 — пожаловаться на spam
!todo [add|done|list|del]    — персональный TODO
!qr <текст>                  — генерация QR-кода
!backup                      — резервное копирование данных
!hash [algo]                 — хэш-функция

# Системные (owner-only)
!health                      — расширенная диагностика
!stats                       — статистика (FinOps/Translator/Swarm)
!sysinfo                     — системная информация
!version                     — версия Краба
!model [list|switch|info]    — управление моделью
!model switch <model>        — сменить модель
!reasoning [low|medium|high] — уровень reasoning
!config [key] [value]        — просмотр/изменение конфигурации
!set <key> <value>           — быстрый set config
!scope [scope]               — управление scopes OpenClaw
!acl [allow|deny] <user>     — управление ACL
!notify [on|off|status]      — управление уведомлениями
!restart                     — перезапуск Краба
!debug [on|off|trace]        — режим отладки
!diagnose                    — диагностика всей экосистемы
!agent <prompt>              — прямой вызов AI агента
!context [clear|save]        — управление контекстом OpenClaw
!cronstatus                  — статус cron-задач
!cron list/add/remove/toggle — управление cron
!panel                       — URL owner panel
!browser [status|tabs]       — состояние браузера
!macos <команда>             — macOS автоматизация
!hs <команда>                — Hammerspoon bridge
!codex / !gemini / !claude   — CLI AI инструменты
!inbox [list|update]         — управление inbox
!role <role>                 — сменить роль агента
!chatban [ban|unban|list]    — бан в чате
!silence [on|off|status]     — режим тишины
!costs                       — FinOps отчёт
!budget [сумма]              — бюджет
!digest                      — дайджест

# Chat policy (Smart Routing)
!chatpolicy show             — текущая политика чата
!chatpolicy set <mode>       — режим (silent/cautious/normal/chatty)
!chatpolicy threshold <0-1>  — порог implicit trigger
!chatpolicy add-blocked-topic — добавить блокируемую тему
!chatpolicy stats            — статистика policy
!chatpolicy list             — все чаты с policy
!chatpolicy reset            — сброс policy чата

# Translator (full suite)
!translator status            — статус переводчика
!translator on / off          — включить/выключить
!translator lang <es-ru|…>   — пара языков
!translator auto              — авто-определение языка
!translator mode <bilingual|auto_to_ru|auto_to_en>
!translator strategy <voice-first|subtitles-first>
!translator ordinary <on|off>
!translator internet <on|off>
!translator subtitles|timeline|summary|diagnostics <on|off>
!translator phrase add/remove — кастомные фразы
!translator reset             — сброс настроек
!translator test <текст>      — быстрый тест перевода
!translator history           — статистика переводов
!translator help              — список субкоманд
!translator session status/start/pause/resume/stop/mute/unmute/replay/clear

# Voice
!voice on|off|toggle         — голосовой режим
!voice speed <0.75..2.5>     — скорость речи
!voice voice <edge-tts-id>   — выбор голоса
!voice delivery <text+voice|voice-only>
!voice block <chat_id>       — заблокировать чат для голоса
!voice unblock <chat_id>     — разблокировать
!voice blocked               — список заблокированных
!voice reset                 — сброс голосовых настроек

# Swarm
!swarm <team> <задача>       — запустить агентную сессию
!swarm teams                 — список команд
!swarm research <topic>      — глубокий веб-ресёрч
!swarm summary / !swarm сводка — сводка активностей
!swarm info <team>           — детали команды
!swarm stats                 — статистика по всем командам
!swarm report                — просмотр markdown отчётов
!swarm setup                 — настройка Forum Topics
!swarm schedule [add|list]   — рекуррентный планировщик
!swarm memory [team]         — память свёрма
!swarm task board            — Kanban-доска
!swarm task list [team]      — список задач
!swarm task create <team> <title>
!swarm task done|fail <id>
!swarm task assign <id>
!swarm task status <id>
!swarm task priority <id> <level>
!swarm task count
!swarm task clear

# Search & Web
!search <запрос>             — AI-режим поиска
!search --raw <запрос>       — сырые результаты
!web login/screen/gpt        — браузерный контроль
!shop <запрос>               — поиск в Mercadona

# Files & Memory
!ls [path]                   — список файлов
!read <path>                 — прочитать файл
!write <path> <content>      — записать файл
!remember <key> <value>      — сохранить в память
!recall <key>                — прочитать из памяти

# Agent Engine (session 35)
!engine show                 — текущий engine для чата
!engine here <openclaw|hermes|auto> — сменить engine для чата
!engine room                 — engine для всей AgentRoom
!engine status               — статус всех engine

# SkillCurator (session 35 — Steps 1-4 LIVE)
!curator ab start <skill_id>          — запустить A/B тест промпта
!curator ab status [id]               — статус A/B теста
!curator ab evaluate <id> <win|lose>  — завершить тест с результатом
!curator ab cancel <id>               — отменить тест
!curator ab list                      — список активных тестов
!curator apply <skill_id>             — применить предложение с подтверждением
!curator rollback <skill_id>          — откатить последнее изменение
!curator overlays                     — список активных оверлеев промптов

# Beta (Session 17)
!mem                         — быстрый поиск по архиву памяти
!chado                       — chado-sync агент (W5.4)
!filter                      — фильтрация сообщений по паттерну
```

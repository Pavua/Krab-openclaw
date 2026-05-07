# 🦀 Krab — гид по использованию

Краткий справочник: где обычным текстом, где `!команда`, что куда писать.

> Дата обновления: 2026-05-07. Поддерживается актуальным как при изменениях
> архитектуры — пинай Krab если что-то устарело и он перепишет этот файл.

---

## 1. Где какой режим общения

### 1.1 DM с **Yung Nagato** (главный Krab) → `@yung_nagato`

**Можно писать обычным текстом** — Krab понимает естественную речь и сам выбирает что делать (LLM routing). Примеры:

- `напомни мне завтра в 18:00 принять таблетки` → создаст Apple Reminder
- `запиши встречу с Иваном 12 мая в 15:30 на час, локация Aliсante` → Apple Calendar event
- `найди все письма с отчётами за апрель` → ищет в Gmail
- `что я обсуждал с Иваном на прошлой неделе?` → ищет в memory + Telegram history
- `сделай brief по рынку BTC` → AI ответ
- `прочитай эту статью и расскажи` (с reply на forwarded ссылку) → web fetch + summary
- `посмотри Sentry за последний час` → tool call

**Команды `!` тоже работают** — они формальные триггеры, всегда обходят routing logic. Используй когда хочешь точное поведение без ambiguity.

### 1.2 DM с **командами свёрма** → `@p0lrdp_AI` (Traders), `@p0lrdp_worldwide` (Coders), `@hard2boof` (Analysts), `@opiodimeo` (Creative)

**Только обычным текстом, без `!команд`.** Каждый team account отвечает в своей роли:
- Traders — рынок, риски, торговля
- Coders — код, архитектура, DevOps
- Analysts — исследования, OSINT, факты
- Creative — идеи, копирайтинг, нейминг

Внутри ответа любая команда может **делегировать другой**:
> `[DELEGATE: analysts] проверь доходность за прошлый квартал`

→ Krab перехватит, запустит round Analysts со seed-вопросом, вернёт результат.

### 1.3 Группы (мульти-юзер) → главный Krab `@yung_nagato` в группе

**По умолчанию НЕ отвечает на обычный текст** — smart routing (chat policy) фильтрует. Чтобы получить ответ:

- **Mention**: `@yung_nagato что думаешь?` — гарантированный ответ
- **Reply**: ответь на любое сообщение Krab → отвечает
- **`!команда`** в любом виде → отвечает

> Smart routing настраивается per-chat: `!chatpolicy show` / `!chatpolicy set <silent|cautious|normal|chatty>` /
> `!chatpolicy threshold <0.0–1.0>`. Для каждой группы можно подобрать уровень отзывчивости.

### 1.4 Группа **🐝 Krab Swarm** (forum) → особый случай

Любое сообщение в этой группе автоматически видят **все 4 team accounts** (traders/coders/analysts/creative). Без mention, без `!команды`. Каждая команда отвечает в своей роли. Это пространство для:

- Мульти-перспективного брейнсторма
- Cross-team дискуссии
- Демо/тест swarm

`@yung_nagato` (главный Krab) тоже там, но обычно молчит — он arbitrator на случай когда нужно рестарт пайплайна.

---

## 1.5 Decision tree — куда писать задачу?

| Задача | Канал | Почему |
|--------|-------|--------|
| Быстрый вопрос / brief / поиск | DM `@yung_nagato` | 1 LLM call, routing сам выберет tool |
| Код: review / архитектура / DevOps | DM `@p0lrdp_worldwide` (Coders) | Одна роль, один call, быстро |
| Рынок, риски, трейдинг | DM `@p0lrdp_AI` (Traders) | Одна роль, один call |
| Исследование / OSINT / факты | DM `@hard2boof` (Analysts) | Одна роль, один call |
| Идеи, нейминг, копирайтинг | DM `@opiodimeo` (Creative) | Одна роль, один call |
| Мнение 4 ролей сразу (brainstorm) | В группу 🐝 Krab Swarm — любой текст | 4 LLM calls, diverse perspectives |
| Team-round (3 роли одной команды) | `!swarm coders <тема>` в DM Краба | 3 LLM calls (roles внутри team) |
| Долгая задача с трекингом | `!swarm task create <team> <title>` | persistent, survive restart, приоритеты |
| Глубокий веб-ресёрч | `!swarm research <тема>` | многошаговый пайплайн + sources |
| Инициировать дискуссию команд | Пишешь в топик конкретной команды в 🐝 | Команда читает свой топик, остальные — нет |
| Форсировать delegate | В ответе роли: `[DELEGATE: analysts] <task>` | Авто-перехват Krab, run sub-round |

### Сценарии

**"Нужен brief по рынку BTC прямо сейчас"**
→ DM `@yung_nagato` или DM `@p0lrdp_AI`: `brief по BTC за сегодня, ключевые уровни`
→ 1 LLM call, ответ за ~10 сек

**"Хочу 4 разных угла на идею"**
→ В группу 🐝 Krab Swarm: `у меня идея нового продукта X — что думаете?`
→ Traders (рыночный риск), Coders (техн. реализация), Analysts (данные/конкуренты), Creative (позиционирование) — 4 параллельных ответа

**"Хочу инфра-баг разобрать за 2 мин"**
→ DM `@yung_nagato`: `смотри лог [вставь], что не так?` (или reply на log msg в чате)
→ Krab вызывает tool (Sentry / log fetch), ответ через ~15 сек

**"Нужен месячный план разработки"**
→ `!swarm task create coders месячный план разработки Q2`
→ Видишь в board, можешь назначить приоритет, команда обработает, артефакт прикрепится

**"Code refactor proposal + metrics"**
→ DM `@p0lrdp_worldwide` (Coders): `предложи рефакторинг модуля X`
→ Если в ответе: `[DELEGATE: analysts] измерь impact рефакторинга по метрикам` — Krab авто-запустит Analysts round

**"Brainstorm naming + валидация данными"**
→ DM `@opiodimeo` (Creative): `придумай 5 имён для фичи Y`
→ Добавь `[DELEGATE: analysts] проверь узнаваемость через поиск` в промпт или дождись delegate из ответа Creative

---

## 2. Свёрм — мульти-агентные обсуждения

### 2.1 Запуск из DM с Krab (yung_nagato) или из группы

```
!swarm <команда> <тема>          — раунд одной команды
!swarm <тема>                    — дефолтный room (analyst→critic→integrator)
!swarm loop [N] <тема>           — итеративный режим (N раундов)
!swarm <команда> loop [N] <тема> — итеративный для команды
!swarm research <тема>           — глубокий веб-ресёрч
```

Команды (псевдонимы поддерживаются):
- `traders` / `трейдеры`
- `coders` / `кодеры`
- `analysts` / `аналитика` / `аналитики`
- `creative` / `креатив`

Примеры:

```
!swarm coders проверь архитектуру модуля X, найди 2-3 риска
!swarm analysts loop 3 что обсуждалось в Sentry за неделю → выводы
!swarm research квантовые компьютеры в трейдинге 2026
```

### 2.2 Cost matrix — сколько квоты тратит каждый канал

| Канал / действие | LLM calls | Когда использовать |
|------------------|-----------|--------------------|
| DM `@yung_nagato` обычный текст | 1 | Всё простое: вопрос, поиск, tool, recall |
| DM `@<team_account>` обычный текст | 1 | Одна роль, быстрый ответ |
| `!swarm coders <тема>` в DM Краба | ~3 | Team round: 3 роли внутри команды (например Coders = architect + reviewer + devops) |
| Текст в группу 🐝 Krab Swarm | 4 | Все 4 team listeners реагируют одновременно |
| `!swarm <team> <тема>` в группу 🐝 | ~3 + 0 | Только один team round (остальные listeners не реагируют на `!` команды) |
| `!swarm research <тема>` | 5–10+ | Многошаговый пайплайн (поиск → анализ → синтез) |
| `[DELEGATE: <team>]` в ответе | +1–3 | Авто sub-round делегированной команды |
| `!swarm loop N <тема>` | N × 3–4 | Итеративные раунды |

> **Правило экономии:** если нужен один ответ — DM к конкретному агенту. Группу 🐝 Krab Swarm используй только когда реально нужны 4 perspective одновременно.

### 2.3 Task Board — когда использовать vs прямой `!swarm`

**Прямой `!swarm`** — одноразовый round, ответ нужен прямо сейчас, не нужен трекинг:
```
!swarm coders проверь архитектуру модуля X, найди риски
```
→ Получаешь ответ, всё. Не persist после рестарта Krab.

**`!swarm task`** — долгая задача, нужен трекинг, приоритеты, артефакты, survive restart:

```bash
# Создать задачу
!swarm task create coders реализуй авто-резерв инфры на S3

# Посмотреть доску
!swarm task board              # → видишь задачу в pending

# Форсировать назначение если нужно
!swarm task assign <id>

# Время идёт, команда обрабатывает в фоне...

# Проверить прогресс
!swarm task status <id>        # → детали + прикреплённые артефакты

# Закрыть когда принято
!swarm task done <id>

# Очистка доски
!swarm task clear              # удаляет done/failed
```

**Когда НЕ нужен task board:**
- Micro-вопрос ("что такое WAL?") → DM Краба
- Single-shot ответ ("придумай 3 названия") → DM Creative
- Чисто диалог / уточнение → обычный чат

#### Task Board — команды

```
!swarm task board                — Kanban-вид
!swarm task list [team]          — задачи (опц. фильтр по team)
!swarm task create <team> <title> — создать
!swarm task done <id>            — закрыть как done
!swarm task fail <id>            — закрыть как failed
!swarm task status <id>          — детали
!swarm task priority <id> <low|medium|high|critical>
!swarm task count                — счётчик
!swarm task clear                — cleanup done/failed
```

Чем хорош task board: tasks **persist между сессиями**, есть приоритеты, статусы, артефакты прикрепляются автоматически.

### 2.4 Cross-team delegation — когда и зачем

В **любой роли** во время round'а команда может вписать в свой ответ:

```
[DELEGATE: analysts] нужны цифры по аудитории конкурента X
```

→ Krab regex-парсит → запускает round Analysts с этой темой → результат вмерживается в финальный ответ. Полностью автоматически.

**Когда delegate полезен:**
- Роль обнаружила prerequisite от другой команды (Coders нашли баг производительности → `[DELEGATE: analysts] измерь throughput до/после`)
- Dual approach: Creative придумывает → Analysts валидирует данными
- Последовательная цепочка: Traders оценивают риск → `[DELEGATE: coders] как технически хеджировать?`

**Когда delegate НЕ нужен:**
- Линейная задача, которую одна команда закроет полностью
- Micro-вопросы (overhead выше пользы)
- Когда ты сам можешь просто написать второй команде в DM

### 2.5 Полезные команды для свёрма

```
!swarm teams       — список команд + описания
!swarm info <team> — детали роли
!swarm stats       — статистика всех команд
!swarm summary     — сводка активности
!swarm memory      — память свёрма (последние раунды)
!swarm report      — markdown-отчёты
!swarm setup       — настройка Forum Topics в текущей группе
```

---

## 3. Apple ecosystem — Reminders, Calendar, Notes

Krab пишет в Apple Reminders/Calendar/Notes напрямую через `osascript` (macOS automation).
Включается через env `KRAB_MCP_APPLE_WRITE_ENABLED=1` (уже включено).

### 3.1 Естественный язык — основной способ

```
напомни мне принять таблетки завтра в 18:00
напомни через час позвонить маме
запиши встречу 14 мая 18:20 на час с доктором, Quironsalud
создай заметку в Apple Notes с этим текстом → "Идея для проекта X..."
```

Krab парсит → распознаёт время/дату → создаёт Reminder/Event/Note → отвечает что готово.

### 3.2 `!` команды если хочешь точно

```
!remind <время> <текст>          — Apple Reminder
!remind list                     — все pending
!remind cancel <n>               — отменить N-й
!schedule <time> <текст>         — отложенное Telegram-сообщение
```

---

## 4. Голос — Krab Ear

Krab Ear — отдельное macOS приложение (Swift) в menu bar. После запуска `Krab Ear.app` иконка появляется в верхней панели справа.

### 4.1 Hotkeys

| Шорткат | Действие |
|---------|----------|
| **Cmd+Shift+\\** (примерно, см. Settings) | **Toggle диктовки** — start/stop запись + транскрипция |
| Cmd+Shift+T | Перевод выделенного текста |
| Cmd+Shift+P | Quick preset (быстрая обработка) |
| Cmd+Shift+B | Bookmark момента |
| Cmd+Shift+R | Quick Replace (замена слова в clipboard) |

### 4.2 Mode toggle vs hold

В Settings → Hotkey можно переключить:
- **toggle** (default): нажал — запись пошла, нажал ещё раз — стоп + транскрипция
- **hold**: держишь шорткат — запись, отпустил — транскрипция

### 4.3 Whisper модель

- `mlx-community/whisper-large-v3-mlx` (MLX optimization для M-серии)
- Latency ~3.4s warmup при запуске
- GigaAM (русский) тоже warm — fallback если Whisper не справляется

---

## 5. AI-помощь и поиск

### 5.1 Естественные

```
найди в тг сообщения от Ивана за прошлую неделю
посмотри что в моём календаре завтра
расскажи что было в чате X за последние 50 сообщений
```

### 5.2 `!` команды

```
!ask <вопрос>             — AI ответ в этом чате
!search <запрос>           — AI поиск через Brave + summary
!search --raw <запрос>     — сырые результаты (без summary)
!summary [N]               — recap последних N сообщений в чате
!catchup                   — алиас !summary 100
!translate <текст>         — перевод
!report [daily|weekly]     — отчёт по активности
!define <слово>            — определение
!yt <url>                  — транскрипция YouTube
!news [тема]               — актуальные новости
!ocr                       — распознать текст из фото (reply)
```

---

## 6. Системное и инфра

### 6.1 Health и статус

```
!status        — runtime + последний роут модели
!health        — расширенный health-check
!sysinfo       — системная информация
!quota         — статус квот (codex/gemini/vertex)
!costs         — отчёт по расходам
!digest        — weekly digest сразу
```

### 6.2 Модели и роутинг

```
!model                — текущая модель
!model list           — все доступные
!model switch <name>  — сменить primary
!reasoning low|medium|high — глубина reasoning
```

### 6.3 Переводчик (full suite)

```
!translator status              — настройки
!translator on / off            — вкл/выкл
!translator lang es-ru          — пара языков
!translator auto                — авто-определение
!translator mode bilingual|auto_to_ru|auto_to_en
!translator session start/stop  — live trial
```

### 6.4 Заметки и закладки

```
!memo [текст]              — заметка в текущем чате
!memo list / del <n>
!note <текст>              — быстрая заметка
!bookmark / !bm [url]      — закладка (из reply или URL)
!bm list / del <n>
!export [формат]           — экспорт заметок/закладок
```

### 6.5 Чат и группа

```
!react <emoji>             — реакция на reply
!afk [причина]             — режим отсутствия
!silence on|off            — режим тишины
!notify on|off             — управление уведомлениями
!chatpolicy show           — политика отзывчивости
!chatpolicy set normal     — стандартный уровень
!chatpolicy threshold 0.4  — порог LLM intent
```

---

## 6.6 SkillCurator — auto-improve prompts свёрма

Krab умеет анализировать собственные swarm prompts и предлагать улучшения
на основе реальных артефактов прошлых раундов:

```
venv/bin/python scripts/skill_curator_analyze.py --team coders   # одна команда
venv/bin/python scripts/skill_curator_analyze.py                 # все 4
venv/bin/python scripts/skill_curator_analyze.py --dry-run       # без LLM call
```

Output → markdown отчёт в `~/.openclaw/krab_runtime_state/skill_curator_reports/`
с 3-5 предложениями per team (clarity / structure / delegation hints / output format).
Manual approval: ты сам решаешь применять или нет — ничего не auto-apply.

## 6.7 Krab Ear glossary — учим Whisper доменным терминам

Если Whisper стабильно ошибается на каком-то слове (например "битвовка"
вместо "диктовка") — добавь его в biased prompt:

```bash
# Файл: ~/Library/Application Support/KrabEar/auto_glossary.json
{"terms": ["диктовка", "Krab", "свёрм", "transcription", ...]}
```

Backend перечитает glossary автоматически на следующей транскрипции (TTL 6h).
Сейчас в glossary 37 terms (Krab/swarm/codex-cli/Whisper/MLX/Quironsalud/etc).

---

## 7. Когда что-то не работает — чек-лист

1. **Krab вообще жив?** — `!status` или DM "проверка связи"
2. **Не отвечает в группе?** — попробуй `@yung_nagato ...` или `!ask ...`
3. **Hotkey диктовки не работает** — Krab Ear menu bar иконка есть? Если нет — `open "Krab Ear.app"`
4. **Reminder/Calendar tool failed** — проверь `KRAB_MCP_APPLE_WRITE_ENABLED=1` в `.env`
5. **Swarm молчит** — `!swarm teams` для проверки что 4 команды зарегистрированы
6. **Перезапуск** — `new Stop Krab.command` → подождать 3-5 сек → `new start_krab.command`. Это restart Krab + Krab Ear (Session 40 фикс — KE LaunchAgents теперь auto-bootstrap).

---

## 8. TL;DR — самое нужное

| Хочу… | Как |
|-------|-----|
| Напоминалку | DM Krab: `напомни …` |
| Календарь event | DM Krab: `запиши встречу …` |
| AI ответ в группе | `@yung_nagato …` или `!ask …` |
| Брейншторм | `!swarm <team> <тема>` или в группу 🐝 Krab Swarm любой текст |
| Конкретная роль | DM с team account (analysts → @hard2boof и т.д.) |
| Делегировать | в ответе роли вписать `[DELEGATE: <team>] <task>` |
| Task tracking | `!swarm task create <team> <title>` + `!swarm task board` |
| Голос → текст | Cmd+Shift+\ (toggle hotkey, см. KE Settings) |
| Перевод | `!translate <text>` или Cmd+Shift+T в KE |
| Отчёт за неделю | `!digest` или `!report weekly` |
| Что я просил вчера | DM Krab: `что я просил тебя вчера?` (memory recall) |

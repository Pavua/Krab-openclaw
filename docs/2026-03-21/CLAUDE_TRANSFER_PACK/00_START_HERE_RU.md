# Start Here — Передача В Новый Чат — 21.03.2026

## Что это

Это главный навигатор для передачи проекта в новый чат.

Если ты продолжаешь работу:

- в новом чате Codex
- в Claude
- в любом другом сильном агенте

то начинай именно с этого файла.

## Самый простой вариант

Если хочешь сделать передачу максимально быстро и без лишних решений:

1. Прикрепи эти 4 файла:
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/START_HERE_RU.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/HANDOFF_PORTABLE_RU.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/KNOWN_AND_DISCUSSED_RU.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/CLAUDE_READY_FIRST_MESSAGE_RU.md`
2. Если есть место, дополнительно приложи:
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/LM_STUDIO_MCP_SETUP_RU.md`
3. В новый чат вставь текст из:
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/CLAUDE_READY_FIRST_MESSAGE_RU.md`

Этого уже достаточно, чтобы следующий агент стартовал без пересказа по памяти.

## Если лимит на вложения маленький

Минимально достаточно 2 файлов:

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/HANDOFF_PORTABLE_RU.md`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/KNOWN_AND_DISCUSSED_RU.md`

И короткого сообщения:

`Используй приложенные файлы как source of truth и не опирайся на старые пересказы.`

## Что именно в этих файлах

### `HANDOFF_PORTABLE_RU.md`

Главный подробный handoff:

- текущее состояние проекта
- что подтверждено живьём
- что менялось в коде
- чем проверено
- какой реальный блокер остался

### `KNOWN_AND_DISCUSSED_RU.md`

Защита от потери контекста:

- что уже обсуждали
- какие гипотезы уже проверены
- что не надо снова прогонять по кругу

### `CLAUDE_READY_FIRST_MESSAGE_RU.md`

Готовый первый месседж:

- без длинной ручной сборки
- с указанием порядка чтения
- с правильным фокусом следующего шага

## Текущая truth в двух строках

- проект живой, web/openclaw/voice/ear подняты, warmup truthful через `google-gemini-cli/gemini-3.1-pro-preview`, `active_tier=paid`
- главный текущий confirmed blocker: ordinary Chrome attach к default profile на Chrome `146.0.7680.154` блокируется политикой самого Chrome, это не просто permission prompt

## Что не забыть сказать новому агенту

- live progress проекта около `91%`
- baseline master-plan около `31%` и не равен live progress
- USER2 Codex MCP уже usable
- browser truth и owner/debug Chrome path уже разведены
- current browser blocker уже локализован, не надо снова крутить гипотезу “нажми approve ещё раз”

## Рекомендуемый порядок чтения для нового агента

1. `START_HERE_RU.md`
2. `HANDOFF_PORTABLE_RU.md`
3. `KNOWN_AND_DISCUSSED_RU.md`
4. `MASTER_PLAN_SOURCE_OF_TRUTH.md`
5. `MASTER_PLAN_VNEXT_RU.md`
6. `LM_STUDIO_MCP_SETUP_RU.md`

## Если продолжаешь именно в Claude

Лучший вариант:

1. Прикрепи 4 базовых файла из этой папки
2. Вставь текст из `CLAUDE_READY_FIRST_MESSAGE_RU.md`
3. В первом сообщении ничего не пересказывай своими словами сверх этого

Причина:

- пересказ руками почти всегда хуже уже собранного truthful handoff
- здесь уже отдельно вынесены и факты, и обсуждавшиеся развилки

## Где лежит вся папка

`/Users/pablito/Antigravity_AGENTS/Краб/docs/2026-03-21/`

Если хочешь передавать “пакетом”, можешь просто открыть эту папку и брать файлы оттуда.

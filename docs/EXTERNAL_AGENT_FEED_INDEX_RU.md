<!--
Индекс подачи контекста во внешние нейросети.
Нужен, чтобы владелец проекта не путался: что и куда отправлять, а что не отправлять.
-->

# Что скармливать во внешние нейросети

## 0) Общий базовый пакет (кормить всем)

1. `AGENTS.md`
2. `docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md`
3. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
4. `artifacts/context/next_chat_context_*.md` (самый свежий)

## 1) Если диалог в Antigravity (backend/telegram поток)

### Кормить (Antigravity)

1. `docs/ANTIGRAVITY_START_HERE.md`
2. `docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md`
3. `docs/ANTIGRAVITY_BACKLOG_V8.md`
4. `docs/ANTIGRAVITY_NEXT_SPRINTS_V8.md`
5. `docs/ANTIGRAVITY_REMAINING_V8.md`
6. `docs/parallel_execution_split_v8.md`
7. `config/workstreams/antigravity_paths.txt`
8. `config/workstreams/codex_paths.txt`

### НЕ кормить (Antigravity)

1. Полные большие логи на много экранов.
2. Длинные сырые diff целиком.
3. Любые секреты из `.env`.
4. Неактуальные архивы из `_trash/`.

## 2) Если диалог в Gemini 3 Pro (frontend/design поток)

### Кормить (Gemini 3 Pro)

1. `docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md` (текущий раунд)
2. `docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_RU.md` (предыдущий раунд, для справки)
3. `docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md`
4. `docs/CHAT_TRANSITION_PLAYBOOK_RU.md`
5. `src/web/index.html`
6. `docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md`
7. `artifacts/context/next_chat_context_*.md` (самый свежий)
8. `config/workstreams/gemini_design_paths.txt`

### НЕ кормить (Gemini 3 Pro)

1. Backend-ориентированные roadmap целиком.
2. Все файлы тестов подряд.
3. Сырые runtime-логи.

## 3) Если диалог в Nano Banana Pro (UI implementation поток)

### Кормить (Nano Banana Pro)

1. `docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md`
2. `docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md`
3. `src/web/index.html`
4. `docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md`
5. `artifacts/context/next_chat_context_*.md` (самый свежий)
6. `config/workstreams/nanobanana_ui_paths.txt`
7. (опционально) результат Gemini-дизайна как спецификацию.

### НЕ кормить (Nano Banana Pro)

1. Файлы с backend-логикой Python.
2. Полные legacy-документы.
3. Нерелевантные ветки roadmap.

## 4) Команды перед интеграцией обратно в Krab

1. `python3 scripts/check_workstream_overlap.py`
2. `python3 scripts/merge_guard.py`
3. `python3 scripts/merge_guard.py --full` (финальный прогон)

## 5) Актуальные промпты этого раунда (R5)

1. Backend окно (Gemini 3.1 Flash):
   - `docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R5_RU.md`
2. Frontend окно (Gemini 3.1 Pro):
   - `docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R5_RU.md`

## 6) Актуальные промпты следующего раунда

1. Backend окно (Gemini 3.1 Flash):
   - `docs/EXTERNAL_PROMPT_GEMINI3FLASH_BACKEND_R8_RU.md`
2. Frontend окно (Gemini 3.1 Pro):
   - `docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_R8_RU.md`

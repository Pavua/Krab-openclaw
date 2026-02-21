# MIGRATION.md

Обновлено: 2026-02-19  
Статус: Active (R0 source-of-truth consolidation)

Этот документ — быстрый и точный вход в проект для нового треда/агента.
Цель: не терять контекст, не дублировать функционал OpenClaw и держать единый план по экосистеме.

## 1. Что это за проект (суть)

1. Krab Core — Telegram userbot (Pyrogram) + локальная оркестрация.
2. OpenClaw — reasoning/tools/channels gateway.
3. Krab Ear — отдельный сервис уха/аудиовхода.
4. Krab Voice Gateway — отдельный voice/call backend.

Архитектурный принцип: **thin client**.
Krab не должен дублировать channel/tool runtime, если он уже есть в OpenClaw.

## 2. Каноничные файлы (Source of Truth)

### 2.1 Экосистема
1. `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP_ECOSYSTEM.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/HANDOVER.md`

### 2.2 Krab Core
1. `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/config/soul.md`

### 2.3 Krab Ear (канон в отдельном репозитории)
1. `/Users/pablito/Antigravity_AGENTS/Krab Ear/ROADMAP_KRAB_EAR.md`
2. `/Users/pablito/Antigravity_AGENTS/Krab Ear/docs/ROADMAP.md`

### 2.4 Krab Voice Gateway (канон в отдельном репозитории)
1. `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/ROADMAP_KRAB_VOICE_GATEWAY.md`
2. `/Users/pablito/Antigravity_AGENTS/Krab Voice Gateway/README.md`

### 2.5 OpenClaw ops policy
1. `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_CHANNELS_SKILLS_BASELINE_RU.md`

Важно:
1. `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP_KRAB_EAR.md` и
   `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP_KRAB_VOICE_GATEWAY.md`
   это mirror-pointer файлы (навигация), не канон.
2. Статусы Ear/Voice правятся только в их репозиториях.

## 3. Где мы сейчас (актуальный фокус)

### 3.1 Завершено (ключевое)
1. Очередь per-chat FIFO, forward/reply/author attribution.
2. Loop/repetition protection для stream и пост-санитайз.
3. Reaction + mood feedback слой.
4. Базовый OpenClaw ops контур и one-click скрипты prod/lab.
5. Source-of-truth R0 по roadmap синхронизирован.

### 3.2 В работе (наиболее важное)
1. Единый межпроектный контракт Krab <-> Ear <-> Voice.
2. Единый E2E smoke-runner для трёх проектов.
3. Agentic reliability: `plan -> execute -> verify -> self-critique`.
4. Model governance и cost policy без рестартов.
5. Automation map: где нужен n8n, где достаточно встроенного scheduler.

## 4. Порядок действий для нового агента

1. Прочитать:
   - `/Users/pablito/Antigravity_AGENTS/Краб/ROADMAP_ECOSYSTEM.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/HANDOVER.md` (верхние свежие секции)
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/codex_context_hygiene.md`
2. Проверить рантайм:
   - `openclaw status`
   - `./openclaw_ops_guard.command`
   - `./verify_project.command`
3. Работать маленькими инкрементами: код -> тест -> фиксы -> документирование.
4. После крупного шага сделать checkpoint:
   - `/Users/pablito/Antigravity_AGENTS/Краб/new_chat_checkpoint.command`

## 5. Контур OpenClaw: что делаем и чего не делаем

### 5.1 Делаем в Krab
1. Telegram userbot UX, персоны, owner-команды.
2. Бизнес-логика, state-менеджмент, orchestration.
3. Локальные fail-safe и runtime guards.

### 5.2 Делаем в OpenClaw
1. Skills/channels/tool execution.
2. Внешние коннекторы (Slack/Discord/Signal/iMessage и т.д.).
3. Централизованный reasoning/tool gateway.

### 5.3 Не дублируем
1. Не пишем второй раз те же каналы/инструменты внутри Krab.
2. Если capability уже есть в OpenClaw — интегрируем через gateway.

## 6. Контекст-гигиена и защита от 413

1. Порог переключения треда: правило `3 из 6` в
   `/Users/pablito/Antigravity_AGENTS/Краб/docs/codex_context_hygiene.md`.
2. Перед сменой треда всегда запускать:
   - `/Users/pablito/Antigravity_AGENTS/Краб/new_chat_checkpoint.command`
3. В новый тред переносить блок `[CHECKPOINT]` из созданного файла.

## 7. Рабочие one-click команды

1. Запуск ядра: `/Users/pablito/Antigravity_AGENTS/Краб/start_krab.command`
2. Жёсткий перезапуск ядра: `/Users/pablito/Antigravity_AGENTS/Краб/restart_core_hard.command`
3. Диагностика OpenClaw: `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_ops_guard.command`
4. Усиление PROD-профиля OpenClaw:
   `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_prod_harden.command`
5. LAB-профиль OpenClaw:
   `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_lab_beta.command`
6. Checkpoint для нового чата:
   `/Users/pablito/Antigravity_AGENTS/Краб/new_chat_checkpoint.command`
7. Bootstrap каналов/скиллов OpenClaw:
   `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`

## 8. Definition of Done для ближайшего этапа

1. Единый versioned schema-контракт для Krab/Ear/Voice зафиксирован в docs.
2. Один smoke-запуск поднимает и проверяет 3 сервиса end-to-end.
3. Для agentic задач есть verify-артефакт (тест/health/report) по умолчанию.
4. Решения по каналам/скиллам OpenClaw не конфликтуют с userbot-контуром Krab.

# ANTIGRAVITY TASK PACK R19 (2 окна: Frontend + Backend)

Дата: 2026-02-23  
Режим: параллельная разработка в 2 окнах Antigravity  
Цель: снять нагрузку с квоты Codex и ускорить стабилизацию Krab без ломки контрактов.

---

## Что прикладывать в Antigravity

Окно 1 (Frontend):

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R19_FRONTEND_RU.md`

Окно 2 (Backend):

- `/Users/pablito/Antigravity_AGENTS/Краб/docs/EXTERNAL_PROMPT_AG_R19_BACKEND_RU.md`

Важно:

1. В каждом окне прикладывать только свой файл.
2. Отправлять как есть, без сокращений.
3. В обоих окнах просить полный diff + команды проверки + фактический вывод.

---

## Единые правила для обоих окон

1. Ветка отдельная, unrelated файлы не трогать.
2. Комментарии/docstring в коде на русском.
3. Не менять секреты/ключи и `.env`.
4. Не ломать существующие API контракты.
5. По завершении вернуть:
   - `git diff --name-only`
   - список ключевых изменений
   - команды проверок и их результат
   - риски/ограничения

---

## Режим запуска (быстро)

1. Открыть 2 окна Antigravity.
2. Вставить в Окно 1 `EXTERNAL_PROMPT_AG_R19_FRONTEND_RU.md`.
3. Вставить в Окно 2 `EXTERNAL_PROMPT_AG_R19_BACKEND_RU.md`.
4. Дождаться завершения обоих окон.
5. Принести сюда оба отчёта целиком.

---

## Что делаем в Codex параллельно, пока Antigravity работает

1. Живой E2E по каналам (Telegram bot/userbot + iMessage):
   - подтверждаем, что нет утечек tool/scaffold в фактические ответы.
2. Жёсткая донастройка sanitizer-контуров:
   - проверяем реальные трассы `model crashed/no models loaded` и финальные user-facing fallback.
3. Приёмка результатов Antigravity:
   - ревью diff,
   - интеграция только безопасных изменений,
   - локальные targeted тесты и smoke,
   - фиксация в `HANDOVER.md`.

---

## Критерий успеха R19

1. Frontend: Web Panel даёт предсказуемые статусы/диагностику без флапов.
2. Backend: нет silent-failure и нет сырого тех-мусора в пользовательских каналах.
3. End-to-end: `live_channel_smoke` зелёный в проектных логах и без критичных ошибок в OpenClaw логах.

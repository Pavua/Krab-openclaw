# NEW CHAT BOOTSTRAP PROMPT

Ниже шаблон для старта нового окна без потери контекста.

---

Работаем в проекте Krab/OpenClaw.  
Текущий этап: стабилизация runtime (reliability-first), не добавление новых фич.

## Прочитай сначала
1. [docs/MIGRATION_HANDOFF_2026-03-02.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/MIGRATION_HANDOFF_2026-03-02.md)
2. [docs/OPEN_ISSUES_CHECKLIST.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPEN_ISSUES_CHECKLIST.md)
3. Свежий bundle: `artifacts/handoff_<timestamp>/runtime_snapshot.json`
4. Свежий bundle: `artifacts/handoff_<timestamp>/known_issues_matrix.md`
5. (если есть) `artifacts/handoff_<timestamp>/e1e3_acceptance_<timestamp>.json`
6. (если есть) `artifacts/handoff_<timestamp>/channels_photo_chrome_acceptance_<timestamp>.json`
7. Свежий `artifacts/handoff_<timestamp>/START_NEXT_CHAT.md`

## Ключевые проблемы
1. Telegram session lifecycle (`auth key not found`, sqlite I/O на stop).
2. `No models loaded` в каналах вне userbot.
3. `EMPTY MESSAGE` / `model crashed` в LM Studio.
4. `401 Unauthorized` в cloud path.
5. Зависание фото-пути на `Разглядываю фото...`.
6. Ошибки edit в Telegram (`MESSAGE_ID_INVALID`, `MESSAGE_EMPTY`).
7. Нестабильный отклик на голосовые/триггеры в группах.
8. Chrome relay и Krab Ear watchdog требуют детерминированного состояния.

## Что сделать первым
1. Проверить свежий bundle и актуальный `git status`.
2. Зафиксировать статус acceptance артефактов (`E1→E3`, `channels+photo+chrome`).
3. Доделать Telegram safe-stop + lifecycle lock.
4. Внедрить единый local autoload guard для всех каналов.
5. Добавить runtime endpoint’ы handoff/recover и расширить `health/lite`.
6. Закрыть empty stream / model crash fallback.
7. Прогнать unit/integration smoke и обновить handoff bundle.

## Ограничения
1. Ключи/токены — только masked в логах/доках.
2. Комментарии/докстринги в коде — на русском.
3. Сначала стабильность, потом расширение возможностей.

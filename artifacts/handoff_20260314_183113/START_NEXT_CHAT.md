# Старт следующего чата (Krab / OpenClaw)

Дата: `2026-03-14`

Этот пакет подготовлен в учетке `USER3` и нужен, чтобы без потерь продолжить разработку
в новом диалоге и/или другой macOS-учётке с оплаченной квотой.

## Краткий статус

- Runtime запущен в `USER3`.
- Порты живы: `:8080` (owner panel), `:18789` (OpenClaw), `:8090` (Voice Gateway).
- Voice Gateway поднят через fallback (нет прав на `.gateway.pid` и `gateway.log` в shared repo).
- Krab Ear поднят через fallback runtime binary, watchdog активен.
- Owner: `@yung_nagato`.
- Translator readiness: `READY`, Voice replies: `ON`.
- iPhone companion зарегистрирован: `device_id = iphone-dev-1`.
- Legacy `agents.defaults.thinkingDefault=auto` в `USER3` починен до `adaptive`, поэтому `:18789` снова healthy после controlled restart.
- Companion сейчас в `BOUND`, session = `vs_0b93dc247b1d`, но `current device binding status = pending` до первого real device connect.
- Delivery matrix = `TRIAL READY`, live trial preflight = `READY FOR TRIAL`.
- Push token по-прежнему отсутствует и это ожидаемо для free signing / первого trial.

## Что исправлено в коде

1) Экспорт onboarding packet больше не падает при `Permission denied` на общий `*_latest.json`.
   Теперь пишется `translator_mobile_onboarding_latest_{user}.json` и UI показывает фактический путь.
2) UI показывает путь к реально сохранённому onboarding packet + текст ошибки,
   если общий `latest` не обновился.
3) Документация обновлена про `ops latest` в multi-account.
4) Runtime-controls больше не пишут legacy `thinkingDefault=auto`: owner UI и backend нормализуют его в `adaptive`, совместимый с OpenClaw 2026.3.11.

Ветка: `codex/companion-runtime-adaptive-fix`  
Базовая сохранённая ветка: `codex/onboarding-export-fallback` (HEAD = origin, не потеряна)

## Артефакты и доказательства

- Скрин owner panel (export + updated UI):
  - `artifacts/krab-owner-panel-onboarding-export-2026-03-14.png`
- Скрин owner panel (companion ATTENTION):
  - `artifacts/krab-owner-panel-companion-attention-2026-03-14.png`
- Скрин owner panel (companion trial ready / session bound):
  - `artifacts/krab-owner-panel-companion-trial-ready-2026-03-14.png`
- Экспорт onboarding packet (USER3 fallback latest):
  - `artifacts/ops/translator_mobile_onboarding_latest_user3.json`
- Артефакт trial-ready snapshot (USER3):
  - `artifacts/ops/translator_mobile_trial_ready_user3_latest.json`
- Артефакт runtime alias-fix (USER3):
  - `artifacts/ops/openclaw_runtime_thinking_alias_fix_user3_latest.json`

## Следующий фокус

1) Запустить реальный iPhone companion через Xcode Free Signing (free Apple ID).
2) В приложении проверить `Health-check` и подключение к `http://<IP Mac>:8090`.
3) На живом устройстве зафиксировать first live subtitles/timeline для session `vs_0b93dc247b1d` или новой trial-session.
4) После device proof обновить handoff bundle свежим on-device evidence.

## Что приложить в новый диалог

Просто приложи папку этого handoff:

`/Users/Shared/Antigravity_AGENTS/Краб/artifacts/handoff_20260314_183113`

Если новый аккаунт Codex не видит skills, нужно перенести:

- `cp -a /Users/USER3/.codex/skills ~/.codex/`
- убедиться, что `context7` активен в `~/.lmstudio/mcp.json`

Дополнительно по желанию:
- `artifacts/ops/translator_mobile_onboarding_latest_user3.json`
- последние скриншоты (они уже в этом handoff)

## Важные напоминания

- Не выключать runtime на `pablito`, если он снова нужен; перед стартом здесь его нужно остановить.
- Для free signing PushKit токен не обязателен; даже без него `trial_ready`/`bound` уже достижимы, а device proof идёт следующим шагом.
- OAuth не удаляем и не обрезаем — все профили должны остаться.

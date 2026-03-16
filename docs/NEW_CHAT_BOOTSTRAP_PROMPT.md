# NEW CHAT BOOTSTRAP PROMPT

Ниже актуальный шаблон для старта нового окна по состоянию на `2026-03-16`.

Работаем в проекте Krab/OpenClaw.  
Текущая рабочая ветка: `codex/translator-finish-gate-user3`.  
Текущая ориентировочная готовность большого плана: `~52%`.

## Прочитай сначала

1. [docs/NEXT_CHAT_CHECKPOINT_RU.md](/Users/Shared/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md)
2. [docs/OPENCLAW_KRAB_ROADMAP.md](/Users/Shared/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md)
3. `artifacts/ops/translator_finish_gate_user3_latest.json`
4. `artifacts/ops/pre_release_smoke_latest.json`
5. `artifacts/ops/r20_merge_gate_latest.json`
6. Если есть свежий bundle: `artifacts/handoff_<timestamp>/START_NEXT_CHAT.md`
7. Если есть свежий bundle: `artifacts/handoff_<timestamp>/ATTACH_SUMMARY_RU.md`
8. Если есть свежий bundle: `runtime_snapshot.json`, `HANDOFF_MANIFEST.json`

## Что уже считается фактом

1. Для проекта есть два разных UI-контура: наша owner panel на `:8080` и официальный dashboard OpenClaw на `:18789`; операционную truth по проекту читаем прежде всего через `:8080`.
2. Текущая рабочая dev-учётка: `USER3`; `pablito` пока остаётся финальным acceptance/release-контуром.
3. Runtime на `:8080` жив; текущий live route сейчас идёт через `google-gemini-cli/gemini-3.1-pro-preview`, а `openai-codex/gpt-5.4` зафиксирован внутри `translator_finish_gate_user3_latest.json` как truth конкретного automation-run.
4. Свежий truthful artifact `translator_finish_gate_user3_latest.json` уже подтверждает автоматическую часть translator gate.
5. Gateway regression по translator-блоку проходит зелёно: `17 passed`.
6. iOS companion на `iPhone 14 Pro Max` собирается и ставится; текущий хвост упирается в device unlock/manual retest, а не в build/install.
7. Memory/amnesia bug уже закрыт на уровне кода и persisted cache:
   - `src/core/openclaw_workspace.py` читает свежий tail memory-файлов;
   - `src/openclaw_client.py` санирует старую chat-history/in-memory session;
   - `Repair Chat Memory Cache.command` вычищает накопленный reasoning-мусор из `history_cache.db`.
8. Пользователь отдельно сообщал о плавающем scaling-regression в iPhone companion; если он повторится, это нужно считать отдельным acceptance-риском.
9. `main` не трогаем до подтверждённого воспроизведения ключевых сценариев на `pablito`.
10. После закрытия translator-блока и финального flush live memory-session оптимально снова включить `Plan Mode` и спланировать следующую фазу уже от свежей truth-base.

## Что сделать первым

1. Проверить `git status --short --branch` и не трогать чужие незакоммиченные изменения.
2. Проверить live truth:
   - `curl http://127.0.0.1:8080/api/health/lite`
   - `curl http://127.0.0.1:8080/api/translator/readiness`
   - `curl http://127.0.0.1:8080/api/openclaw/model-routing/status`
   - `curl http://127.0.0.1:8080/api/ops/runtime_snapshot`
3. Прочитать свежие gate artifacts:
   - `artifacts/ops/translator_finish_gate_user3_latest.json`
   - `artifacts/ops/pre_release_smoke_latest.json`
   - `artifacts/ops/r20_merge_gate_latest.json`
4. Для translator-блока опираться прежде всего на артефакт gate и owner panel `:8080`, а не на пересказ из чата.
5. `Plan Mode` включать уже после закрытия translator gate и обновления handoff/docs.

## Ограничения

1. Комментарии и докстринги в коде должны оставаться на русском.
2. Не дублировать уже существующий функционал OpenClaw, если его можно честно вызвать или отобразить.
3. Не удалять `legacy antigravity`; он intentionally остаётся как отдельный квотный контур.
4. Все статусы и проценты в docs должны опираться только на код, тест, smoke или live acceptance.
5. Merge в `main` только после актуального release-gate и свежего handoff bundle.

## Короткий стартовый промпт

```text
Продолжаем Krab/OpenClaw в ветке codex/translator-finish-gate-user3.

Сначала прочитай:
1) NEXT_CHAT_CHECKPOINT_RU.md
2) OPENCLAW_KRAB_ROADMAP.md
3) translator_finish_gate_user3_latest.json
4) если есть свежий bundle: START_NEXT_CHAT.md и ATTACH_SUMMARY_RU.md

Текущее состояние на 2026-03-16:
- готовность проекта ~52%
- текущая dev-учётка = USER3, `pablito` оставляем финальным acceptance/release-контуром
- для проекта есть owner panel на :8080 и отдельный официальный dashboard OpenClaw на :18789
- current live route = google-gemini-cli/gemini-3.1-pro-preview
- свежий truthful artifact: `artifacts/ops/translator_finish_gate_user3_latest.json`
- автоматическая часть translator gate уже зелёная:
  - gateway regression = 17 passed
  - iOS build/install = ok
  - launch attempt = locked, то есть хвост сейчас не кодовый
- внутри этого артефакта route `openai-codex/gpt-5.4` относится к конкретному automation-run, а не к текущему live runtime
- memory/amnesia fix уже в коде и persisted cache:
  - `Repair Chat Memory Cache.command` уже вычистил reasoning-мусор из `chat_history:312322764`
- незакрытый остаток: короткий ручной `ru -> es` retest на iPhone 14 Pro Max после unlock
- нужно отдельно проверить, что `Recognition request was canceled` больше не всплывает после stop/start
- для полного closure амнезии нужен финальный flush уже загруженной live session у `pablito` (`!clear` или controlled restart)
- пользователь отдельно сообщал о плавающем scaling-regression в iPhone companion; если повторится, фиксировать как отдельный UX-блокер acceptance
- `main` не трогать до подтверждения на `pablito`
- после closure translator + amnesia хвостов оптимально сразу включить Plan Mode и планировать следующий блок уже без этих хвостов

Первый шаг:
1) проверить git status
2) проверить /api/health/lite, /api/translator/readiness, /api/openclaw/model-routing/status, /api/ops/runtime_snapshot
3) проверить `artifacts/ops/translator_finish_gate_user3_latest.json`
4) закрыть ручной translator retest и flush-нуть live memory-session
5) обновить handoff/docs
6) потом включить Plan Mode для следующей фазы
```

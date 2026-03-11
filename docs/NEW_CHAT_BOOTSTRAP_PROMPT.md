# NEW CHAT BOOTSTRAP PROMPT

Ниже актуальный шаблон для старта нового окна по состоянию на `2026-03-12`.

Работаем в проекте Krab/OpenClaw.  
Текущая рабочая ветка: `codex/handoff-bundle-polish`.  
Техническая база этой ветки: `codex/web-runtime-smoke-hardening @ 859cf05`.  
Текущая ориентировочная готовность большого плана: `~99%`.

## Прочитай сначала

1. Если приложен свежий bundle: `artifacts/handoff_<timestamp>/START_NEXT_CHAT.md`
2. Если приложен свежий bundle: `artifacts/handoff_<timestamp>/ATTACH_SUMMARY_RU.md`
3. Если работа продолжается на основной учётке: `artifacts/handoff_<timestamp>/PABLITO_RETURN_CHECKLIST.md`
4. [docs/NEXT_CHAT_CHECKPOINT_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md)
5. [docs/OPENCLAW_KRAB_ROADMAP.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md)
6. Если приложен свежий bundle: `runtime_snapshot.json`, `HANDOFF_MANIFEST.json`, `known_issues_matrix.md`

## Что уже считается фактом

1. Runtime primary в OpenClaw уже переведён на `openai-codex/gpt-5.4`.
2. Owner UI на `:8080` уже показывает truthful runtime/model/browser state, а не заглушки.
3. Truthful блок параллелизма уже реализован и подтверждён unit + browser smoke на изолированном `:18081`.
4. Release gate и merge-gate уже доведены до рабочего состояния.
5. `signal_alert_route` и web-based runtime smoke уже ужесточены под временную учётку `USER2`.
6. `pre_release_smoke --full --strict-runtime` на `USER2` теперь блокируется только по owner-only шагам, а не по ложным кодовым фейлам.
7. Git/push на временной учётке уже настроен и ветки восстановления уже лежат в `origin`.

## Что сделать первым

1. Проверить `git status --short --branch` и не трогать чужие незакоммиченные изменения.
2. Если работа идёт уже на `pablito`, сразу выполнить `Verify Live Parallelism On Pablito.command`.
3. Прочитать свежие `pre_release_smoke_latest.json` и `r20_merge_gate_latest.json` из bundle.
4. Снять с roadmap хвост live `:8080` по parallelism только после реального переподтверждения от владельца `pablito`.
5. Дальше решать, нужен ли ещё строгий owner/reserve Telegram E2E как финальный процентный хвост.

## Ограничения

1. Комментарии и докстринги в коде должны оставаться на русском.
2. Не дублировать уже существующий функционал OpenClaw, если его можно вызвать или честно отобразить.
3. Все статусы и проценты в docs должны опираться только на код, тест, smoke или live acceptance.
4. Merge в `main` только после smoke/e2e и актуального handoff bundle.

## Короткий стартовый промпт

```text
Продолжаем Krab/OpenClaw в ветке codex/handoff-bundle-polish.

Я приложил свежий handoff bundle. Сначала прочитай:
1) START_NEXT_CHAT.md
2) ATTACH_SUMMARY_RU.md
3) PABLITO_RETURN_CHECKLIST.md
4) NEXT_CHAT_CHECKPOINT_RU.md
5) OPENCLAW_KRAB_ROADMAP.md

Текущее состояние на 2026-03-12:
- готовность проекта ~99%
- technical baseline = codex/web-runtime-smoke-hardening @ 859cf05
- truthful parallelism UI уже реализован и подтверждён unit + изолированным browser smoke
- release gate зелёный, strict runtime smoke на USER2 блокируется только owner-only шагами
- последний реальный хвост перед финишем: live verify нового parallelism блока на основном :8080 после restart от владельца pablito

Первый шаг:
1) проверить git status и текущую ветку
2) если работа идёт на pablito — запустить Verify Live Parallelism On Pablito.command
3) затем продолжить с ближайшего незакрытого пункта roadmap
```

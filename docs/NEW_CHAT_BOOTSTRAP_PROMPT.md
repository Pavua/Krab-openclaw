# NEW CHAT BOOTSTRAP PROMPT

Ниже актуальный шаблон для старта нового окна по ветке `GPT-5.4 / userbot-primary`.

---

Работаем в проекте Krab/OpenClaw.  
Текущая ветка: `codex/gpt54-userbot-primary`.  
Текущая ориентировочная готовность большого плана: `~82%`.

## Прочитай сначала

1. [docs/NEXT_CHAT_CHECKPOINT_RU.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md)
2. [docs/OPENCLAW_KRAB_ROADMAP.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md)
3. (если уже собран) свежий `artifacts/handoff_<timestamp>/START_NEXT_CHAT.md`
4. (если уже собран) свежий `artifacts/handoff_<timestamp>/runtime_snapshot.json`
5. (если уже собран) свежий `artifacts/handoff_<timestamp>/known_issues_matrix.md`

## Что уже считается фактом

1. `openai-codex/gpt-4.5-preview` был ложным primary и падал `404`.
2. `GPT-5.4` уже зарегистрирован в runtime registry OpenClaw.
3. Live compat probe для `openai-codex/gpt-5.4` уже дал `READY`.
4. Runtime primary уже переведён на `openai-codex/gpt-5.4`.
5. Userbot использует общий workspace `~/.openclaw/workspace-main-messaging`.
6. ACL `owner / full / partial / guest` реализован.
7. Telegram Bot уже переведён в reserve-safe policy.

## Что сделать первым

1. Проверить `git status` и не трогать чужие незакоммиченные изменения.
2. Проверить controlled restart runtime / web panel.
3. Подтвердить, что `:8080` подхватил новый код web ACL и текущий routing.
4. Довести `browser/MCP readiness`.
5. Прогнать E2E:
   - owner message через userbot
   - emergency message через reserve Telegram Bot
6. Проверить консистентность `openclaw models status` после promotion `GPT-5.4`.

## Ограничения

1. Комментарии и докстринги в коде — на русском.
2. Не дублировать уже имеющийся функционал OpenClaw.
3. Сначала использовать существующие runtime/repair/diagnostics механизмы, потом писать новый glue-код.
4. Merge в `main` только после smoke/e2e.

## Короткий стартовый промпт

```text
Продолжаем Krab/OpenClaw в ветке codex/gpt54-userbot-primary.

Прочитай сначала:
1) docs/NEXT_CHAT_CHECKPOINT_RU.md
2) docs/OPENCLAW_KRAB_ROADMAP.md
3) если есть свежий bundle — artifacts/handoff_<timestamp>/START_NEXT_CHAT.md

Текущий факт:
- готовность плана ~82%
- GPT-5.4 уже READY в OpenClaw compat probe
- runtime primary уже переключён на openai-codex/gpt-5.4
- userbot сидит на общем workspace
- ACL owner/full/partial реализован
- Telegram Bot уже в reserve-safe policy

Следующий этап:
1) controlled restart runtime / web panel
2) browser/MCP readiness
3) E2E userbot
4) E2E reserve Telegram Bot
5) проверить консистентность openclaw models status после promotion
```

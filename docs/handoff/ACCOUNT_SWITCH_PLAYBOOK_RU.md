# Account Switch Playbook — 23.03.2026

Этот документ нужен как единая точка входа при переключении между `pablito`,
`USER2` и `USER3`, чтобы не восстанавливать состояние проекта по памяти.

## Канонические точки истины

- Pushable git-ветка и актуальные коммиты:
  `/Users/USER3/Antigravity_AGENTS/Краб`
- Рабочая ветка:
  `codex/fix-handle-shop-export`
- Актуальный HEAD на момент этого playbook:
  `91f96f8`
- Runtime/OpenClaw source-of-truth:
  `/Users/pablito/.openclaw/workspace-main-messaging`
- Свежий attach-ready bundle:
  `/Users/USER3/Antigravity_AGENTS/Краб/artifacts/handoff_20260323_233038`

## Что открыть первым в любом новом диалоге

1. `artifacts/handoff_20260323_233038/START_NEXT_CHAT.md`
2. `docs/handoff/SESSION_HANDOFF.md`
3. `docs/handoff/AUDIT_STATUS_2026-03-23_RU.md`
4. `artifacts/handoff_20260323_233038/PABLITO_RETURN_CHECKLIST.md`
5. `artifacts/handoff_20260323_233038/THIRD_ACCOUNT_BOOTSTRAP_RU.md`

## Если продолжаешь на `pablito`

- Основная цель: подтянуть ветку и проверить live/runtime truth уже от владельца
  боевого OpenClaw-контура.
- Короткий путь:

```bash
cd /Users/Shared/Antigravity_AGENTS/Краб
git fetch origin
git switch codex/fix-handle-shop-export
git pull --ff-only
```

- Затем смотри:
  - `artifacts/handoff_20260323_233038/PABLITO_RETURN_CHECKLIST.md`
  - `docs/handoff/SESSION_HANDOFF.md`
- Важно:
  - shared repo использовать как рабочую checkout-точку можно;
  - но за git-truth по этой итерации ориентируемся на ветку, уже запушенную из
    `USER3`.

## Если продолжаешь на `USER2`

- Основная цель: поднять helper-account контур и не перепутать его с `pablito`.
- Короткий путь:

```bash
cd /Users/Shared/Antigravity_AGENTS/Краб
git fetch origin
git switch codex/fix-handle-shop-export
git pull --ff-only
./Check\ New\ Account\ Readiness.command
```

- Затем смотри:
  - `artifacts/handoff_20260323_233038/THIRD_ACCOUNT_BOOTSTRAP_RU.md`
  - `docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`
  - `docs/handoff/AUDIT_STATUS_2026-03-23_RU.md`

## Если продолжаешь на `USER3`

- Канонический локальный repo уже находится здесь:
  `/Users/USER3/Antigravity_AGENTS/Краб`
- Короткий путь:

```bash
cd /Users/USER3/Antigravity_AGENTS/Краб
git fetch origin
git switch codex/fix-handle-shop-export
git pull --ff-only
```

- Затем смотри:
  - `docs/handoff/SESSION_HANDOFF.md`
  - `docs/handoff/AUDIT_STATUS_2026-03-23_RU.md`
  - `artifacts/handoff_20260323_233038/START_NEXT_CHAT.md`

## Что уже точно зафиксировано

- `#2 macOS Permission Audit` закрыт и пишет evidence-файлы:
  - `artifacts/ops/macos_permission_audit_user3_latest.json`
  - `artifacts/ops/macos_permission_audit_user3_20260323_232954Z.json`
- Full ecosystem cycle в `USER3` уже подтверждён:
  `Start Full Ecosystem.command` -> `kraab_running` -> `r20_merge_gate.py` ->
  `Stop Full Ecosystem.command`
- regression в `/api/openclaw/browser/start` закрыт
- multi-account launcher/handoff слой доведён до рабочего состояния

## Что ещё не считать полностью закрытым

- `#7 long-request transparency`
  Уже зелёный по тестам, но пока не зафиксирован отдельным чистым коммитом,
  потому что сидит в очень грязных файлах (`src/openclaw_client.py` и связанные тесты).
- `#8 Telegram transport voice/document`
- `#11 Inbox folder`
- `#12 global macOS hotkey`
- `#13 Hammerspoon window control`

## Анти-путаница

- Не ориентируйся на старые проценты готовности внутри старых bundle-файлов:
  часть export-шаблонов всё ещё может содержать устаревшие цифры вроде `~31%`.
- Для текущего truthful статуса опирайся на:
  - `docs/handoff/SESSION_HANDOFF.md`
  - `docs/handoff/AUDIT_STATUS_2026-03-23_RU.md`
  - этот playbook


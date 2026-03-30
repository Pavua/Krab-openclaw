---
name: krab-openclaw-workspace-prompt-curator
description: "Кураторить runtime prompt-слой OpenClaw для проекта `/Users/pablito/Antigravity_AGENTS/Краб`, включая `SOUL.md`, `USER.md`, `TOOLS.md` и соседние workspace-файлы в `~/.openclaw/workspace-main-messaging`, не создавая drift между repo-level docs и живым runtime truth. Использовать, когда нужно обновить persona, tool-инструкции, runtime memory scaffolding или согласовать prompt-слой Краба с фактическими возможностями и ограничениями системы."
---

# Krab OpenClaw Workspace Prompt Curator

Используй этот навык, когда меняется именно runtime prompt-layer Краба, а не просто repo-level документация.

## Канонические пути

- `~/.openclaw/workspace-main-messaging/SOUL.md`
- `~/.openclaw/workspace-main-messaging/USER.md`
- `~/.openclaw/workspace-main-messaging/TOOLS.md`
- repo-level truth: `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
- repo-level skill notes: `/Users/pablito/Antigravity_AGENTS/Краб/SKILLS.md`

## Рабочий цикл

1. Сначала проверь, что именно нужно обновить:
   - persona или tone;
   - tool usage contract;
   - runtime memory/bootstrap instructions;
   - ограничения по media/browser/transport.
2. Перед редактированием собери truth:
   - что реально умеет код и runtime;
   - что подтверждено smoke/evidence;
   - что уже зафиксировано в repo docs.
3. Изменяй минимальный слой:
   - не трогай `SOUL.md`, если меняется только tool checklist;
   - не трогай `USER.md`, если меняется только ops/runbook часть;
   - не дублируй один и тот же смысл в трёх файлах.
4. После изменения явно проверь, не появился ли drift между runtime prompts и repo-level источниками.

## Красные флаги

- Не превращай repo docs в копию runtime persona.
- Не добавляй в runtime prompt утверждения, которые не подтверждены кодом или проверкой.
- Не тащи developer-only Codex/Claude skills прямо в `SOUL.md` или `USER.md`.

## Рекомендуемые связки с другими skills

- `krab-workspace-truth-sync` для сверки repo и runtime.
- `krab-docs-maintainer`, если после prompt fix нужно обновить repo-level note.
- `krab-openclaw-auth-models-keeper`, если prompt-layer зависит от живого provider/auth состояния.

## Ресурсы

- Scope matrix по prompt-файлам: `references/prompt-scope-matrix.md`
- Checklist перед runtime prompt edit: `assets/workspace-curation-checklist.md`

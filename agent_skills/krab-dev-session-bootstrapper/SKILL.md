---
name: krab-dev-session-bootstrapper
description: "Безопасно стартовать новую Codex или Claude dev-сессию для проекта `/Users/pablito/Antigravity_AGENTS/Краб` на `pablito`, `USER2`, `USER3` или другой macOS-учётке, не перепутав текущего владельца runtime, профиль repo-level skills, writable-state shared repo и границу между developer layer и `~/.openclaw`. Использовать в начале нового чата, при переходе между учётками, перед делегированием задачи другому агенту и перед первым запуском sync/install."
---

# Krab Dev Session Bootstrapper

Используй этот навык, когда нужно быстро понять, в какой учётке и в каком режиме можно безопасно работать прямо сейчас.

Его задача не запустить всё подряд, а сначала определить безопасный режим: `code-only`, `dev-admin`, `handoff-only` или `runtime-admin`.

## Что проверить в первую минуту

- Кто текущая macOS-учётка: `whoami`
- Какой repo path открыт: `/Users/pablito/Antigravity_AGENTS/Краб` или shared path
- Какая ветка и есть ли грязное дерево
- Нужен ли только dev-layer или ожидается работа с runtime/OpenClaw
- Установлен ли нужный профиль repo-level skills в `~/.codex/skills` или `~/.claude/krab-agents`

## Режимы старта

1. `code-only`
   - писать код, тесты, docs, skills и launchers;
   - не трогать `~/.openclaw`, OAuth, browser profiles и live runtime ownership.
2. `dev-admin`
   - обновлять repo-level tools, Codex/Claude skills, install runbooks и shared repo policy;
   - по-прежнему не лезть в живой runtime без отдельной необходимости.
3. `runtime-admin`
   - работать с prompt/auth/runtime truth только если это действительно текущая задача и ownership понятен;
   - обычно для `pablito` или специально подготовленной helper-сессии.
4. `handoff-only`
   - если среда сомнительная, repo грязный не по задаче или ownership неясен;
   - сначала собрать truthful status и только потом что-то менять.

## Рабочий цикл

1. Определи текущую учётку и путь репозитория.
2. Проверь, нужна ли только разработка или ещё и runtime-доступ.
3. Выбери профиль skills:
   - `dev-tools` для большинства dev-сессий;
   - `runtime-admin`, если задача касается prompt/auth/runtime слоя через Codex/Claude;
   - `full`, если эта учётка часто используется как основная инженерная.
4. Убедись, что sync/install идёт только в:
   - `~/.codex/skills`
   - `~/.claude/krab-agents`
5. Не разрешай себе писать в `~/.openclaw`, если задача про dev-layer и нет явной причины лезть в runtime.
6. Перед делегированием другому агенту зафиксируй:
   - активную ветку;
   - текущий режим;
   - что считается запрещённой зоной записи.

## Минимальный safe-start protocol

- Для Codex:
  - `/Users/pablito/Antigravity_AGENTS/Краб/Sync Krab Agent Skills.command --profile dev-tools`
- Для Claude Code:
  - `KRAB_AGENT_PROFILE=dev-tools /Users/pablito/Antigravity_AGENTS/Краб/Start Claude Code With Krab Agents.command`
- Для полной инженерной учётки:
  - использовать профиль `full`
- Для OpenClaw runtime:
  - не применять этот sync-слой автоматически вообще

## Красные флаги

- Не путай `repo-level skills` и runtime prompt-layer.
- Не проси OpenClaw самоустанавливаться этими skills внутрь `~/.openclaw`.
- Не считай helper-учётку владельцем live runtime по умолчанию.
- Не запускай второй runtime поверх уже живого owner runtime.
- Не начинай сложную работу, пока не понял, чей сейчас branch, repo и acceptance responsibility.

## Рекомендуемые связки с другими skills

- `krab-multi-account-dev-coordinator` для межаккаунтной дисциплины.
- `krab-parallel-agent-orchestrator`, если задача сразу делится на несколько lane.
- `krab-branch-handoff-governor`, если нужна строгая веточная и handoff-дисциплина.
- `krab-openclaw-workspace-prompt-curator`, если после bootstrap видно, что задача реально про runtime prompts.

## Ресурсы

- Матрица выбора режима: `references/session-bootstrap-matrix.md`
- Быстрый чеклист новой сессии: `assets/new-session-checklist.md`

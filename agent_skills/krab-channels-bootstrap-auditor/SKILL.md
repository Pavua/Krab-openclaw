---
name: krab-channels-bootstrap-auditor
description: "Аудировать и при необходимости применять channels/skills bootstrap baseline для проекта `/Users/pablito/Antigravity_AGENTS/Краб` через `openclaw_channels_skills_bootstrap`. Использовать, когда нужно проверить каналы OpenClaw на безопасный baseline, получить markdown-отчёт по каналам, применить baseline `apply`, локализовать bootstrap drift или убедиться, что channel/skills слой не унаследовал старый мусор bootstrap-контура."
---

# Krab Channels Bootstrap Auditor

Используй этот навык для channels/skills bootstrap baseline. Он нужен, когда надо проверить или безопасно применить baseline по каналам, а не когда требуется общий runtime repair.

## Предусловие

```bash
cd /Users/pablito/Antigravity_AGENTS/Краб
```

## Ключевые точки входа

- `/Users/pablito/Antigravity_AGENTS/Краб/openclaw_channels_skills_bootstrap.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_channels_skills_bootstrap.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/artifacts/openclaw_bootstrap/`

## Рабочий цикл

1. Запустить audit и получить markdown-отчёт.
2. Понять, нужен ли apply baseline или достаточно аудита.
3. Если baseline применяется, повторно собрать отчёт после apply.
4. Явно показать, какие каналы затронуты.

## Ограничения

- Не применять baseline вслепую ко всем каналам, если задача узкая.
- Не путать bootstrap-аудит и runtime repair sessions/bootstrap мусора.
- После `apply` всегда перечитывать отчёт, а не полагаться на stdout.

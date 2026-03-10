# SKILLS.md

Этот документ описывает навыки проекта на уровне репозитория.
Он не заменяет runtime `SKILL.md` в `~/.openclaw/workspace-main-messaging`.

## Базовые инженерные навыки проекта

- Runtime repair и стабилизация OpenClaw
- Telegram userbot и reserve bot интеграция
- Управление маршрутами моделей и fallback-цепочками
- Диагностика browser relay / DevTools / MCP
- Разработка owner-oriented web control panel на `:8080`
- Smoke, e2e и merge-gate автоматизация

## Принцип применения навыков

- Сначала переиспользуем штатные возможности OpenClaw
- Кастомный код добавляем только там, где upstream-функционала нет
- Любой новый skill-подобный сценарий должен появляться вместе с проверкой и краткой документацией

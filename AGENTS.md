# AGENTS.md

> **AI Coding Assistant Instructions** - This document guides AI tools (GitHub Copilot, Cursor, Claude, etc.) on how to work with this codebase effectively.

---

## Project Overview

**Description**: Персональный AI-ассистент на базе OpenClaw с доступом через Telegram Userbot.

**Tech Stack**:
- **Framework**: React
- **Language**: JavaScript
- **Build Tool**: Not detected
- **Styling**: CSS Modules
- **State Management**: React Context API
- **Routing**: Not configured
- **Data Fetching**: fetch API
- **Forms**: Native forms
- **Validation**: Manual validation
- **Testing**: Not configured
- **Package Manager**: npm

---

## Quick Start

```bash
# Setup
npm install

# Development
npm run dev

# Build
npm run build

# Testing
npm run test

# Linting
npm run lint
```

---

## Project Structure

```
src/
├── __pycache__/
└── skills/
```

**Directory Purposes**:

- **`__pycache__/`** - Project-specific directory
- **`skills/`** - Project-specific directory

---

## Code Conventions

### General Guidelines

- **Language**: Use JavaScript for all files
- **Components**: Use functional components with hooks
- **File Naming**: PascalCase for components, camelCase for utilities

### Component Structure

```tsx
import { useState } from 'react';

export function UserCard({ user, onEdit }) {
  const [isExpanded, setIsExpanded] = useState(false);
  
  return (
    <div>
      {/* Component content */}
    </div>
  );
}
```

### Import Organization

```tsx
// 1. External dependencies
import { useState } from 'react';

// 2. Internal modules (use path aliases)
import { Component } from '../components/Component';

// 3. Types
import type { User } from '@/types';

// 4. Styles (if applicable)
import styles from './Component.module.css';
```

---

## Styling Approach

**Primary Method**: CSS Modules

- One CSS module per component
- Use camelCase for class names
- Leverage composition with `composes`

---

## State Management

**Approach**: React Context API

- Create context providers in `src/context/`
- Separate context by domain
- Use custom hooks to access context

---

## Data Fetching

**Method**: fetch API

- All API calls should be organized in the services layer
- Use proper error handling and loading states
- Leverage fetch API features for caching and optimistic updates

---

## Routing

**Router**: Not configured



---

## Forms & Validation

**Forms**: Native forms
**Validation**: Manual validation



---

## Testing

**Framework**: Not configured

### Conventions

- Test file location: Co-located with components
- Naming: `ComponentName.test.tsx`
- Focus on user behavior and integration tests

---

## Environment Variables

**Location**: `.env.local`

```bash
TELEGRAM_API_ID=[value]
TELEGRAM_API_HASH=[value]
TELEGRAM_SESSION_NAME=kraab
OPENCLAW_URL=http://127.0.0.1:18789
OPENCLAW_TOKEN=sk-nexus-bridge
LM_STUDIO_URL=http://192.168.0.171:1234
GEMINI_API_KEY=[value]
MAX_RAM_GB=24
LOG_LEVEL=INFO
```

**Note**: Never commit `.env.local` - use `.env.example` as template

---

## Available Scripts

- `npm run start` - Start production server
- `npm run test` - Run tests
- `npm run test:unit` - pytest tests/unit/ -v
- `npm run test:integration` - pytest tests/integration/ -v
- `npm run test:cov` - pytest tests/ --cov=src --cov-report=html
- `npm run lint` - Run linter
- `npm run format` - Format code with Prettier

---

## Path Aliases

No path aliases configured.

---

## AI Assistant Guidelines

### When Generating Code

1. **Follow existing patterns**: Match the style and structure in the codebase
2. **Use type safety**: Always use JavaScript types
3. **Use path aliases**: Import using configured aliases
4. **Match styling approach**: Use CSS Modules conventions
5. **Follow state management**: Use React Context API patterns

### When Refactoring

1. Preserve functionality
2. Maintain type safety
3. Update related tests
4. Follow established conventions

---

## Правила Работы Агентов

### Язык и стиль работы

- Всегда отвечать пользователю **строго по-русски**.
- Все новые docstring и комментарии в коде писать **по-русски**.
- Не останавливаться на анализе, если задачу можно дожать до рабочего состояния в текущем окне.

### Протокол выполнения

1. Сначала быстро собрать локальный контекст.
2. Затем вносить точечные изменения.
3. Сразу после правок запускать проверку:
   - unit/integration тесты;
   - при необходимости `py_compile`;
   - при необходимости live-проверку через браузер/DevTools.
4. В финале фиксировать:
   - что изменено;
   - как проверено;
   - что осталось.

### Экономия квоты и режим размышления

- Для тяжёлой архитектурной работы допустим `high`.
- Для интеграционных и стабилизационных задач предпочитать `medium`.
- При низком остатке квоты предпочитать:
   - короткие checkpoint-обновления;
   - минимальное число новых terminal sessions;
   - отсутствие повторных login-flow без новой гипотезы.

### Работа с terminal / exec

- Не плодить новые процессы без необходимости.
- Если возможно, использовать уже открытые terminal sessions.
- Если система предупреждает о лимите unified exec processes, считать это сигналом:
   - не запускать лишние фоновые команды;
   - не дублировать long-running процессы;
   - завершать или не переиспользовать тупиковые интерактивные сценарии.

### Правки файлов

- Любые ручные изменения файлов делать **только через `apply_patch`**.
- Не использовать `exec_command` для `apply_patch`.
- Не создавать огромные монолитные файлы без явной необходимости.
- Поддерживать модульную архитектуру: если файл становится труднообозримым, выносить отдельную ответственность в новый модуль.

### Git и ветки

- Рабочие ветки создавать с префиксом `codex/`.
- Не мержить в `main` ничего, что не прошло реальную проверку.
- Перед risky-этапами делать checkpoint в git.
- Если делается commit/push:
   - коммит должен отражать реально завершённый кусок;
   - не смешивать в одном commit несвязанные изменения.

### Перед переходом в новое окно

- Обязательно обновить checkpoint-документы, если состояние проекта существенно изменилось.
- Минимальный набор документов для handoff:
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/NEXT_CHAT_CHECKPOINT_RU.md`
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md`
- В checkpoint обязательно фиксировать:
   - текущий процент готовности;
   - последние рабочие изменения;
   - незакрытые баги;
   - статус OAuth / подписок;
   - следующий приоритет.

### Безопасность и подписки

- Не запускать непроверенные community OAuth-скрипты "как есть".
- Не хранить токены и секреты в markdown, `/tmp`, временных json или в репозитории.
- Для OpenClaw использовать только:
   - официальный flow, если он поддерживается;
   - либо явно помеченный неофициальный flow с отдельным предупреждением о рисках.
- Любой новый шаг по OAuth сначала документировать в:
   - `/Users/pablito/Antigravity_AGENTS/Краб/docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md`

### Текущие боевые приоритеты проекта

- Стабильность внешних каналов OpenClaw.
- Очистка `reply_to` мусора в iMessage.
- Выравнивание truth между userbot, OpenClaw channels, dashboard и web panel.
- Предсказуемое переключение local/cloud/vision маршрутов.
- После стабилизации transport/runtime — безопасное подключение подписок.

---

**Last Generated**: 2026-02-09  
**Auto-generated from**: package.json, tsconfig.json, and project structure

> 💡 **Tip**: Use the Agent Automation dashboard to regenerate this file after major changes.

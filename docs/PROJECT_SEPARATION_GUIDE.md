# Project Separation Guide — Krab vs Krab Ear

> Когда работаешь над Krab и Krab Ear в одном Claude Code чате, документация легко
> перемешивается. Этот guide описывает как держать каждый проект self-contained
> чтобы будущая сессия могла открыть только одну папку и иметь полный контекст.

---

## Два независимых проекта

| Проект      | Путь                                              | Что это                                       | Язык      |
|-------------|---------------------------------------------------|-----------------------------------------------|-----------|
| **Krab**    | `/Users/pablito/Antigravity_AGENTS/Краб/`          | Telegram userbot (pyrofork + OpenClaw)        | Python    |
| **Krab Ear**| `/Users/pablito/Antigravity_AGENTS/Krab Ear/`      | macOS voice assistant (Swift + Python STT)    | Swift+Py  |

**Они НЕ являются monorepo.** У каждого свой git repo, свои тесты, свой деплоймент.

---

## Что должно жить ОТДЕЛЬНО в каждом проекте

### Self-contained docs

Каждый проект должен иметь:

- `CLAUDE.md` — высокоуровневый guide для Claude Code (архитектура, команды,
  правила).
- `.remember/next_session.md` — handoff между сессиями. Текущее состояние,
  pending tasks, история wave'ов.
- `docs/` — детальные специации, дизайн-документы, troubleshooting.

**Правило**: handoff project'а X описывает ТОЛЬКО state и задачи project'а X.
Если в текущей сессии трогали оба проекта — каждое изменение пишется в handoff
правильного проекта.

### Тесты, скрипты, runtime

- `Краб/tests/`, `Краб/scripts/`, `Краб/venv/` — только Krab.
- `Krab Ear/KrabEar/`, `Krab Ear/native/`, `Krab Ear/tests/` — только Krab Ear.

---

## Что разделено на уровне shared infra

| Resource                       | Owner    | Cross-reference                                     |
|--------------------------------|----------|-----------------------------------------------------|
| `~/.openclaw/`                 | Krab     | KE через `telegram_bridge` шлёт `POST /api/notify` |
| `/Applications/Krab Ear.app`   | Krab Ear | Krab может observe но не модифицирует              |
| LaunchAgents (`~/Library/LaunchAgents/`) | shared | разные labels: `ai.krab.core` vs `ai.krab.ear.backend` |
| LM Studio model `gemma-4-e4b`  | KE primary, Krab fallback | оба могут load — testing ONE AT A TIME (RAM 36GB) |
| Coexistence monitor (`scripts/krab_ear_coexistence_monitor.py`) | Krab | следит за RSS обоих |

**Cross-reference правило**: упоминание в handoff допустимо ТОЛЬКО когда оно
описывает integration point (notify endpoint, shared model, coexistence).
Описание состояния другого проекта (что у KE сделано, что у Krab сломано) —
**НЕ** в handoff противоположного проекта.

---

## Команды по принадлежности

### Krab restart

```bash
# Полный (Stop + Start)
bash "/Users/pablito/Antigravity_AGENTS/new Stop Krab.command"
sleep 3
bash "/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Точечный (без KE):
launchctl kickstart -k gui/$UID/ai.krab.core
```

### Krab Ear restart

```bash
# Backend
launchctl kickstart -k gui/$UID/ai.krab.ear.backend

# Перед kill backend — проверить нет ли активного transcribe:
tail -n 30 ~/Library/Logs/krab-ear/err.log | grep frames/s
# Если есть прогресс-бары frames/s → НЕ убивать!
```

### OpenClaw (общий, влияет на оба)

```bash
openclaw gateway     # restart (НЕ SIGHUP)
```

---

## Workflow recommendations

### При открытии новой Claude Code сессии

**Если работа над Krab**: открой папку `/Users/pablito/Antigravity_AGENTS/Краб/`.
Claude увидит `CLAUDE.md` + `.remember/next_session.md` — полный контекст.

**Если работа над Krab Ear**: открой папку `/Users/pablito/Antigravity_AGENTS/Krab Ear/`.
Аналогично — самодостаточный контекст.

**Если работа касается обоих** (например, integration через notify endpoint): открой
тот проект, в котором будет main change. Cross-reference на другой делается через
ссылку на конкретный файл другого проекта (абсолютным путём).

### При закрытии сессии

В **end-of-session ritual**:

1. Identify в каждое изменение какому проекту относится.
2. Update `.remember/next_session.md` ТОЛЬКО того проекта, чьи changes были.
3. Если был cross-cutting change (например, codesign fix Krab Ear из Krab session) —
   запиши краткое уведомление в обоих handoff: "X сделано / X ждёт пользователя".
   Detailed inversion — в handoff owner'а изменения.

### Skill `krab-session-handoff`

Skill `/krab-session-handoff` готов автоматизировать этот ritual для Krab проекта.
Для Krab Ear — пока вручную.

---

## История слияний и почему этот guide появился

В сессиях 24-37 (April-May 2026) работа часто шла одновременно по обоим проектам:

- Wave 37 (09.05.2026) сделал основной fix для Krab (heartbeat, reply target, anaphora,
  tech-metaphors) и оставил **только manual codesign action** для Krab Ear.
- Предыдущие handoff смешивали "Main Krab — диагностика" с "Krab Ear: Variant B завершён"
  в одном файле — это и есть проблема которую этот guide устраняет.

**Going forward**: каждый handoff говорит ТОЛЬКО про свой проект. Cross-mention'ы — короткие
указатели "см. другой handoff", не дублирование информации.

---

## Известные integration points (cross-reference)

### Krab Ear → Krab (notify)

`Krab Ear/KrabEar/backend/telegram_bridge.py` отправляет `POST http://127.0.0.1:8080/api/notify`
на главный Krab userbot для уведомлений (например, окончание транскрипции).

### Coexistence monitor (Krab observes KE)

`Краб/scripts/krab_ear_coexistence_monitor.py` следит за RSS Krab + KE (LaunchAgent
`ai.krab.coexist-monitor`). При swap > 28GB → alert. См. Wave 22 changes (handoff
2026-05-08).

### Shared LM Studio model

Обa проекта могут использовать `gemma-4-e4b-it-mlx` (KE — для transcript rewrite,
Krab — fallback при cloud failure). **Правило**: тестировать ONE AT A TIME — параллельные
inference вызывают RAM overflow на 36GB M4 Max.

---

## Если этот guide устарел

Этот файл — living document. Если cross-reference structure меняется (например,
KE backend перейдёт на отдельный port вместо UNIX socket) — update'ни здесь.

Last updated: 2026-05-09 (Wave 37 close).

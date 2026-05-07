# Swarm Round Resume After Failure — Design Spec

**Статус:** Draft  
**Дата:** 2026-05-07  
**Ветка:** `claude/naughty-ellis-f5a58e`

---

## 1. Контекст и мотивация

Swarm-раунд — это последовательный pipeline из 3 ролей (analyst → critic → integrator для `analysts`;
market_analyst → risk_assessor → trader для `traders`, и т.д.), с возможным делегированием
([DELEGATE: team]) на другую команду через `SwarmBus.dispatch()`.

**Проблема:** если раунд падает посередине (quota exceeded, сетевая ошибка, restart Krab,
таймаут LLM), весь прогресс теряется. Пользователь видит либо silence, либо неполный результат.
Перезапуск `!swarm team topic` запускает раунд заново с нуля.

**Цель:** после восстановления Krab продолжить раунд с последней успешно выполненной роли,
сохранив накопленный контекст.

---

## 2. Use Cases

### UC-1: Quota exceeded mid-round

Пример: `traders` — market_analyst ответил, risk_assessor упал с HTTP 429 (Gemini quota).

**Ожидаемое поведение:** после сброса квоты (обычно 60 сек – 1 мин) Krab видит pending
round, автоматически продолжает с role_idx=1 (risk_assessor), инжектируя уже накопленный
контекст от market_analyst.

### UC-2: Krab restart mid-delegation

Пример: `analysts` role=critic выдал `[DELEGATE: coders]`, SwarmBus начал dispatch, и в этот
момент произошёл restart (обновление, краш).

**Ожидаемое поведение:** на старте Krab находит pending round в состоянии
`delegating: coders`, перезапускает только delegation и продолжает с инжектированным результатом.

### UC-3: Team account session crash

Пример: swarm_team_listener для `creative` упал из-за Pyrogram `AuthKeyUnregistered`.

**Ожидаемое поведение:** round помечается как `interrupted` (не `failed`). После
reconnect listener'а pending round подхватывается и продолжается.

### UC-4: Timeout in multi-role round

Пример: `coders` — developer role ушёл в таймаут (> 90 секунд, как установлено в cron).

**Ожидаемое поведение:** роль помечается `role_timeout`, retry через N минут только для
этой конкретной роли (не весь раунд).

---

## 3. Architecture Sketch

### 3.1 Pending Round State Format

Файл: `~/.openclaw/krab_runtime_state/swarm_pending/<round_id>.json`

```json
{
  "round_id": "analysts_1777411075_f3a2",
  "team": "analysts",
  "topic": "анализ рынка BTC",
  "created_at": "2026-05-07T12:00:00Z",
  "ttl_expires_at": "2026-05-08T12:00:00Z",
  "status": "interrupted",
  "attempt_count": 1,
  "max_attempts": 3,
  "cursor": {
    "role_idx": 1,
    "role_name": "critic",
    "delegation_pending": null
  },
  "accumulated_context": "...(сохранённый контекст ролей 0..role_idx-1)...",
  "completed_roles": [
    {
      "role_name": "analyst",
      "emoji": "🔬",
      "title": "Аналитик",
      "text": "...",
      "completed_at": "2026-05-07T12:00:15Z"
    }
  ],
  "delegation_tree": [],
  "initiator": {
    "chat_id": -1001234567890,
    "message_id": 42
  },
  "failure_reason": "quota_exceeded",
  "ab_id": null,
  "ab_variant": null
}
```

**Ключевые поля:**

- `cursor.role_idx` — с какой роли продолжать (0-based)
- `cursor.delegation_pending` — если прервалось во время delegation, хранит `{target_team, topic}`
- `accumulated_context` — полный контекст ролей 0..role_idx-1 (≤ 8 000 символов, clip)
- `completed_roles` — сохранённые ответы завершённых ролей (для финальной сборки результата)
- `attempt_count` / `max_attempts` — защита от infinite retry loop
- `ttl_expires_at` — TTL 24h по умолчанию

### 3.2 Checkpoint Writes

В `AgentRoom.run_round()` после каждой успешно завершённой роли:

```
# Существующий код:
round_results.append({...})
accumulated_context += f"[{emoji} {title}]:\n{clipped}\n\n"

# Добавить сразу после:
await _checkpoint_round(round_id, role_idx + 1, accumulated_context, round_results, ...)
```

`_checkpoint_round` — новая функция в `swarm_resume.py` (отдельный модуль).

Файл пишется **атомарно** (`tmp` → `rename`) чтобы не получить corrupt state при crash.

### 3.3 Lifecycle

```
create pending file (status=pending, cursor.role_idx=0)
    ↓
for each role:
    execute role
    on success → update cursor (role_idx += 1), append completed_roles, rewrite file
    on failure → update status=interrupted, failure_reason, rewrite file → BREAK
    ↓ (если все роли OK)
finalize → удалить pending file (или пометить status=done и хранить 1h для debug)
```

### 3.4 Resume Entry Points

**Entry point 1: Startup sweep** — `swarm_bus.resume_pending_rounds()` вызывается
в `userbot_bridge.py` в конце фазы инициализации (после `app.start()`).

Логика:
1. Сканирует `swarm_pending/*.json`
2. Фильтрует: `status in {interrupted, pending}`, `ttl_expires_at` не протух, `attempt_count < max_attempts`
3. Для каждого: `attempt_count += 1`, `status = resuming`, dispatch через `_run_resume_round()`
4. Успех → cleanup файла; провал → increment attempt_count, status = interrupted (снова)

**Entry point 2: Periodic sweep (optional, Phase 4)** — cron-job каждые 5 минут,
только если `KRAB_SWARM_RESUME_CRON_ENABLED=1`. Использует тот же sweep.
Важно: не конкурировать с Entry point 1 — используем file lock (`.lock` sidecar).

### 3.5 SwarmBus изменения

```python
# Новый метод в SwarmBus:
async def resume_pending_rounds(self, router_factory: Any) -> list[str]:
    """Startup sweep: подхватываем interrupted rounds."""
    ...
```

Отдельный модуль `src/core/swarm_resume.py` содержит:
- `SwarmPendingStore` — чтение/запись/удаление pending files
- `checkpoint_round()` — atomic write checkpoint
- `resume_round()` — re-instantiate AgentRoom с pre-loaded context
- `sweep_and_resume()` — startup sweep логика

### 3.6 TTL и cleanup

| Событие | TTL / действие |
|---------|----------------|
| Round успешно завершён | Удалить файл немедленно |
| `attempt_count >= max_attempts` | Пометить `status=exhausted`, уведомить owner, оставить 24h |
| `ttl_expires_at` протух | Sweep удаляет, пишет warning в лог |
| Startup и найден exhausted | Пропустить, не пытаться снова |

Дефолт: TTL = 24h, max_attempts = 3.
Конфиг: `KRAB_SWARM_RESUME_TTL_HOURS=24`, `KRAB_SWARM_RESUME_MAX_ATTEMPTS=3`.

---

## 4. Edge Cases

### EC-1: Протухший / противоречащий prompt

После рестарта topic может быть устаревшим (пример: "анализ BTC 2026-05-06" — вчерашнее).

**Решение:** Resume выполняется как есть. Если owner хочет отменить — `!swarm task fail <id>`
(см. EC-3). Добавить prefix в первый возобновляемый prompt: `[RESUME: продолжение раунда от {created_at}]`.
Это сигнализирует LLM что контекст был прерван.

### EC-2: Двойной рестарт → infinite loop

**Решение:** `attempt_count` инкрементируется при каждом resume. При достижении `max_attempts=3`
— `status=exhausted`, файл остаётся для аудита, но не retry-ится больше.
Startup sweep пропускает `status=exhausted`. Нотификация в Telegram (Saved Messages).

### EC-3: Owner отменил через `!swarm task fail <id>`

Task board уже имеет статус `failed`. Resume sweep должен проверять task board:
если существует task с `parent_round_id == round_id` и `status == failed` — пропустить resume.

**Связь:** при создании pending файла записываем `task_board_id` (если task был создан).
Sweep проверяет `swarm_task_board.get_task(task_board_id).status != "failed"` перед resume.

### EC-4: Параллельные раунды одной команды

Например, два параллельных `!swarm analysts` в разных чатах.

**Решение:** `round_id` содержит `chat_id` как компонент (`analysts_{chat_id}_{ts}_{nonce}`).
Sweep обрабатывает их независимо. File lock per `round_id` предотвращает двойной resume
одного и того же round_id.

### EC-5: Delegation прервалась в target team

Если `coders` был делегатом от `traders`, и именно `coders` упал — pending file хранит
`cursor.delegation_pending = {target_team: "coders", topic: "..."}`.

Resume: повторяет только delegation dispatch (не всю цепочку traders).
Глубина делегирования `_MAX_DEPTH=1` упрощает логику — делегат не может сам делегировать.

---

## 5. Migration / Backward Compatibility

### 5.1 Существующие артефакты

`swarm_artifacts/*.json` — **не трогаем**. Они пишутся только после успешного завершения раунда.
Pending state — отдельная директория `swarm_pending/`. Нет overlap.

### 5.2 Существующий task board

`swarm_task_board.json` и `SwarmTask` dataclass — **не изменяем структуру**.

Добавляем опциональное поле `resume_round_id: str = ""` в `SwarmTask` (backward compat:
from_dict использует `d.get("resume_round_id", "")`).

### 5.3 swarm_memory.py

Без изменений. Memory context инжектируется как обычно при resume — `swarm_memory.get_context_for_injection(team)`.

### 5.4 swarm_team_prompts.py

Без изменений. Resume передаёт те же роли из TEAM_REGISTRY.

### 5.5 AgentRoom.run_round сигнатура

Добавляем опциональный параметр `_resume_state: dict | None = None` — backward compat,
дефолт None. При None — поведение идентично текущему.

---

## 6. Implementation Phases

### Phase 1: Persist checkpoint (write-only)

**Цель:** захватить intermediate state, ничего не resume-ить.

Новые файлы:
- `src/core/swarm_resume.py` — `SwarmPendingStore`, `checkpoint_round()`

Изменения:
- `swarm.py / AgentRoom.run_round()` — вызывать `checkpoint_round()` после каждой роли
- `swarm.py / AgentRoom.run_round()` — создавать pending file в начале, удалять в конце при успехе

Тесты: write checkpoint → verify JSON schema, atomic rename (tmp file), TTL field set.

### Phase 2: Detection on startup

**Цель:** логировать pending rounds при старте, но не выполнять (observability first).

Изменения:
- `src/core/swarm_resume.py` — `sweep_pending_rounds()` (dry run, только log + Telegram notify)
- `userbot_bridge.py` — вызвать sweep в конце init

Owner видит в Telegram: `"⚠️ Найдено 1 незавершённых swarm-раунда: analysts/тема (прерван 5 мин назад)"`

### Phase 3: Resume execution

**Цель:** re-dispatch с cursor.

Изменения:
- `swarm_resume.py` — `resume_round()`: создаёт AgentRoom, передаёт `_resume_state`
- `AgentRoom.run_round()` — обрабатывает `_resume_state`: пропускает роли 0..cursor.role_idx-1,
  инжектирует `completed_roles` в `round_results` и `accumulated_context`
- `SwarmBus` — метод `resume_pending_rounds(router_factory)`
- Owner notify: `"✅ Swarm round analysts/тема возобновлён (роль 2/3)"`

### Phase 4: UI и cron sweep

**Цель:** operator visibility и periodic retry.

Новые endpoints:
- `GET /api/swarm/pending` — список pending rounds
- `POST /api/swarm/pending/{round_id}/cancel` — отменить pending
- `POST /api/swarm/pending/{round_id}/retry` — форсировать retry

Новые команды:
- `!swarm pending` — список незавершённых раундов
- `!swarm resume <round_id>` — форсировать resume
- `!swarm cancel <round_id>` — отменить

Cron sweep (опционально): `KRAB_SWARM_RESUME_CRON_ENABLED=1`, каждые 5 минут.

---

## 7. Tests Strategy

### Unit tests (pytest)

```python
# tests/unit/test_swarm_resume.py

class TestSwarmPendingStore:
    def test_checkpoint_creates_file(self)
    def test_checkpoint_atomic_write(self)          # проверяем tmp→rename
    def test_checkpoint_updates_cursor(self)
    def test_ttl_expiry_detection(self)
    def test_sweep_skips_exhausted(self)
    def test_sweep_skips_cancelled_task(self)

class TestResumeExecution:
    async def test_resume_from_role_idx_1(self)     # mock LLM: роль 0 OK, роль 1 crash → resume с idx=1
    async def test_resume_preserves_context(self)   # проверяем accumulated_context при resume
    async def test_max_attempts_exceeded(self)      # 3 краша → exhausted, нет 4-го retry
    async def test_concurrent_rounds_independent(self) # два round_id → независимые файлы

class TestDelegationResume:
    async def test_resume_delegation_pending(self)  # cursor.delegation_pending → dispatch only delegate
    async def test_delegation_not_nested(self)      # depth guard сохраняется при resume
```

### Integration test (mock LLM crash)

```python
# tests/integration/test_swarm_resume_e2e.py
async def test_round_crash_and_resume():
    # 1. Запустить round, mock роль 1 → raise QuotaError
    # 2. Verify: pending file создан, cursor.role_idx == 1
    # 3. Симулировать startup sweep
    # 4. Verify: round завершён, результат содержит все 3 роли
    # 5. Verify: pending file удалён
```

---

## 8. Open Questions

1. **Router serialization**: `router_factory` при resume должен создавать fresh router
   для правильного team context. Нужно убедиться что `_router_factory` паттерн из `swarm_bus.dispatch()`
   применим при startup (нет live router object — только factory).

2. **chat_id при startup**: pending file хранит `initiator.chat_id`. Нужно ли отправлять
   результат resume в тот же чат? Или только в Saved Messages / swarm forum topic?
   **Предложение:** отправлять в Saved Messages + swarm forum topic, не в оригинальный чат
   (он может быть давно "холодным").

3. **Cron sweep vs startup sweep race**: если Krab стартует и sweep запускается,
   а одновременно cron job пытается тот же round — нужен file lock. Реализовать через
   `.lock` sidecar файл (простой `O_EXCL open`)?

4. **Глубина delegation при resume**: при `delegation_pending` нужно ли заново запускать
   target team с нуля или тоже с checkpoint? Предложение: delegate-раунды не checkpoint-ируются
   (слишком сложно для MVP), просто retry полного delegate dispatch.

5. **Размер `accumulated_context`**: при 3 ролях с `role_context_clip=3000` каждая —
   до 9 000 символов. В pending файле clip до 8 000? Или хранить всё?
   Предложение: clip до 8 000 (покрывает 2.5 роли), с предупреждением в лог при truncation.

6. **Совместимость с A/B тестами (SkillCurator)**: при resume нужно ли использовать тот же
   `ab_variant` что был при оригинальном запуске? Предложение: да, сохранять `ab_id` и
   `ab_variant` в pending file и передавать при resume (детерминированность результатов).

---

## 9. Config Reference

| Переменная | Дефолт | Описание |
|-----------|--------|----------|
| `KRAB_SWARM_RESUME_ENABLED` | `1` | Включить/выключить feature |
| `KRAB_SWARM_RESUME_TTL_HOURS` | `24` | TTL pending round в часах |
| `KRAB_SWARM_RESUME_MAX_ATTEMPTS` | `3` | Макс. попыток resume |
| `KRAB_SWARM_RESUME_CRON_ENABLED` | `0` | Periodic sweep каждые 5 мин |
| `KRAB_SWARM_RESUME_CONTEXT_CLIP` | `8000` | Макс. символов accumulated_context в pending |
| `KRAB_SWARM_RESUME_NOTIFY` | `1` | Уведомлять owner о resume/failure |

---

## 10. File Layout

```
src/core/
  swarm_resume.py              — NEW: SwarmPendingStore, checkpoint_round, sweep_and_resume
  swarm.py                     — MODIFY: checkpoint calls в run_round, _resume_state param
  swarm_bus.py                 — MODIFY: resume_pending_rounds() метод
  
~/.openclaw/krab_runtime_state/
  swarm_pending/               — NEW directory
    analysts_-100123_1746700800_a3f2.json
    traders_-100456_1746700500_b1c8.json
    
tests/unit/
  test_swarm_resume.py         — NEW: 12+ unit tests

tests/integration/
  test_swarm_resume_e2e.py     — NEW: e2e mock crash → resume
```

---

*Spec ready for discussion. Implementation — отдельный шаг после approval.*

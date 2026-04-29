# Smart Message Routing — Design Spec (Session 26)

> **Контекст**: пользователь пожаловался что Krab отвечает «не всегда вовремя» — текущий regex-based trigger detector (`src/core/trigger_detector.py`, 203 LOC) пропускает контекст-aware cases и срабатывает на ложные положительные.
>
> **Constraints (max reasoning, Session 26)**:
> 1. **Точность > latency** — LLM call ~500ms приемлемо
> 2. **Per-chat policy** — каждый чат имеет свой config
> 3. **Negative learning (Option E)** — track owner reactions

## Архитектура: 5-stage pipeline

```
Message arrives
    ↓
[1. Hard gates] (regex, deterministic, instant)
    - DM private chat? → ALWAYS respond
    - @mention / explicit krab username → ALWAYS
    - reply_to_me → ALWAYS
    - explicit !command → ALWAYS
    - audio_message + GROUP_VOICE_FALLBACK_TRIGGER → ALWAYS
    ↓ (нет hard trigger)
[2. Per-chat policy check]
    - load policy from chat_response_policy.py JSON store
    - mode=silent → DROP early
    - mode=chatty → lower threshold
    - mode=normal/cautious → стандарт
    ↓
[3. Regex fast filter] (existing trigger_detector.py)
    - score >= 0.6 → respond
    - score < 0.2 → DROP
    - 0.2 ≤ score < 0.6 → escalate to LLM
    ↓
[4. LLM intent classifier] (NEW)
    - chat context: last 5-10 messages из chat_window_manager
    - per-chat persona hint (chatty/cautious режим в prompt)
    - local LM Studio (Qwen3.5 9B, already loaded)
    - cache by hash(text + context_5_msgs + chat_id)
    - YES → respond | NO → DROP
    ↓
[5. Post-response feedback]
    - после Krab ответа в группе — мониторить:
      - owner deletes Krab reply within 5 min → negative_signal++
      - owner reacts 👎 / 🤡 → negative_signal++
      - owner reacts 👍 / ❤️ / replies positively → positive_signal++
    - auto-adjust mode если negative_signals > N
```

## Components — 8 новых модулей

### 1. `src/core/chat_response_policy.py`

```python
@dataclass
class ChatResponsePolicy:
    chat_id: str
    mode: ChatMode  # SILENT | CAUTIOUS | NORMAL | CHATTY
    threshold_override: float | None = None  # ручной override
    negative_signals: int = 0
    positive_signals: int = 0
    last_negative_ts: float | None = None
    auto_adjust_enabled: bool = True
    blocked_topics: list[str] = field(default_factory=list)
    notes: str = ""

class ChatMode(str, Enum):
    SILENT = "silent"      # никогда не отвечать кроме hard gates
    CAUTIOUS = "cautious"  # threshold 0.7
    NORMAL = "normal"      # threshold 0.5 (default)
    CHATTY = "chatty"      # threshold 0.3
```

JSON store: `~/.openclaw/krab_runtime_state/chat_response_policies.json`

API:
- `get_policy(chat_id) -> ChatResponsePolicy` (default NORMAL)
- `update_policy(chat_id, **fields)`
- `record_negative_signal(chat_id, reason: str)`
- `record_positive_signal(chat_id, reason: str)`
- `_auto_adjust_if_needed(policy)` — auto-shift NORMAL→CAUTIOUS если negative_count > 5 за 24h

### 2. `src/core/llm_intent_classifier.py`

```python
@dataclass
class IntentResult:
    should_respond: bool
    confidence: float  # 0.0-1.0
    reasoning: str  # для audit log
    decision_path: str  # "regex_high" | "llm_yes" | "llm_no" | "policy_silent"
    cached: bool = False

async def classify_intent_for_krab(
    text: str,
    chat_context: list[ChatMessage],  # last 5-10 msgs
    chat_id: str,
    policy: ChatResponsePolicy,
    *,
    timeout_sec: float = 2.0,
) -> IntentResult:
    ...
```

**LLM prompt template** (структурированный JSON output):

```
Ты — детектор обращений к Telegram userbot Krab. Твоя задача — определить,
адресовано ли последнее сообщение к Krab или нет.

ПОЛИТИКА ЭТОГО ЧАТА: mode={mode}, threshold={threshold}
{policy_hint}  # "более активный режим" / "будь осторожен" / etc.

ИСТОРИЯ (последние {N} сообщений):
[1] {user1}: ...
[2] [Krab]: ...
[3] {user2}: ...
...
[{N}] {sender}: {текущее сообщение}

ЗАДАЧА: должен ли Krab ответить на сообщение [{N}]?

YES если:
- явное обращение (имя/прозвище/«ты»)
- followup на твой ответ выше
- вопрос риторический ко всем но релевантный для AI
- продолжение разговора между Krab и user

NO если:
- разговор между другими пользователями (не для тебя)
- off-topic (трейдинг/политика/etc если такой в blocked_topics)
- слишком короткий или мусорный
- эхо/forwarded без relevance
- ты только что ответил и user благодарит — не нужно doubling

Ответь СТРОГО в JSON (без markdown, без preamble):
{"should_respond": <true|false>, "confidence": <0.0-1.0>, "reasoning": "<краткое объяснение>"}
```

**Caching**:
- LRU cache, max 500 entries
- Key = SHA256(text + last 5 msg ids + chat_id + mode)
- TTL: 5 min (chat context может измениться)

### 3. `src/core/feedback_tracker.py`

```python
@dataclass
class KrabResponse:
    chat_id: str
    message_id: int
    sent_at: float
    decision_path: str  # из IntentResult
    confidence: float

class FeedbackTracker:
    def __init__(self, policy_store: ChatResponsePolicyStore):
        self._recent_responses: dict[str, KrabResponse] = {}  # message_id → response
        self._policy_store = policy_store

    def record_krab_response(self, response: KrabResponse) -> None:
        """Вызывается из userbot_bridge после успешного reply."""
        ...

    async def on_message_deleted(self, chat_id: str, message_id: int, deleted_by: int) -> None:
        """Telegram delete event — если Krab response, и owner удалил → negative."""
        if message_id not in self._recent_responses:
            return
        if deleted_by != OWNER_USER_ID:
            return
        self._policy_store.record_negative_signal(chat_id, reason="owner_deleted_krab_reply")

    async def on_reaction_added(self, chat_id: str, message_id: int, reaction: str, user_id: int) -> None:
        """Reaction события — track 👎/🤡 (negative) vs 👍/❤️ (positive)."""
        if user_id != OWNER_USER_ID:
            return
        if message_id not in self._recent_responses:
            return
        NEGATIVE_REACTIONS = {"👎", "🤡", "💩", "🖕"}
        POSITIVE_REACTIONS = {"👍", "❤️", "🔥", "🎉", "👏", "💯"}
        if reaction in NEGATIVE_REACTIONS:
            self._policy_store.record_negative_signal(chat_id, reason=f"owner_reaction_{reaction}")
        elif reaction in POSITIVE_REACTIONS:
            self._policy_store.record_positive_signal(chat_id, reason=f"owner_reaction_{reaction}")
```

### 4. `src/core/trigger_detector.py` (extend)

```python
@dataclass
class SmartTriggerResult:
    should_respond: bool
    decision_path: str  # "hard_gate" | "regex_high" | "regex_low_llm_yes" | "llm_no" | "policy_silent"
    confidence: float
    legacy_result: TriggerResult  # backward compat
    intent_result: IntentResult | None = None

async def detect_smart_trigger(
    text: str,
    chat_id: str,
    *,
    is_reply_to_me: bool,
    has_explicit_mention: bool,
    has_command: bool,
    chat_context: list[ChatMessage],
    chat_window_manager: ChatWindowManager,
    policy_store: ChatResponsePolicyStore,
    llm_classifier: LLMIntentClassifier,
) -> SmartTriggerResult:
    """5-stage pipeline."""
    # Stage 1: hard gates
    if has_command or has_explicit_mention or is_reply_to_me:
        return SmartTriggerResult(should_respond=True, decision_path="hard_gate", confidence=1.0)

    # Stage 2: per-chat policy
    policy = policy_store.get_policy(chat_id)
    if policy.mode == ChatMode.SILENT:
        return SmartTriggerResult(should_respond=False, decision_path="policy_silent", confidence=1.0)

    # Stage 3: regex fast filter
    legacy = detect_implicit_mention(text, chat_id)
    if legacy.score >= 0.6:
        return SmartTriggerResult(should_respond=True, decision_path="regex_high", ...)
    if legacy.score < 0.2:
        return SmartTriggerResult(should_respond=False, decision_path="regex_low", ...)

    # Stage 4: LLM intent (only on borderline 0.2-0.6)
    intent = await llm_classifier.classify_intent_for_krab(text, chat_context, chat_id, policy)
    threshold = policy.threshold_override or policy.mode.default_threshold()
    return SmartTriggerResult(
        should_respond=(intent.should_respond and intent.confidence >= threshold),
        decision_path=f"llm_{'yes' if intent.should_respond else 'no'}",
        confidence=intent.confidence,
        intent_result=intent,
    )
```

### 5. `src/userbot_bridge.py` (modify)

Заменить блок `has_implicit_trigger` (строки ~3795-3830) на вызов `detect_smart_trigger()`. Добавить post-response hook → `feedback_tracker.record_krab_response()`.

### 6. `src/handlers/commands/policy_commands.py`

Owner commands:

```
!chatpolicy                      — show current chat's policy
!chatpolicy show <chat_id>       — show specific chat
!chatpolicy set silent           — switch to silent mode
!chatpolicy set cautious|normal|chatty
!chatpolicy threshold 0.7        — manual override
!chatpolicy clear-blocked-topic <topic>
!chatpolicy add-blocked-topic <topic>
!chatpolicy stats                — view negative/positive signals
!chatpolicy reset                — reset to defaults
```

### 7. `src/modules/web_routers/chat_policy_router.py`

REST API через factory `build_chat_policy_router(ctx)`:

- `GET /api/chat/policy/{chat_id}` — get
- `POST /api/chat/policy/{chat_id}` — update (через `ctx.assert_write_access`)
- `GET /api/chat/policies` — list all chats with custom policies
- `DELETE /api/chat/policy/{chat_id}` — reset to defaults

### 8. Tests

- `tests/unit/test_chat_response_policy.py` — store CRUD, mode logic, auto-adjust
- `tests/unit/test_llm_intent_classifier.py` — mocked LLM, cache, prompt template, fallback на regex if LLM down
- `tests/unit/test_feedback_tracker.py` — delete/reaction handling, signal updates
- `tests/unit/test_smart_trigger_integration.py` — full pipeline, all 5 stages

## Phasing — 5 phases / 12-17h total

| Phase | Scope | Effort | Risk | Зависимости |
|---|---|---|---|---|
| **1** | `chat_response_policy.py` + JSON store + tests | 2-3h | low | — |
| **2** | `llm_intent_classifier.py` + caching + tests | 3-4h | medium (prompt tuning) | Phase 1 |
| **3** | `feedback_tracker.py` + Telegram event hooks + tests | 2-3h | medium (Pyrogram events) | Phase 1 |
| **4** | `policy_commands.py` + `chat_policy_router.py` + tests | 2-3h | low | Phase 1 |
| **5** | `trigger_detector.py` extend + `userbot_bridge.py` integration + smoke | 3-4h | medium (production wire) | Phases 1-3 |

## Trade-offs explicitly resolved

- ✅ **Точность > latency**: 500ms LLM call OK для borderline messages
- ✅ **Per-chat policy**: ChatResponsePolicy dataclass с modes
- ✅ **Negative learning (E)**: feedback_tracker + auto_adjust

## Risks + mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| LLM hallucinates на classification | High | validation set + log decisions для audit; fallback на regex |
| Cache poisoning | Medium | cache key включает chat context hash |
| LLM down / slow | Medium | timeout 2s + fallback на regex |
| Privacy (chat data в LLM) | Low | local LM Studio, no external calls |
| Pyrogram event hook fragility | Medium | best-effort delete/reaction tracking, graceful degrade |
| False negative (Krab misses real address) | High | TWO mechanisms: regex high + LLM low — оба должны say NO для drop |
| False positive (Krab responds when shouldn't) | High | TWO mechanisms: regex high + LLM yes |
| Policy drift (auto_adjust over-aggressive) | Medium | rate limit auto-adjust (1 transition / 6h max) |

## Implementation Plan — 3 parallel rounds

**Round 1** (parallel sub-agents):
- A: Phase 1 (chat_response_policy)
- B: Phase 4 (commands + Web API skeleton — без LLM dependency)

**Round 2** (parallel after Round 1):
- C: Phase 2 (LLM intent classifier)
- D: Phase 3 (feedback tracker)

**Round 3**:
- E: Phase 5 (trigger_detector extend + userbot_bridge wire + integration tests)

## Future enhancements (out of scope)

- Topic classification (auto-derive blocked_topics из chat history)
- Multi-account support (different policies per Krab account)
- Web UI dashboard для policy management
- Continual learning через RLHF на feedback signals

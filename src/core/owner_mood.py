# -*- coding: utf-8 -*-
"""
Owner Mood Detection — динамический suffix к system prompt по настроению owner.

Идея (Feature F): Krab анализирует тон последних N сообщений owner в чате
и определяет одно из настроений: neutral / annoyed / relaxed / business /
playful / focused. По результату добавляет короткий suffix в system prompt
после `chat_persona_profile`-suffix (Feature C).

Heuristics — детерминированные, без LLM:
- annoyed: высокая плотность мата + капс + восклицательные;
- playful: эмодзи density + смайлики + lol/хах/lmao;
- business: длинные структурированные сообщения (>300 символов, списки);
- focused: «срочно», «нужно», «?» + «!» одновременно, дедлайны;
- relaxed: короткие casual без давления + позитивные эмодзи;
- neutral: всё остальное.

Архитектура:
- `MoodSnapshot` — TypedDict-подобный dict {mood, confidence, evidence}.
- `OwnerMoodTracker` — in-memory LRU (50 chats), TTL 30 min. Потокобезопасен.
- `analyze_recent_messages(messages, owner_id)` — pure function, отдаёт
  MoodSnapshot. Не зависит от store.
- `format_mood_suffix(chat_id, owner_id)` — обёртка-singleton. Default-safe:
  при любой ошибке возвращает "" (fail-open).

Feature flag: `KRAB_MOOD_DETECTION_ENABLED` (default True).
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .logger import get_logger

logger = get_logger(__name__)


# TTL для одной mood-оценки. После истечения снимок считается устаревшим
# и format_mood_suffix отдаёт "".
MOOD_TTL_MINUTES: float = 30.0

# Размер LRU — сколько чатов держим в памяти одновременно.
LRU_CAPACITY: int = 50

# Минимальное количество owner-сообщений в окне, чтобы делать вывод.
MIN_MESSAGES: int = 2

# Размер скользящего окна (последние N owner-сообщений в чате).
WINDOW_SIZE: int = 10

# Confidence-порог: ниже считаем mood = neutral.
MIN_CONFIDENCE: float = 0.35


# --- Heuristics dictionaries ----------------------------------------------

# RU/EN мат и грубые маркеры. Минимально — для частотной оценки достаточно.
_MAT_MARKERS: frozenset[str] = frozenset(
    {
        "блять",
        "бля",
        "блядь",
        "сука",
        "пиздец",
        "пздц",
        "хуй",
        "хуйня",
        "охуеть",
        "ебать",
        "ебаный",
        "ёбаный",
        "нахуй",
        "нахер",
        "fuck",
        "fucking",
        "shit",
        "damn",
        "wtf",
        "bullshit",
    }
)

# Маркеры срочности / focused mood.
_URGENT_MARKERS: frozenset[str] = frozenset(
    {
        "срочно",
        "asap",
        "быстрее",
        "быстро",
        "сейчас",
        "немедленно",
        "горит",
        "дедлайн",
        "deadline",
        "urgent",
        "now",
        "fix",
        "сломалось",
        "не работает",
    }
)

# Маркеры игривости.
_PLAYFUL_MARKERS: frozenset[str] = frozenset(
    {
        "лол",
        "lol",
        "хаха",
        "ахаха",
        "хах",
        "lmao",
        "rofl",
        "кек",
        "ору",
        "топ",
        "ггг",
        "хех",
    }
)

# Маркеры расслабленности.
_RELAXED_MARKERS: frozenset[str] = frozenset(
    {
        "спс",
        "спасибо",
        "ок",
        "норм",
        "круто",
        "класс",
        "вкусно",
        "приятно",
        "thx",
        "thanks",
        "cool",
        "nice",
    }
)

# Эмодзи-диапазоны Unicode (приблизительный охват emoji-блоков).
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U0001f600-\U0001f64f\U0001f900-\U0001f9ff\U00002600-\U000027bf]"
)

# Markers «делового» структурированного контекста.
_BUSINESS_BULLET_RE = re.compile(r"(?m)^\s*([-*•]|\d+\.)\s+\S")


# --- Pure analysis ---------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Простая токенизация в lower-case, только буквенно-цифровые."""
    return [t for t in re.findall(r"[\wЀ-ӿ]+", text.lower()) if t]


def _count_markers(tokens: Iterable[str], markers: frozenset[str]) -> int:
    """Подсчёт совпадений токенов с набором маркеров."""
    cnt = 0
    for tok in tokens:
        if tok in markers:
            cnt += 1
    return cnt


def analyze_recent_messages(
    messages: Iterable[str],
    owner_id: str | int | None = None,
) -> dict[str, Any]:
    """Анализирует список owner-сообщений и возвращает MoodSnapshot.

    MoodSnapshot = {
        "mood": "neutral|annoyed|relaxed|business|playful|focused",
        "confidence": float 0..1,
        "evidence": list[str] — короткие пометки причин.
    }

    Default-safe: при пустом списке отдаёт neutral с confidence=0.
    """
    msgs = [str(m or "") for m in messages if str(m or "").strip()]
    msgs = msgs[-WINDOW_SIZE:]

    if len(msgs) < MIN_MESSAGES:
        return {"mood": "neutral", "confidence": 0.0, "evidence": []}

    joined = "\n".join(msgs)
    tokens = _tokenize(joined)
    total_tokens = max(1, len(tokens))
    total_chars = max(1, len(joined))

    # Числовые сигналы.
    mat_hits = _count_markers(tokens, _MAT_MARKERS)
    urgent_hits = _count_markers(tokens, _URGENT_MARKERS)
    playful_hits = _count_markers(tokens, _PLAYFUL_MARKERS)
    relaxed_hits = _count_markers(tokens, _RELAXED_MARKERS)
    emoji_hits = len(_EMOJI_RE.findall(joined))
    excl_hits = joined.count("!")
    quest_hits = joined.count("?")

    # Доля капса (без emoji/цифр) — индикатор крика.
    letters = [c for c in joined if c.isalpha()]
    if letters:
        caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    else:
        caps_ratio = 0.0

    avg_msg_len = total_chars / len(msgs)

    bullet_lines = len(_BUSINESS_BULLET_RE.findall(joined))

    # Эвристические скоринги (нормализованы в 0..1).
    scores: dict[str, float] = {}

    # annoyed: мат + капс + восклицательные
    annoyed = (
        min(1.0, mat_hits / max(1, total_tokens) * 25)
        + min(1.0, caps_ratio * 4)
        + min(0.5, excl_hits / max(1, len(msgs)) * 0.5)
    )
    scores["annoyed"] = min(1.0, annoyed / 2.0)

    # playful: эмодзи + смешные маркеры
    playful = min(1.0, emoji_hits / max(1, len(msgs)) * 0.6) + min(
        1.0, playful_hits / max(1, total_tokens) * 30
    )
    scores["playful"] = min(1.0, playful / 1.5)

    # business: длина + bullets, мало эмодзи
    business = 0.0
    if avg_msg_len > 300:
        business += 0.5
    if avg_msg_len > 500:
        business += 0.2
    if bullet_lines >= 2:
        business += 0.4
    if emoji_hits == 0 and avg_msg_len > 200:
        business += 0.2
    scores["business"] = min(1.0, business)

    # focused: срочность + ?+!
    focused = min(1.0, urgent_hits / max(1, len(msgs)) * 1.0) + (
        0.4 if (quest_hits >= 1 and excl_hits >= 1) else 0.0
    )
    scores["focused"] = min(1.0, focused / 1.4)

    # relaxed: короткие позитивные без срочности и без мата
    relaxed = 0.0
    if avg_msg_len < 80 and mat_hits == 0 and urgent_hits == 0:
        relaxed += 0.3
    relaxed += min(0.7, relaxed_hits / max(1, len(msgs)) * 0.7)
    if emoji_hits >= 1 and mat_hits == 0:
        relaxed += 0.2
    scores["relaxed"] = min(1.0, relaxed)

    # Выбираем mood с максимальным score.
    best_mood, best_score = max(scores.items(), key=lambda kv: kv[1])

    evidence: list[str] = []
    if mat_hits:
        evidence.append(f"mat:{mat_hits}")
    if caps_ratio >= 0.25:
        evidence.append(f"caps:{caps_ratio:.2f}")
    if emoji_hits:
        evidence.append(f"emoji:{emoji_hits}")
    if urgent_hits:
        evidence.append(f"urgent:{urgent_hits}")
    if playful_hits:
        evidence.append(f"playful:{playful_hits}")
    if relaxed_hits:
        evidence.append(f"relaxed:{relaxed_hits}")
    if bullet_lines:
        evidence.append(f"bullets:{bullet_lines}")
    evidence.append(f"avg_len:{int(avg_msg_len)}")

    if best_score < MIN_CONFIDENCE:
        return {
            "mood": "neutral",
            "confidence": round(best_score, 3),
            "evidence": evidence,
        }

    return {
        "mood": best_mood,
        "confidence": round(best_score, 3),
        "evidence": evidence,
    }


# --- LRU tracker -----------------------------------------------------------


class OwnerMoodTracker:
    """In-memory LRU-кэш mood snapshots по чатам.

    Ключ — `(chat_id, owner_id)` нормализованный в str. Значение —
    `(MoodSnapshot, timestamp)`. При переполнении выкидывается LRU.
    """

    def __init__(
        self,
        *,
        capacity: int = LRU_CAPACITY,
        ttl_minutes: float = MOOD_TTL_MINUTES,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._capacity = max(1, int(capacity))
        self._ttl = timedelta(minutes=float(ttl_minutes))
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        # OrderedDict: rightmost = recently used.
        self._cache: OrderedDict[str, tuple[dict[str, Any], datetime]] = OrderedDict()

    @staticmethod
    def _key(chat_id: Any, owner_id: Any) -> str:
        return f"{str(chat_id or '').strip()}::{str(owner_id or '').strip()}"

    def get(self, chat_id: Any, owner_id: Any) -> dict[str, Any] | None:
        """Возвращает свежий snapshot или None если нет/устарел."""
        key = self._key(chat_id, owner_id)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            snapshot, ts = entry
            if self._now() - ts > self._ttl:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return dict(snapshot)

    def store(
        self,
        chat_id: Any,
        owner_id: Any,
        snapshot: dict[str, Any],
    ) -> None:
        """Сохраняет snapshot, выселяет LRU при переполнении."""
        key = self._key(chat_id, owner_id)
        with self._lock:
            self._cache[key] = (dict(snapshot), self._now())
            self._cache.move_to_end(key)
            while len(self._cache) > self._capacity:
                self._cache.popitem(last=False)

    def update_from_messages(
        self,
        chat_id: Any,
        owner_id: Any,
        messages: Iterable[str],
    ) -> dict[str, Any]:
        """Анализирует messages, сохраняет и возвращает snapshot."""
        snap = analyze_recent_messages(messages, owner_id=owner_id)
        self.store(chat_id, owner_id, snap)
        return snap

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# Module-level singleton (паттерн совпадает с chat_ban_cache, silence_mode,
# chat_persona_store).
owner_mood_tracker = OwnerMoodTracker()


# --- Public helpers --------------------------------------------------------


def _is_feature_enabled() -> bool:
    """Читает config.KRAB_MOOD_DETECTION_ENABLED, default True. Fail-open."""
    try:
        from ..config import config  # noqa: PLC0415

        return bool(getattr(config, "KRAB_MOOD_DETECTION_ENABLED", True))
    except Exception:
        return True


# Маппинг mood → suffix-текст для system prompt.
_MOOD_SUFFIXES: dict[str, str] = {
    "annoyed": (
        "Тон owner — раздражён. Отвечай кратко, по делу, без шуток и "
        "без меню вариантов. Не предлагай альтернативы — выполняй или "
        "честно сообщай о препятствии."
    ),
    "playful": (
        "Тон owner — игривый. Можно лёгкие шутки и непринуждённый стиль, "
        "сохраняй полезность ответа."
    ),
    "business": (
        "Деловой контекст. Ответ должен быть структурированным и "
        "формальным, без сленга и эмодзи. Длина — соразмерно вопросу."
    ),
    "focused": (
        "Owner сосредоточен/торопится. Сначала ответ по сути, потом "
        "детали. Без отступлений и без приглашений к диалогу."
    ),
    "relaxed": ("Тон расслабленный. Можно короткие casual ответы, дружелюбно, без формальностей."),
    # neutral — пусто, базовое поведение.
}


def format_mood_suffix(
    chat_id: Any,
    owner_id: Any,
    *,
    enabled: bool | None = None,
    tracker: OwnerMoodTracker | None = None,
) -> str:
    """Возвращает mood-suffix или "" если нет данных / mood=neutral.

    Default-safe: при любой ошибке возвращает "".
    """
    if not chat_id or not owner_id:
        return ""
    if enabled is False:
        return ""
    if enabled is None and not _is_feature_enabled():
        return ""

    tracker = tracker or owner_mood_tracker
    try:
        snap = tracker.get(chat_id, owner_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "owner_mood_lookup_failed",
            chat_id=str(chat_id),
            owner_id=str(owner_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ""

    if not snap:
        return ""

    mood = str(snap.get("mood") or "neutral")
    suffix = _MOOD_SUFFIXES.get(mood, "")
    if not suffix:
        return ""

    confidence = float(snap.get("confidence") or 0.0)
    if confidence < MIN_CONFIDENCE:
        return ""

    return f"Контекст настроения owner: {suffix}"

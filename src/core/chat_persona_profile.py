# -*- coding: utf-8 -*-
"""
Per-Chat Persona Drift — динамический suffix к system prompt по чатам.

Идея (Feature C, Bug 11 follow-up): каждый чат имеет свой стилистический
паттерн (тон, длина сообщений, частые слова). Krab анализирует last N
сообщений и сохраняет profile в JSON. При сборке system prompt для
конкретного `chat_id` — добавляет короткий suffix вида
«контекст чата: технический, тон: casual_pro, длина ответа: short».

Архитектура:

- `ChatPersonaStore` — потокобезопасный JSON-store, lazy-loaded.
  Singleton `chat_persona_store` инициализируется при первом доступе
  через дефолтный путь `~/.openclaw/krab_runtime_state/chat_persona_profile.json`.
- `build_profile_from_messages(chat_id, messages)` — детерминированный
  анализ списка строк (без LLM), пишет profile в store. Heuristics:
  tone (technical / casual / family / formal), formality (formal /
  casual_pro / casual), preferred_reply_length (short / medium / long),
  common_words (top-N stems после стоп-слов).
- `format_persona_suffix(chat_id)` — собирает текстовый suffix или ""
  если profile отсутствует/просрочен/feature off. Default-safe.

Инварианты:
- TTL 6 часов по умолчанию (`PROFILE_TTL_HOURS`). Просроченные profile
  считаются «нет profile» — fallthrough на пустой suffix.
- Mat / nsfw слова отфильтровываются из `common_words`, чтобы system
  prompt оставался clean даже если в чате модерация мягкая.
- Feature flag `KRAB_PERSONA_DRIFT_ENABLED` (default True). Опциональный
  per-context override через аргумент `enabled` в `format_persona_suffix`.

Не делает:
- Не строит profile сам в фоне. Build вызывается извне (out-of-scope для
  Feature C — будет интегрирован отдельно через `memory_archive` или
  on-demand background task).
- Не зависит от других core-модулей кроме `logger`.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .logger import get_logger

logger = get_logger(__name__)


# TTL profile в часах: после истечения считаем, что данные устарели и
# не используем suffix (но сам profile в JSON остаётся — будет перезаписан
# при следующем build).
PROFILE_TTL_HOURS: float = 6.0

# Лимит «общих слов» в suffix — чтобы prompt не разрастался.
COMMON_WORDS_LIMIT: int = 5

# Минимальное количество сообщений, чтобы profile считался валидным.
MIN_MESSAGES_FOR_PROFILE: int = 5

# Дефолтный путь, если bootstrap не вызвал configure_default_path.
_DEFAULT_STORAGE_PATH = (
    Path.home() / ".openclaw" / "krab_runtime_state" / "chat_persona_profile.json"
)

# --- Stop-words / mat / banned ---------------------------------------------

# Минимальный набор RU/EN стоп-слов. Не FullText — для частотного анализа
# хватает топ-100, а не лингвистически выверенного списка.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # ru
        "и",
        "в",
        "не",
        "на",
        "что",
        "я",
        "с",
        "а",
        "как",
        "это",
        "то",
        "по",
        "но",
        "из",
        "за",
        "у",
        "о",
        "же",
        "ну",
        "так",
        "вот",
        "был",
        "была",
        "было",
        "есть",
        "быть",
        "к",
        "от",
        "до",
        "для",
        "или",
        "если",
        "же",
        "там",
        "тут",
        "там",
        "тебе",
        "мне",
        "меня",
        "себя",
        "его",
        "её",
        "их",
        "мы",
        "вы",
        "ты",
        "он",
        "она",
        "они",
        "оно",
        "да",
        "нет",
        "при",
        "под",
        "над",
        "между",
        "тоже",
        "также",
        "ещё",
        "уже",
        "только",
        "кто",
        "где",
        "когда",
        "почему",
        "зачем",
        "какой",
        "какая",
        "какие",
        # en
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "and",
        "or",
        "but",
        "not",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "can",
        "may",
        "might",
        "if",
        "then",
    }
)

# Mat / nsfw / clearly inappropriate stems. Список нарочно обширный — лучше
# отфильтровать лишнее, чем пропустить в system prompt.
_BANNED_WORD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bбля",
        r"\bхуй",
        r"\bхуе",
        r"\bпизд",
        r"\bебан",
        r"\bебать",
        r"\bебл",
        r"\bёб",
        r"\bебу",
        r"\bсук[аи]\b",
        r"\bмудак",
        r"\bговн",
        r"\bжоп",
        r"\bпидор",
        r"\bfuck",
        r"\bshit",
        r"\bbitch",
        r"\bcunt",
        r"\bdick",
        r"\bass\b",
    )
)

# Технические маркеры — для tone=technical.
_TECHNICAL_MARKERS: frozenset[str] = frozenset(
    {
        "api",
        "код",
        "code",
        "python",
        "js",
        "ts",
        "rust",
        "go",
        "модель",
        "model",
        "llm",
        "промпт",
        "prompt",
        "git",
        "commit",
        "bug",
        "баг",
        "fix",
        "deploy",
        "сервер",
        "server",
        "docker",
        "kubernetes",
        "k8s",
        "regex",
        "json",
        "yaml",
        "config",
        "env",
        "endpoint",
        "router",
        "test",
        "тест",
        "ci",
        "cd",
        "lint",
        "venv",
        "pip",
        "npm",
        "build",
        "compile",
        "stack",
        "trace",
        "error",
        "exception",
        "log",
        "лог",
        "metric",
        "метрик",
        "function",
        "функция",
        "class",
        "класс",
        "import",
        "module",
        "модуль",
        "thread",
        "async",
        "корутина",
        "coroutine",
    }
)

# Family / casual markers.
_FAMILY_MARKERS: frozenset[str] = frozenset(
    {
        "мам",
        "пап",
        "сын",
        "дочь",
        "бабушк",
        "дедушк",
        "брат",
        "сестр",
        "семья",
        "родител",
        "ребён",
        "ребен",
        "малыш",
        "обед",
        "ужин",
        "магазин",
        "приехал",
        "позвон",
    }
)

# Formal markers.
_FORMAL_MARKERS: frozenset[str] = frozenset(
    {
        "уважаем",
        "коллег",
        "договор",
        "сотруднич",
        "процедур",
        "регламент",
        "официальн",
        "просим",
        "ходатайств",
        "пожалуйста",
        "благодар",
        "извещ",
    }
)

# Word-stem regex: буквы + цифры, минимум 3 символа.
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9]{2,}")


def _is_banned_word(word: str) -> bool:
    """Возвращает True если слово содержит mat / nsfw маркер."""
    for pattern in _BANNED_WORD_PATTERNS:
        if pattern.search(word):
            return True
    return False


def _stem(word: str) -> str:
    """Очень простой стеммер: lowercase + обрезка типичных RU окончаний.

    Для частотного анализа этого достаточно — не надо тащить snowball/pymorphy.
    """
    w = word.lower()
    # Минимальная нормализация русских окончаний (порядок важен — длинные
    # суффиксы проверяем раньше).
    suffixes = (
        "ыми",
        "ими",
        "ого",
        "его",
        "ому",
        "ему",
        "ыми",
        "ими",
        "ой",
        "ей",
        "ая",
        "яя",
        "ое",
        "ее",
        "ие",
        "ые",
        "ам",
        "ям",
        "ах",
        "ях",
        "ом",
        "ем",
        "ов",
        "ев",
        "ка",
        "ки",
        "ке",
        "у",
        "ю",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "о",
    )
    if len(w) >= 5:
        for suf in suffixes:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[: -len(suf)]
    return w


# --- Tone / formality / length detection ----------------------------------


def _detect_tone(stems: list[str]) -> str:
    """Определяет основной тон чата по плотности маркеров."""
    if not stems:
        return "neutral"
    tech = sum(1 for s in stems if s in _TECHNICAL_MARKERS)
    fam = sum(1 for s in stems if s in _FAMILY_MARKERS)
    formal = sum(1 for s in stems if s in _FORMAL_MARKERS)
    total = len(stems)
    # Пороги осознанно низкие — типичный чат имеет 1-3% маркеров.
    if tech / total >= 0.03 and tech >= max(fam, formal):
        return "technical"
    if fam / total >= 0.03 and fam >= max(tech, formal):
        return "family"
    if formal / total >= 0.03 and formal >= max(tech, fam):
        return "formal"
    return "casual"


def _detect_formality(messages: list[str]) -> str:
    """Грубая эвристика формальности по mat и emoji density."""
    if not messages:
        return "casual"
    total = len(messages)
    mat_hits = sum(1 for msg in messages if any(p.search(msg) for p in _BANNED_WORD_PATTERNS))
    # Очень простой emoji detector: символы из BMP supplementary planes
    # и базовый набор «хвостовых» эмодзи. Точность не нужна.
    emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
    emoji_hits = sum(1 for msg in messages if emoji_re.search(msg))
    mat_ratio = mat_hits / total
    emoji_ratio = emoji_hits / total
    if mat_ratio >= 0.2 or emoji_ratio >= 0.5:
        return "casual"
    if mat_ratio == 0 and emoji_ratio < 0.05:
        return "formal"
    return "casual_pro"


def _detect_reply_length(avg_msg_length: float) -> str:
    """Маппит среднюю длину сообщения в категорию."""
    if avg_msg_length < 60:
        return "short"
    if avg_msg_length < 200:
        return "medium"
    return "long"


# --- Profile builder -------------------------------------------------------


def analyze_messages(messages: Iterable[str]) -> dict[str, Any]:
    """Чистая функция: messages → profile dict (без записи в store).

    Удобно для тестов и для возможного in-memory probe без persist.
    """
    msg_list = [str(m or "") for m in messages if str(m or "").strip()]
    if not msg_list:
        return {
            "tone": "neutral",
            "avg_msg_length": 0.0,
            "common_words": [],
            "formality": "casual",
            "preferred_reply_length": "short",
            "message_count": 0,
        }

    # Длина в символах (не токенах — нам нужна оценка вербозности юзеров).
    total_len = sum(len(m) for m in msg_list)
    avg_len = total_len / len(msg_list)

    # Общие слова: counter по stems после фильтрации.
    counter: dict[str, int] = {}
    for msg in msg_list:
        for raw in _WORD_RE.findall(msg):
            if _is_banned_word(raw):
                continue
            stem = _stem(raw)
            if stem in _STOPWORDS or len(stem) < 3:
                continue
            counter[stem] = counter.get(stem, 0) + 1

    common_words = [
        word
        for word, _count in sorted(
            counter.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[: COMMON_WORDS_LIMIT * 2]  # запас, но cap'ним ниже
    ]

    all_stems = [s for s, c in counter.items() for _ in range(c)]

    return {
        "tone": _detect_tone(all_stems),
        "avg_msg_length": round(avg_len, 1),
        "common_words": common_words[:COMMON_WORDS_LIMIT],
        "formality": _detect_formality(msg_list),
        "preferred_reply_length": _detect_reply_length(avg_len),
        "message_count": len(msg_list),
    }


# --- Store -----------------------------------------------------------------


class ChatPersonaStore:
    """JSON-store для chat persona profiles. Singleton-pattern.

    Lazy bootstrap: если `_storage_path` не настроен явно, при первом
    обращении используем `_DEFAULT_STORAGE_PATH`. Это нужно потому, что
    мы не можем расширять `userbot_bridge.start()` (out-of-scope feature).
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._chats: dict[str, dict[str, Any]] = {}
        self._loaded: bool = False
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь и перечитывает с диска. Используется в bootstrap."""
        with self._lock:
            self._storage_path = storage_path
            self._chats = {}
            self._loaded = False
            self._load_from_disk()

    def _ensure_bootstrap(self) -> None:
        """Lazy bootstrap — если путь не задан, берём дефолтный."""
        if self._storage_path is not None and self._loaded:
            return
        with self._lock:
            if self._storage_path is None:
                self._storage_path = _DEFAULT_STORAGE_PATH
            if not self._loaded:
                self._load_from_disk()

    # ---- Public API -----------------------------------------------------

    def get_profile(self, chat_id: Any) -> dict[str, Any] | None:
        """Возвращает копию profile для chat_id или None.

        Не учитывает TTL — для проверки свежести используй `is_fresh`.
        """
        target = self._normalize(chat_id)
        if not target:
            return None
        self._ensure_bootstrap()
        with self._lock:
            entry = self._chats.get(target)
            if entry is None:
                return None
            return dict(entry)

    def is_fresh(self, chat_id: Any) -> bool:
        """True если profile есть и не просрочен."""
        profile = self.get_profile(chat_id)
        if not profile:
            return False
        last_updated = profile.get("last_updated_at")
        if not last_updated:
            return False
        try:
            ts = datetime.fromisoformat(str(last_updated))
        except (TypeError, ValueError):
            return False
        # ts может быть naive — нормализуем к UTC.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return self._now() - ts < timedelta(hours=PROFILE_TTL_HOURS)

    def save_profile(
        self,
        chat_id: Any,
        profile: dict[str, Any],
        *,
        title_hint: str | None = None,
    ) -> None:
        """Сохраняет profile, добавляет timestamp и persist'ит."""
        target = self._normalize(chat_id)
        if not target:
            return
        self._ensure_bootstrap()
        entry = dict(profile)
        if title_hint is not None:
            entry["title_hint"] = str(title_hint)
        entry["last_updated_at"] = self._now().isoformat()
        with self._lock:
            self._chats[target] = entry
            self._persist_to_disk()
        logger.info(
            "chat_persona_profile_saved",
            chat_id=target,
            tone=entry.get("tone"),
            formality=entry.get("formality"),
            preferred_reply_length=entry.get("preferred_reply_length"),
            message_count=entry.get("message_count"),
        )

    def list_profiles(self) -> list[dict[str, Any]]:
        """Snapshot всех profile (для owner UI / диагностики)."""
        self._ensure_bootstrap()
        with self._lock:
            result: list[dict[str, Any]] = []
            for chat_id, entry in self._chats.items():
                snapshot = dict(entry)
                snapshot["chat_id"] = chat_id
                result.append(snapshot)
            return result

    def clear(self, chat_id: Any) -> bool:
        """Удаляет profile для чата."""
        target = self._normalize(chat_id)
        if not target:
            return False
        self._ensure_bootstrap()
        with self._lock:
            if target not in self._chats:
                return False
            del self._chats[target]
            self._persist_to_disk()
        logger.info("chat_persona_profile_cleared", chat_id=target)
        return True

    # ---- Internal -------------------------------------------------------

    @staticmethod
    def _normalize(chat_id: Any) -> str:
        return str(chat_id or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        self._loaded = True  # помечаем даже при ошибке, чтобы не зацикливаться
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "chat_persona_profile_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("chat_persona_profile_load_malformed", path=str(path))
            return
        chats = raw.get("chats") if "chats" in raw else raw
        if not isinstance(chats, dict):
            return
        for key, value in chats.items():
            if isinstance(value, dict):
                self._chats[str(key)] = dict(value)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"chats": self._chats}
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "chat_persona_profile_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton (паттерн совпадает с chat_ban_cache, silence_mode).
chat_persona_store = ChatPersonaStore()


# --- Public helpers --------------------------------------------------------


def build_profile_from_messages(
    chat_id: Any,
    messages: Iterable[str],
    *,
    title_hint: str | None = None,
    store: ChatPersonaStore | None = None,
) -> dict[str, Any] | None:
    """Анализирует messages, сохраняет profile в store.

    Возвращает сохранённый profile (с `last_updated_at`) или None если
    сообщений недостаточно.
    """
    store = store or chat_persona_store
    msg_list = [str(m or "") for m in messages if str(m or "").strip()]
    if len(msg_list) < MIN_MESSAGES_FOR_PROFILE:
        return None
    profile = analyze_messages(msg_list)
    store.save_profile(chat_id, profile, title_hint=title_hint)
    return store.get_profile(chat_id)


def _is_feature_enabled() -> bool:
    """Читает config.KRAB_PERSONA_DRIFT_ENABLED, default True. Fail-open."""
    try:
        from ..config import config  # noqa: PLC0415

        return bool(getattr(config, "KRAB_PERSONA_DRIFT_ENABLED", True))
    except Exception:
        return True


# Маппинг categorical → человекочитаемая RU подпись для suffix.
_TONE_LABELS: dict[str, str] = {
    "technical": "технический чат про разработку/AI",
    "casual": "повседневный чат",
    "family": "семейный чат",
    "formal": "формальный чат",
    "neutral": "нейтральный чат",
}

_FORMALITY_LABELS: dict[str, str] = {
    "casual": "расслабленный, разговорный",
    "casual_pro": "разговорный, по делу",
    "formal": "формальный, выдержанный",
}

_LENGTH_LABELS: dict[str, str] = {
    "short": "короткие ответы",
    "medium": "ответы средней длины",
    "long": "развёрнутые ответы допустимы",
}


def format_persona_suffix(
    chat_id: Any,
    *,
    enabled: bool | None = None,
    store: ChatPersonaStore | None = None,
    borrowed_template: dict[str, Any] | None = None,
) -> str:
    """Возвращает persona-suffix для system prompt или "" если нет данных.

    Default-safe: при любой ошибке возвращает "".

    Feature I (Cross-Chat Transfer): если target chat не имеет свежего
    profile, caller может передать `borrowed_template` (см. модуль
    `cross_chat_transfer`). В этом случае suffix формируется по
    borrowed-данным с пометкой «(заимствовано)». Read-only — store не
    модифицируется.
    """
    if not chat_id:
        return ""
    if enabled is False:
        return ""
    if enabled is None and not _is_feature_enabled():
        return ""

    store = store or chat_persona_store
    profile: dict[str, Any] | None = None
    is_borrowed = False
    try:
        if store.is_fresh(chat_id):
            profile = store.get_profile(chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat_persona_suffix_failed",
            chat_id=str(chat_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ""

    # Fallback: нет своего profile — пробуем borrowed template.
    if not profile and borrowed_template:
        profile = dict(borrowed_template)
        is_borrowed = bool(profile.get("borrowed", True))

    # Feature I (Cross-Chat Transfer auto-bootstrap): если caller не передал
    # borrowed_template и у нас нет fresh profile — пробуем подобрать похожий
    # чат на основе stale/частичного профиля. Read-only, fail-open.
    if not profile:
        try:
            from .cross_chat_transfer import bootstrap_borrowed_profile  # noqa: PLC0415

            partial = store.get_profile(chat_id)
            if partial:
                template = bootstrap_borrowed_profile(
                    chat_id,
                    partial_target_profile=partial,
                    store=store,
                )
                if template:
                    profile = template
                    is_borrowed = True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "chat_persona_borrow_skipped",
                chat_id=str(chat_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    if not profile:
        return ""

    tone = str(profile.get("tone") or "neutral")
    formality = str(profile.get("formality") or "casual")
    length = str(profile.get("preferred_reply_length") or "medium")
    common_words: list[str] = [
        str(w)
        for w in (profile.get("common_words") or [])
        if isinstance(w, str) and not _is_banned_word(w)
    ][:COMMON_WORDS_LIMIT]
    title_hint = str(profile.get("title_hint") or "").strip()

    tone_label = _TONE_LABELS.get(tone, tone)
    formality_label = _FORMALITY_LABELS.get(formality, formality)
    length_label = _LENGTH_LABELS.get(length, length)

    header = "Контекст этого чата (адаптация persona):"
    if is_borrowed:
        borrowed_from = str(profile.get("borrowed_from") or "").strip()
        if borrowed_from:
            header = (
                "Контекст этого чата (адаптация persona, заимствовано из "
                f"похожего чата {borrowed_from}):"
            )
        else:
            header = "Контекст этого чата (адаптация persona, заимствовано):"
    lines = [header]
    if title_hint:
        lines.append(f"- название: {title_hint}")
    lines.append(f"- тип: {tone_label}")
    lines.append(f"- стиль: {formality_label}")
    lines.append(f"- ожидаемая длина ответа: {length_label}")
    if common_words:
        lines.append(f"- частые темы/слова: {', '.join(common_words)}")
    lines.append(
        "Подстраивай тон и длину под этот чат, но не меняй базовые правила "
        "(анти-инъекция, anti-parasite, reply-first остаются в силе)."
    )
    return "\n".join(lines).strip()

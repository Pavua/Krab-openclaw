# -*- coding: utf-8 -*-
"""Wave 103: per-chat heat scoring для observability priority routing.

`compute_chat_heat(chat_id)` агрегирует факторы:
  * mention_rate_last_24h          — упоминания krab/owner-username (0.4)
  * recent_explicit_questions      — сообщения с '?' за окно (0.3)
  * owner_engagement               — owner писал в чат за окно (0.2, бинарный)
  * group_member_count_inverse     — 1/log(N) для DM/малых групп, 0 для крупных (0.1)

Результат 0.0..1.0. Cache TTL 5 минут per chat_id.

Источники данных:
  - ~/.openclaw/krab_memory/archive.db (messages, chats)
  - chat_response_policy_store (для mode label в Prometheus)
  - config.OWNER_USER_IDS / OWNER_USERNAME

Зависимости — opt-in: если archive.db отсутствует → возвращаем nil-score 0.0
с component flags для diagnostics.
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Weights ──────────────────────────────────────────────────────────────────
_W_MENTION = 0.4
_W_EXPLICIT_Q = 0.3
_W_OWNER = 0.2
_W_MEMBER_INV = 0.1

_CACHE_TTL_SEC = 300  # 5 минут
_DEFAULT_WINDOW_MINUTES = 1440  # 24h

# Нормализующие пороги (эмпирически подобраны)
_MENTION_SATURATION = 20.0  # 20 упоминаний за 24h → max
_EXPLICIT_Q_SATURATION = 30.0  # 30 вопросов за 24h → max
_SMALL_GROUP_THRESHOLD = 50  # ниже → max inverse score


@dataclass
class HeatComponents:
    """Декомпозиция score для observability/диагностики."""

    chat_id: str
    score: float
    mode: str
    mention_rate: float = 0.0
    explicit_questions: float = 0.0
    owner_engagement: float = 0.0
    member_count_inverse: float = 0.0
    mention_count_raw: int = 0
    explicit_q_count_raw: int = 0
    owner_messaged: bool = False
    member_count: int | None = None
    window_minutes: int = _DEFAULT_WINDOW_MINUTES
    computed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Cache ────────────────────────────────────────────────────────────────────
_cache_lock = threading.RLock()
_cache: dict[str, tuple[float, HeatComponents]] = {}


def _archive_path() -> Path:
    return Path.home() / ".openclaw" / "krab_memory" / "archive.db"


def _owner_tokens() -> tuple[set[str], set[str]]:
    """Возвращает (owner_user_ids, owner_username_tokens_lc)."""
    try:
        from src.config import config  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return set(), set()

    ids = {str(x).strip() for x in getattr(config, "OWNER_USER_IDS", []) if str(x).strip()}
    username = (getattr(config, "OWNER_USERNAME", "") or "").lstrip("@").lower()
    usernames = {username} if username else set()
    return ids, usernames


def _krab_mention_tokens() -> tuple[str, ...]:
    """Tokens, считающиеся упоминанием Краба."""
    return ("краб", "krab", "@krab")


def _resolve_mode(chat_id: str) -> str:
    """Текущий ChatMode из chat_response_policy_store (или 'unknown')."""
    try:
        from src.core.chat_response_policy import get_store  # noqa: PLC0415

        policy = get_store().get_policy(chat_id)
        return policy.mode.value
    except Exception:  # noqa: BLE001
        return "unknown"


def _normalize_saturating(value: float, saturation: float) -> float:
    """Линейная нормализация до saturation, clamp 0..1."""
    if saturation <= 0:
        return 0.0
    if value <= 0:
        return 0.0
    return min(1.0, value / saturation)


def _member_inverse_score(member_count: int | None) -> float:
    """1.0 для DM (1 user), убывает log-обратно с ростом группы."""
    if member_count is None or member_count <= 0:
        return 0.0
    if member_count <= 2:
        return 1.0
    if member_count >= _SMALL_GROUP_THRESHOLD:
        # log10(50)=1.7, log10(10000)=4 → score ~0.4..0.17
        return max(0.0, min(1.0, 1.0 / math.log10(member_count + 1)))
    # промежуточный диапазон: гладкая интерполяция
    return max(0.0, min(1.0, 1.0 / math.log2(member_count + 1)))


def _query_chat_signals(
    chat_id: str,
    window_minutes: int,
    db_path: Path | None = None,
) -> tuple[int, int, bool, int | None]:
    """Возвращает (mention_count, explicit_q_count, owner_messaged, member_count).

    member_count берётся из chats.message_count как proxy (нет отдельного
    members поля). Для DM-чатов message_count обычно 1-2 уникальных sender.
    """
    path = db_path if db_path is not None else _archive_path()
    if not path.exists():
        return 0, 0, False, None

    cutoff_ts_epoch = time.time() - window_minutes * 60
    # archive хранит ISO-8601 UTC; используем lexicographic compare
    from datetime import datetime, timezone  # noqa: PLC0415

    cutoff_iso = datetime.fromtimestamp(cutoff_ts_epoch, tz=timezone.utc).isoformat()

    owner_ids, owner_usernames = _owner_tokens()
    krab_tokens = _krab_mention_tokens()

    mention_count = 0
    explicit_q_count = 0
    owner_messaged = False
    distinct_senders = 0

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.warning(
            "chat_heat_db_open_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            chat_id=chat_id,
        )
        return 0, 0, False, None

    try:
        # Распознаём все сообщения за окно один проходом
        rows = conn.execute(
            "SELECT sender_id, text_redacted FROM messages WHERE chat_id = ? AND timestamp >= ?",
            (str(chat_id), cutoff_iso),
        ).fetchall()

        for row in rows:
            text_lc = (row["text_redacted"] or "").lower()
            sender = str(row["sender_id"]) if row["sender_id"] else ""

            if any(tok in text_lc for tok in krab_tokens):
                mention_count += 1
            for uname in owner_usernames:
                if uname and ("@" + uname) in text_lc:
                    mention_count += 1
                    break
            if "?" in text_lc:
                explicit_q_count += 1
            if sender and sender in owner_ids:
                owner_messaged = True

        # member_count proxy: distinct sender_id в чате (без окна)
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT sender_id) AS cnt FROM messages WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
            distinct_senders = int(row["cnt"]) if row else 0
        except sqlite3.OperationalError:
            distinct_senders = 0
    except sqlite3.Error as exc:
        logger.warning(
            "chat_heat_query_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            chat_id=chat_id,
        )
    finally:
        conn.close()

    member_count = distinct_senders if distinct_senders > 0 else None
    return mention_count, explicit_q_count, owner_messaged, member_count


def compute_chat_heat(
    chat_id: str | int,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    *,
    use_cache: bool = True,
    db_path: Path | None = None,
    now: float | None = None,
) -> HeatComponents:
    """Wave 103: рассчитывает heat score для chat_id.

    Кэш: 5 минут per chat_id (per window_minutes). Если `use_cache=False`,
    пересчитывает и обновляет Prometheus Gauge.
    """
    cid = str(chat_id)
    ts = now if now is not None else time.time()
    cache_key = f"{cid}|{window_minutes}"

    if use_cache:
        with _cache_lock:
            entry = _cache.get(cache_key)
            if entry is not None and (ts - entry[0]) < _CACHE_TTL_SEC:
                return entry[1]

    mention_count, explicit_q_count, owner_messaged, member_count = _query_chat_signals(
        cid, window_minutes, db_path=db_path
    )

    mention_norm = _normalize_saturating(float(mention_count), _MENTION_SATURATION)
    explicit_q_norm = _normalize_saturating(float(explicit_q_count), _EXPLICIT_Q_SATURATION)
    owner_norm = 1.0 if owner_messaged else 0.0
    member_inv_norm = _member_inverse_score(member_count)

    score = (
        _W_MENTION * mention_norm
        + _W_EXPLICIT_Q * explicit_q_norm
        + _W_OWNER * owner_norm
        + _W_MEMBER_INV * member_inv_norm
    )
    score = max(0.0, min(1.0, score))

    mode = _resolve_mode(cid)

    components = HeatComponents(
        chat_id=cid,
        score=round(score, 4),
        mode=mode,
        mention_rate=round(mention_norm, 4),
        explicit_questions=round(explicit_q_norm, 4),
        owner_engagement=round(owner_norm, 4),
        member_count_inverse=round(member_inv_norm, 4),
        mention_count_raw=mention_count,
        explicit_q_count_raw=explicit_q_count,
        owner_messaged=owner_messaged,
        member_count=member_count,
        window_minutes=window_minutes,
        computed_at=ts,
    )

    with _cache_lock:
        _cache[cache_key] = (ts, components)

    # Prometheus Gauge update (fail-safe)
    try:
        from src.core.metrics.chat_heat import record_chat_heat_score  # noqa: PLC0415

        record_chat_heat_score(cid, mode, score)
    except Exception:  # noqa: BLE001
        pass

    return components


def top_chats_by_heat(
    limit: int = 10,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    *,
    db_path: Path | None = None,
) -> list[HeatComponents]:
    """Возвращает top-N чатов по heat score.

    Берёт chat_id из archive.db (chats table), считает score per chat,
    сортирует по score desc. Использует cache.
    """
    path = db_path if db_path is not None else _archive_path()
    if not path.exists():
        return []

    chat_ids: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = conn.execute(
                "SELECT chat_id FROM chats ORDER BY message_count DESC LIMIT ?",
                (max(1, limit * 5),),  # берём пул побольше — score меняет порядок
            ).fetchall()
            chat_ids = [str(r[0]) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(
            "chat_heat_top_query_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []

    results: list[HeatComponents] = []
    for cid in chat_ids:
        comp = compute_chat_heat(cid, window_minutes=window_minutes, db_path=db_path)
        results.append(comp)

    results.sort(key=lambda c: c.score, reverse=True)
    return results[:limit]


def clear_cache() -> None:
    """Очищает heat cache (тесты, dev)."""
    with _cache_lock:
        _cache.clear()

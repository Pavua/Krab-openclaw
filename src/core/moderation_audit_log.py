# -*- coding: utf-8 -*-
"""
Wave 108: append-only audit log модерационных действий.

Зачем: `chat_ban_cache` хранит текущее состояние (забанен ли чат сейчас, до
когда), но не историю. Когда owner спрашивает "когда Krab был забанен в YMB
и почему?" — приходится grep'ать `~/.openclaw/krab_runtime_state/logs/*` за
несколько недель. Это медленно и теряется при ротации логов.

Решение: маленькая SQLite база с одним append-only table. Каждое модерационное
действие (ban_user, unban, krab_banned_in_chat, unmute и подобное) пишется
строкой с timestamp/chat_id/action/reason/by_user_id/context_json. Запросы
идут с фильтрами по chat_id и action.

Инварианты:
- **Append-only.** Никаких UPDATE/DELETE. История — это история.
- **Никогда не ломает hot path.** Любая ошибка SQLite/serialize → warning
  лог и возврат без exception. Модерация не должна падать из-за audit log.
- **Lazy init.** База создаётся при первом write/read, не при импорте.
- **Singleton с configurable path** — pattern как у chat_ban_cache.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Известные action типы. Это НЕ enforcement (callers могут логировать любую
# строку), но docstring чтобы было понятно что писать в обычных сценариях.
KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "ban_user",  # Krab забанил юзера в чате (owner command)
        "unban_user",  # Krab разбанил юзера
        "mute_user",  # Krab замьютил юзера
        "unmute_user",  # Krab размьютил
        "krab_banned_in_chat",  # Сам Krab забанен/ограничен в чате (capture с chat_ban_cache.mark_banned)
        "krab_unbanned_in_chat",  # Cache очищен (clear / expiry)
        "chat_ban_cache_clear",  # Ручная очистка ban cache
    }
)


class ModerationAuditLog:
    """Persisted SQLite append-only audit log.

    `storage_path` — путь к .db файлу. Singleton конфигурируется через
    `configure_default_path()` из bootstrap'а. Принимает `now_fn` для тестов.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._initialized: bool = False
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к sqlite-файлу. Идемпотентно."""
        with self._lock:
            self._storage_path = storage_path
            self._initialized = False
            # Ленивая инициализация: схему создадим при первом обращении.

    def _ensure_db(self) -> sqlite3.Connection | None:
        """Открывает connection и (при необходимости) создаёт schema.

        Возвращает None если path не сконфигурирован или sqlite сломалось.
        Caller обязан вызывать close() на возвращаемом connection.
        """
        path = self._storage_path
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=5.0)
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "moderation_audit_db_open_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        if not self._initialized:
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS moderation_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        reason TEXT,
                        by_user_id TEXT,
                        context TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_chat ON moderation_audit(chat_id, ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_action ON moderation_audit(action, ts DESC)"
                )
                conn.commit()
                self._initialized = True
            except sqlite3.Error as exc:
                logger.warning(
                    "moderation_audit_schema_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                conn.close()
                return None
        return conn

    # ---- Core API -------------------------------------------------------

    def log_action(
        self,
        chat_id: Any,
        action: str,
        *,
        reason: str | None = None,
        by_user_id: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Append одной записи в audit log.

        Возвращает True если запись добавлена. False если что-то пошло не
        так (path не сконфигурирован, sqlite сломан, action пустой).
        Никогда не raise'ит — модерационный hot path не должен ломаться.
        """
        chat_id_str = str(chat_id or "").strip()
        action_str = str(action or "").strip()
        if not chat_id_str or not action_str:
            return False

        reason_str: str | None
        if reason is None:
            reason_str = None
        else:
            reason_str = str(reason).strip() or None

        by_user_str: str | None
        if by_user_id is None:
            by_user_str = None
        else:
            by_user_str = str(by_user_id).strip() or None

        context_json: str | None
        if context is None:
            context_json = None
        else:
            try:
                context_json = json.dumps(context, ensure_ascii=False, default=str)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "moderation_audit_context_serialize_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                context_json = None

        ts = self._now_fn().isoformat()

        with self._lock:
            conn = self._ensure_db()
            if conn is None:
                return False
            try:
                conn.execute(
                    """
                    INSERT INTO moderation_audit
                        (ts, chat_id, action, reason, by_user_id, context)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ts, chat_id_str, action_str, reason_str, by_user_str, context_json),
                )
                conn.commit()
            except sqlite3.Error as exc:
                logger.warning(
                    "moderation_audit_insert_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    chat_id=chat_id_str,
                    action=action_str,
                )
                conn.close()
                return False
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        # Wave 108: Prometheus counter — импорт лениво чтобы избежать
        # циклической зависимости при импорте на старте.
        try:
            from .metrics.moderation_audit import record_action

            record_action(action_str)
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            "moderation_audit_logged",
            chat_id=chat_id_str,
            action=action_str,
            has_reason=reason_str is not None,
            by_user_id=by_user_str,
        )
        return True

    def query_recent(
        self,
        *,
        chat_id: Any | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Возвращает последние записи (DESC по ts), с опциональными фильтрами.

        Чистая read-only операция. При ошибках возвращает пустой список.
        """
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = 50
        if limit_int <= 0:
            return []
        if limit_int > 1000:
            limit_int = 1000

        clauses: list[str] = []
        params: list[Any] = []
        if chat_id is not None:
            chat_id_str = str(chat_id).strip()
            if chat_id_str:
                clauses.append("chat_id = ?")
                params.append(chat_id_str)
        if action is not None:
            action_str = str(action).strip()
            if action_str:
                clauses.append("action = ?")
                params.append(action_str)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, ts, chat_id, action, reason, by_user_id, context "
            f"FROM moderation_audit {where} ORDER BY ts DESC, id DESC LIMIT ?"
        )
        params.append(limit_int)

        with self._lock:
            conn = self._ensure_db()
            if conn is None:
                return []
            try:
                cursor = conn.execute(sql, tuple(params))
                rows = cursor.fetchall()
            except sqlite3.Error as exc:
                logger.warning(
                    "moderation_audit_query_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return []
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        result: list[dict[str, Any]] = []
        for row in rows:
            id_, ts, c_id, act, reason, by_uid, ctx = row
            ctx_parsed: Any = None
            if ctx:
                try:
                    ctx_parsed = json.loads(ctx)
                except (TypeError, ValueError):
                    ctx_parsed = ctx  # fallback: raw string
            result.append(
                {
                    "id": id_,
                    "ts": ts,
                    "chat_id": c_id,
                    "action": act,
                    "reason": reason,
                    "by_user_id": by_uid,
                    "context": ctx_parsed,
                }
            )
        return result


# Module-level singleton. Path конфигурируется в bootstrap через
# `moderation_audit_log.configure_default_path(...)`.
moderation_audit_log = ModerationAuditLog()

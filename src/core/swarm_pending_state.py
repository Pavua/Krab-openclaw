# -*- coding: utf-8 -*-
"""
src/core/swarm_pending_state.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Phase 1: Персистентное хранилище промежуточного состояния swarm-раунда.

Пишем checkpoint после каждой роли — читаем (Phase 2+) и возобновляем (Phase 3+) позже.
Ошибки записи глушатся silent: state persistence не должна ломать раунд.

Директория: ~/.openclaw/krab_runtime_state/swarm_pending/
Файл: <round_id>.json, временный: <round_id>.json.tmp
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Конфиг (можно переопределить через env)
# ---------------------------------------------------------------------------

_RESUME_ENABLED: bool = os.environ.get("KRAB_SWARM_RESUME_ENABLED", "1").strip() == "1"
_TTL_HOURS: int = int(os.environ.get("KRAB_SWARM_RESUME_TTL_HOURS", "24"))
_MAX_ATTEMPTS: int = int(os.environ.get("KRAB_SWARM_RESUME_MAX_ATTEMPTS", "3"))
_CONTEXT_CLIP: int = int(os.environ.get("KRAB_SWARM_RESUME_CONTEXT_CLIP", "8000"))

# Директория хранения pending-файлов
_PENDING_DIR: Path = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_pending"


# ---------------------------------------------------------------------------
# Генерация round_id
# ---------------------------------------------------------------------------


def make_round_id(team: str, chat_id: int | None = None) -> str:
    """Генерирует уникальный round_id вида ``analysts_{chat_id}_{ts}_{nonce}``."""
    ts = int(time.time())
    nonce = secrets.token_hex(2)  # 4 hex-символа
    chat_part = str(chat_id) if chat_id is not None else "0"
    return f"{team}_{chat_part}_{ts}_{nonce}"


# ---------------------------------------------------------------------------
# Dataclass для состояния
# ---------------------------------------------------------------------------


@dataclass
class SwarmRoundState:
    """Полное промежуточное состояние swarm-раунда."""

    round_id: str
    team: str
    topic: str
    created_at: str  # ISO-8601 UTC
    ttl_expires_at: str  # ISO-8601 UTC
    status: str = "pending"  # pending | interrupted | done | exhausted | failed
    attempt_count: int = 0
    max_attempts: int = _MAX_ATTEMPTS
    # Курсор — с какой роли продолжать
    cursor_role_idx: int = 0
    cursor_role_name: str = ""
    delegation_pending: dict[str, str] | None = None
    # Накопленный контекст (clipped до _CONTEXT_CLIP)
    accumulated_context: str = ""
    # Завершённые роли
    completed_roles: list[dict[str, Any]] = field(default_factory=list)
    delegation_tree: list[str] = field(default_factory=list)
    # Инициатор сообщения (для Phase 2+ resume reply)
    initiator_chat_id: int | None = None
    initiator_message_id: int | None = None
    failure_reason: str | None = None
    # A/B данные — сохраняем для детерминированности при resume
    ab_id: str | None = None
    ab_variant: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Сериализация в JSON-структуру согласно spec."""
        return {
            "round_id": self.round_id,
            "team": self.team,
            "topic": self.topic,
            "created_at": self.created_at,
            "ttl_expires_at": self.ttl_expires_at,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "cursor": {
                "role_idx": self.cursor_role_idx,
                "role_name": self.cursor_role_name,
                "delegation_pending": self.delegation_pending,
            },
            "accumulated_context": self.accumulated_context[:_CONTEXT_CLIP],
            "completed_roles": self.completed_roles,
            "delegation_tree": self.delegation_tree,
            "initiator": {
                "chat_id": self.initiator_chat_id,
                "message_id": self.initiator_message_id,
            },
            "failure_reason": self.failure_reason,
            "ab_id": self.ab_id,
            "ab_variant": self.ab_variant,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SwarmRoundState":
        """Десериализация из JSON-словаря."""
        cursor = d.get("cursor") or {}
        initiator = d.get("initiator") or {}
        return cls(
            round_id=d.get("round_id", ""),
            team=d.get("team", ""),
            topic=d.get("topic", ""),
            created_at=d.get("created_at", ""),
            ttl_expires_at=d.get("ttl_expires_at", ""),
            status=d.get("status", "pending"),
            attempt_count=int(d.get("attempt_count", 0)),
            max_attempts=int(d.get("max_attempts", _MAX_ATTEMPTS)),
            cursor_role_idx=int(cursor.get("role_idx", 0)),
            cursor_role_name=str(cursor.get("role_name", "")),
            delegation_pending=cursor.get("delegation_pending"),
            accumulated_context=d.get("accumulated_context", ""),
            completed_roles=list(d.get("completed_roles", [])),
            delegation_tree=list(d.get("delegation_tree", [])),
            initiator_chat_id=initiator.get("chat_id"),
            initiator_message_id=initiator.get("message_id"),
            failure_reason=d.get("failure_reason"),
            ab_id=d.get("ab_id"),
            ab_variant=d.get("ab_variant"),
        )

    def is_expired(self) -> bool:
        """Проверяет, не истёк ли TTL."""
        try:
            expires = datetime.fromisoformat(self.ttl_expires_at)
            # Добавляем tzinfo если отсутствует (backward compat)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > expires
        except Exception:  # noqa: BLE001
            return False


# ---------------------------------------------------------------------------
# SwarmPendingStore — основной класс для работы с файлами
# ---------------------------------------------------------------------------


class SwarmPendingStore:
    """
    Хранилище промежуточных состояний swarm-раундов.

    Phase 1: только write. Phase 2+: read + resume.
    Все ошибки — silent (логируются, но не бросают наружу).
    """

    def __init__(self, pending_dir: Path | None = None) -> None:
        self._dir = pending_dir or _PENDING_DIR

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _ensure_dir(self) -> bool:
        """Создаёт директорию если нет. Возвращает False при ошибке."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_pending_mkdir_failed", dir=str(self._dir), error=str(exc))
            return False

    def _path(self, round_id: str) -> Path:
        return self._dir / f"{round_id}.json"

    def _tmp_path(self, round_id: str) -> Path:
        return self._dir / f"{round_id}.json.tmp"

    def _atomic_write(self, round_id: str, data: dict[str, Any]) -> bool:
        """Атомарная запись через tmp → rename. Возвращает False при ошибке."""
        if not self._ensure_dir():
            return False
        tmp = self._tmp_path(round_id)
        dest = self._path(round_id)
        try:
            raw = json.dumps(data, ensure_ascii=False, indent=2)
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(dest)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_pending_write_failed", round_id=round_id, error=str(exc))
            # Подчищаем tmp если остался
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return False

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def create_initial(
        self,
        round_id: str,
        team: str,
        topic: str,
        *,
        initiator_chat_id: int | None = None,
        initiator_message_id: int | None = None,
        ab_id: str | None = None,
        ab_variant: str | None = None,
    ) -> bool:
        """
        Создаёт initial pending-файл в начале раунда (status=pending, cursor=0).
        Phase 1: write-only. Вызывается из run_round перед первой ролью.
        """
        if not _RESUME_ENABLED:
            return False
        now = datetime.now(timezone.utc)
        ttl = now.timestamp() + _TTL_HOURS * 3600
        state = SwarmRoundState(
            round_id=round_id,
            team=team,
            topic=topic,
            created_at=now.isoformat(timespec="seconds"),
            ttl_expires_at=datetime.fromtimestamp(ttl, tz=timezone.utc).isoformat(
                timespec="seconds"
            ),
            status="pending",
            attempt_count=0,
            max_attempts=_MAX_ATTEMPTS,
            initiator_chat_id=initiator_chat_id,
            initiator_message_id=initiator_message_id,
            ab_id=ab_id,
            ab_variant=ab_variant,
        )
        ok = self._atomic_write(round_id, state.to_dict())
        if ok:
            logger.info(
                "swarm_pending_created",
                round_id=round_id,
                team=team,
                topic_len=len(topic),
            )
        return ok

    def write_checkpoint(
        self,
        round_id: str,
        *,
        next_role_idx: int,
        next_role_name: str,
        accumulated_context: str,
        completed_roles: list[dict[str, Any]],
        delegation_tree: list[str] | None = None,
        status: str = "pending",
    ) -> bool:
        """
        Обновляет checkpoint после успешного выполнения очередной роли.
        Курсор сдвигается на следующую роль (next_role_idx).
        Silent при ошибках.
        """
        if not _RESUME_ENABLED:
            return False
        # Читаем текущий файл для сохранения инвариантных полей
        existing = self._read(round_id)
        if existing is None:
            # Файл мог не создаться (write в _create_initial упал) — игнорируем
            logger.debug("swarm_pending_checkpoint_no_file", round_id=round_id)
            return False

        existing.cursor_role_idx = next_role_idx
        existing.cursor_role_name = next_role_name
        existing.accumulated_context = accumulated_context
        existing.completed_roles = list(completed_roles)
        existing.delegation_tree = list(delegation_tree or [])
        existing.status = status

        ok = self._atomic_write(round_id, existing.to_dict())
        if ok:
            logger.debug(
                "swarm_pending_checkpoint_written",
                round_id=round_id,
                cursor=next_role_idx,
                roles_done=len(completed_roles),
            )
        return ok

    def mark_round_complete(self, round_id: str) -> None:
        """
        Удаляет pending-файл после успешного завершения раунда.
        Silent при ошибках.
        """
        if not _RESUME_ENABLED:
            return
        target = self._path(round_id)
        try:
            target.unlink(missing_ok=True)
            logger.info("swarm_pending_deleted_complete", round_id=round_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_pending_delete_failed", round_id=round_id, error=str(exc))

    def mark_round_failed(self, round_id: str, reason: str) -> None:
        """
        Помечает раунд как interrupted (не удаляет файл — Phase 2 подхватит).
        Silent при ошибках.
        """
        if not _RESUME_ENABLED:
            return
        existing = self._read(round_id)
        if existing is None:
            return
        existing.status = "interrupted"
        existing.failure_reason = reason
        self._atomic_write(round_id, existing.to_dict())
        logger.info(
            "swarm_pending_marked_failed",
            round_id=round_id,
            reason=reason,
        )

    # -----------------------------------------------------------------------
    # Phase 2+ helpers (read — нужны для тестов и future resume)
    # -----------------------------------------------------------------------

    def _read(self, round_id: str) -> SwarmRoundState | None:
        """Читает pending файл. Возвращает None если не существует или сломан."""
        path = self._path(round_id)
        try:
            if not path.exists():
                return None
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return SwarmRoundState.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_pending_read_failed", round_id=round_id, error=str(exc))
            return None

    def list_pending(self) -> list[SwarmRoundState]:
        """Возвращает все pending-файлы (для Phase 2 startup sweep)."""
        if not self._dir.exists():
            return []
        result = []
        for p in self._dir.glob("*.json"):
            if p.name.endswith(".tmp"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                result.append(SwarmRoundState.from_dict(data))
            except Exception:  # noqa: BLE001
                pass
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

swarm_pending_store = SwarmPendingStore()

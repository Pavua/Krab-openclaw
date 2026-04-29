# -*- coding: utf-8 -*-
"""
ChatResponsePolicyStore — per-chat response policy для Smart Routing (Session 26).

Phase 1 Smart Routing: storage layer. Каждый чат имеет ChatMode (silent/cautious/
normal/chatty) с порогом срабатывания и счётчиком сигналов. Авто-подстройка
режима по истории сигналов за последние 24h, с rate-limit 6h между переходами.

Storage: JSON, thread-safe, путь по умолчанию
~/.openclaw/krab_runtime_state/chat_response_policies.json (паттерн как
inbox_state.json / chat_filters.json).

См. docs/SMART_ROUTING_DESIGN.md (Component 1).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_STORE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "chat_response_policies.json"

# Авто-подстройка
_NEGATIVE_WINDOW_SEC = 24 * 3600
_POSITIVE_WINDOW_SEC = 24 * 3600
_NEGATIVE_THRESHOLD = 5  # >5 negatives → downshift
_POSITIVE_THRESHOLD = 10  # >10 positives + 0 negatives → upshift
_AUTO_ADJUST_COOLDOWN_SEC = 6 * 3600  # 1 transition / 6h max


class ChatMode(str, Enum):
    SILENT = "silent"
    CAUTIOUS = "cautious"
    NORMAL = "normal"
    CHATTY = "chatty"

    @classmethod
    def default(cls) -> "ChatMode":
        return cls.NORMAL

    def default_threshold(self) -> float:
        return {
            "silent": 1.1,  # никогда не triggers (порог > max score)
            "cautious": 0.7,
            "normal": 0.5,
            "chatty": 0.3,
        }[self.value]


@dataclass
class ChatResponsePolicy:
    chat_id: str
    mode: ChatMode = ChatMode.NORMAL
    threshold_override: float | None = None
    negative_signals: int = 0
    positive_signals: int = 0
    last_negative_ts: float | None = None
    last_positive_ts: float | None = None
    last_auto_adjust_ts: float | None = None
    auto_adjust_enabled: bool = True
    blocked_topics: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def effective_threshold(self) -> float:
        if self.threshold_override is not None:
            return self.threshold_override
        return self.mode.default_threshold()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatResponsePolicy":
        mode_raw = data.get("mode", ChatMode.NORMAL.value)
        try:
            mode = ChatMode(mode_raw) if not isinstance(mode_raw, ChatMode) else mode_raw
        except ValueError:
            mode = ChatMode.NORMAL
        return cls(
            chat_id=str(data["chat_id"]),
            mode=mode,
            threshold_override=data.get("threshold_override"),
            negative_signals=int(data.get("negative_signals", 0)),
            positive_signals=int(data.get("positive_signals", 0)),
            last_negative_ts=data.get("last_negative_ts"),
            last_positive_ts=data.get("last_positive_ts"),
            last_auto_adjust_ts=data.get("last_auto_adjust_ts"),
            auto_adjust_enabled=bool(data.get("auto_adjust_enabled", True)),
            blocked_topics=list(data.get("blocked_topics") or []),
            notes=str(data.get("notes") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


# Mode transition lattice
_DOWNSHIFT = {
    ChatMode.CHATTY: ChatMode.NORMAL,
    ChatMode.NORMAL: ChatMode.CAUTIOUS,
    # CAUTIOUS / SILENT — не двигаем дальше при negatives (silent уже max-restraint)
}
_UPSHIFT = {
    ChatMode.CAUTIOUS: ChatMode.NORMAL,
    ChatMode.NORMAL: ChatMode.CHATTY,
    # SILENT — намеренно не апшифтим (пользовательский lock)
    # CHATTY — потолок
}


class ChatResponsePolicyStore:
    """Thread-safe JSON-backed store policy для каждого чата."""

    def __init__(self, path: Path = _STORE_PATH):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cache: dict[str, ChatResponsePolicy] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────

    def _load(self) -> None:
        with self._lock:
            self._cache.clear()
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text())
            except Exception as e:  # noqa: BLE001
                logger.warning("chat_response_policy_load_failed", error=str(e))
                return
            if not isinstance(raw, dict):
                logger.warning("chat_response_policy_invalid_format")
                return
            for chat_id, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                payload = {**payload, "chat_id": str(chat_id)}
                try:
                    policy = ChatResponsePolicy.from_dict(payload)
                    self._cache[policy.chat_id] = policy
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "chat_response_policy_decode_failed",
                        chat_id=chat_id,
                        error=str(e),
                    )

    def _persist(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {cid: p.to_dict() for cid, p in self._cache.items()}
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                tmp.replace(self._path)
            except Exception as e:  # noqa: BLE001
                logger.warning("chat_response_policy_save_failed", error=str(e))
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:  # noqa: BLE001
                    pass

    # ── CRUD ──────────────────────────────────────────────────

    def get_policy(self, chat_id: str | int) -> ChatResponsePolicy:
        cid = str(chat_id)
        with self._lock:
            policy = self._cache.get(cid)
            if policy is not None:
                return policy
            # default — не персистим до явного update
            return ChatResponsePolicy(chat_id=cid)

    def update_policy(self, chat_id: str | int, **fields_: Any) -> ChatResponsePolicy:
        cid = str(chat_id)
        with self._lock:
            policy = self._cache.get(cid) or ChatResponsePolicy(chat_id=cid)
            for key, value in fields_.items():
                if not hasattr(policy, key):
                    logger.warning("chat_response_policy_unknown_field", field=key)
                    continue
                if key == "mode" and not isinstance(value, ChatMode):
                    try:
                        value = ChatMode(value)
                    except ValueError:
                        logger.warning(
                            "chat_response_policy_invalid_mode",
                            chat_id=cid,
                            mode=value,
                        )
                        continue
                setattr(policy, key, value)
            policy.updated_at = time.time()
            self._cache[cid] = policy
            self._persist()
            return policy

    def reset_policy(self, chat_id: str | int) -> bool:
        cid = str(chat_id)
        with self._lock:
            existed = cid in self._cache
            self._cache.pop(cid, None)
            if existed:
                self._persist()
                logger.info("chat_response_policy_reset", chat_id=cid)
            return existed

    def list_all(self) -> list[ChatResponsePolicy]:
        with self._lock:
            return sorted(self._cache.values(), key=lambda p: p.chat_id)

    # ── Signals ───────────────────────────────────────────────

    def record_negative_signal(self, chat_id: str | int, *, reason: str = "") -> ChatResponsePolicy:
        cid = str(chat_id)
        now = time.time()
        with self._lock:
            policy = self._cache.get(cid) or ChatResponsePolicy(chat_id=cid)
            policy.negative_signals += 1
            policy.last_negative_ts = now
            policy.updated_at = now
            self._cache[cid] = policy
            self._maybe_auto_adjust(policy, now=now)
            self._persist()
            logger.info(
                "chat_response_policy_negative",
                chat_id=cid,
                count=policy.negative_signals,
                reason=reason,
                mode=policy.mode.value,
            )
            return policy

    def record_positive_signal(self, chat_id: str | int, *, reason: str = "") -> ChatResponsePolicy:
        cid = str(chat_id)
        now = time.time()
        with self._lock:
            policy = self._cache.get(cid) or ChatResponsePolicy(chat_id=cid)
            policy.positive_signals += 1
            policy.last_positive_ts = now
            policy.updated_at = now
            self._cache[cid] = policy
            self._maybe_auto_adjust(policy, now=now)
            self._persist()
            logger.info(
                "chat_response_policy_positive",
                chat_id=cid,
                count=policy.positive_signals,
                reason=reason,
                mode=policy.mode.value,
            )
            return policy

    # ── Auto-adjust ───────────────────────────────────────────

    def _maybe_auto_adjust(self, policy: ChatResponsePolicy, *, now: float | None = None) -> bool:
        """Возможный сдвиг режима. Вызывается под self._lock.

        Rules:
        - >5 negatives за последние 24h → downshift (CHATTY→NORMAL, NORMAL→CAUTIOUS).
        - >10 positives за 24h при 0 negatives за то же окно → upshift
          (CAUTIOUS→NORMAL, NORMAL→CHATTY).
        - SILENT не двигается автоматически (явный lock пользователя).
        - Rate limit: один переход каждые 6 часов.

        Returns True если режим изменился.
        """
        if not policy.auto_adjust_enabled:
            return False
        if policy.mode == ChatMode.SILENT:
            return False

        now = now if now is not None else time.time()

        # rate-limit
        if (
            policy.last_auto_adjust_ts is not None
            and (now - policy.last_auto_adjust_ts) < _AUTO_ADJUST_COOLDOWN_SEC
        ):
            return False

        recent_negative = (
            policy.last_negative_ts is not None
            and (now - policy.last_negative_ts) <= _NEGATIVE_WINDOW_SEC
        )
        recent_positive = (
            policy.last_positive_ts is not None
            and (now - policy.last_positive_ts) <= _POSITIVE_WINDOW_SEC
        )

        # Downshift по negatives
        if (
            recent_negative
            and policy.negative_signals > _NEGATIVE_THRESHOLD
            and policy.mode in _DOWNSHIFT
        ):
            old = policy.mode
            policy.mode = _DOWNSHIFT[old]
            policy.last_auto_adjust_ts = now
            policy.updated_at = now
            logger.info(
                "chat_response_policy_auto_downshift",
                chat_id=policy.chat_id,
                from_mode=old.value,
                to_mode=policy.mode.value,
                negatives=policy.negative_signals,
            )
            return True

        # Upshift по positives при отсутствии recent negatives
        if (
            recent_positive
            and policy.positive_signals > _POSITIVE_THRESHOLD
            and not recent_negative
            and policy.mode in _UPSHIFT
        ):
            old = policy.mode
            policy.mode = _UPSHIFT[old]
            policy.last_auto_adjust_ts = now
            policy.updated_at = now
            logger.info(
                "chat_response_policy_auto_upshift",
                chat_id=policy.chat_id,
                from_mode=old.value,
                to_mode=policy.mode.value,
                positives=policy.positive_signals,
            )
            return True

        return False


# Singleton (lazy на default-path; тесты создают свои инстансы с tmp_path)
_singleton: ChatResponsePolicyStore | None = None


def get_store() -> ChatResponsePolicyStore:
    global _singleton
    if _singleton is None:
        _singleton = ChatResponsePolicyStore()
    return _singleton

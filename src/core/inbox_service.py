# -*- coding: utf-8 -*-
"""
inbox_service.py — persisted owner-visible inbox и escalation foundation.

Что это:
- единый persisted inbox-state для текущей macOS-учётки;
- базовый identity-конверт для событий runtime (`operator/account/channel/team/trace`);
- минимальный сервис, который уже сейчас можно подключать к reminders,
  proactive watch, approvals и будущим transport/task слоям.

Зачем нужно:
- master plan требует, чтобы pending actions, alerts и escalations не терялись;
- reminders и watch уже существуют, но раньше жили разрозненно и не давали
  владельцу одного owner-visible списка открытых задач/сигналов;
- foundation должен переживать restart и не смешиваться между macOS-учётками.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger
from .operator_identity import build_trace_id, current_account_id, current_operator_id


logger = get_logger(__name__)


def _now_utc_iso() -> str:
    """Возвращает UTC timestamp в детерминированном формате."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_state_path() -> Path:
    """
    Возвращает per-account путь inbox-state.

    Shared repo у нас общий, но inbox относится к mutable runtime-state, поэтому
    его нельзя хранить в репозитории, иначе учётки начнут перетирать друг другу
    pending items и статусы подтверждения.
    """
    return Path.home() / ".openclaw" / "krab_runtime_state" / "inbox_state.json"


@dataclass
class InboxIdentity:
    """Базовый identity-конверт inbox item-а."""

    operator_id: str
    account_id: str
    channel_id: str = ""
    team_id: str = ""
    trace_id: str = ""
    approval_scope: str = "owner"


@dataclass
class InboxItem:
    """Persisted inbox item."""

    item_id: str
    dedupe_key: str
    kind: str
    source: str
    status: str
    severity: str
    title: str
    body: str
    created_at_utc: str
    updated_at_utc: str
    identity: InboxIdentity
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InboxItem":
        """Восстанавливает item из persisted JSON."""
        identity_payload = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        return cls(
            item_id=str(payload.get("item_id") or ""),
            dedupe_key=str(payload.get("dedupe_key") or ""),
            kind=str(payload.get("kind") or ""),
            source=str(payload.get("source") or ""),
            status=str(payload.get("status") or "open"),
            severity=str(payload.get("severity") or "info"),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            created_at_utc=str(payload.get("created_at_utc") or ""),
            updated_at_utc=str(payload.get("updated_at_utc") or ""),
            identity=InboxIdentity(
                operator_id=str(identity_payload.get("operator_id") or ""),
                account_id=str(identity_payload.get("account_id") or ""),
                channel_id=str(identity_payload.get("channel_id") or ""),
                team_id=str(identity_payload.get("team_id") or ""),
                trace_id=str(identity_payload.get("trace_id") or ""),
                approval_scope=str(identity_payload.get("approval_scope") or "owner"),
            ),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализует item в JSON-friendly dict."""
        payload = asdict(self)
        payload["identity"] = asdict(self.identity)
        return payload


class InboxService:
    """
    Persisted inbox для owner-visible pending actions и escalations.

    Первая версия намеренно минималистична:
    - один JSON-state;
    - детерминированный upsert по `dedupe_key`;
    - read/write API без тяжёлой БД;
    - пригодно для reminders/watch прямо сейчас и расширяемо под approvals/task bus.
    """

    _open_statuses = {"open", "acked"}
    _closed_statuses = {"done", "cancelled", "approved", "rejected"}
    _allowed_statuses = _open_statuses | _closed_statuses
    _allowed_severities = {"info", "warning", "error"}

    def __init__(self, *, state_path: Path | None = None, max_items: int = 200) -> None:
        self.state_path = state_path or _default_state_path()
        self.max_items = max(20, int(max_items or 200))

    @staticmethod
    def build_identity(
        *,
        channel_id: str = "",
        team_id: str = "",
        trace_id: str = "",
        approval_scope: str = "owner",
    ) -> InboxIdentity:
        """Строит identity-конверт для текущей учётки."""
        return InboxIdentity(
            operator_id=current_operator_id(),
            account_id=current_account_id(),
            channel_id=str(channel_id or "").strip(),
            team_id=str(team_id or "").strip(),
            trace_id=str(trace_id or "").strip(),
            approval_scope=str(approval_scope or "owner").strip() or "owner",
        )

    def _normalize_status(self, value: str) -> str:
        normalized = str(value or "").strip().lower() or "open"
        if normalized not in self._allowed_statuses:
            raise ValueError("inbox_invalid_status")
        return normalized

    def _normalize_severity(self, value: str) -> str:
        normalized = str(value or "").strip().lower() or "info"
        if normalized not in self._allowed_severities:
            raise ValueError("inbox_invalid_severity")
        return normalized

    def _load_state(self) -> dict[str, Any]:
        """Читает persisted JSON без падения runtime."""
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("inbox_state_read_failed", path=str(self.state_path), error=str(exc))
            return {}

    def _save_items(self, items: list[InboxItem]) -> None:
        """Сохраняет inbox-state детерминированным JSON."""
        payload = {
            "updated_at_utc": _now_utc_iso(),
            "items": [item.to_dict() for item in items[: self.max_items]],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_items(self) -> list[InboxItem]:
        """Возвращает список items из persisted state."""
        payload = self._load_state()
        rows = payload.get("items") if isinstance(payload, dict) else []
        items: list[InboxItem] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                item = InboxItem.from_dict(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning("inbox_item_restore_failed", error=str(exc))
                continue
            if item.item_id:
                items.append(item)
        items.sort(key=lambda item: item.updated_at_utc, reverse=True)
        return items[: self.max_items]

    def list_items(
        self,
        *,
        status: str = "",
        kind: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Возвращает inbox items с простыми фильтрами."""
        normalized_status = str(status or "").strip().lower()
        normalized_kind = str(kind or "").strip().lower()
        rows: list[dict[str, Any]] = []
        for item in self._load_items():
            if normalized_status and item.status != normalized_status:
                continue
            if normalized_kind and item.kind != normalized_kind:
                continue
            rows.append(item.to_dict())
            if len(rows) >= max(1, int(limit or 20)):
                break
        return rows

    def get_summary(self) -> dict[str, Any]:
        """Возвращает краткий owner-facing summary inbox."""
        items = self._load_items()
        open_items = [item for item in items if item.status in self._open_statuses]
        warning_items = [item for item in open_items if item.severity in {"warning", "error"}]
        reminder_items = [item for item in open_items if item.kind == "reminder"]
        escalation_items = [item for item in open_items if item.kind.startswith("watch_")]
        owner_task_items = [item for item in open_items if item.kind == "owner_task"]
        approval_items = [item for item in open_items if item.kind == "approval_request"]
        owner_request_items = [item for item in open_items if item.kind == "owner_request"]
        owner_mention_items = [item for item in open_items if item.kind == "owner_mention"]
        return {
            "state_path": str(self.state_path),
            "account_id": current_account_id(),
            "operator_id": current_operator_id(),
            "total_items": len(items),
            "open_items": len(open_items),
            "attention_items": len(warning_items),
            "pending_reminders": len(reminder_items),
            "open_escalations": len(escalation_items),
            "pending_owner_tasks": len(owner_task_items),
            "pending_approvals": len(approval_items),
            "pending_owner_requests": len(owner_request_items),
            "pending_owner_mentions": len(owner_mention_items),
            "latest_open_items": [item.to_dict() for item in open_items[:5]],
        }

    @staticmethod
    def _dedupe_key(prefix: str, raw_key: str = "") -> str:
        """
        Возвращает dedupe-key для item-а.

        Если вызывающий не дал внешний ключ, создаём локальный уникальный id:
        для owner-created tasks/approvals это предпочтительнее, чем случайно
        склеить разные запросы только по похожему заголовку.
        """
        normalized_prefix = str(prefix or "item").strip().lower() or "item"
        normalized_key = str(raw_key or "").strip()
        if normalized_key:
            return f"{normalized_prefix}:{normalized_key}"
        return f"{normalized_prefix}:{uuid.uuid4().hex[:12]}"

    def upsert_item(
        self,
        *,
        dedupe_key: str,
        kind: str,
        source: str,
        title: str,
        body: str,
        severity: str = "info",
        status: str = "open",
        identity: InboxIdentity | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Создаёт или обновляет inbox item по dedupe_key."""
        dedupe = str(dedupe_key or "").strip()
        if not dedupe:
            raise ValueError("inbox_empty_dedupe_key")
        normalized_kind = str(kind or "").strip().lower()
        if not normalized_kind:
            raise ValueError("inbox_empty_kind")
        normalized_source = str(source or "").strip().lower() or "runtime"
        normalized_status = self._normalize_status(status)
        normalized_severity = self._normalize_severity(severity)
        now_iso = _now_utc_iso()
        items = self._load_items()
        current_identity = identity or self.build_identity()
        item = next((row for row in items if row.dedupe_key == dedupe), None)
        created = item is None
        if item is None:
            item = InboxItem(
                item_id=uuid.uuid4().hex[:12],
                dedupe_key=dedupe,
                kind=normalized_kind,
                source=normalized_source,
                status=normalized_status,
                severity=normalized_severity,
                title=str(title or "").strip(),
                body=str(body or "").strip(),
                created_at_utc=now_iso,
                updated_at_utc=now_iso,
                identity=current_identity,
                metadata=dict(metadata or {}),
            )
            items.insert(0, item)
        else:
            item.kind = normalized_kind
            item.source = normalized_source
            item.status = normalized_status
            item.severity = normalized_severity
            item.title = str(title or "").strip() or item.title
            item.body = str(body or "").strip() or item.body
            item.updated_at_utc = now_iso
            item.identity = current_identity
            item.metadata = dict(metadata or {})
            items = [row for row in items if row.item_id != item.item_id]
            items.insert(0, item)

        self._save_items(items)
        return {
            "ok": True,
            "created": created,
            "item": item.to_dict(),
        }

    def set_item_status(self, item_id: str, *, status: str) -> dict[str, Any]:
        """Обновляет статус item по id."""
        normalized_status = self._normalize_status(status)
        target_id = str(item_id or "").strip()
        if not target_id:
            return {"ok": False, "error": "inbox_empty_item_id"}
        items = self._load_items()
        item = next((row for row in items if row.item_id == target_id), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        item.status = normalized_status
        item.updated_at_utc = _now_utc_iso()
        items = [row for row in items if row.item_id != item.item_id]
        items.insert(0, item)
        self._save_items(items)
        return {"ok": True, "item": item.to_dict()}

    def set_status_by_dedupe(self, dedupe_key: str, *, status: str) -> dict[str, Any]:
        """Обновляет статус item по dedupe_key, если item существует."""
        normalized_status = self._normalize_status(status)
        dedupe = str(dedupe_key or "").strip()
        items = self._load_items()
        item = next((row for row in items if row.dedupe_key == dedupe), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        item.status = normalized_status
        item.updated_at_utc = _now_utc_iso()
        items = [row for row in items if row.item_id != item.item_id]
        items.insert(0, item)
        self._save_items(items)
        return {"ok": True, "item": item.to_dict()}

    def upsert_reminder(
        self,
        *,
        reminder_id: str,
        chat_id: str,
        text: str,
        due_at_iso: str,
        retries: int = 0,
        last_error: str = "",
    ) -> dict[str, Any]:
        """Публикует reminder как pending inbox item."""
        reminder_text = str(text or "").strip()
        title = "Напоминание ждёт выполнения"
        body = (
            f"Чат: `{chat_id}`\n"
            f"Когда: `{due_at_iso}`\n"
            f"Текст: {reminder_text}"
            + (f"\nПовторные попытки: `{retries}`" if int(retries or 0) > 0 else "")
            + (f"\nПоследняя ошибка: `{last_error}`" if str(last_error or "").strip() else "")
        )
        severity = "warning" if int(retries or 0) > 0 or str(last_error or "").strip() else "info"
        return self.upsert_item(
            dedupe_key=f"reminder:{str(reminder_id or '').strip()}",
            kind="reminder",
            source="scheduler",
            title=title,
            body=body,
            severity=severity,
            status="open",
            identity=self.build_identity(channel_id=str(chat_id or "").strip()),
            metadata={
                "reminder_id": str(reminder_id or "").strip(),
                "chat_id": str(chat_id or "").strip(),
                "text": reminder_text,
                "due_at_iso": str(due_at_iso or "").strip(),
                "retries": int(retries or 0),
                "last_error": str(last_error or "").strip(),
            },
        )

    def resolve_reminder(self, reminder_id: str, *, status: str = "done") -> dict[str, Any]:
        """Закрывает inbox item reminder-а."""
        return self.set_status_by_dedupe(f"reminder:{str(reminder_id or '').strip()}", status=status)

    def upsert_owner_task(
        self,
        *,
        title: str,
        body: str,
        task_key: str = "",
        source: str = "owner",
        severity: str = "info",
        channel_id: str = "",
        team_id: str = "",
        trace_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Публикует owner-task в persisted inbox."""
        return self.upsert_item(
            dedupe_key=self._dedupe_key("task", task_key),
            kind="owner_task",
            source=str(source or "owner").strip().lower() or "owner",
            title=str(title or "").strip(),
            body=str(body or "").strip(),
            severity=severity,
            status="open",
            identity=self.build_identity(
                channel_id=channel_id,
                team_id=team_id or "owner",
                trace_id=str(trace_id or "").strip() or build_trace_id("task", task_key or title),
                approval_scope="owner",
            ),
            metadata=dict(metadata or {}),
        )

    def upsert_approval_request(
        self,
        *,
        title: str,
        body: str,
        request_key: str = "",
        source: str = "owner",
        severity: str = "warning",
        channel_id: str = "",
        team_id: str = "",
        approval_scope: str = "owner",
        requested_action: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Публикует approval-request в persisted inbox."""
        payload_metadata = dict(metadata or {})
        if requested_action:
            payload_metadata["requested_action"] = str(requested_action).strip()
        if approval_scope:
            payload_metadata["approval_scope"] = str(approval_scope).strip()
        return self.upsert_item(
            dedupe_key=self._dedupe_key("approval", request_key),
            kind="approval_request",
            source=str(source or "owner").strip().lower() or "owner",
            title=str(title or "").strip(),
            body=str(body or "").strip(),
            severity=severity,
            status="open",
            identity=self.build_identity(
                channel_id=channel_id,
                team_id=team_id or "owner",
                trace_id=build_trace_id("approval", request_key or title, requested_action),
                approval_scope=str(approval_scope or "owner").strip() or "owner",
            ),
            metadata=payload_metadata,
        )

    def resolve_approval(self, item_id: str, *, approved: bool) -> dict[str, Any]:
        """Закрывает approval-request решением owner-а."""
        target_id = str(item_id or "").strip()
        if not target_id:
            return {"ok": False, "error": "inbox_empty_item_id"}
        item = next((row for row in self._load_items() if row.item_id == target_id), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        if item.kind != "approval_request":
            return {"ok": False, "error": "inbox_item_not_approval"}
        return self.set_item_status(target_id, status="approved" if approved else "rejected")

    def upsert_incoming_owner_request(
        self,
        *,
        chat_id: str,
        message_id: str,
        text: str,
        sender_id: str = "",
        sender_username: str = "",
        chat_type: str = "private",
        is_reply_to_me: bool = False,
        has_trigger: bool = False,
        has_photo: bool = False,
        has_audio: bool = False,
    ) -> dict[str, Any]:
        """
        Публикует входящий owner request / mention в persisted inbox.

        Почему это отдельный helper:
        - userbot не должен вручную собирать payload item-а в нескольких местах;
        - distinction `private request` vs `group mention` нужна уже сейчас для
          owner workflow, а дальше её переиспользует transport/task слой.
        """
        normalized_chat_type = str(chat_type or "private").strip().lower() or "private"
        normalized_chat_id = str(chat_id or "").strip()
        normalized_message_id = str(message_id or "").strip()
        excerpt = str(text or "").strip()
        kind = "owner_request" if normalized_chat_type == "private" else "owner_mention"
        title = "Входящий owner request" if kind == "owner_request" else "Упоминание / owner request в чате"
        body_lines = [
            f"Чат: `{normalized_chat_id}`",
            f"Сообщение: `{normalized_message_id}`",
        ]
        if sender_username:
            body_lines.append(f"От: `@{sender_username}`")
        elif sender_id:
            body_lines.append(f"От: `{sender_id}`")
        if excerpt:
            body_lines.append(f"Текст: {excerpt}")
        if has_photo:
            body_lines.append("Вложение: `photo`")
        if has_audio:
            body_lines.append("Вложение: `audio`")
        if is_reply_to_me:
            body_lines.append("Контекст: reply_to_me")
        if has_trigger:
            body_lines.append("Контекст: explicit_trigger")
        return self.upsert_item(
            dedupe_key=f"incoming:{normalized_chat_id}:{normalized_message_id}",
            kind=kind,
            source="telegram-userbot",
            title=title,
            body="\n".join(body_lines),
            severity="info",
            status="open",
            identity=self.build_identity(
                channel_id=normalized_chat_id,
                team_id="owner",
                trace_id=build_trace_id("telegram", normalized_chat_id, normalized_message_id),
                approval_scope="owner",
            ),
            metadata={
                "chat_id": normalized_chat_id,
                "message_id": normalized_message_id,
                "chat_type": normalized_chat_type,
                "sender_id": str(sender_id or "").strip(),
                "sender_username": str(sender_username or "").strip(),
                "is_reply_to_me": bool(is_reply_to_me),
                "has_trigger": bool(has_trigger),
                "has_photo": bool(has_photo),
                "has_audio": bool(has_audio),
                "text_excerpt": excerpt[:500],
            },
        )

    def report_watch_transition(
        self,
        *,
        reason: str,
        digest: str,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Публикует действительно важные watch transitions в inbox.

        Осознанно не тащим сюда все переходы:
        - `route_model_changed` и `frontmost_app_changed` полезны для memory/history,
          но не обязаны захламлять owner inbox;
        - inbox в foundation-слое должен держать только actionable изменения.
        """
        normalized_reason = str(reason or "").strip().lower()
        metadata = dict(snapshot or {})
        if normalized_reason == "gateway_down":
            return self.upsert_item(
                dedupe_key="watch:gateway_down",
                kind="watch_alert",
                source="proactive-watch",
                title="Gateway недоступен",
                body=str(digest or "").strip(),
                severity="error",
                status="open",
                identity=self.build_identity(
                    team_id="ops",
                    trace_id=build_trace_id("watch", normalized_reason, metadata.get("ts_utc")),
                ),
                metadata=metadata,
            )
        if normalized_reason == "gateway_recovered":
            return self.set_status_by_dedupe("watch:gateway_down", status="done")
        if normalized_reason == "scheduler_backlog_created":
            return self.upsert_item(
                dedupe_key="watch:scheduler_backlog",
                kind="watch_alert",
                source="proactive-watch",
                title="Scheduler накопил backlog",
                body=str(digest or "").strip(),
                severity="warning",
                status="open",
                identity=self.build_identity(
                    team_id="ops",
                    trace_id=build_trace_id("watch", normalized_reason, metadata.get("ts_utc")),
                ),
                metadata=metadata,
            )
        if normalized_reason == "scheduler_backlog_cleared":
            return self.set_status_by_dedupe("watch:scheduler_backlog", status="done")
        return {"ok": False, "error": "watch_reason_not_actionable"}


inbox_service = InboxService()

__all__ = ["InboxIdentity", "InboxItem", "InboxService", "inbox_service"]

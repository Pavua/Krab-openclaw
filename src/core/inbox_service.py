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
from datetime import datetime, timedelta, timezone
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
        identity_payload = (
            payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        )
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
    _stale_processing_after = timedelta(minutes=15)
    _stale_open_after = timedelta(hours=12)

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

    @staticmethod
    def _normalize_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
        """Нормализует metadata item-а в безопасный dict."""
        return dict(payload or {})

    @staticmethod
    def _append_workflow_event(
        metadata: dict[str, Any] | None,
        *,
        action: str,
        actor: str,
        status: str,
        note: str = "",
        extra: dict[str, Any] | None = None,
        max_events: int = 12,
    ) -> dict[str, Any]:
        """
        Добавляет компактное событие в workflow trail item-а.

        Trail нужен не ради аудита "на века", а чтобы handoff/runtime snapshot
        могли объяснить судьбу owner-request без чтения всего лога Telegram.
        """
        normalized = InboxService._normalize_metadata(metadata)
        events_raw = normalized.get("workflow_events")
        events = (
            [dict(row) for row in events_raw if isinstance(row, dict)]
            if isinstance(events_raw, list)
            else []
        )
        event = {
            "ts_utc": _now_utc_iso(),
            "action": str(action or "updated").strip().lower() or "updated",
            "actor": str(actor or "runtime").strip().lower() or "runtime",
            "status": str(status or "").strip().lower(),
        }
        if note:
            event["note"] = str(note).strip()
        for key, value in dict(extra or {}).items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            event[str(key)] = value
        events.insert(0, event)
        normalized["workflow_events"] = events[: max(1, int(max_events or 12))]
        return normalized

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

    def _find_item_by_id(self, item_id: str) -> InboxItem | None:
        """Возвращает item по id из persisted state, если он существует."""
        target_id = str(item_id or "").strip()
        if not target_id:
            return None
        return next((row for row in self._load_items() if row.item_id == target_id), None)

    @staticmethod
    def _is_owner_action_actor(actor: str) -> bool:
        """Определяет, относится ли actor к owner-facing контуру управления."""
        normalized = str(actor or "").strip().lower()
        return normalized in {"owner", "telegram-owner", "owner-ui"}

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
            # "all" или пустой → без фильтра по статусу
            if normalized_status and normalized_status != "all":
                if normalized_status == "open" and item.status not in self._open_statuses:
                    continue
                elif normalized_status != "open" and item.status != normalized_status:
                    continue
            if normalized_kind and item.kind != normalized_kind:
                continue
            rows.append(item.to_dict())
            if len(rows) >= max(1, int(limit or 20)):
                break
        return rows

    def filter_by_age(
        self,
        *,
        older_than_date: str,
        kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Возвращает items старше указанной даты, отсортированные по created_at_utc (старые первыми).

        Args:
            older_than_date: ISO timestamp для фильтрации (items старше этой даты)
            kind: Опциональный фильтр по типу item-а
            status: Опциональный фильтр по статусу
            limit: Максимальное количество возвращаемых items
        """
        try:
            # Парсим ISO timestamp
            cutoff_date = datetime.fromisoformat(
                str(older_than_date or "").strip().replace("Z", "+00:00")
            )
        except (ValueError, TypeError) as exc:
            logger.warning("filter_by_age_invalid_date", date=older_than_date, error=str(exc))
            return []

        normalized_kind = str(kind or "").strip().lower()
        normalized_status = str(status or "").strip().lower()

        # Загружаем все items и фильтруем
        all_items = self._load_items()
        filtered_items: list[InboxItem] = []

        for item in all_items:
            try:
                # Парсим created_at_utc item-а
                item_date = datetime.fromisoformat(item.created_at_utc.replace("Z", "+00:00"))

                # Проверяем, что item старше cutoff_date
                if item_date >= cutoff_date:
                    continue

                # Применяем фильтры kind и status
                if normalized_kind and item.kind != normalized_kind:
                    continue
                if normalized_status == "open" and item.status not in self._open_statuses:
                    continue
                elif normalized_status and item.status != normalized_status:
                    continue

                filtered_items.append(item)

            except (ValueError, TypeError) as exc:
                logger.warning(
                    "filter_by_age_invalid_item_date",
                    item_id=item.item_id,
                    date=item.created_at_utc,
                    error=str(exc),
                )
                continue

        # Сортируем по created_at_utc (старые первыми)
        filtered_items.sort(key=lambda item: item.created_at_utc)

        # Применяем лимит и возвращаем как dict
        return [item.to_dict() for item in filtered_items[: max(1, int(limit or 50))]]

    def list_stale_processing_items(
        self,
        *,
        kind: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Возвращает реально застрявшие `acked` item-ы с возрастом обработки.

        Нужен для owner-facing remediation runbook, где владелец сначала видит
        stale-кандидатов, а затем может применить безопасный bulk-action только
        к ним.
        """
        normalized_kind = str(kind or "").strip().lower()
        stale_items: list[tuple[datetime, InboxItem]] = []
        for item in self._load_items():
            if normalized_kind and item.kind != normalized_kind:
                continue
            if not self._is_processing_stale(item):
                continue
            activity_at = self._parse_item_activity_at(item)
            if activity_at is None:
                continue
            stale_items.append((activity_at, item))

        stale_items.sort(key=lambda row: row[0])
        rows: list[dict[str, Any]] = []
        now_utc = datetime.now(timezone.utc)
        for activity_at, item in stale_items[: max(1, int(limit or 20))]:
            payload = item.to_dict()
            payload["processing_age_sec"] = max(0, int((now_utc - activity_at).total_seconds()))
            rows.append(payload)
        return rows

    def list_stale_open_items(
        self,
        *,
        kind: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Возвращает старые `open` item-ы с возрастом, чтобы не смешивать их с fresh inbox.

        Такой список нужен для truthful owner UI и для безопасного remediation
        legacy-open запросов, которые уже точно не являются "новыми".
        """
        normalized_kind = str(kind or "").strip().lower()
        stale_items: list[tuple[datetime, InboxItem]] = []
        for item in self._load_items():
            if normalized_kind and item.kind != normalized_kind:
                continue
            if not self._is_open_stale(item):
                continue
            activity_at = self._parse_item_activity_at(item)
            if activity_at is None:
                continue
            stale_items.append((activity_at, item))

        stale_items.sort(key=lambda row: row[0])
        rows: list[dict[str, Any]] = []
        now_utc = datetime.now(timezone.utc)
        for activity_at, item in stale_items[: max(1, int(limit or 20))]:
            payload = item.to_dict()
            payload["open_age_sec"] = max(0, int((now_utc - activity_at).total_seconds()))
            rows.append(payload)
        return rows

    def archive_by_kind(
        self,
        *,
        kind: str,
        actor: str = "system-cleanup",
        note: str = "",
    ) -> dict[str, Any]:
        """
        Архивирует все items указанного kind, устанавливая статус "cancelled".

        Args:
            kind: Тип items для архивирования
            actor: Актор для записи в workflow events (по умолчанию "system-cleanup")
            note: Опциональная заметка для workflow event

        Returns:
            dict с archived_count и item_ids списком
        """
        normalized_kind = str(kind or "").strip().lower()
        if not normalized_kind:
            return {"ok": False, "error": "inbox_empty_kind", "archived_count": 0, "item_ids": []}

        normalized_actor = str(actor or "system-cleanup").strip().lower() or "system-cleanup"
        items = self._load_items()
        archived_items: list[InboxItem] = []
        archived_ids: list[str] = []

        # Находим все items указанного kind
        for item in items:
            if item.kind == normalized_kind:
                # Обновляем статус на "cancelled"
                item.status = "cancelled"
                item.updated_at_utc = _now_utc_iso()

                # Обновляем metadata с resolution информацией
                metadata = self._normalize_metadata(item.metadata)
                metadata["last_action_actor"] = normalized_actor
                metadata["last_action_status"] = "cancelled"
                metadata["last_action_at_utc"] = item.updated_at_utc
                metadata["resolved_at_utc"] = item.updated_at_utc
                metadata["resolved_by"] = normalized_actor

                if note:
                    metadata["last_action_note"] = str(note).strip()
                    metadata["resolution_note"] = str(note).strip()

                # Добавляем workflow event
                item.metadata = self._append_workflow_event(
                    metadata,
                    action="archived",
                    actor=normalized_actor,
                    status="cancelled",
                    note=note,
                )

                archived_items.append(item)
                archived_ids.append(item.item_id)

        # Сохраняем обновленные items
        if archived_items:
            self._save_items(items)

        return {
            "ok": True,
            "archived_count": len(archived_items),
            "item_ids": archived_ids,
        }

    def bulk_update_status(
        self,
        *,
        item_ids: list[str],
        status: str,
        actor: str = "owner",
        note: str = "",
        max_batch_size: int = 50,
    ) -> dict[str, Any]:
        """
        Обновляет статус для множества items в одной операции.

        Args:
            item_ids: Список ID items для обновления
            status: Новый статус для всех items
            actor: Актор для записи в workflow events
            note: Опциональная заметка для workflow events
            max_batch_size: Максимальный размер batch (по умолчанию 50)

        Returns:
            dict с success_count, error_count и details списком
        """
        normalized_status = self._normalize_status(status)
        normalized_actor = str(actor or "owner").strip().lower() or "owner"

        # Валидируем размер batch
        ids_list = [
            str(item_id or "").strip() for item_id in (item_ids or []) if str(item_id or "").strip()
        ]
        if len(ids_list) > max_batch_size:
            return {
                "ok": False,
                "error": f"batch_size_exceeded_max_{max_batch_size}",
                "success_count": 0,
                "error_count": len(ids_list),
                "details": [],
            }

        if not ids_list:
            return {
                "ok": True,
                "success_count": 0,
                "error_count": 0,
                "details": [],
            }

        items = self._load_items()
        items_by_id = {item.item_id: item for item in items}

        # Валидируем что все items существуют
        missing_ids = [item_id for item_id in ids_list if item_id not in items_by_id]
        if missing_ids:
            return {
                "ok": False,
                "error": "items_not_found",
                "success_count": 0,
                "error_count": len(ids_list),
                "details": [{"item_id": item_id, "error": "not_found"} for item_id in missing_ids],
            }

        # Обновляем все items
        success_count = 0
        error_count = 0
        details = []
        now_iso = _now_utc_iso()

        for item_id in ids_list:
            try:
                item = items_by_id[item_id]

                # Обновляем статус и timestamp
                item.status = normalized_status
                item.updated_at_utc = now_iso

                # Обновляем metadata
                metadata = self._normalize_metadata(item.metadata)
                metadata["last_action_actor"] = normalized_actor
                metadata["last_action_status"] = normalized_status
                metadata["last_action_at_utc"] = now_iso

                if note:
                    metadata["last_action_note"] = str(note).strip()

                # Для закрытых статусов записываем resolution metadata
                if normalized_status in self._closed_statuses:
                    metadata["resolved_at_utc"] = now_iso
                    metadata["resolved_by"] = normalized_actor
                    if note:
                        metadata["resolution_note"] = str(note).strip()

                # Добавляем workflow event
                item.metadata = self._append_workflow_event(
                    metadata,
                    action="bulk_updated",
                    actor=normalized_actor,
                    status=normalized_status,
                    note=note,
                )

                success_count += 1
                details.append({"item_id": item_id, "status": "updated"})

            except Exception as exc:
                logger.warning("bulk_update_status_item_failed", item_id=item_id, error=str(exc))
                error_count += 1
                details.append({"item_id": item_id, "error": str(exc)})

        # Сохраняем обновленные items
        if success_count > 0:
            self._save_items(items)

        return {
            "ok": error_count == 0,
            "success_count": success_count,
            "error_count": error_count,
            "details": details,
        }

    def _build_summary(self, items: list[InboxItem]) -> dict[str, Any]:
        """Собирает owner-facing summary из уже загруженного набора item-ов."""
        open_items = [item for item in items if item.status in self._open_statuses]
        fresh_open_items = [
            item for item in open_items if item.status == "open" and not self._is_open_stale(item)
        ]
        stale_open_items = [
            item for item in open_items if item.status == "open" and self._is_open_stale(item)
        ]
        acked_items = [item for item in open_items if item.status == "acked"]
        stale_acked_items = [item for item in acked_items if self._is_processing_stale(item)]
        warning_items = [item for item in open_items if item.severity in {"warning", "error"}]
        reminder_items = [item for item in open_items if item.kind == "reminder"]
        escalation_items = [item for item in open_items if item.kind.startswith("watch_")]
        owner_task_items = [item for item in open_items if item.kind == "owner_task"]
        approval_items = [item for item in open_items if item.kind == "approval_request"]
        owner_request_items = [item for item in open_items if item.kind == "owner_request"]
        owner_mention_items = [item for item in open_items if item.kind == "owner_mention"]
        new_owner_request_items = [
            item
            for item in owner_request_items
            if item.status == "open" and not self._is_open_stale(item)
        ]
        stale_open_owner_request_items = [
            item
            for item in owner_request_items
            if item.status == "open" and self._is_open_stale(item)
        ]
        processing_owner_request_items = [
            item for item in owner_request_items if item.status == "acked"
        ]
        new_owner_mention_items = [
            item
            for item in owner_mention_items
            if item.status == "open" and not self._is_open_stale(item)
        ]
        stale_open_owner_mention_items = [
            item
            for item in owner_mention_items
            if item.status == "open" and self._is_open_stale(item)
        ]
        processing_owner_mention_items = [
            item for item in owner_mention_items if item.status == "acked"
        ]
        stale_owner_request_items = [
            item for item in processing_owner_request_items if self._is_processing_stale(item)
        ]
        stale_owner_mention_items = [
            item for item in processing_owner_mention_items if self._is_processing_stale(item)
        ]
        return {
            "state_path": str(self.state_path),
            "account_id": current_account_id(),
            "operator_id": current_operator_id(),
            "total_items": len(items),
            "open_items": len(open_items),
            "fresh_open_items": len(fresh_open_items),
            "stale_open_items": len(stale_open_items),
            "acked_items": len(acked_items),
            "stale_processing_items": len(stale_acked_items),
            "attention_items": len(warning_items),
            "pending_reminders": len(reminder_items),
            "open_escalations": len(escalation_items),
            "pending_owner_tasks": len(owner_task_items),
            "pending_approvals": len(approval_items),
            "pending_owner_requests": len(owner_request_items),
            "new_owner_requests": len(new_owner_request_items),
            "stale_open_owner_requests": len(stale_open_owner_request_items),
            "processing_owner_requests": len(processing_owner_request_items),
            "stale_processing_owner_requests": len(stale_owner_request_items),
            "pending_owner_mentions": len(owner_mention_items),
            "new_owner_mentions": len(new_owner_mention_items),
            "stale_open_owner_mentions": len(stale_open_owner_mention_items),
            "processing_owner_mentions": len(processing_owner_mention_items),
            "stale_processing_owner_mentions": len(stale_owner_mention_items),
            "latest_open_items": [item.to_dict() for item in open_items[:5]],
        }

    @classmethod
    def _parse_item_activity_at(cls, item: InboxItem) -> datetime | None:
        """
        Возвращает timestamp последней активности item-а для truthful processing age.

        Для фоновой обработки важнее момент последнего action/update, чем исходное
        время создания. Это помогает отделить живую обработку от реально
        застрявших `acked` item-ов.
        """
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        for raw_value in (
            metadata.get("last_action_at_utc"),
            item.updated_at_utc,
            item.created_at_utc,
        ):
            if not raw_value:
                continue
            try:
                return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
            except ValueError:
                continue
        return None

    @classmethod
    def _is_processing_stale(cls, item: InboxItem) -> bool:
        """Определяет, что `acked` item слишком долго висит без нового прогресса."""
        if item.status != "acked":
            return False
        activity_at = cls._parse_item_activity_at(item)
        if activity_at is None:
            return False
        return datetime.now(timezone.utc) - activity_at >= cls._stale_processing_after

    @classmethod
    def _is_open_stale(cls, item: InboxItem) -> bool:
        """Определяет, что `open` item уже нельзя считать свежим inbox-сигналом."""
        if item.status != "open":
            return False
        activity_at = cls._parse_item_activity_at(item)
        if activity_at is None:
            return False
        return datetime.now(timezone.utc) - activity_at >= cls._stale_open_after

    def get_summary(self) -> dict[str, Any]:
        """Возвращает краткий owner-facing summary inbox."""
        return self._build_summary(self._load_items())

    @staticmethod
    def _compact_item(item: InboxItem) -> dict[str, Any]:
        """
        Возвращает компактный workflow-friendly вид item-а.

        Полный persisted payload остаётся доступен через `list_items`, а этот вид
        нужен для handoff/runtime snapshot, где важны trace/correlation сигналы,
        но не нужен весь JSON целиком.
        """
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        selected_metadata: dict[str, Any] = {}
        for key in (
            "chat_id",
            "message_id",
            "sender_id",
            "sender_username",
            "requested_action",
            "task_key",
            "request_key",
            "reminder_id",
            "watch_reason",
            "reply_sent_at_utc",
            "reply_delivery_mode",
            "reply_actor",
            "reply_count",
            "resolved_at_utc",
            "resolved_by",
            "approval_decision",
            "last_action_actor",
            "last_action_status",
            "source_item_id",
            "source_kind",
            "source_trace_id",
            "followup_count",
            "followup_latest_item_id",
            "followup_latest_kind",
            "followup_latest_status",
            "followup_latest_at_utc",
        ):
            value = metadata.get(key)
            if value not in {"", None}:
                selected_metadata[key] = value
        excerpt = str(metadata.get("text_excerpt") or "").strip()
        if excerpt:
            selected_metadata["text_excerpt"] = excerpt[:140]
        reply_excerpt = str(metadata.get("reply_excerpt") or "").strip()
        if reply_excerpt:
            selected_metadata["reply_excerpt"] = reply_excerpt[:140]
        resolution_note = str(metadata.get("resolution_note") or "").strip()
        if resolution_note:
            selected_metadata["resolution_note"] = resolution_note[:140]
        last_action_note = str(metadata.get("last_action_note") or "").strip()
        if last_action_note:
            selected_metadata["last_action_note"] = last_action_note[:140]
        source_excerpt = str(metadata.get("source_excerpt") or "").strip()
        if source_excerpt:
            selected_metadata["source_excerpt"] = source_excerpt[:140]
        reply_message_ids = metadata.get("reply_message_ids")
        if isinstance(reply_message_ids, list) and reply_message_ids:
            selected_metadata["reply_message_ids"] = [
                str(row).strip() for row in reply_message_ids if str(row).strip()
            ]
        followup_ids = metadata.get("followup_ids")
        if isinstance(followup_ids, list) and followup_ids:
            selected_metadata["followup_ids"] = [
                str(row).strip() for row in followup_ids if str(row).strip()
            ]
        if item.kind == "owner_task" and item.dedupe_key.startswith("task:"):
            task_key = str(item.dedupe_key.partition(":")[2] or "").strip()
            if task_key:
                selected_metadata.setdefault("task_key", task_key)
        if item.kind == "approval_request" and item.dedupe_key.startswith("approval:"):
            request_key = str(item.dedupe_key.partition(":")[2] or "").strip()
            if request_key:
                selected_metadata.setdefault("request_key", request_key)
        return {
            "item_id": item.item_id,
            "kind": item.kind,
            "status": item.status,
            "severity": item.severity,
            "title": item.title,
            "source": item.source,
            "updated_at_utc": item.updated_at_utc,
            "created_at_utc": item.created_at_utc,
            "identity": {
                "channel_id": item.identity.channel_id,
                "team_id": item.identity.team_id,
                "trace_id": item.identity.trace_id,
                "approval_scope": item.identity.approval_scope,
            },
            "metadata": selected_metadata,
            "last_event": (
                dict(item.metadata.get("workflow_events")[0])
                if isinstance(item.metadata.get("workflow_events"), list)
                and item.metadata.get("workflow_events")
                else None
            ),
        }

    def get_workflow_snapshot(
        self,
        *,
        limit_per_bucket: int = 4,
        trace_limit: int = 12,
    ) -> dict[str, Any]:
        """
        Возвращает компактный operator-workflow snapshot для runtime/handoff truth.

        Зачем отдельный snapshot:
        - summary показывает только счётчики, но не объясняет, какие именно
          owner requests / approvals сейчас открыты;
        - handoff и observability должны видеть traceable workflow-состояние,
          не таща полный persisted inbox целиком.
        """
        items = self._load_items()
        open_items = [item for item in items if item.status in self._open_statuses]
        bucket_limit = max(1, int(limit_per_bucket or 4))
        traces_limit = max(1, int(trace_limit or 12))

        def _bucket(*, kind: str, statuses: set[str] | None = None) -> list[dict[str, Any]]:
            allowed_statuses = statuses or self._open_statuses
            rows = [item for item in items if item.kind == kind and item.status in allowed_statuses]
            return [self._compact_item(item) for item in rows[:bucket_limit]]

        attention_items = [
            self._compact_item(item) for item in open_items if item.severity in {"warning", "error"}
        ][:bucket_limit]

        approval_history = _bucket(kind="approval_request", statuses={"approved", "rejected"})
        recent_approval_decisions = [
            self._compact_item(item)
            for item in items
            if item.kind == "approval_request" and item.status in {"approved", "rejected"}
        ][:bucket_limit]
        escalated_owner_items = [
            self._compact_item(item)
            for item in items
            if item.kind in {"owner_request", "owner_mention"}
            and int((item.metadata or {}).get("followup_count", 0) or 0) > 0
        ][:bucket_limit]
        linked_followups = [
            self._compact_item(item)
            for item in items
            if str((item.metadata or {}).get("source_item_id") or "").strip()
        ][:bucket_limit]
        replied_requests = [
            self._compact_item(item)
            for item in items
            if item.kind in {"owner_request", "owner_mention"}
            and str((item.metadata or {}).get("reply_sent_at_utc") or "").strip()
        ][:bucket_limit]
        trace_index: list[dict[str, Any]] = []
        seen_traces: set[str] = set()
        for item in items:
            trace_id = str(item.identity.trace_id or "").strip()
            if not trace_id or trace_id in seen_traces:
                continue
            seen_traces.add(trace_id)
            trace_index.append(
                {
                    "trace_id": trace_id,
                    "item_id": item.item_id,
                    "kind": item.kind,
                    "status": item.status,
                    "updated_at_utc": item.updated_at_utc,
                    "approval_scope": item.identity.approval_scope,
                }
            )
            if len(trace_index) >= traces_limit:
                break
        recent_activity: list[dict[str, Any]] = []
        for item in items:
            metadata = item.metadata if isinstance(item.metadata, dict) else {}
            events = metadata.get("workflow_events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                recent_activity.append(
                    {
                        "ts_utc": str(event.get("ts_utc") or ""),
                        "action": str(event.get("action") or ""),
                        "actor": str(event.get("actor") or ""),
                        "status": str(event.get("status") or ""),
                        "note": str(event.get("note") or ""),
                        "item_id": item.item_id,
                        "kind": item.kind,
                        "title": item.title,
                        "trace_id": item.identity.trace_id,
                        "approval_scope": item.identity.approval_scope,
                    }
                )
        recent_activity.sort(key=lambda row: str(row.get("ts_utc") or ""), reverse=True)
        recent_owner_actions = [
            dict(row)
            for row in recent_activity
            if self._is_owner_action_actor(str(row.get("actor") or ""))
            and str(row.get("action") or "") not in {"created", "upserted", "followup_created"}
        ][:traces_limit]

        return {
            "summary": self._build_summary(items),
            "attention_items": attention_items,
            "pending_approvals": _bucket(kind="approval_request"),
            "approval_history": approval_history,
            "recent_approval_decisions": recent_approval_decisions,
            "pending_owner_tasks": _bucket(kind="owner_task"),
            "incoming_owner_requests": _bucket(kind="owner_request"),
            "incoming_owner_mentions": _bucket(kind="owner_mention"),
            "escalated_owner_items": escalated_owner_items,
            "linked_followups": linked_followups,
            "recent_replied_requests": replied_requests,
            "recent_owner_actions": recent_owner_actions,
            "recent_activity": recent_activity[:traces_limit],
            "trace_index": trace_index,
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
            metadata_payload = self._append_workflow_event(
                metadata,
                action="created",
                actor=normalized_source,
                status=normalized_status,
            )
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
                metadata=metadata_payload,
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
            merged_metadata = self._normalize_metadata(item.metadata)
            merged_metadata.update(self._normalize_metadata(metadata))
            item.metadata = self._append_workflow_event(
                merged_metadata,
                action="upserted",
                actor=normalized_source,
                status=normalized_status,
            )
            items = [row for row in items if row.item_id != item.item_id]
            items.insert(0, item)

        self._save_items(items)
        return {
            "ok": True,
            "created": created,
            "item": item.to_dict(),
        }

    def set_item_status(
        self,
        item_id: str,
        *,
        status: str,
        actor: str = "owner",
        note: str = "",
        event_action: str = "",
        metadata_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        normalized_actor = str(actor or "owner").strip().lower() or "owner"
        metadata = self._normalize_metadata(item.metadata)
        metadata["last_action_actor"] = normalized_actor
        metadata["last_action_status"] = normalized_status
        metadata["last_action_at_utc"] = item.updated_at_utc
        if note:
            metadata["last_action_note"] = str(note).strip()
        if normalized_status in self._closed_statuses:
            metadata["resolved_at_utc"] = item.updated_at_utc
            metadata["resolved_by"] = normalized_actor
            if note:
                metadata["resolution_note"] = str(note).strip()
        metadata.update(self._normalize_metadata(metadata_updates))
        item.metadata = self._append_workflow_event(
            metadata,
            action=str(event_action or normalized_status or "status_changed").strip().lower()
            or "status_changed",
            actor=normalized_actor,
            status=normalized_status,
            note=note,
        )
        items = [row for row in items if row.item_id != item.item_id]
        items.insert(0, item)
        self._save_items(items)
        return {"ok": True, "item": item.to_dict()}

    def set_status_by_dedupe(
        self,
        dedupe_key: str,
        *,
        status: str,
        actor: str = "owner",
        note: str = "",
        event_action: str = "",
        metadata_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Обновляет статус item по dedupe_key, если item существует."""
        normalized_status = self._normalize_status(status)
        dedupe = str(dedupe_key or "").strip()
        items = self._load_items()
        item = next((row for row in items if row.dedupe_key == dedupe), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        item.status = normalized_status
        item.updated_at_utc = _now_utc_iso()
        normalized_actor = str(actor or "owner").strip().lower() or "owner"
        metadata = self._normalize_metadata(item.metadata)
        metadata["last_action_actor"] = normalized_actor
        metadata["last_action_status"] = normalized_status
        metadata["last_action_at_utc"] = item.updated_at_utc
        if note:
            metadata["last_action_note"] = str(note).strip()
        if normalized_status in self._closed_statuses:
            metadata["resolved_at_utc"] = item.updated_at_utc
            metadata["resolved_by"] = normalized_actor
            if note:
                metadata["resolution_note"] = str(note).strip()
        metadata.update(self._normalize_metadata(metadata_updates))
        item.metadata = self._append_workflow_event(
            metadata,
            action=str(event_action or normalized_status or "status_changed").strip().lower()
            or "status_changed",
            actor=normalized_actor,
            status=normalized_status,
            note=note,
        )
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
        return self.set_status_by_dedupe(
            f"reminder:{str(reminder_id or '').strip()}", status=status
        )

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

    def _build_followup_metadata(
        self,
        *,
        source_item: InboxItem,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Собирает metadata followup item-а, связанного с исходным inbox событием.

        Это нужно, чтобы task/approval, созданные из owner mention/request, не
        теряли происхождение после restart/handoff и были читаемы в snapshot-е.
        """
        payload = dict(metadata or {})
        source_meta = source_item.metadata if isinstance(source_item.metadata, dict) else {}
        payload.setdefault("source_item_id", source_item.item_id)
        payload.setdefault("source_kind", source_item.kind)
        payload.setdefault("source_trace_id", source_item.identity.trace_id)
        payload.setdefault("source_title", source_item.title)
        payload.setdefault(
            "source_excerpt",
            str(source_meta.get("text_excerpt") or source_item.title or "").strip()[:500],
        )
        payload.setdefault(
            "source_chat_id",
            str(source_meta.get("chat_id") or source_item.identity.channel_id or "").strip(),
        )
        payload.setdefault("source_message_id", str(source_meta.get("message_id") or "").strip())
        return payload

    def _link_followup_to_source(
        self,
        *,
        source_item_id: str,
        followup_item_id: str,
        followup_kind: str,
        followup_status: str,
        actor: str,
    ) -> dict[str, Any]:
        """Записывает на исходный item факт созданного followup-а и общий trace trail."""
        target_id = str(source_item_id or "").strip()
        if not target_id:
            return {"ok": False, "error": "inbox_empty_source_item_id"}
        items = self._load_items()
        source_item = next((row for row in items if row.item_id == target_id), None)
        if source_item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        metadata = self._normalize_metadata(source_item.metadata)
        followup_ids = (
            [str(row).strip() for row in metadata.get("followup_ids", []) if str(row).strip()]
            if isinstance(metadata.get("followup_ids"), list)
            else []
        )
        normalized_followup_id = str(followup_item_id or "").strip()
        already_linked = normalized_followup_id in followup_ids if normalized_followup_id else False
        if normalized_followup_id and normalized_followup_id not in followup_ids:
            followup_ids.insert(0, normalized_followup_id)
        metadata["followup_ids"] = followup_ids[:8]
        metadata["followup_count"] = int(metadata.get("followup_count", 0) or 0) + (
            0 if already_linked else 1
        )
        metadata["followup_latest_item_id"] = normalized_followup_id
        metadata["followup_latest_kind"] = str(followup_kind or "").strip().lower()
        metadata["followup_latest_status"] = str(followup_status or "").strip().lower()
        metadata["followup_latest_at_utc"] = _now_utc_iso()
        source_item.updated_at_utc = _now_utc_iso()
        source_item.metadata = self._append_workflow_event(
            metadata,
            action="followup_created",
            actor=actor,
            status=source_item.status,
            extra={
                "followup_item_id": normalized_followup_id,
                "followup_kind": metadata["followup_latest_kind"],
                "followup_status": metadata["followup_latest_status"],
            },
        )
        items = [row for row in items if row.item_id != source_item.item_id]
        items.insert(0, source_item)
        self._save_items(items)
        return {"ok": True, "item": source_item.to_dict()}

    def escalate_item_to_owner_task(
        self,
        *,
        source_item_id: str,
        title: str,
        body: str,
        task_key: str = "",
        source: str = "owner",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Создаёт owner-task, связанный с исходным inbox item и его trace."""
        source_item = self._find_item_by_id(source_item_id)
        if source_item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        result = self.upsert_owner_task(
            title=title,
            body=body,
            task_key=task_key,
            source=source,
            severity=severity,
            channel_id=source_item.identity.channel_id,
            team_id=source_item.identity.team_id or "owner",
            trace_id=source_item.identity.trace_id,
            metadata=self._build_followup_metadata(source_item=source_item, metadata=metadata),
        )
        if result.get("ok") and isinstance(result.get("item"), dict):
            self._link_followup_to_source(
                source_item_id=source_item.item_id,
                followup_item_id=str(result["item"].get("item_id") or ""),
                followup_kind="owner_task",
                followup_status=str(result["item"].get("status") or ""),
                actor=str(source or "owner").strip().lower() or "owner",
            )
        return result

    def escalate_item_to_approval_request(
        self,
        *,
        source_item_id: str,
        title: str,
        body: str,
        request_key: str = "",
        source: str = "owner",
        severity: str = "warning",
        approval_scope: str = "owner",
        requested_action: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Создаёт approval-request, связанный с исходным inbox item и его trace."""
        source_item = self._find_item_by_id(source_item_id)
        if source_item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        result = self.upsert_approval_request(
            title=title,
            body=body,
            request_key=request_key,
            source=source,
            severity=severity,
            channel_id=source_item.identity.channel_id,
            team_id=source_item.identity.team_id or "owner",
            trace_id=source_item.identity.trace_id,
            approval_scope=approval_scope,
            requested_action=requested_action,
            metadata=self._build_followup_metadata(source_item=source_item, metadata=metadata),
        )
        if result.get("ok") and isinstance(result.get("item"), dict):
            self._link_followup_to_source(
                source_item_id=source_item.item_id,
                followup_item_id=str(result["item"].get("item_id") or ""),
                followup_kind="approval_request",
                followup_status=str(result["item"].get("status") or ""),
                actor=str(source or "owner").strip().lower() or "owner",
            )
        return result

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
        trace_id: str = "",
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
                trace_id=str(trace_id or "").strip()
                or build_trace_id("approval", request_key or title, requested_action),
                approval_scope=str(approval_scope or "owner").strip() or "owner",
            ),
            metadata=payload_metadata,
        )

    def resolve_approval(
        self,
        item_id: str,
        *,
        approved: bool,
        actor: str = "owner",
        note: str = "",
    ) -> dict[str, Any]:
        """Закрывает approval-request решением owner-а."""
        target_id = str(item_id or "").strip()
        if not target_id:
            return {"ok": False, "error": "inbox_empty_item_id"}
        item = next((row for row in self._load_items() if row.item_id == target_id), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}
        if item.kind != "approval_request":
            return {"ok": False, "error": "inbox_item_not_approval"}
        return self.set_item_status(
            target_id,
            status="approved" if approved else "rejected",
            actor=actor,
            note=note,
            event_action="approved" if approved else "rejected",
            metadata_updates={"approval_decision": "approved" if approved else "rejected"},
        )

    def record_incoming_owner_reply(
        self,
        *,
        chat_id: str,
        message_id: str,
        response_text: str,
        delivery_mode: str,
        reply_message_ids: list[str] | None = None,
        actor: str = "kraab",
        note: str = "",
        status: str = "done",
    ) -> dict[str, Any]:
        """
        Фиксирует, что по owner request уже был отправлен ответ.

        Это связывает transport-факт "ответ реально ушёл" с persisted inbox item-ом,
        чтобы handoff видел не только входящий запрос, но и его outcome.
        """
        dedupe = f"incoming:{str(chat_id or '').strip()}:{str(message_id or '').strip()}"
        normalized_status = self._normalize_status(status)
        items = self._load_items()
        item = next((row for row in items if row.dedupe_key == dedupe), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}

        excerpt = str(response_text or "").strip()
        reply_ids = [str(row).strip() for row in (reply_message_ids or []) if str(row).strip()]
        metadata = self._normalize_metadata(item.metadata)
        metadata["reply_sent_at_utc"] = _now_utc_iso()
        metadata["reply_delivery_mode"] = str(delivery_mode or "text").strip().lower() or "text"
        metadata["reply_excerpt"] = excerpt[:500]
        metadata["reply_actor"] = str(actor or "kraab").strip().lower() or "kraab"
        metadata["reply_message_ids"] = reply_ids
        metadata["reply_count"] = int(metadata.get("reply_count", 0) or 0) + 1
        item.metadata = self._append_workflow_event(
            metadata,
            action="reply_sent",
            actor=actor,
            status=normalized_status,
            note=note,
            extra={
                "delivery_mode": metadata["reply_delivery_mode"],
                "reply_message_ids": reply_ids,
                "reply_excerpt": excerpt[:140],
            },
        )
        item.status = normalized_status
        item.updated_at_utc = _now_utc_iso()
        items = [row for row in items if row.item_id != item.item_id]
        items.insert(0, item)
        self._save_items(items)
        return {"ok": True, "item": item.to_dict()}

    def record_relay_delivery(
        self,
        *,
        chat_id: str,
        message_id: str,
        notification_text: str,
        delivery_mode: str,
        delivered_to_chat_id: str = "",
        relay_message_ids: list[str] | None = None,
        actor: str = "kraab",
        note: str = "",
        status: str = "done",
    ) -> dict[str, Any]:
        """
        Фиксирует, что relay-запрос реально доставлен владельцу.

        Почему отдельный helper:
        - `relay_request` отличается от owner_request: его задача считается
          выполненной, как только уведомление ушло владельцу;
        - transport-слой не должен оставлять такой item в `open`, иначе inbox
          врёт о pending-задачах, хотя relay уже завершён;
        - логика закрытия и trace metadata должна жить в одном месте, а не быть
          размазанной по Telegram bridge.
        """
        dedupe = f"relay:{str(chat_id or '').strip()}:{str(message_id or '').strip()}"
        normalized_status = self._normalize_status(status)
        items = self._load_items()
        item = next((row for row in items if row.dedupe_key == dedupe), None)
        if item is None:
            return {"ok": False, "error": "inbox_item_not_found"}

        excerpt = str(notification_text or "").strip()
        relay_ids = [str(row).strip() for row in (relay_message_ids or []) if str(row).strip()]
        metadata = self._normalize_metadata(item.metadata)
        metadata["relay_delivered_at_utc"] = _now_utc_iso()
        metadata["relay_delivery_mode"] = (
            str(delivery_mode or "saved_messages").strip().lower() or "saved_messages"
        )
        metadata["relay_delivery_excerpt"] = excerpt[:500]
        metadata["relay_actor"] = str(actor or "kraab").strip().lower() or "kraab"
        metadata["relay_message_ids"] = relay_ids
        metadata["relay_target_chat_id"] = str(delivered_to_chat_id or "").strip()
        metadata["relay_delivery_count"] = int(metadata.get("relay_delivery_count", 0) or 0) + 1
        if normalized_status in self._closed_statuses:
            metadata["resolved_at_utc"] = metadata["relay_delivered_at_utc"]
            metadata["resolved_by"] = metadata["relay_actor"]
            if note:
                metadata["resolution_note"] = str(note).strip()
        item.metadata = self._append_workflow_event(
            metadata,
            action="relay_sent",
            actor=actor,
            status=normalized_status,
            note=note,
            extra={
                "delivery_mode": metadata["relay_delivery_mode"],
                "relay_message_ids": relay_ids,
                "relay_target_chat_id": metadata["relay_target_chat_id"],
                "relay_excerpt": excerpt[:140],
            },
        )
        item.status = normalized_status
        item.updated_at_utc = _now_utc_iso()
        items = [row for row in items if row.item_id != item.item_id]
        items.insert(0, item)
        self._save_items(items)
        return {"ok": True, "item": item.to_dict()}

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
        title = (
            "Входящий owner request"
            if kind == "owner_request"
            else "Упоминание / owner request в чате"
        )
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

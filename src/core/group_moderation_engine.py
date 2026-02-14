# -*- coding: utf-8 -*-
"""
Group Moderation Engine (Phase C, moderation v2).

Задачи модуля:
1) Хранить per-chat moderation policy (dry-run, banned words, rule actions).
2) Оценивать сообщения по набору правил (ссылки, banned words, caps, repeated chars).
3) Возвращать единое решение для handler-уровня (warn/delete/mute/ban/none).
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from src.core.ai_guardian_client import AIGuardianClient


class _AwaitableDecision(dict):
    """Dict-результат, который также можно await-ить (legacy + async совместимость)."""

    def __await__(self):
        async def _as_coroutine():
            return self
        return _as_coroutine().__await__()


class GroupModerationEngine:
    """Движок правил для групповой модерации Telegram."""

    def __init__(
        self,
        policy_path: str = "artifacts/moderation/group_policies.json",
        default_dry_run: bool = True,
        ai_guardian: AIGuardianClient | None = None,
    ):
        self.policy_path = Path(policy_path)
        self.default_dry_run = bool(default_dry_run)
        self.ai_guardian = ai_guardian
        self._store = self._load_store()

    def _base_policy(self) -> dict[str, Any]:
        """Дефолтная policy для новой группы."""
        return {
            "dry_run": self.default_dry_run,
            "block_links": True,
            "max_links": 0,
            "banned_words": [],
            "max_caps_ratio": 0.72,
            "min_caps_chars": 12,
            "max_repeated_chars": 12,
            "actions": {
                "link": "delete",
                "banned_word": "delete",
                "caps": "warn",
                "repeated_chars": "warn",
                "ai_guardian": "delete",
            },
            "ai_guardian_threshold": 0.8,
            "warn_ttl_sec": 8,
            "mute_minutes": 15,
        }

    def _load_store(self) -> dict[str, Any]:
        if not self.policy_path.exists():
            return {"chats": {}}
        try:
            with self.policy_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict) and isinstance(data.get("chats"), dict):
                    return data
        except Exception:
            pass
        return {"chats": {}}

    def _save_store(self) -> None:
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        with self.policy_path.open("w", encoding="utf-8") as fp:
            json.dump(self._store, fp, ensure_ascii=False, indent=2)

    def _chat_key(self, chat_id: int) -> str:
        return str(int(chat_id))

    def get_policy(self, chat_id: int) -> dict[str, Any]:
        """Возвращает effective policy для группы."""
        chat_key = self._chat_key(chat_id)
        base = self._base_policy()
        stored = self._store.get("chats", {}).get(chat_key, {})
        if not isinstance(stored, dict):
            return base

        result = deepcopy(base)
        for key, value in stored.items():
            if key == "actions" and isinstance(value, dict):
                result["actions"].update(value)
            else:
                result[key] = value

        # Нормализация важных полей.
        result["banned_words"] = self._normalize_words(result.get("banned_words", []))
        result["max_links"] = max(0, int(result.get("max_links", 0)))
        result["min_caps_chars"] = max(1, int(result.get("min_caps_chars", 12)))
        result["max_repeated_chars"] = max(2, int(result.get("max_repeated_chars", 12)))
        result["warn_ttl_sec"] = max(3, int(result.get("warn_ttl_sec", 8)))
        result["mute_minutes"] = max(1, int(result.get("mute_minutes", 15)))

        try:
            ratio = float(result.get("max_caps_ratio", 0.72))
        except Exception:
            ratio = 0.72
        result["max_caps_ratio"] = min(max(ratio, 0.1), 1.0)

        return result

    def update_policy(self, chat_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        """Частично обновляет policy и сохраняет на диск."""
        if not isinstance(patch, dict):
            raise ValueError("patch должен быть словарем")

        chat_key = self._chat_key(chat_id)
        current = self._store.setdefault("chats", {}).setdefault(chat_key, {})

        for key, value in patch.items():
            if key == "actions" and isinstance(value, dict):
                actions = current.setdefault("actions", {})
                for rule_name, action in value.items():
                    if self._is_valid_action(str(action)):
                        actions[str(rule_name)] = str(action)
            elif key == "banned_words":
                current["banned_words"] = self._normalize_words(value)
            else:
                current[key] = value

        self._save_store()
        return self.get_policy(chat_id)

    def add_banned_word(self, chat_id: int, word: str) -> dict[str, Any]:
        """Добавляет banned word в policy группы."""
        normalized = self._sanitize_word(word)
        if not normalized:
            return self.get_policy(chat_id)

        policy = self.get_policy(chat_id)
        words = set(policy.get("banned_words", []))
        words.add(normalized)
        return self.update_policy(chat_id, {"banned_words": sorted(words)})

    def remove_banned_word(self, chat_id: int, word: str) -> dict[str, Any]:
        """Удаляет banned word из policy группы."""
        normalized = self._sanitize_word(word)
        policy = self.get_policy(chat_id)
        words = [w for w in policy.get("banned_words", []) if w != normalized]
        return self.update_policy(chat_id, {"banned_words": words})

    @property
    def templates(self) -> dict[str, dict[str, Any]]:
        """Доступные пресеты настроек."""
        return {
            "strict": {
                "dry_run": False,
                "block_links": True,
                "max_links": 0,
                "max_caps_ratio": 0.40,
                "actions": {
                    "link": "ban",
                    "banned_word": "ban",
                    "caps": "mute",
                    "repeated_chars": "delete",
                },
            },
            "balanced": {
                "dry_run": True,
                "block_links": True,
                "max_links": 1,
                "max_caps_ratio": 0.72,
                "actions": {
                    "link": "delete",
                    "banned_word": "delete",
                    "caps": "warn",
                    "repeated_chars": "warn",
                },
            },
            "lenient": {
                "dry_run": True,
                "block_links": False,
                "max_caps_ratio": 0.90,
                "actions": {
                    "link": "none",
                    "banned_word": "warn",
                    "caps": "none",
                    "repeated_chars": "none",
                },
            },
            "spam": {
                "dry_run": False,
                "block_links": True,
                "max_links": 0,
                "max_caps_ratio": 0.60,
                "actions": {
                    "link": "ban",
                    "banned_word": "delete",
                    "caps": "warn",
                    "repeated_chars": "delete",
                },
            },
            "abuse": {
                "dry_run": False,
                "block_links": True,
                "max_links": 2,
                "max_caps_ratio": 0.50,
                "actions": {
                    "link": "delete",
                    "banned_word": "ban",
                    "caps": "mute",
                    "repeated_chars": "warn",
                },
            },
        }

    def apply_template(self, chat_id: int, template_name: str) -> dict[str, Any]:
        """Применяет один из готовых шаблонов к группе."""
        tpl = self.templates.get(template_name.lower())
        if not tpl:
            raise ValueError(f"Шаблон '{template_name}' не найден. Доступны: {list(self.templates.keys())}")
        
        # Мы сбрасываем специфичные поля к шаблону, но сохраняем banned_words
        return self.update_policy(chat_id, tpl)

    def _evaluate_non_ai(
        self,
        chat_id: int,
        text: str,
        entities: Optional[list[Any]] = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Синхронная часть проверки без AI Guardian."""
        policy = self.get_policy(chat_id)
        message = (text or "").strip()
        normalized = message.lower()
        violations: list[dict[str, Any]] = []

        link_count = self._count_links(message, entities)
        if policy.get("block_links", True):
            max_links = int(policy.get("max_links", 0))
            if link_count > max_links:
                violations.append(
                    {
                        "rule": "link",
                        "reason": f"Обнаружены ссылки: {link_count} (лимит {max_links})",
                        "meta": {"link_count": link_count, "limit": max_links},
                    }
                )

        banned_matches = self._find_banned_words(normalized, policy.get("banned_words", []))
        if banned_matches:
            violations.append(
                {
                    "rule": "banned_word",
                    "reason": "Обнаружены запрещенные слова",
                    "meta": {"matches": banned_matches},
                }
            )

        caps_ratio = self._caps_ratio(message)
        min_caps_chars = int(policy.get("min_caps_chars", 12))
        if len(message) >= min_caps_chars and caps_ratio > float(policy.get("max_caps_ratio", 0.72)):
            violations.append(
                {
                    "rule": "caps",
                    "reason": f"Слишком много CAPS ({caps_ratio:.2f})",
                    "meta": {"caps_ratio": caps_ratio},
                }
            )

        max_repeated_chars = int(policy.get("max_repeated_chars", 12))
        repeated_match = re.search(rf"(.)\1{{{max_repeated_chars},}}", message)
        if repeated_match:
            violations.append(
                {
                    "rule": "repeated_chars",
                    "reason": "Слишком длинная повторяющаяся последовательность символов",
                    "meta": {"sequence": repeated_match.group(0)[:32]},
                }
            )

        return policy, violations, message

    def _build_decision(self, policy: dict[str, Any], violations: list[dict[str, Any]]) -> dict[str, Any]:
        """Собирает финальное решение модерации."""
        if not violations:
            return {
                "matched": False,
                "dry_run": bool(policy.get("dry_run", True)),
                "action": "none",
                "primary_rule": None,
                "violations": [],
                "policy": policy,
            }

        # Приоритет правил: бан-слова, ссылки и AI выше caps/повторов.
        priority = {"banned_word": 100, "ai_guardian": 95, "link": 90, "repeated_chars": 60, "caps": 50}
        primary = sorted(violations, key=lambda item: priority.get(item.get("rule", ""), 1), reverse=True)[0]
        primary_rule = primary.get("rule", "caps")
        action = policy.get("actions", {}).get(primary_rule, "warn")
        if not self._is_valid_action(action):
            action = "warn"

        return {
            "matched": True,
            "dry_run": bool(policy.get("dry_run", True)),
            "action": action,
            "primary_rule": primary_rule,
            "violations": violations,
            "policy": policy,
        }

    async def _evaluate_with_ai(
        self,
        chat_id: int,
        text: str,
        entities: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        """Полная проверка с AI Guardian."""
        policy, violations, message = self._evaluate_non_ai(chat_id, text, entities)
        if self.ai_guardian and message:
            ai_result = await self.ai_guardian.analyze_text(message)
            threshold = float(policy.get("ai_guardian_threshold", 0.8))
            if not ai_result.get("safe", True) and ai_result.get("score", 0.0) >= threshold:
                violations.append(
                    {
                        "rule": "ai_guardian",
                        "reason": ai_result.get("reason", "AI Guardian violation"),
                        "meta": {"score": ai_result.get("score"), "threshold": threshold},
                    }
                )
        return self._build_decision(policy, violations)

    def evaluate_message(
        self,
        chat_id: int,
        text: str,
        entities: Optional[list[Any]] = None,
    ):
        """
        Совместимый API:
        - без AI Guardian: возвращает dict, который также можно await-ить;
        - с AI Guardian: возвращает coroutine (ожидается await).
        """
        if self.ai_guardian:
            return self._evaluate_with_ai(chat_id, text, entities)
        policy, violations, _ = self._evaluate_non_ai(chat_id, text, entities)
        return _AwaitableDecision(self._build_decision(policy, violations))

    def _count_links(self, text: str, entities: Optional[list[Any]]) -> int:
        """Считает ссылки из entities и plain-text URL."""
        count = 0
        if entities:
            for entity in entities:
                entity_type = getattr(entity, "type", None)
                type_name = getattr(entity_type, "name", str(entity_type)).lower()
                if type_name in {"url", "text_link"}:
                    count += 1
        # Дополняем regex-поиском, чтобы не пропустить plain text.
        count += len(re.findall(r"https?://\S+|www\.\S+", text, flags=re.IGNORECASE))
        return count

    def _find_banned_words(self, lowered_text: str, words: list[str]) -> list[str]:
        """Ищет banned words (case-insensitive)."""
        matches: list[str] = []
        for word in self._normalize_words(words):
            if not word:
                continue
            if word in lowered_text:
                matches.append(word)
        return sorted(set(matches))

    def _caps_ratio(self, text: str) -> float:
        """Оценивает долю заглавных букв."""
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return 0.0
        caps = [ch for ch in letters if ch.isupper()]
        return len(caps) / len(letters)

    def _normalize_words(self, words: Any) -> list[str]:
        """Нормализует список banned words."""
        if not isinstance(words, (list, tuple, set)):
            return []
        normalized = [self._sanitize_word(str(word)) for word in words]
        return sorted({word for word in normalized if word})

    def _sanitize_word(self, word: str) -> str:
        value = (word or "").strip().lower()
        value = re.sub(r"\s+", " ", value)
        return value

    def _is_valid_action(self, action: str) -> bool:
        return str(action) in {"none", "warn", "delete", "mute", "ban"}

# -*- coding: utf-8 -*-
"""
Cross-Chat Learning Transfer — bootstrap нового чата по похожему существующему.

Идея (Feature I): новый чат не имеет своего persona-profile (Feature C),
поэтому persona drift suffix пуст, и Krab отвечает обобщённо. Решение —
подобрать похожий чат из существующих (по tone/formality/common_words)
и временно «одолжить» его profile с тегом `borrowed=True`.

Контракт:

- `find_similar_chat(target_chat_id, store, *, threshold=0.7)` — пробегает
  все profile в store, считает cosine similarity по бинарным фичам
  (tone, formality, preferred_reply_length, common_words). Возвращает
  source_chat_id с максимальным score >= threshold или None.
- `suggest_template(source_chat_id, target_chat_id, store)` — возвращает
  borrowed template: копия profile с пометкой `borrowed=True` и ссылкой
  на источник `borrowed_from`. Не пишет в store — read-only от source.
- `bootstrap_borrowed_profile(target_chat_id, store, *, threshold=0.7)` —
  combines обе функции; возвращает borrowed dict либо None.

Инварианты:

- Read-only от source: никогда не модифицируем профиль исходного чата.
- Идемпотентно: повторный вызов даёт тот же результат при том же store.
- Фичфлаг: при необходимости можно отключить через `KRAB_CROSS_CHAT_TRANSFER_ENABLED`
  (default True). Fail-open — при любой ошибке возвращаем None и логируем.
- Не зависит от других core-модулей кроме `logger` и `chat_persona_profile`.
"""

from __future__ import annotations

import math
from typing import Any

from .chat_persona_profile import ChatPersonaStore, chat_persona_store
from .logger import get_logger

logger = get_logger(__name__)


# Минимальный порог similarity — ниже считаем «не похож».
DEFAULT_SIMILARITY_THRESHOLD: float = 0.7

# Веса для категориальных фич — tone весит больше всего, потом formality.
_FEATURE_WEIGHTS: dict[str, float] = {
    "tone": 2.0,
    "formality": 1.5,
    "preferred_reply_length": 1.0,
}


def _is_feature_enabled() -> bool:
    """Читает config.KRAB_CROSS_CHAT_TRANSFER_ENABLED, default True."""
    try:
        from ..config import config  # noqa: PLC0415

        return bool(getattr(config, "KRAB_CROSS_CHAT_TRANSFER_ENABLED", True))
    except Exception:
        return True


def _profile_to_vector(profile: dict[str, Any]) -> dict[str, float]:
    """Превращает profile в разреженный взвешенный вектор фич.

    Категориальные поля (tone/formality/length) идут как «featurename=value»
    с весом из `_FEATURE_WEIGHTS`. common_words идут как бинарные стемы
    с весом 1.0.
    """
    vec: dict[str, float] = {}
    for key, weight in _FEATURE_WEIGHTS.items():
        value = profile.get(key)
        if value:
            vec[f"{key}={value}"] = weight
    for word in profile.get("common_words") or []:
        if isinstance(word, str) and word.strip():
            vec[f"word={word.strip().lower()}"] = 1.0
    return vec


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity между двумя разреженными dict-векторами."""
    if not a or not b:
        return 0.0
    # Скалярное произведение по общим ключам.
    dot = 0.0
    for key, va in a.items():
        vb = b.get(key)
        if vb:
            dot += va * vb
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_similar_chat(
    target_chat_id: Any,
    store: ChatPersonaStore | None = None,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> str | None:
    """Ищет наиболее похожий chat_id в store.

    Если у target уже есть свой profile — возвращает None (нечего
    bootstrap'ить). Если нет ни одного валидного source profile — None.
    """
    if not target_chat_id:
        return None
    store = store or chat_persona_store
    target_norm = str(target_chat_id).strip()
    if not target_norm:
        return None

    try:
        all_profiles = store.list_profiles()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cross_chat_list_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    # У target уже есть свой profile — bootstrap не нужен.
    target_profile = next(
        (p for p in all_profiles if str(p.get("chat_id")) == target_norm),
        None,
    )
    if target_profile:
        return None

    # Кандидаты — все остальные profile.
    candidates = [p for p in all_profiles if str(p.get("chat_id")) != target_norm]
    if not candidates:
        return None

    # Без target_profile у нас нет vector для сравнения. Используем
    # «дефолтный» нейтральный вектор — это не имеет смысла, поэтому
    # альтернативный подход: возьмём наиболее «сильный» (по message_count)
    # profile только если threshold == 0. Иначе — None.
    # Однако более правильный путь: caller сначала наполняет частичный
    # target_profile (например через analyze_messages по N сообщений из
    # нового чата), и передаёт его. Для cold-start без сообщений мы
    # просто не можем измерить similarity и возвращаем None.
    return None


def find_similar_chat_for_profile(
    partial_target_profile: dict[str, Any],
    target_chat_id: Any,
    store: ChatPersonaStore | None = None,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> tuple[str | None, float]:
    """Сравнивает partial profile target с каждым существующим profile.

    Возвращает (source_chat_id, score) при score >= threshold, иначе (None, best_score).
    """
    store = store or chat_persona_store
    target_norm = str(target_chat_id or "").strip()

    try:
        all_profiles = store.list_profiles()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cross_chat_list_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None, 0.0

    if not partial_target_profile:
        return None, 0.0

    target_vec = _profile_to_vector(partial_target_profile)
    if not target_vec:
        return None, 0.0

    best_id: str | None = None
    best_score: float = 0.0
    for entry in all_profiles:
        source_id = str(entry.get("chat_id") or "")
        if not source_id or source_id == target_norm:
            continue
        source_vec = _profile_to_vector(entry)
        score = _cosine_similarity(target_vec, source_vec)
        if score > best_score:
            best_score = score
            best_id = source_id

    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


def suggest_template(
    source_chat_id: Any,
    target_chat_id: Any,
    store: ChatPersonaStore | None = None,
) -> dict[str, Any] | None:
    """Возвращает borrowed template на основе source profile.

    Read-only от source — копируем profile, добавляем теги borrowed=True
    и borrowed_from. Не пишем в store.
    """
    if not source_chat_id or not target_chat_id:
        return None
    store = store or chat_persona_store
    try:
        source_profile = store.get_profile(source_chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cross_chat_get_source_failed",
            source_chat_id=str(source_chat_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None
    if not source_profile:
        return None

    template = dict(source_profile)
    template["borrowed"] = True
    template["borrowed_from"] = str(source_chat_id)
    template["target_chat_id"] = str(target_chat_id)
    # Копия списка common_words чтобы caller не мог сломать source.
    if isinstance(template.get("common_words"), list):
        template["common_words"] = list(template["common_words"])
    return template


def bootstrap_borrowed_profile(
    target_chat_id: Any,
    *,
    partial_target_profile: dict[str, Any] | None = None,
    store: ChatPersonaStore | None = None,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    """Cold-start helper: ищет похожий чат и возвращает borrowed template.

    Если partial_target_profile задан (caller уже посчитал черновой
    профиль из N последних сообщений нового чата) — используем его для
    similarity. Иначе вернуть None (нет данных для сравнения).

    Read-only: ничего не пишет в store.
    """
    if not _is_feature_enabled():
        return None
    if not target_chat_id:
        return None
    store = store or chat_persona_store
    try:
        if store.get_profile(target_chat_id):
            # У target уже есть свой profile — borrow не нужен.
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cross_chat_target_lookup_failed",
            target_chat_id=str(target_chat_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    if not partial_target_profile:
        return None

    source_id, score = find_similar_chat_for_profile(
        partial_target_profile,
        target_chat_id,
        store=store,
        threshold=threshold,
    )
    if not source_id:
        return None

    template = suggest_template(source_id, target_chat_id, store=store)
    if template is None:
        return None
    template["similarity_score"] = round(score, 4)
    logger.info(
        "cross_chat_profile_borrowed",
        target_chat_id=str(target_chat_id),
        source_chat_id=source_id,
        score=round(score, 4),
    )
    return template

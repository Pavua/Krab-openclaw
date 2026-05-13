"""Wave 247: аудит Gemini auth setup на старте Krab — проверяем что bonus
Vertex AI ADC активен, а paid AI Studio ключ НЕ потребляется.

Контекст: пользователь обнаружил что bonus Vertex AI кредиты не движутся
(подозревал paid AI Studio key). После Wave 58-A + Wave 66-B + Wave 67
конфигурация такова:

* ``GEMINI_PAID_KEY_ENABLED=0`` в ``.env`` → ``config.GEMINI_API_KEY`` резолвится
  на free key (не paid).
* ``KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED=1`` → ``google_genai_direct.py`` ходит
  через ``genai.Client(vertexai=True, project=..., location=...)`` используя
  ADC из ``~/.config/gcloud/application_default_credentials.json``.
* ``KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=1`` (default) → httpx hook (Wave 67)
  блокирует ЛЮБОЙ исходящий request к ``generativelanguage.googleapis.com``.

Этот модуль НЕ блокирует ничего сам — это assertion + logging для
post-mortem прозрачности. Вызывается из ``bootstrap/runtime.run_app()``
сразу после ``register_paid_gemini_guard()``.

Single source of truth: возвращаемый ``GeminiAuthAudit`` dataclass со всеми
проверенными полями. Логируем структурированный event ``gemini_auth_setup``
с полем ``mode`` ∈ {``vertex_adc``, ``ai_studio_paid``, ``ai_studio_free``,
``misconfigured``}.

Sentry: если mode != ``vertex_adc`` AND paid key включён — пишем
``logger.warning`` (не error, чтобы избежать Sentry spam как в Wave 170).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Канонический путь ADC creds (gcloud default). Перебивается env переменной
# GOOGLE_APPLICATION_CREDENTIALS (если задана — приоритет за ней).
_DEFAULT_ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


# Имена режимов — стабильное API для логов/тестов/dashboard'ов.
MODE_VERTEX_ADC = "vertex_adc"
MODE_AI_STUDIO_PAID = "ai_studio_paid"
MODE_AI_STUDIO_FREE = "ai_studio_free"
MODE_MISCONFIGURED = "misconfigured"


@dataclass(frozen=True)
class GeminiAuthAudit:
    """Snapshot Gemini auth state на момент проверки.

    Все поля — read-only (frozen). ``mode`` — resolved итог, остальное —
    raw сигналы для post-mortem.
    """

    mode: str  # MODE_* константа
    vertex_preferred: bool  # KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED
    vertex_project: str  # KRAB_VERTEX_PROJECT или дефолт
    vertex_location: str  # KRAB_VERTEX_REGION или дефолт
    adc_path_exists: bool  # есть ли credentials.json
    adc_path: str  # путь до credentials (GOOGLE_APPLICATION_CREDENTIALS или дефолт)
    paid_key_enabled_flag: bool  # GEMINI_PAID_KEY_ENABLED
    paid_key_present_in_env: bool  # GEMINI_API_KEY_PAID задан
    guard_mode: str  # KRAB_BLOCK_PAID_GEMINI_AI_STUDIO: block|warn|off
    suspicious: bool  # True если состояние подозрительное (paid_key + flag=0 + guard=off)

    def to_dict(self) -> dict[str, Any]:
        """Удобный сериализатор для log/Sentry context."""
        return asdict(self)


def _env_bool(name: str, default: str = "0") -> bool:
    """Парсинг bool из env (1/true/yes/on case-insensitive)."""
    return str(os.environ.get(name, default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_adc_path() -> Path:
    """Возвращает путь до ADC credentials.

    Приоритет: ``GOOGLE_APPLICATION_CREDENTIALS`` env > канонический gcloud
    ``~/.config/gcloud/application_default_credentials.json``.
    """
    override = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_ADC_PATH


def _resolve_guard_mode() -> str:
    """Тонкая копия ``paid_gemini_guard._guard_mode`` — чтобы не импортить guard
    (избегаем cycle при импорте audit раньше регистрации guard).
    """
    raw = str(os.environ.get("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")).strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        return "off"
    if raw == "warn":
        return "warn"
    return "block"


def audit_gemini_auth() -> GeminiAuthAudit:
    """Снимает текущее состояние Gemini auth и возвращает audit snapshot.

    Не делает сетевых вызовов — только читает env + проверяет наличие
    credentials.json на диске. Безопасно вызывать на старте без рисков
    blocking I/O.

    Решающее правило для ``mode``:

    * ``vertex_preferred=True`` AND ``adc_path_exists=True`` → ``vertex_adc``.
    * ``paid_key_enabled_flag=True`` AND ``paid_key_present`` → ``ai_studio_paid``.
    * Иначе если free key есть → ``ai_studio_free``.
    * Иначе → ``misconfigured``.

    ``suspicious=True`` если есть paid_key в env, но flag=0 И guard=off —
    означает дыру: paid key может утечь без блока guard'ом.
    """
    vertex_preferred = _env_bool("KRAB_GOOGLE_DIRECT_VERTEX_PREFERRED", "1")
    vertex_project = (
        os.environ.get("KRAB_VERTEX_PROJECT", "").strip()
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("VERTEX_AI_PROJECT_ID", "").strip()
        or "caramel-anvil-492816-t5"
    )
    vertex_location = (
        os.environ.get("KRAB_VERTEX_REGION", "").strip()
        or os.environ.get("VERTEX_AI_LOCATION", "").strip()
        or "global"
    )

    adc_path = _resolve_adc_path()
    adc_exists = adc_path.exists()

    paid_flag = _env_bool("GEMINI_PAID_KEY_ENABLED", "0")
    paid_present = bool((os.environ.get("GEMINI_API_KEY_PAID") or "").strip())
    free_present = bool((os.environ.get("GEMINI_API_KEY_FREE") or "").strip())

    guard_mode = _resolve_guard_mode()

    # Резолв режима по приоритету.
    if vertex_preferred and adc_exists:
        mode = MODE_VERTEX_ADC
    elif paid_flag and paid_present:
        mode = MODE_AI_STUDIO_PAID
    elif free_present:
        mode = MODE_AI_STUDIO_FREE
    else:
        mode = MODE_MISCONFIGURED

    # Дыра: paid_key есть, flag=0, но guard=off → ничего не блокирует.
    suspicious = paid_present and (not paid_flag) and guard_mode == "off"

    return GeminiAuthAudit(
        mode=mode,
        vertex_preferred=vertex_preferred,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        adc_path_exists=adc_exists,
        adc_path=str(adc_path),
        paid_key_enabled_flag=paid_flag,
        paid_key_present_in_env=paid_present,
        guard_mode=guard_mode,
        suspicious=suspicious,
    )


def log_gemini_auth_setup() -> GeminiAuthAudit:
    """Вызывается на старте: audit + structured log + Sentry warning при опасной конфигурации.

    Возвращает GeminiAuthAudit для caller'а (используется в тестах и для
    отображения в ``/api/model/status``).
    """
    audit = audit_gemini_auth()

    # Основной event — всегда info. Caller'ы grep'ают ``gemini_auth_setup``.
    logger.info(
        "gemini_auth_setup",
        mode=audit.mode,
        vertex_preferred=audit.vertex_preferred,
        vertex_project=audit.vertex_project,
        vertex_location=audit.vertex_location,
        adc_path_exists=audit.adc_path_exists,
        adc_path=audit.adc_path,
        paid_key_enabled_flag=audit.paid_key_enabled_flag,
        paid_key_present_in_env=audit.paid_key_present_in_env,
        guard_mode=audit.guard_mode,
    )

    # Warning кейсы — Sentry увидит, но без error spam (см. Wave 170).
    if audit.mode == MODE_AI_STUDIO_PAID:
        # Paid mode явно включён — это не ошибка, но фиксируем чтобы owner мог
        # увидеть в Sentry/dashboard что bonus credits НЕ используются.
        logger.warning(
            "gemini_auth_paid_mode_active",
            detail="paid AI Studio key consumed; set GEMINI_PAID_KEY_ENABLED=0 to use bonus Vertex ADC",
            project=audit.vertex_project,
        )
    elif audit.suspicious:
        # Paid key лежит в env, флаг=0, но guard выключен — нет никакой защиты
        # от утечки. Owner должен либо удалить ключ, либо включить guard.
        logger.warning(
            "gemini_auth_paid_key_leak_risk",
            detail=(
                "GEMINI_API_KEY_PAID present, GEMINI_PAID_KEY_ENABLED=0, "
                "but KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=0 — guard disabled, "
                "paid key may leak. Re-enable guard or remove paid key from env."
            ),
        )
    elif audit.mode == MODE_MISCONFIGURED:
        logger.warning(
            "gemini_auth_misconfigured",
            detail="no Vertex ADC AND no free/paid AI Studio key; Gemini direct calls will fail",
        )

    return audit


__all__ = [
    "GeminiAuthAudit",
    "MODE_AI_STUDIO_FREE",
    "MODE_AI_STUDIO_PAID",
    "MODE_MISCONFIGURED",
    "MODE_VERTEX_ADC",
    "audit_gemini_auth",
    "log_gemini_auth_setup",
]

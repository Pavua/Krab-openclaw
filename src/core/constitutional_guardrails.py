# -*- coding: utf-8 -*-
"""
Constitutional Guardrails (Idea 12).

Pre-send фильтр исходящего ответа Краба. Проверяет, что сгенерированный текст
не содержит запрещённых паттернов (PII passthrough, прокинутые prompt injection,
утечки телефонов/карт/паролей, мат в business-контекстах и т. д.) перед тем,
как отправить его в Telegram.

### Дизайн

* **Pure module.** Никакого I/O, никаких внешних обращений. Зависит только от
  ``pii_redactor`` (опционально, через DI). Wire-up в llm_flow откладывается в
  backlog — сейчас отдаём только движок и тесты.
* **Three severity tiers** — каждая нарушение помечается уровнем:
  * ``block`` — отказаться отправлять (вернуть рефуз/тишину наружу)
  * ``rewrite`` — авто-редакт, отправить отредактированную версию
  * ``warn`` — только лог, отправить как есть
* **Idempotent rewrite.** Повторный прогон auto-rewrite не плодит маркеры
  (PII redactor уже idempotent; mat-замена использует тот же маркер).
* **Deterministic.** Один и тот же ответ + контекст всегда дают один и тот же
  ``GuardResult`` (важно для cache idempotency и тестируемости).

### Не решает

* Не интегрируется в llm_flow (см. backlog).
* Не делает NER — мат и инъекции по словарю/regex.
* Не редактирует имена/адреса/паспорта (deferred to PII redactor v2).

### Конфиг

Активация — через ``KRAB_GUARDRAILS_ENABLED`` (по умолчанию ``False``,
careful rollout). Сам движок флаг не читает — это решение более высокого слоя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

__all__ = [
    "GuardrailEngine",
    "GuardResult",
    "Severity",
    "Violation",
]

Severity = Literal["block", "rewrite", "warn"]

# --- Prompt injection ---------------------------------------------------
# Прокидка инструкций — типичные русские/английские паттерны. Расчёт на то,
# что в нормальном ответе ассистента таких фраз быть не должно. Если LLM
# повторяет инъекцию пользователя — блокируем.
_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?i)\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?|rules?)"
    ),
    re.compile(r"(?i)игнорируй\s+(?:все\s+)?(?:предыдущ|прежн)\w*\s+(?:инструкц|правил)"),
    re.compile(r"(?i)забудь\s+(?:про\s+|об?\s+)?(?:все\s+)?(?:правил|инструкц|систем)"),
    re.compile(r"(?i)\bты\s+теперь\s+(?:не\s+|больше\s+не\s+)?(?:краб|ассистент|бот|ai)"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(?:a\s+|an\s+)?(?:dan|jailbroken|unrestricted)"),
    re.compile(
        r"(?i)act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:dan|jailbroken|no\s+restrictions)"
    ),
    re.compile(r"(?i)system\s*[:>]\s*you\s+(?:must|will|should)\s+now"),
)

# --- Mat (russian profanity) ---------------------------------------------
# Намеренно консервативный список — три корня + типичные склонения. Используем
# границу слова чтобы не ловить "херувим" / "сукуленты" и т. п.
_MAT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?i)\b(?:х[уy]й\w*|х[уy]ё\w*|п[иi]зд\w*|еб[аеёиуыя]\w*|бл[яja]д\w*|сук[аиу]\b|г[оa]вн\w*)"
    ),
)

# --- Plaintext PII (fallback если pii_redactor не подключён) -------------
_PLAINTEXT_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)(?:\+|00)\s?\d{1,3}[\s\-().]{0,2}\d{1,4}[\s\-().]{0,2}\d{1,4}[\s\-().]{0,2}\d{1,4}(?!\w)",
)
_PLAINTEXT_CC_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_PLAINTEXT_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|bearer)\s*[:=]\s*\S{6,}",
)

# --- Маркер уже редактированного фрагмента -----------------------------
_REDACTED_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\[REDACTED:[a-z_]+\]")


class _Redactor(Protocol):
    """Минимальный контракт PII-редактора. Совместим с ``PIIRedactor``."""

    def redact(self, text: str) -> str: ...


@dataclass(frozen=True)
class Violation:
    """Одно зафиксированное нарушение."""

    kind: str  # тип нарушения: 'pii_phone', 'pii_cc', 'pii_secret', 'mat', 'injection'
    severity: Severity
    detail: str = ""  # краткое описание (для логов / debug)


@dataclass(frozen=True)
class GuardResult:
    """Результат прогонки одного ответа через guardrails."""

    passed: bool  # True если можно отправлять (даже с rewrite/warn)
    violations: tuple[Violation, ...] = ()
    severity: Severity | None = None  # максимальный уровень: block > rewrite > warn
    rewritten: str | None = None  # если severity == 'rewrite' — итоговый текст

    @property
    def violation_kinds(self) -> tuple[str, ...]:
        return tuple(v.kind for v in self.violations)


# Порядок приоритета severity (block самый сильный).
_SEVERITY_RANK: Final[dict[Severity, int]] = {"warn": 0, "rewrite": 1, "block": 2}


def _max_severity(items: list[Violation]) -> Severity | None:
    if not items:
        return None
    return max(items, key=lambda v: _SEVERITY_RANK[v.severity]).severity


@dataclass
class GuardrailConfig:
    """Конфигурация движка. Уровни нарушения настраиваются вызывающим кодом."""

    pii_severity: Severity = "rewrite"
    injection_severity: Severity = "block"
    mat_severity: Severity = (
        "warn"  # warn по умолчанию: повышается до 'rewrite' для business-context
    )

    # Контексты, в которых мат повышается до 'rewrite'. Сравнение точное по строке.
    business_contexts: frozenset[str] = field(
        default_factory=lambda: frozenset({"business", "work", "corp", "client"})
    )


class GuardrailEngine:
    """Pre-send гард для исходящих ответов Краба.

    Использование::

        engine = GuardrailEngine(redactor=PIIRedactor())
        result = engine.check("Мой пароль: hunter2", {"context_kind": "business"})
        if not result.passed:
            # отказ от отправки
            ...
        elif result.severity == "rewrite":
            send(result.rewritten)
        else:
            send(original)
    """

    def __init__(
        self,
        *,
        redactor: _Redactor | None = None,
        config: GuardrailConfig | None = None,
    ) -> None:
        self._redactor = redactor
        self._config = config or GuardrailConfig()

    # ---- Public --------------------------------------------------------

    def check(self, answer: str, context: dict[str, object] | None = None) -> GuardResult:
        """Прогнать ``answer`` через все checks, вернуть консолидированный результат."""
        ctx = context or {}
        if not isinstance(answer, str) or not answer:
            return GuardResult(passed=True)

        violations: list[Violation] = []
        violations.extend(self._check_injection(answer))
        violations.extend(self._check_pii(answer))
        violations.extend(self._check_mat(answer, ctx))

        severity = _max_severity(violations)

        # block — отказ от отправки.
        if severity == "block":
            return GuardResult(passed=False, violations=tuple(violations), severity="block")

        # rewrite — авто-редакт. Применяем PII-редактор + замену мата.
        if severity == "rewrite":
            rewritten = self._rewrite(answer, violations)
            return GuardResult(
                passed=True,
                violations=tuple(violations),
                severity="rewrite",
                rewritten=rewritten,
            )

        # warn (или нет нарушений) — отправляем оригинал.
        return GuardResult(
            passed=True,
            violations=tuple(violations),
            severity=severity,  # 'warn' или None
        )

    # ---- Internal: detection ------------------------------------------

    def _check_injection(self, text: str) -> list[Violation]:
        out: list[Violation] = []
        for pat in _INJECTION_PATTERNS:
            m = pat.search(text)
            if m:
                out.append(
                    Violation(
                        kind="injection",
                        severity=self._config.injection_severity,
                        detail=m.group(0)[:80],
                    )
                )
        return out

    def _check_pii(self, text: str) -> list[Violation]:
        # Если есть внешний redactor — спросим его в "виртуальном" режиме:
        # сравним redact() с оригиналом, и если что-то поменялось → есть PII.
        if self._redactor is not None:
            redacted = self._redactor.redact(text)
            if redacted != text:
                kinds = self._marker_kinds(redacted) - self._marker_kinds(text)
                if not kinds:
                    # маркеры были и там и там, но что-то изменилось — generic
                    kinds = {"unknown"}
                return [
                    Violation(
                        kind=f"pii_{kind}",
                        severity=self._config.pii_severity,
                        detail=kind,
                    )
                    for kind in sorted(kinds)
                ]
            return []

        # Fallback: минимальный inline-чек без redactor.
        out: list[Violation] = []
        if _PLAINTEXT_PHONE_RE.search(text):
            out.append(Violation(kind="pii_phone", severity=self._config.pii_severity))
        if _PLAINTEXT_CC_RE.search(text):
            out.append(Violation(kind="pii_cc", severity=self._config.pii_severity))
        if _PLAINTEXT_SECRET_RE.search(text):
            out.append(Violation(kind="pii_secret", severity=self._config.pii_severity))
        return out

    def _check_mat(self, text: str, ctx: dict[str, object]) -> list[Violation]:
        out: list[Violation] = []
        for pat in _MAT_PATTERNS:
            m = pat.search(text)
            if m:
                # Эскалация в business-контексте.
                ctx_kind = str(ctx.get("context_kind", "")).lower()
                severity = self._config.mat_severity
                if ctx_kind in self._config.business_contexts and severity == "warn":
                    severity = "rewrite"
                out.append(
                    Violation(
                        kind="mat",
                        severity=severity,
                        detail=m.group(0)[:40],
                    )
                )
                # Только одно нарушение типа "mat" на ответ — иначе шум в логах.
                break
        return out

    # ---- Internal: rewriting ------------------------------------------

    def _rewrite(self, text: str, violations: list[Violation]) -> str:
        result = text

        # PII — через injected redactor если есть; иначе fallback.
        has_pii = any(v.kind.startswith("pii_") for v in violations)
        if has_pii:
            if self._redactor is not None:
                result = self._redactor.redact(result)
            else:
                result = _PLAINTEXT_SECRET_RE.sub("[REDACTED:secret]", result)
                result = _PLAINTEXT_CC_RE.sub("[REDACTED:cc]", result)
                result = _PLAINTEXT_PHONE_RE.sub("[REDACTED:phone]", result)

        # Мат — заменяем целиком word на маркер.
        if any(v.kind == "mat" for v in violations):
            for pat in _MAT_PATTERNS:
                result = pat.sub("[REDACTED:mat]", result)

        return result

    @staticmethod
    def _marker_kinds(text: str) -> set[str]:
        """Извлечь множество видов маркеров (phone/cc/secret/...) из строки."""
        return {m.group(0)[len("[REDACTED:") : -1] for m in _REDACTED_MARKER_RE.finditer(text)}

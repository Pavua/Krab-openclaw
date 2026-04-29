# -*- coding: utf-8 -*-
"""
InlineQueryRouter — pure-логика inline-mode lookups для Krab.

Этот модуль НЕ подписывается на pyrogram-события напрямую (userbot-аккаунты
обычно не получают inline-запросов от Telegram). Это подготовка: чистый
маршрутизатор строки запроса в `InlineResult`'ы. Будущая интеграция:
1) Botfather + dedicated bot account → подписка `on_inline_query` в bridge.
2) Локальный fallback — отрисовка в чат через `!q <query>`.

Поддерживаемые маршруты (read-only, без побочных эффектов):
- weather <city>      — заглушка-форматтер (реальный fetch — backlog)
- calc <expr>         — безопасный numeric-only computation через AST whitelist
- define <word>       — заглушка-форматтер
- convert <val> <from> <to> — простая конвертация единиц (length/mass/temp)
- currency <amt> <from> <to> — заглушка курса
"""

from __future__ import annotations

import ast
import operator as _op
from dataclasses import dataclass, field
from typing import Any, Callable

from .logger import get_logger

logger = get_logger(__name__)

# Максимальная длина snippet
_SNIPPET_LEN = 200


@dataclass
class InlineResult:
    """Результат inline-запроса (готов к рендеру в Telegram inline-card)."""

    title: str
    content: str
    snippet: str = ""
    kind: str = "text"  # text | url | file
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # snippet — обрезанный preview
        if not self.snippet:
            self.snippet = self.content[:_SNIPPET_LEN]


# ─── Безопасный вычислитель арифметических выражений ─────────────────────────
# AST-whitelist: только числовые литералы и базовые операции, без имен/вызовов.

_ALLOWED_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_ALLOWED_UNARYOPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: _op.pos,
    ast.USub: _op.neg,
}


def _compute_arith(expr: str) -> float:
    """Вычисляет арифметическое выражение через AST-whitelist (без builtins)."""
    tree = ast.parse(expr, mode="eval")

    def _walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            handler = _ALLOWED_BINOPS.get(type(node.op))
            if handler is None:
                raise ValueError(f"unsupported_binop:{type(node.op).__name__}")
            return handler(_walk(node.left), _walk(node.right))
        if isinstance(node, ast.UnaryOp):
            handler = _ALLOWED_UNARYOPS.get(type(node.op))
            if handler is None:
                raise ValueError(f"unsupported_unaryop:{type(node.op).__name__}")
            return handler(_walk(node.operand))
        raise ValueError(f"unsupported_node:{type(node).__name__}")

    return _walk(tree)


# ─── Конвертация единиц (минимальный набор) ──────────────────────────────────

# Длина в метрах
_LENGTH_TO_M = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "mi": 1609.344,
    "ft": 0.3048,
    "in": 0.0254,
    "yd": 0.9144,
}
# Масса в граммах
_MASS_TO_G = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "lb": 453.59237,
    "oz": 28.349523125,
}


def _convert_units(value: float, src: str, dst: str) -> float | None:
    """Конвертация единиц. None если пара не поддерживается."""
    src_l, dst_l = src.lower(), dst.lower()
    # Длина
    if src_l in _LENGTH_TO_M and dst_l in _LENGTH_TO_M:
        return value * _LENGTH_TO_M[src_l] / _LENGTH_TO_M[dst_l]
    # Масса
    if src_l in _MASS_TO_G and dst_l in _MASS_TO_G:
        return value * _MASS_TO_G[src_l] / _MASS_TO_G[dst_l]
    # Температура
    if src_l in {"c", "f", "k"} and dst_l in {"c", "f", "k"}:
        # Сначала в Цельсий
        if src_l == "c":
            c = value
        elif src_l == "f":
            c = (value - 32.0) * 5.0 / 9.0
        else:
            c = value - 273.15
        # Из Цельсия в целевую
        if dst_l == "c":
            return c
        if dst_l == "f":
            return c * 9.0 / 5.0 + 32.0
        return c + 273.15
    return None


# ─── Маршрутизатор ───────────────────────────────────────────────────────────


class InlineQueryRouter:
    """Чистый маршрутизатор inline-запросов.

    Использование:
        router = InlineQueryRouter()
        results = router.route("calc 23*7")
    """

    def __init__(self) -> None:
        # Карта команд → обработчиков
        self._routes: dict[str, Callable[[str], list[InlineResult]]] = {
            "weather": self._route_weather,
            "calc": self._route_calc,
            "define": self._route_define,
            "convert": self._route_convert,
            "currency": self._route_currency,
        }

    # ─── Публичный API ────────────────────────────────────────────────────

    def route(self, query: str) -> list[InlineResult]:
        """Маршрутизирует запрос в список inline-результатов."""
        if not query or not query.strip():
            return [self._empty_hint()]

        text = query.strip()
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        handler = self._routes.get(cmd)
        if handler is None:
            return [self._unknown_hint(text)]

        try:
            return handler(rest)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "inline_route_failed",
                extra={
                    "cmd": cmd,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return [
                InlineResult(
                    title=f"Ошибка: {cmd}",
                    content=f"Не удалось обработать: {exc}",
                    kind="text",
                    meta={"error": True},
                )
            ]

    @property
    def supported_commands(self) -> list[str]:
        """Список поддерживаемых команд (для help/UI)."""
        return sorted(self._routes.keys())

    # ─── Обработчики маршрутов ────────────────────────────────────────────

    def _route_weather(self, rest: str) -> list[InlineResult]:
        # Заглушка — реальный fetch делегируется существующему !weather handler.
        city = rest.strip()
        if not city:
            return [
                InlineResult(
                    title="weather: укажи город",
                    content="Использование: weather <город>",
                    kind="text",
                )
            ]
        return [
            InlineResult(
                title=f"Погода: {city}",
                content=f"[prep] Lookup погоды для {city} (реальный fetch — backlog)",
                kind="text",
                meta={"city": city, "stub": True},
            )
        ]

    def _route_calc(self, rest: str) -> list[InlineResult]:
        expr = rest.strip()
        if not expr:
            return [
                InlineResult(
                    title="calc: пустое выражение",
                    content="Использование: calc <выражение>",
                    kind="text",
                )
            ]
        result = _compute_arith(expr)
        return [
            InlineResult(
                title=f"= {result}",
                content=f"{expr} = {result}",
                kind="text",
                meta={"expr": expr, "result": result},
            )
        ]

    def _route_define(self, rest: str) -> list[InlineResult]:
        word = rest.strip()
        if not word:
            return [
                InlineResult(
                    title="define: укажи слово",
                    content="Использование: define <слово>",
                    kind="text",
                )
            ]
        return [
            InlineResult(
                title=f"Определение: {word}",
                content=f"[prep] Определение для {word} (источник — backlog)",
                kind="text",
                meta={"word": word, "stub": True},
            )
        ]

    def _route_convert(self, rest: str) -> list[InlineResult]:
        parts = rest.split()
        if len(parts) != 3:
            return [
                InlineResult(
                    title="convert: формат",
                    content="Использование: convert <значение> <from> <to> (например: convert 10 km mi)",
                    kind="text",
                )
            ]
        try:
            value = float(parts[0])
        except ValueError:
            return [
                InlineResult(
                    title="convert: ошибка",
                    content=f"Не число: {parts[0]}",
                    kind="text",
                    meta={"error": True},
                )
            ]
        src, dst = parts[1], parts[2]
        out = _convert_units(value, src, dst)
        if out is None:
            return [
                InlineResult(
                    title="convert: неподдерживаемые единицы",
                    content=(
                        "Поддержка: длина (m/km/cm/mm/mi/ft/in/yd), масса (g/kg/mg/lb/oz), "
                        f"температура (C/F/K). Запрос: {src}→{dst}"
                    ),
                    kind="text",
                    meta={"error": True},
                )
            ]
        return [
            InlineResult(
                title=f"{value} {src} = {out:g} {dst}",
                content=f"{value} {src} = {out:g} {dst}",
                kind="text",
                meta={"value": value, "src": src, "dst": dst, "result": out},
            )
        ]

    def _route_currency(self, rest: str) -> list[InlineResult]:
        parts = rest.split()
        if len(parts) < 2:
            return [
                InlineResult(
                    title="currency: формат",
                    content="Использование: currency <сумма> <FROM> <TO> (например: currency 100 USD EUR)",
                    kind="text",
                )
            ]
        # Поддерживаем "100 USD EUR" и "100 USD/EUR"
        try:
            amount = float(parts[0])
        except ValueError:
            return [
                InlineResult(
                    title="currency: ошибка",
                    content=f"Не число: {parts[0]}",
                    kind="text",
                    meta={"error": True},
                )
            ]
        if len(parts) >= 3:
            src, dst = parts[1].upper(), parts[2].upper()
        else:
            pair = parts[1].upper().replace("/", " ").split()
            if len(pair) != 2:
                return [
                    InlineResult(
                        title="currency: формат пары",
                        content="Используй: currency 100 USD EUR",
                        kind="text",
                        meta={"error": True},
                    )
                ]
            src, dst = pair[0], pair[1]
        # Заглушка — реальный курс берётся из !currency handler / external API
        return [
            InlineResult(
                title=f"{amount} {src} → {dst}",
                content=f"[prep] Курс {src}→{dst} для {amount} (live rate — backlog)",
                kind="text",
                meta={"amount": amount, "src": src, "dst": dst, "stub": True},
            )
        ]

    # ─── Подсказки ────────────────────────────────────────────────────────

    def _empty_hint(self) -> InlineResult:
        cmds = ", ".join(self.supported_commands)
        return InlineResult(
            title="Inline lookup",
            content=f"Команды: {cmds}",
            kind="text",
            meta={"hint": True},
        )

    def _unknown_hint(self, raw: str) -> InlineResult:
        cmds = ", ".join(self.supported_commands)
        return InlineResult(
            title="Неизвестная команда",
            content=f"'{raw}' не поддерживается. Доступно: {cmds}",
            kind="text",
            meta={"unknown": True},
        )


# Singleton для повторного использования в bridge (когда bridge научится подписке)
inline_query_router = InlineQueryRouter()

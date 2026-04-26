# -*- coding: utf-8 -*-
"""
text_utils — Phase 2 first-domain extraction (Session 27).

Простые text-processing команды: !calc, !b64, !hash, !len/!count, !json,
!sed, !diff, !regex, !rand.

Pure text manipulation — без сложных bot.client API вызовов; зависят
только от ``bot._get_command_args`` и ``message.reply``.

См. ``docs/CODE_SPLITS_PLAN.md`` § Phase 2 — domain extractions.
"""

from __future__ import annotations

import ast as _ast
import base64 as _base64
import hashlib
import json
import math as _math
import operator as _operator
import re
from typing import TYPE_CHECKING

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# !hash
# ---------------------------------------------------------------------------


async def handle_hash(bot: "KraabUserbot", message: Message) -> None:
    """
    Хэширует текст и возвращает MD5 / SHA1 / SHA256.

    Синтаксис:
      !hash <текст>         — все три хэша
      !hash md5 <текст>     — только MD5
      !hash sha1 <текст>    — только SHA1
      !hash sha256 <текст>  — только SHA256
      !hash (reply)         — хэши текста из ответного сообщения
    """
    _known_algos = {"md5", "sha1", "sha256"}

    raw_args = bot._get_command_args(message).strip()

    algo_filter: str | None = None
    text: str = ""

    if raw_args:
        parts = raw_args.split(maxsplit=1)
        if parts[0].lower() in _known_algos:
            algo_filter = parts[0].lower()
            text = parts[1].strip() if len(parts) > 1 else ""
        else:
            text = raw_args

    if not text and message.reply_to_message:
        replied = message.reply_to_message
        text = (replied.text or replied.caption or "").strip()

    if not text:
        raise UserInputError(
            user_message=(
                "🔐 Укажи текст: `!hash <текст>`, `!hash md5 <текст>` "
                "или ответь командой на сообщение."
            )
        )

    encoded = text.encode("utf-8")
    hashes = {
        "md5": hashlib.md5(encoded).hexdigest(),
        "sha1": hashlib.sha1(encoded).hexdigest(),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }

    if algo_filter:
        result = f"🔐 `{algo_filter.upper()}`\n─────\n`{hashes[algo_filter]}`"
    else:
        result = (
            "🔐 Hash\n"
            "─────\n"
            f"MD5:    `{hashes['md5']}`\n"
            f"SHA1:   `{hashes['sha1']}`\n"
            f"SHA256: `{hashes['sha256']}`"
        )

    await message.reply(result)


# ---------------------------------------------------------------------------
# !calc — безопасный калькулятор (compile + ограниченный namespace)
# ---------------------------------------------------------------------------

_CALC_BINOPS: dict[type, object] = {
    _ast.Add: _operator.add,
    _ast.Sub: _operator.sub,
    _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv,
    _ast.Mod: _operator.mod,
    _ast.Pow: _operator.pow,
    _ast.FloorDiv: _operator.floordiv,
}

_CALC_UNOPS: dict[type, object] = {
    _ast.UAdd: _operator.pos,
    _ast.USub: _operator.neg,
}

_CALC_NAMESPACE: dict[str, object] = {
    "sqrt": _math.sqrt,
    "sin": _math.sin,
    "cos": _math.cos,
    "tan": _math.tan,
    "log": _math.log,
    "log2": _math.log2,
    "log10": _math.log10,
    "abs": abs,
    "round": round,
    "pi": _math.pi,
    "e": _math.e,
    "inf": _math.inf,
}


def _calc_eval_node(node: _ast.AST) -> float | int:
    """Рекурсивно вычисляет AST-узел. Разрешены только безопасные операции."""
    if isinstance(node, _ast.Expression):
        return _calc_eval_node(node.body)
    if isinstance(node, _ast.Constant):
        if isinstance(node.value, bool):
            raise UserInputError(user_message=f"❌ Недопустимый литерал: {node.value!r}")
        if isinstance(node.value, (int, float)):
            return node.value
        raise UserInputError(user_message=f"❌ Недопустимый литерал: {node.value!r}")
    if isinstance(node, _ast.BinOp):
        op_fn = _CALC_BINOPS.get(type(node.op))
        if op_fn is None:
            raise UserInputError(user_message="❌ Недопустимая операция.")
        left = _calc_eval_node(node.left)
        right = _calc_eval_node(node.right)
        try:
            return op_fn(left, right)  # type: ignore[operator]
        except ZeroDivisionError:
            raise UserInputError(user_message="❌ Деление на ноль.")
    if isinstance(node, _ast.UnaryOp):
        op_fn = _CALC_UNOPS.get(type(node.op))
        if op_fn is None:
            raise UserInputError(user_message="❌ Недопустимая унарная операция.")
        return op_fn(_calc_eval_node(node.operand))  # type: ignore[operator]
    if isinstance(node, _ast.Call):
        if not isinstance(node.func, _ast.Name):
            raise UserInputError(user_message="❌ Недопустимый вызов функции.")
        fn = _CALC_NAMESPACE.get(node.func.id)
        if fn is None:
            raise UserInputError(user_message=f"❌ Функция не поддерживается: {node.func.id}()")
        if node.keywords:
            raise UserInputError(user_message="❌ Именованные аргументы не поддерживаются.")
        args = [_calc_eval_node(a) for a in node.args]
        try:
            return fn(*args)  # type: ignore[operator]
        except (ValueError, TypeError) as exc:
            raise UserInputError(user_message=f"❌ Ошибка вычисления: {exc}")
    if isinstance(node, _ast.Name):
        val = _CALC_NAMESPACE.get(node.id)
        if val is None or not isinstance(val, (int, float)):
            raise UserInputError(user_message=f"❌ Неизвестная переменная: {node.id}")
        return val  # type: ignore[return-value]
    raise UserInputError(user_message=f"❌ Недопустимая конструкция: {type(node).__name__}")


def safe_calc(expression: str) -> float | int:
    """
    Безопасно вычисляет математическое выражение через AST.
    Не использует eval/exec напрямую — только разбор AST и whitelisted операции.
    """
    expression = expression.strip()
    if not expression:
        raise UserInputError(user_message="❌ Пустое выражение.")
    if len(expression) > 200:
        raise UserInputError(user_message="❌ Выражение слишком длинное (макс. 200 символов).")
    try:
        tree = compile(expression, "<calc>", "eval", _ast.PyCF_ONLY_AST)
    except SyntaxError as exc:
        raise UserInputError(user_message=f"❌ Синтаксическая ошибка: {exc.msg}")
    return _calc_eval_node(tree)


async def handle_calc(bot: "KraabUserbot", message: Message) -> None:
    """!calc <выражение> — безопасный калькулятор."""
    expr = bot._get_command_args(message).strip()
    if not expr:
        raise UserInputError(
            user_message=(
                "🧮 **Калькулятор**\n\n"
                "Использование: `!calc <выражение>`\n\n"
                "Примеры:\n"
                "`!calc 2+2*3` → `= 8`\n"
                "`!calc sqrt(144)` → `= 12.0`\n"
                "`!calc sin(pi/2)` → `= 1.0`\n\n"
                "Поддерживаются: `+`, `-`, `*`, `/`, `**`, `%`, `//`\n"
                "Функции: `sqrt`, `sin`, `cos`, `tan`, `log`, `log2`, `log10`, `abs`, `round`\n"
                "Константы: `pi`, `e`"
            )
        )
    result = safe_calc(expr)
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        formatted = str(int(result))
    else:
        formatted = str(result)
    await message.reply(f"= {formatted}")


# ---------------------------------------------------------------------------
# !b64 — кодирование/декодирование Base64
# ---------------------------------------------------------------------------


def _b64_is_valid(text: str) -> bool:
    """Проверяет, является ли строка валидным Base64 (паддинг добавляется автоматически)."""
    stripped = text.strip().replace("\n", "").replace(" ", "")
    if not stripped:
        return False
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    try:
        _base64.b64decode(padded, validate=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _b64_encode(text: str) -> str:
    """Кодирует текст в Base64 (UTF-8)."""
    return _base64.b64encode(text.encode("utf-8")).decode("ascii")


def _b64_decode(b64: str) -> str:
    """Декодирует Base64 в строку (UTF-8, с мягким паддингом)."""
    stripped = b64.strip().replace("\n", "").replace(" ", "")
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    return _base64.b64decode(padded).decode("utf-8")


async def handle_b64(bot: "KraabUserbot", message: Message) -> None:
    """!b64 — Base64 кодирование и декодирование."""
    args = bot._get_command_args(message).strip()

    if args.lower().startswith("encode "):
        payload = args[len("encode ") :].strip()
        if not payload:
            raise UserInputError(
                user_message="❌ Укажи текст для кодирования: `!b64 encode <текст>`"
            )
        result = _b64_encode(payload)
        await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    if args.lower().startswith("decode "):
        payload = args[len("decode ") :].strip()
        if not payload:
            raise UserInputError(
                user_message="❌ Укажи Base64 для декодирования: `!b64 decode <base64>`"
            )
        try:
            result = _b64_decode(payload)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Невалидный Base64: {exc}") from exc
        await message.reply(f"🔓 **Base64 (decode):**\n`{result}`")
        return

    reply_text: str | None = None
    if message.reply_to_message and message.reply_to_message.text:
        reply_text = message.reply_to_message.text

    if not args and reply_text:
        if _b64_is_valid(reply_text):
            try:
                result = _b64_decode(reply_text)
                await message.reply(f"🔓 **Base64 (decode):**\n`{result}`")
            except Exception as exc:  # noqa: BLE001
                raise UserInputError(user_message=f"❌ Невалидный Base64: {exc}") from exc
        else:
            result = _b64_encode(reply_text)
            await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    if args:
        result = _b64_encode(args)
        await message.reply(f"🔐 **Base64 (encode):**\n`{result}`")
        return

    raise UserInputError(
        user_message=(
            "🔐 **Base64 — справка**\n\n"
            "`!b64 encode <текст>` — закодировать\n"
            "`!b64 decode <base64>` — декодировать\n"
            "`!b64 <текст>` — закодировать (короткий вариант)\n"
            "`!b64` в reply — автоопределение по содержимому"
        )
    )


# ---------------------------------------------------------------------------
# !len / !count — статистика текста
# ---------------------------------------------------------------------------


def _plural_chars(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "символов"
    r = n % 10
    if r == 1:
        return "символ"
    if 2 <= r <= 4:
        return "символа"
    return "символов"


def _plural_words(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "слов"
    r = n % 10
    if r == 1:
        return "слово"
    if 2 <= r <= 4:
        return "слова"
    return "слов"


def _plural_lines(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "строк"
    r = n % 10
    if r == 1:
        return "строка"
    if 2 <= r <= 4:
        return "строки"
    return "строк"


async def handle_len(bot: "KraabUserbot", message: Message) -> None:
    """!len <текст> / !count <текст> — подсчёт символов, слов и строк."""
    raw_args = bot._get_command_args(message).strip()

    if not raw_args and message.reply_to_message:
        replied = message.reply_to_message
        raw_args = (replied.text or replied.caption or "").strip()

    if not raw_args:
        raise UserInputError(
            user_message=(
                "📏 **!len — статистика текста**\n\n"
                "`!len <текст>` — символы, слова, строки\n"
                "`!count <текст>` — то же самое\n"
                "_Или ответь командой на любое сообщение._"
            )
        )

    chars = len(raw_args)
    words = len(raw_args.split())
    lines = len(raw_args.splitlines()) or 1

    result = (
        f"📏 Текст: {chars} {_plural_chars(chars)}, "
        f"{words} {_plural_words(words)}, "
        f"{lines} {_plural_lines(lines)}"
    )
    await message.reply(result)


# ---------------------------------------------------------------------------
# !json — форматирование/валидация/минификация
# ---------------------------------------------------------------------------


def _json_extract_text(message: Message, args: str) -> str | None:
    """Извлекает текст: сначала из args, затем из reply."""
    if args:
        return args
    if message.reply_to_message:
        return message.reply_to_message.text or message.reply_to_message.caption or None
    return None


async def handle_json(bot: "KraabUserbot", message: Message) -> None:
    """!json — форматирование/валидация/минификация JSON."""
    raw_args = bot._get_command_args(message).strip()

    sub: str | None = None
    payload: str = raw_args

    lower = raw_args.lower()
    if lower.startswith("validate ") or lower == "validate":
        sub = "validate"
        payload = raw_args[len("validate") :].strip()
    elif lower.startswith("minify ") or lower == "minify":
        sub = "minify"
        payload = raw_args[len("minify") :].strip()

    if not payload:
        payload = _json_extract_text(message, "") or ""

    if not payload:
        raise UserInputError(
            user_message=(
                "🔧 **JSON-утилита**\n\n"
                "`!json <текст>` — форматировать (pretty print)\n"
                "`!json` в reply — форматировать текст ответа\n"
                "`!json validate <текст>` — проверить валидность\n"
                "`!json minify <текст>` — минифицировать"
            )
        )

    if sub == "validate":
        try:
            json.loads(payload)
            await message.reply("✅ JSON валиден.")
        except json.JSONDecodeError as exc:
            await message.reply(
                f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
            )
        return

    if sub == "minify":
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise UserInputError(
                user_message=f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
            ) from exc
        minified = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        await message.reply(f"```json\n{minified}\n```")
        return

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise UserInputError(
            user_message=f"❌ JSON невалиден: {exc.msg}: line {exc.lineno} column {exc.colno}"
        ) from exc
    pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    await message.reply(f"```json\n{pretty}\n```")


# ---------------------------------------------------------------------------
# !sed — IRC-style regex замена
# ---------------------------------------------------------------------------

_SED_VALID_FLAGS = frozenset("gi")


def _parse_sed_expr(expr: str):
    """Парсит выражение вида s/old/new/[flags].

    Возвращает (compiled_pattern, replacement, count).
    Бросает ValueError при некорректном формате.
    """
    if len(expr) < 2 or expr[0] != "s":
        raise ValueError("Выражение должно начинаться с 's'")

    sep = expr[1]
    parts = expr[2:].split(sep)

    if len(parts) < 2:
        raise ValueError("Недостаточно частей в s-выражении")

    pattern = parts[0]
    replacement = parts[1]
    raw_flags = parts[2] if len(parts) > 2 else ""

    unknown = set(raw_flags) - _SED_VALID_FLAGS
    if unknown:
        raise ValueError(f"Неизвестные флаги: {''.join(sorted(unknown))}")

    re_flags = 0
    count = 1

    if "i" in raw_flags:
        re_flags |= re.IGNORECASE
    if "g" in raw_flags:
        count = 0

    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as exc:
        raise ValueError(f"Ошибка в regex: {exc}") from exc

    return compiled, replacement, count


async def handle_sed(bot: "KraabUserbot", message: Message) -> None:
    """!sed s/old/new/[flags] — IRC-style regex замена."""
    parts = message.command
    if not parts or len(parts) < 2:
        await message.reply(
            "✏️ **!sed — IRC-style замена**\n\n"
            "Использование: `!sed s/old/new/[флаги]` в reply на сообщение\n\n"
            "Флаги:\n"
            "`g` — заменить все совпадения (глобально)\n"
            "`i` — без учёта регистра\n\n"
            "Примеры:\n"
            "`!sed s/опечатка/слово/` — первое вхождение\n"
            "`!sed s/foo/bar/g` — все вхождения\n"
            "`!sed s/Hello/Привет/i` — без учёта регистра"
        )
        return

    expr = parts[1]

    try:
        compiled, replacement, count = _parse_sed_expr(expr)
    except ValueError as exc:
        raise UserInputError(user_message=f"❌ {exc}") from exc

    target = message.reply_to_message
    if target is None:
        raise UserInputError(user_message="❌ Используй `!sed` в reply на сообщение")

    original_text = target.text or target.caption or ""
    if not original_text:
        raise UserInputError(user_message="❌ Целевое сообщение не содержит текста")

    new_text, n_subs = compiled.subn(replacement, original_text, count=count)

    if n_subs == 0:
        await message.reply("⚠️ Паттерн не найден в тексте")
        return

    me = await bot.client.get_me()
    is_own = target.from_user and target.from_user.id == me.id

    if is_own:
        try:
            await bot.client.edit_message_text(
                chat_id=target.chat.id,
                message_id=target.id,
                text=new_text,
            )
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as exc:
            await message.reply(f"❌ Не удалось отредактировать: {exc}")
    else:
        await message.reply(f"✏️ Исправление:\n{new_text}")


# ---------------------------------------------------------------------------
# !diff — сравнение текстов в unified-формате
# ---------------------------------------------------------------------------


def _build_diff_output(old_text: str, new_text: str) -> str:
    """Сравнивает два текста и возвращает unified diff в Telegram-формате."""
    import difflib

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_lines: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            if diff_lines:
                diff_lines.append("")
            continue
        diff_lines.append(line.rstrip("\n"))

    return "\n".join(diff_lines)


async def handle_diff(bot: "KraabUserbot", message: Message) -> None:
    """!diff — сравнение двух текстов в unified-формате."""
    args = bot._get_command_args(message).strip()

    reply = message.reply_to_message
    reply_text: str | None = None
    if reply:
        reply_text = (reply.text or reply.caption or "").strip() or None

    if reply_text is None:
        raise UserInputError(
            user_message=(
                "📊 **!diff — сравнение текстов**\n\n"
                "Используй в reply на сообщение:\n"
                "`!diff <новый текст>` — сравнивает текст reply с твоим текстом\n\n"
                "_Старый текст — сообщение, на которое отвечаешь._\n"
                "_Новый текст — аргумент команды._"
            )
        )

    if not args:
        raise UserInputError(
            user_message=(
                "❌ Укажи новый текст: `!diff <текст>`\n_Старый текст берётся из reply-сообщения._"
            )
        )

    old_text = reply_text
    new_text = args

    diff_body = _build_diff_output(old_text, new_text)

    if not diff_body.strip():
        await message.reply("✅ Тексты идентичны — различий нет.")
        return

    separator = "─" * 5
    header = f"📊 **Diff**\n{separator}"
    full_output = f"{header}\n```\n{diff_body}\n```"

    if len(full_output) > 3900:
        diff_body_trimmed = diff_body[: 3800 - len(header)]
        full_output = f"{header}\n```\n{diff_body_trimmed}\n…(обрезано)```"

    await message.reply(full_output)


# ---------------------------------------------------------------------------
# !regex — тестирование регулярных выражений
# ---------------------------------------------------------------------------


def _format_regex_result(pattern_src: str, text: str) -> str:
    """Форматирует результат regex-матча в читаемый вид для Telegram."""
    compiled = re.compile(pattern_src)
    matches = list(compiled.finditer(text))

    if not matches:
        return f"🔍 Regex: `/{pattern_src}/`\n\n⚠️ Совпадений не найдено"

    lines: list[str] = [
        f"🔍 Regex: `/{pattern_src}/`",
        f"Matches: {len(matches)}",
    ]

    for i, m in enumerate(matches[:10], start=1):
        matched_text = m.group(0)
        if len(matched_text) > 60:
            matched_text = matched_text[:57] + "..."
        start, end = m.span()
        lines.append(f'{i}. `"{matched_text}"` ({start}:{end})')

        if m.lastindex or m.groupdict():
            groups: list[str] = []
            named = m.groupdict()
            if named:
                for name, val in named.items():
                    groups.append(f'{name}="{val}"')
            else:
                for j, val in enumerate(m.groups(), start=1):
                    groups.append(f'group{j}="{val}"')
            if groups:
                lines.append(f"   Groups: {', '.join(groups)}")

    if len(matches) > 10:
        lines.append(f"   … и ещё {len(matches) - 10} совпадений")

    return "\n".join(lines)


async def handle_regex(bot: "KraabUserbot", message: Message) -> None:
    """!regex — тестирование регулярных выражений."""
    raw_args = bot._get_command_args(message).strip()

    pattern_src: str = ""
    text: str = ""

    if raw_args:
        parts = raw_args.split(maxsplit=1)
        pattern_src = parts[0]
        if len(parts) > 1:
            text = parts[1].strip()

    if not text and message.reply_to_message:
        reply = message.reply_to_message
        text = (reply.text or reply.caption or "").strip()

    if not pattern_src:
        raise UserInputError(
            user_message=(
                "🔍 **!regex — тестирование регулярных выражений**\n\n"
                "Использование:\n"
                "`!regex <паттерн> <текст>` — тест на тексте\n"
                "`!regex <паттерн>` (reply) — паттерн, текст из reply\n\n"
                "Примеры:\n"
                r"`!regex \d+ abc123def456`" + " → 2 совпадения\n"
                r"`!regex (foo|bar) foo baz bar`" + " → 2 совпадения\n"
                r"`!regex (?P<name>\w+) Hello World`" + " → именованные группы"
            )
        )

    if not text:
        raise UserInputError(
            user_message=(
                "❌ Укажи текст для проверки или ответь командой на сообщение.\n"
                "Пример: `!regex \\d+ текст 123`"
            )
        )

    try:
        re.compile(pattern_src)
    except re.error as exc:
        raise UserInputError(user_message=f"❌ Невалидный regex: `{exc}`") from exc

    result = _format_regex_result(pattern_src, text)
    await message.reply(result)


# ---------------------------------------------------------------------------
# !rand — генератор случайных значений
# ---------------------------------------------------------------------------


async def handle_rand(bot: "KraabUserbot", message: Message) -> None:
    """Генератор случайных значений: число, монета, кубик, выбор, пароль, UUID."""
    import random
    import secrets
    import string
    import uuid as _uuid_mod

    args = bot._get_command_args(message).strip()

    if not args:
        n = random.randint(1, 100)
        await message.reply(f"🎲 {n}")
        return

    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "coin":
        result = random.choice(["Орёл 🦅", "Решка 🪙"])
        await message.reply(result)
        return

    if sub == "dice":
        n = random.randint(1, 6)
        await message.reply(f"🎲 {n}")
        return

    if sub == "pick":
        if not rest:
            raise UserInputError(user_message="🎲 Формат: `!rand pick item1, item2, item3`")
        items = [item.strip() for item in rest.split(",") if item.strip()]
        if len(items) < 2:
            raise UserInputError(user_message="🎲 Нужно минимум 2 варианта, разделённых запятой.")
        chosen = random.choice(items)
        await message.reply(f"🎲 Выбрано: **{chosen}**")
        return

    if sub == "pass":
        length = 16
        if rest:
            if not rest.isdigit():
                raise UserInputError(user_message="🎲 Формат: `!rand pass [длина]`")
            length = int(rest)
            if not (4 <= length <= 128):
                raise UserInputError(user_message="🎲 Длина пароля: от 4 до 128 символов.")
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        await message.reply(f"🔑 `{password}`")
        return

    if sub == "uuid":
        uid = str(_uuid_mod.uuid4())
        await message.reply(f"`{uid}`")
        return

    try:
        first = int(sub)
    except ValueError:
        raise UserInputError(
            user_message=(
                "🎲 **!rand** — генератор случайных значений\n"
                "`!rand` — число 1–100\n"
                "`!rand N` — число 1–N\n"
                "`!rand N M` — число N–M\n"
                "`!rand coin` — орёл/решка\n"
                "`!rand dice` — кубик 1–6\n"
                "`!rand pick a, b, c` — выбор из списка\n"
                "`!rand pass [N]` — пароль (default 16 символов)\n"
                "`!rand uuid` — UUID4"
            )
        )

    if not rest:
        if first < 1:
            raise UserInputError(user_message="🎲 N должен быть ≥ 1.")
        n = random.randint(1, first)
        await message.reply(f"🎲 {n}")
        return

    try:
        second = int(rest.split()[0])
    except ValueError:
        raise UserInputError(
            user_message="🎲 Формат: `!rand N M` — оба аргумента должны быть целыми числами."
        )
    lo, hi = min(first, second), max(first, second)
    n = random.randint(lo, hi)
    await message.reply(f"🎲 {n}")


__all__ = [
    "handle_calc",
    "handle_b64",
    "handle_hash",
    "handle_len",
    "handle_json",
    "handle_sed",
    "handle_diff",
    "handle_regex",
    "handle_rand",
    "safe_calc",
    "_b64_encode",
    "_b64_decode",
    "_b64_is_valid",
    "_parse_sed_expr",
    "_build_diff_output",
    "_format_regex_result",
    "_json_extract_text",
]

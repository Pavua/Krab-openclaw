# -*- coding: utf-8 -*-
"""Diagnostic / utility / infrastructure commands. Phase 2 Wave 21 (Session 28).

Extracted handlers (см. command_handlers.py для context):
  !help, !screenshot, !bench, !eval, !run, !link, !time, !typing, !say,
  !listen, !filter, !chado, !e2e-smoke
"""

from __future__ import annotations

import ast as _ast
import asyncio
import datetime
import math as _math
import os
import pathlib
import re
import subprocess
import sys
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx
from pyrogram.types import Message

from ...core.access_control import AccessLevel
from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


# =============================================================================
# !help
# =============================================================================


async def handle_help(bot: "KraabUserbot", message: Message) -> None:
    """Справка по командам — генерируется из command_registry, с пагинацией."""
    from ...core.command_registry import registry as _reg

    _category_icons: dict[str, str] = {
        "basic": "📋",
        "ai": "💬",
        "models": "🤖",
        "translator": "🔄",
        "swarm": "🐝",
        "costs": "💰",
        "notes": "📝",
        "management": "⚙️",
        "modes": "🔇",
        "users": "👤",
        "scheduler": "⏰",
        "system": "🖥️",
        "dev": "🛠️",
    }
    _category_labels: dict[str, str] = {
        "basic": "Основные",
        "ai": "AI",
        "models": "Модели",
        "translator": "Translator",
        "swarm": "Swarm (рой агентов)",
        "costs": "Расходы и бюджет",
        "notes": "Заметки и закладки",
        "management": "Управление сообщениями",
        "modes": "Режимы и фильтры",
        "users": "Пользователи и доступ",
        "scheduler": "Планировщик",
        "system": "Система и macOS",
        "dev": "Dev / AI CLI",
    }

    def _build_part(cat_list: list[str], header: str) -> str:
        parts = [header]
        for cat in cat_list:
            icon = _category_icons.get(cat, "•")
            label = _category_labels.get(cat, cat)
            lines = [f"{icon} **{label}**"]
            for cmd in _reg.by_category(cat):
                lines.append(f"`!{cmd.name}` — {cmd.description}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    cats = _reg.categories()
    half = len(cats) // 2
    part1 = _build_part(cats[:half], "🦀 **Krab Commands** (1/2)\n━━━━━━━━━━━━━━━")
    part2 = _build_part(cats[half:], "🦀 **Krab Commands** (2/2)\n━━━━━━━━━━━━━━━")

    page_limit = 4000
    combined = part1 + "\n\n" + part2
    if len(combined) <= page_limit:
        await message.reply(combined)
    elif len(part1) <= page_limit and len(part2) <= page_limit:
        await message.reply(part1)
        await message.reply(part2)
    else:
        current_msg: list[str] = []
        for cat in cats:
            icon = _category_icons.get(cat, "•")
            label = _category_labels.get(cat, cat)
            cat_header = f"{icon} **{label}**"
            cat_lines = [cat_header]
            for cmd in _reg.by_category(cat):
                cat_lines.append(f"`!{cmd.name}` — {cmd.description}")
            cat_text = "\n".join(cat_lines)
            test_msg = "\n\n".join(current_msg + [cat_text]) if current_msg else cat_text
            if len(test_msg) > page_limit and current_msg:
                await message.reply("\n\n".join(current_msg))
                current_msg = [cat_text]
            else:
                current_msg.append(cat_text)
        if current_msg:
            await message.reply("\n\n".join(current_msg))


# =============================================================================
# !screenshot
# =============================================================================


async def handle_screenshot(bot: "KraabUserbot", message: Message) -> None:
    """Снимок экрана текущей вкладки Chrome через CDP."""
    del bot
    from ...integrations.browser_bridge import browser_bridge as _bb

    raw_parts = str(message.text or "").split()
    sub = raw_parts[1].lower().strip() if len(raw_parts) > 1 else ""

    if sub == "health":
        probe = await _bb.health_check()
        status = (
            "✅ CDP ready"
            if probe.get("ok")
            else ("🚫 blocked" if probe.get("blocked") else "⚠️ degraded")
        )
        tabs = probe.get("tab_count", 0)
        err = probe.get("error", "")
        text = f"📡 **Browser CDP**\n{status} — {tabs} вкладок"
        if err:
            text += f"\n`{err[:200]}`"
        await message.reply(text)
        return

    if sub == "ocr":
        lang = raw_parts[2] if len(raw_parts) > 2 else ""
        probe = await _bb.health_check(timeout_sec=4.0)
        if not probe.get("ok"):
            err_detail = probe.get("error") or "CDP недоступен"
            await message.reply(f"📡 **!screenshot ocr**: браузер недоступен\n`{err_detail[:300]}`")
            return
        try:
            png_bytes = await asyncio.wait_for(_bb.screenshot(), timeout=15.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ Таймаут снимка (15 с).")
            return
        if not png_bytes:
            await message.reply("❌ Снимок пустой.")
            return
        try:
            from ...integrations.macos_automation import macos_automation as _ma

            if not _ma.is_ocr_available():
                await message.reply(
                    "📄 **OCR**: tesseract не установлен.\n"
                    "`brew install tesseract` + `brew install tesseract-lang` для русского"
                )
                return
            text_result = await asyncio.wait_for(_ma.ocr_image(png_bytes, lang=lang), timeout=30.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ OCR таймаут (30 с).")
            return
        except Exception as exc:
            await message.reply(f"❌ OCR ошибка: `{str(exc)[:300]}`")
            return
        if not text_result:
            await message.reply("📄 OCR: текст не найден.")
            return
        lang_label = f" [{lang}]" if lang else ""
        await message.reply(f"📄 **OCR{lang_label}:**\n```\n{text_result[:4000]}\n```")
        return

    probe = await _bb.health_check(timeout_sec=4.0)
    if not probe.get("ok"):
        try:
            from ...integrations.dedicated_chrome import launch_dedicated_chrome

            ok_launch, _reason = await asyncio.to_thread(launch_dedicated_chrome)
            if ok_launch:
                logger.info("screenshot_auto_started_chrome")
                probe = await _bb.health_check(timeout_sec=6.0)
        except Exception as _ce:
            logger.warning("screenshot_chrome_autostart_failed", error=repr(_ce))

    png_bytes: bytes | None = None
    cdp_ok = probe.get("ok", False)

    if cdp_ok:
        await message.reply("📸 Делаю снимок…")
        try:
            png_bytes = await asyncio.wait_for(_bb.screenshot(), timeout=15.0)
        except asyncio.TimeoutError:
            await message.reply("⏱ Таймаут снимка (15 с). Попробуй позже.")
            return
        except Exception as exc:
            logger.warning("screenshot_cdp_failed", error=repr(exc))
            png_bytes = None

    if not png_bytes:
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _sc_tmp:
                _sc_path = _sc_tmp.name
            _sc_proc = await asyncio.create_subprocess_exec(
                "screencapture",
                "-x",
                "-t",
                "png",
                _sc_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(_sc_proc.wait(), timeout=10.0)
            import pathlib as _pl

            _sc_file = _pl.Path(_sc_path)
            if _sc_file.exists() and _sc_file.stat().st_size > 0:
                png_bytes = _sc_file.read_bytes()
                _sc_file.unlink(missing_ok=True)
                logger.info("screenshot_screencapture_fallback_ok")
            else:
                _sc_file.unlink(missing_ok=True)
                png_bytes = None
        except Exception as _sc_exc:
            logger.warning("screenshot_screencapture_fallback_failed", error=repr(_sc_exc))
            png_bytes = None

    if not png_bytes:
        err_detail = probe.get("error") or (
            "Chrome не запущен или CDP недоступен" if probe.get("blocked") else "неизвестная ошибка"
        )
        await message.reply(
            f"❌ **!screenshot**: снимок не удался\n"
            f"• CDP: {err_detail[:200]}\n"
            f"• macOS screencapture тоже не сработал\n"
            f"Запусти Chrome: `./scripts/start_dedicated_chrome.command`"
        )
        return

    import tempfile

    if not cdp_ok:
        caption = "📸 Screenshot (macOS screencapture — Chrome CDP недоступен)"
    else:
        caption = "📸 Screenshot"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
        _tmp.write(png_bytes)
        _tmp_path = _tmp.name
    try:
        await message.reply_photo(_tmp_path, caption=caption)
    except Exception as _photo_err:
        logger.warning("reply_photo_failed", error=str(_photo_err))
        try:
            await message.reply_document(_tmp_path, caption=caption + " (doc)")
        except Exception as _doc_err:
            logger.error("reply_document_failed", error=str(_doc_err))
            await message.reply(f"❌ Не удалось отправить скриншот: `{str(_photo_err)[:200]}`")
    finally:
        os.unlink(_tmp_path)


# =============================================================================
# !bench
# =============================================================================


async def handle_bench(bot: "KraabUserbot", message: Message) -> None:
    """!bench [fast|full|fts|semantic] — запуск subset бенчмарков."""
    access = bot._get_access_profile(message.from_user)
    if access.level != AccessLevel.OWNER:
        await message.reply("⛔ Только для владельца.")
        return

    args = (bot._get_command_args(message) or "").strip().lower()
    preset = args if args in ("fast", "full", "fts", "semantic") else "fast"

    from ...core.command_registry import bump_command

    bump_command("bench")

    iterations_map = {"fast": 20, "full": 100, "fts": 50, "semantic": 10}
    iterations = iterations_map.get(preset, 20)

    await message.reply(f"⏱ Benchmark `{preset}` (iterations={iterations})...")

    try:
        krab_root = pathlib.Path.home() / "Antigravity_AGENTS" / "Краб"
        result = subprocess.run(
            [sys.executable, "scripts/benchmark_suite.py", "--iterations", str(iterations)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(krab_root),
        )
        output = result.stdout[-1500:] if len(result.stdout) > 1500 else result.stdout
        if not output:
            output = "(empty output)"
        await message.reply(
            f"📊 **Benchmark results ({preset})**:\n```\n{output}\n```",
        )
        logger.info("handle_bench_done", preset=preset, iterations=iterations)
    except subprocess.TimeoutExpired:
        await message.reply("⚠️ Benchmark timed out после 120 сек")
        logger.warning("handle_bench_timeout", preset=preset)
    except Exception as exc:  # noqa: BLE001
        logger.error("handle_bench_error", preset=preset, error=str(exc))
        await message.reply(f"❌ Benchmark failed: {exc}")


# =============================================================================
# !eval / safe_eval — безопасный AST-eval (без statements)
# =============================================================================

_EVAL_ALLOWED_NODES = (
    _ast.Expression,
    _ast.Constant,
    _ast.BinOp,
    _ast.UnaryOp,
    _ast.BoolOp,
    _ast.Compare,
    _ast.IfExp,
    _ast.Call,
    _ast.Name,
    _ast.Attribute,
    _ast.Subscript,
    _ast.Slice,
    _ast.List,
    _ast.Tuple,
    _ast.Dict,
    _ast.Set,
    _ast.ListComp,
    _ast.SetComp,
    _ast.DictComp,
    _ast.GeneratorExp,
    _ast.comprehension,
    _ast.Add,
    _ast.Sub,
    _ast.Mult,
    _ast.Div,
    _ast.FloorDiv,
    _ast.Mod,
    _ast.Pow,
    _ast.BitAnd,
    _ast.BitOr,
    _ast.BitXor,
    _ast.LShift,
    _ast.RShift,
    _ast.Invert,
    _ast.Not,
    _ast.UAdd,
    _ast.USub,
    _ast.And,
    _ast.Or,
    _ast.Eq,
    _ast.NotEq,
    _ast.Lt,
    _ast.LtE,
    _ast.Gt,
    _ast.GtE,
    _ast.Is,
    _ast.IsNot,
    _ast.In,
    _ast.NotIn,
    _ast.Load,
    _ast.Store,
    _ast.Del,
)

_EVAL_FORBIDDEN_NAMES = frozenset(
    {
        "import",
        "exec",
        "eval",
        "open",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "__build_class__",
        "compile",
        "globals",
        "locals",
        "vars",
        "dir",
        "delattr",
        "setattr",
        "getattr",
        "breakpoint",
        "input",
        "print",
    }
)

_EVAL_NAMESPACE: dict[str, object] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "list": list,
    "tuple": tuple,
    "set": set,
    "dict": dict,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "complex": complex,
    "bytes": bytes,
    "bytearray": bytearray,
    "range": range,
    "type": type,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "repr": repr,
    "hash": hash,
    "hex": hex,
    "oct": oct,
    "bin": bin,
    "ord": ord,
    "chr": chr,
    "divmod": divmod,
    "pow": pow,
    "all": all,
    "any": any,
    "sqrt": _math.sqrt,
    "sin": _math.sin,
    "cos": _math.cos,
    "tan": _math.tan,
    "log": _math.log,
    "log2": _math.log2,
    "log10": _math.log10,
    "ceil": _math.ceil,
    "floor": _math.floor,
    "trunc": _math.trunc,
    "pi": _math.pi,
    "e": _math.e,
    "inf": _math.inf,
    "nan": _math.nan,
    "tau": _math.tau,
    "True": True,
    "False": False,
    "None": None,
}


def _eval_check_node(node: _ast.AST) -> None:
    """Рекурсивно проверяет AST-узел на допустимость для !eval."""
    if not isinstance(node, _EVAL_ALLOWED_NODES):
        raise UserInputError(user_message=f"❌ Недопустимая конструкция: `{type(node).__name__}`")
    if isinstance(node, _ast.Attribute):
        if node.attr.startswith("__"):
            raise UserInputError(user_message=f"❌ Доступ к `{node.attr}` запрещён.")
    if isinstance(node, _ast.Name):
        if node.id in _EVAL_FORBIDDEN_NAMES or node.id.startswith("__"):
            raise UserInputError(user_message=f"❌ Имя `{node.id}` запрещено.")
    for child in _ast.iter_child_nodes(node):
        _eval_check_node(child)


def safe_eval(expression: str) -> object:
    """Безопасно вычисляет Python-выражение через AST + ограниченный namespace."""
    expression = expression.strip()
    if not expression:
        raise UserInputError(user_message="❌ Пустое выражение.")
    if len(expression) > 500:
        raise UserInputError(user_message="❌ Выражение слишком длинное (макс. 500 символов).")

    try:
        tree = compile(expression, "<eval>", "eval", _ast.PyCF_ONLY_AST)
    except SyntaxError as exc:
        raise UserInputError(user_message=f"❌ Синтаксическая ошибка: {exc.msg}")

    _eval_check_node(tree)

    try:
        result = eval(  # noqa: S307
            compile(tree, "<eval>", "eval"),
            {"__builtins__": {}},
            _EVAL_NAMESPACE,
        )
    except ZeroDivisionError:
        raise UserInputError(user_message="❌ Деление на ноль.")
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise UserInputError(user_message=f"❌ Ошибка вычисления: {exc}")
    except MemoryError:
        raise UserInputError(user_message="❌ Результат слишком большой (MemoryError).")
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(user_message=f"❌ Ошибка: {exc}")

    return result


async def handle_eval(bot: "KraabUserbot", message: Message) -> None:
    """!eval <выражение> — безопасный eval Python-выражений. Timeout: 2 секунды."""
    expr = bot._get_command_args(message).strip()
    if not expr:
        raise UserInputError(
            user_message=(
                "\U0001f40d **!eval — Python expressions**\n\n"
                "Использование: `!eval <выражение>`\n\n"
                "Примеры:\n"
                "`!eval 2**100` → большое число\n"
                '`!eval len("hello")` → `5`\n'
                "`!eval sorted([3,1,2])` → `[1, 2, 3]`\n"
                "`!eval [x**2 for x in range(5)]` → `[0, 1, 4, 9, 16]`\n\n"
                "Только expressions. Statements (import, def, class) запрещены.\n"
                "Timeout: 2 секунды."
            )
        )

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, safe_eval, expr),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        await message.reply("⏱ Timeout: вычисление прервано (>2 сек).")
        return

    result_repr = repr(result)
    if len(result_repr) > 3000:
        result_repr = result_repr[:3000] + "…"

    await message.reply(f"= {result_repr}")


# =============================================================================
# !run — выполнение Python-кода в subprocess (owner-only)
# =============================================================================


async def handle_run(bot: "KraabUserbot", message: Message) -> None:
    """!run <код> — выполнить Python в subprocess (owner-only, timeout 5s)."""
    from ...core.subprocess_env import clean_subprocess_env  # noqa: PLC0415

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!run` доступен только владельцу.")

    code = bot._get_command_args(message).strip()
    if not code and message.reply_to_message:
        code = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()

    if not code:
        raise UserInputError(
            user_message=(
                "🐍 **!run — выполнение Python**\n\n"
                "Использование:\n"
                "`!run print('hello')` — выполнить код\n"
                "`!run 2**100` — вычислить выражение\n"
                "`!run` (в reply) — выполнить код из ответного сообщения\n\n"
                "Timeout: 5 секунд."
            )
        )

    exec_code = code
    try:
        _ast.parse(code, mode="eval")
        exec_code = f"__r = {code}\nif __r is not None: print(__r)"
    except SyntaxError:
        exec_code = code

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            exec_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_subprocess_env(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        await message.reply("⏱ Timeout: выполнение прервано (>5 сек).")
        return

    out = stdout.decode("utf-8", errors="replace").rstrip()
    err = stderr.decode("utf-8", errors="replace").rstrip()

    parts: list[str] = []
    if out:
        parts.append(f"```\n{out}\n```")
    if err:
        parts.append(f"⚠️ stderr:\n```\n{err}\n```")
    if not parts:
        rc = proc.returncode
        parts.append(f"✅ Код выполнен (exit {rc}, без вывода).")

    await message.reply("\n".join(parts))


# =============================================================================
# !link — preview / expand / reply-анализ URL
# =============================================================================

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_SHORT_DOMAINS = frozenset(
    [
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "short.link",
        "rb.gy",
        "cutt.ly",
        "is.gd",
        "v.gd",
        "tiny.cc",
        "shorturl.at",
        "clck.ru",
        "vk.cc",
    ]
)


def _is_short_url(url: str) -> bool:
    """Проверяет, является ли URL коротким (шорт-линк)."""
    try:
        from urllib.parse import urlparse  # noqa: PLC0415

        host = urlparse(url).netloc.lower().lstrip("www.")
        return host in _SHORT_DOMAINS
    except Exception:  # noqa: BLE001
        return False


async def _fetch_link_meta(url: str, *, timeout: float = 10.0) -> dict:
    """Загружает страницу и извлекает мета-теги (title, description, og:image)."""
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    result: dict = {"title": "", "description": "", "image": "", "final_url": url}
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_headers,
    ) as client:
        resp = await client.get(url)
        result["final_url"] = str(resp.url)
        html = resp.text

    title_match = re.search(r"<title[^>]*>([^<]{1,300})</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        result["title"] = re.sub(r"\s+", " ", title_match.group(1)).strip()

    og_title = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{1,300})["\']',
        html,
        re.IGNORECASE,
    )
    if og_title:
        result["title"] = og_title.group(1).strip()

    og_desc = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html,
        re.IGNORECASE,
    )
    if not og_desc:
        og_desc = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,500})["\']',
            html,
            re.IGNORECASE,
        )
    if og_desc:
        result["description"] = og_desc.group(1).strip()

    og_img = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']{1,500})["\']',
        html,
        re.IGNORECASE,
    )
    if og_img:
        result["image"] = og_img.group(1).strip()

    return result


async def _expand_url(url: str, *, timeout: float = 10.0) -> str:
    """Разворачивает короткий URL через HEAD-запрос с редиректами."""
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_headers,
    ) as client:
        resp = await client.head(url)
        return str(resp.url)


def _format_link_preview(meta: dict) -> str:
    """Форматирует мета-данные ссылки в стандартный блок."""
    lines = ["🔗 **Link Preview**", "─────"]
    if meta.get("title"):
        lines.append(f"Title: {meta['title']}")
    if meta.get("description"):
        desc = meta["description"]
        if len(desc) > 200:
            desc = desc[:197] + "..."
        lines.append(f"Description: {desc}")
    lines.append(f"URL: {meta['final_url']}")
    if meta.get("image"):
        lines.append(f"Image: {meta['image']}")
    return "\n".join(lines)


async def handle_link(bot: "KraabUserbot", message: Message) -> None:
    """!link — утилиты для ссылок (preview/expand/reply-анализ).

    Dual-namespace: тесты патчат src.handlers.command_handlers._fetch_link_meta /
    _expand_url. Лукапы делаются через namespace command_handlers.*.
    """
    from .. import command_handlers as _ch

    args_raw = bot._get_command_args(message).strip()

    fetch_meta = getattr(_ch, "_fetch_link_meta", _fetch_link_meta)
    expand_fn = getattr(_ch, "_expand_url", _expand_url)
    fmt_preview = getattr(_ch, "_format_link_preview", _format_link_preview)
    url_re = getattr(_ch, "_URL_RE", _URL_RE)

    if not args_raw and message.reply_to_message:
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        urls = url_re.findall(reply_text)
        if not urls:
            raise UserInputError(user_message="❌ В reply-сообщении нет ссылок.")
        url = urls[0]
        await message.reply("⏳ Анализирую ссылку...")
        try:
            meta = await fetch_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(fmt_preview(meta), disable_web_page_preview=True)
        return

    parts = args_raw.split(maxsplit=1)
    if not parts:
        raise UserInputError(
            user_message=(
                "❌ Использование:\n"
                "`!link preview <URL>` — превью страницы\n"
                "`!link expand <URL>` — развернуть короткую ссылку\n"
                "Или ответь на сообщение: `!link`"
            )
        )

    subcommand = parts[0].lower()

    if subcommand == "preview":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи URL: `!link preview <URL>`")
        url = parts[1].strip()
        await message.reply("⏳ Загружаю превью...")
        try:
            meta = await fetch_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(fmt_preview(meta), disable_web_page_preview=True)
        return

    if subcommand == "expand":
        if len(parts) < 2 or not parts[1].strip():
            raise UserInputError(user_message="❌ Укажи URL: `!link expand <URL>`")
        url = parts[1].strip()
        await message.reply("⏳ Разворачиваю ссылку...")
        try:
            final = await expand_fn(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось развернуть: {exc}") from exc
        if final == url:
            text = f"🔗 URL не изменился:\n`{final}`"
        else:
            text = f"🔗 **Expand**\n─────\nИсходный: `{url}`\nФинальный: `{final}`"
        await message.reply(text, disable_web_page_preview=True)
        return

    if parts[0].startswith(("http://", "https://")):
        url = args_raw.strip()
        await message.reply("⏳ Загружаю превью...")
        try:
            meta = await fetch_meta(url)
        except Exception as exc:  # noqa: BLE001
            raise UserInputError(user_message=f"❌ Не удалось загрузить: {exc}") from exc
        await message.reply(fmt_preview(meta), disable_web_page_preview=True)
        return

    raise UserInputError(
        user_message=(
            "❌ Неизвестная подкоманда. Использование:\n"
            "`!link preview <URL>` — превью страницы\n"
            "`!link expand <URL>` — развернуть короткую ссылку\n"
            "Или ответь на сообщение: `!link`"
        )
    )


# =============================================================================
# !time — мировые часы / convert между timezones
# =============================================================================

_TIME_CITY_MAP: dict[str, str] = {
    "madrid": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "moscow": "Europe/Moscow",
    "москва": "Europe/Moscow",
    "london": "Europe/London",
    "лондон": "Europe/London",
    "berlin": "Europe/Berlin",
    "берлин": "Europe/Berlin",
    "paris": "Europe/Paris",
    "париж": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "rome": "Europe/Rome",
    "рим": "Europe/Rome",
    "istanbul": "Europe/Istanbul",
    "стамбул": "Europe/Istanbul",
    "new york": "America/New_York",
    "newyork": "America/New_York",
    "nyc": "America/New_York",
    "нью-йорк": "America/New_York",
    "нью йорк": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "лос-анджелес": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "чикаго": "America/Chicago",
    "toronto": "America/Toronto",
    "торонто": "America/Toronto",
    "sao paulo": "America/Sao_Paulo",
    "são paulo": "America/Sao_Paulo",
    "mexico": "America/Mexico_City",
    "mexico city": "America/Mexico_City",
    "tokyo": "Asia/Tokyo",
    "токио": "Asia/Tokyo",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "пекин": "Asia/Shanghai",
    "шанхай": "Asia/Shanghai",
    "seoul": "Asia/Seoul",
    "сеул": "Asia/Seoul",
    "dubai": "Asia/Dubai",
    "дубай": "Asia/Dubai",
    "singapore": "Asia/Singapore",
    "сингапур": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong",
    "гонконг": "Asia/Hong_Kong",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "мумбаи": "Asia/Kolkata",
    "дели": "Asia/Kolkata",
    "bangkok": "Asia/Bangkok",
    "бангкок": "Asia/Bangkok",
    "sydney": "Australia/Sydney",
    "сидней": "Australia/Sydney",
}

_TIME_DEFAULT_CITIES: list[tuple[str, str]] = [
    ("Madrid", "Europe/Madrid"),
    ("Moscow", "Europe/Moscow"),
    ("New York", "America/New_York"),
    ("Tokyo", "Asia/Tokyo"),
]


def _time_format_dt(dt: "datetime.datetime") -> str:
    """Форматирует datetime в '10:35 Mon, Apr 12'."""
    return dt.strftime("%H:%M %a, %b %-d")


def _time_lookup_tz(city: str) -> str | None:
    """Возвращает IANA timezone для города или None."""
    key = city.strip().lower()
    if key in _TIME_CITY_MAP:
        return _TIME_CITY_MAP[key]
    try:
        ZoneInfo(city)
        return city
    except Exception:  # noqa: BLE001
        return None


async def handle_time(bot: "KraabUserbot", message: Message) -> None:
    """!time — мировые часы и конвертация времени."""
    args = bot._get_command_args(message).strip()

    if args.lower().startswith("convert "):
        rest = args[len("convert ") :].strip()
        time_match = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", rest)
        if not time_match:
            raise UserInputError(
                user_message=(
                    "❌ Формат: `!time convert HH:MM <город_из> <город_в>`\n"
                    "Пример: `!time convert 15:00 Madrid Moscow`"
                )
            )
        time_str = time_match.group(1)
        cities_part = time_match.group(2).strip()

        from_tz: str | None = None
        to_tz: str | None = None
        city_from_name = ""
        city_to_name = ""
        tokens = cities_part.split()
        found = False
        for split_i in range(1, len(tokens)):
            cf = " ".join(tokens[:split_i])
            ct = " ".join(tokens[split_i:])
            tz_f = _time_lookup_tz(cf)
            tz_t = _time_lookup_tz(ct)
            if tz_f and tz_t:
                from_tz, to_tz = tz_f, tz_t
                city_from_name, city_to_name = cf.title(), ct.title()
                found = True
                break

        if not found:
            raise UserInputError(
                user_message=(
                    "❌ Не могу распознать города.\n"
                    "Поддерживаемые: Madrid, Moscow, New York, Tokyo, London, Dubai и др.\n"
                    "Пример: `!time convert 15:00 Madrid Moscow`"
                )
            )

        try:
            hh, mm = map(int, time_str.split(":"))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError("out of range")
        except ValueError:
            raise UserInputError(
                user_message=f"❌ Некорректное время: `{time_str}`. Формат HH:MM (00:00–23:59)."
            )

        today = datetime.date.today()
        dt_from = datetime.datetime(
            today.year, today.month, today.day, hh, mm, 0, tzinfo=ZoneInfo(from_tz)
        )
        dt_to = dt_from.astimezone(ZoneInfo(to_tz))

        await message.reply(
            f"🕐 **Конвертация времени**\n"
            f"`{time_str}` ({city_from_name}, {from_tz})\n"
            f"→ `{dt_to.strftime('%H:%M')}` ({city_to_name}, {to_tz})\n\n"
            f"_{_time_format_dt(dt_from)} → {_time_format_dt(dt_to)}_"
        )
        return

    if args:
        tz_name = _time_lookup_tz(args)

        if not tz_name:
            args_lower = args.lower()
            for city_key, tz in _TIME_CITY_MAP.items():
                if city_key.startswith(args_lower) and len(args_lower) >= 3:
                    tz_name = tz
                    break

        if not tz_name:
            raise UserInputError(
                user_message=(
                    f"❌ Город `{args}` не найден.\n\n"
                    "Поддерживаемые города:\n"
                    "Madrid, Barcelona, Moscow, London, Berlin, Paris,\n"
                    "New York, Los Angeles, Chicago, Toronto,\n"
                    "Tokyo, Dubai, Singapore, Hong Kong, Mumbai, Bangkok, Sydney\n\n"
                    "Или IANA timezone напрямую: `!time Europe/Berlin`"
                )
            )

        dt = datetime.datetime.now(ZoneInfo(tz_name))
        display_name = args.title()
        offset = dt.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""

        await message.reply(
            f"🕐 **{display_name}** ({tz_name})\n`{_time_format_dt(dt)}` {offset_fmt}"
        )
        return

    now_utc = datetime.datetime.now(ZoneInfo("UTC"))
    lines: list[str] = ["🌍 **Мировое время**\n"]
    for city_name, tz_name in _TIME_DEFAULT_CITIES:
        dt = now_utc.astimezone(ZoneInfo(tz_name))
        offset = dt.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
        lines.append(f"**{city_name}** — `{_time_format_dt(dt)}` {offset_fmt}")

    await message.reply("\n".join(lines))


# =============================================================================
# !typing — симуляция набора текста / голосовой записи / загрузки
# =============================================================================

_TYPING_ACTION_MAP: dict[str, str] = {
    "typing": "TYPING",
    "record": "RECORD_AUDIO",
    "upload": "UPLOAD_DOCUMENT",
}

_TYPING_LABEL_MAP: dict[str, str] = {
    "typing": "⌨️ typing...",
    "record": "🎙 recording voice...",
    "upload": "📤 uploading...",
}

_TYPING_DEFAULT_SECONDS = 5
_TYPING_MAX_SECONDS = 30


async def handle_typing(bot: "KraabUserbot", message: Message) -> None:
    """Симулирует действие в чате (typing / recording / uploading). Owner-only."""
    from pyrogram import enums as _pyrogram_enums

    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!typing` доступен только владельцу.")

    args = bot._get_command_args(message).strip().lower().split()

    action_key = "typing"
    seconds = _TYPING_DEFAULT_SECONDS

    if args:
        if args[0] in _TYPING_ACTION_MAP:
            action_key = args[0]
            if len(args) >= 2:
                try:
                    seconds = int(args[1])
                except ValueError:
                    raise UserInputError(
                        user_message=f"❌ Длительность должна быть числом, получено: `{args[1]}`"
                    )
        else:
            try:
                seconds = int(args[0])
            except ValueError:
                raise UserInputError(
                    user_message=(
                        "⌨️ **Симуляция набора текста**\n\n"
                        "`!typing [N]` — typing N секунд (default 5, max 30)\n"
                        "`!typing record [N]` — recording voice...\n"
                        "`!typing upload [N]` — uploading..."
                    )
                )

    seconds = max(1, min(seconds, _TYPING_MAX_SECONDS))

    pyrogram_action = getattr(_pyrogram_enums.ChatAction, _TYPING_ACTION_MAP[action_key])
    label = _TYPING_LABEL_MAP[action_key]
    chat_id = message.chat.id

    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    logger.info("handle_typing: %s в чате %s на %ss", action_key, chat_id, seconds)
    elapsed = 0
    interval = 4
    while elapsed < seconds:
        try:
            await bot.client.send_chat_action(chat_id, pyrogram_action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("handle_typing: send_chat_action ошибка: %s", exc)
            break
        sleep_time = min(interval, seconds - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time

    try:
        await bot.client.send_chat_action(chat_id, _pyrogram_enums.ChatAction.CANCEL)
    except Exception:  # noqa: BLE001
        pass

    logger.info("handle_typing: завершено (%s, %ss)", label, seconds)


# =============================================================================
# !say — тихая отправка
# =============================================================================


async def handle_say(bot: "KraabUserbot", message: Message) -> None:
    """!say <текст> — отправляет от имени юзербота, удаляя команду."""
    raw = bot._get_command_args(message).strip()

    if not raw:
        raise UserInputError(
            user_message="❌ Использование: `!say <текст>` или `!say <chat_id> <текст>`"
        )

    parts = raw.split(maxsplit=1)
    target_chat: int | str = message.chat.id
    text = raw

    if len(parts) == 2:
        first = parts[0]
        try:
            target_chat = int(first)
            text = parts[1]
        except ValueError:
            if first.startswith("@"):
                target_chat = first
                text = parts[1]

    if not text:
        raise UserInputError(user_message="❌ Текст сообщения не может быть пустым.")

    # Резолвим username → числовой peer_id если нужно
    if isinstance(target_chat, str) and not target_chat.lstrip("-").isdigit():
        from ...core.telegram_resolver import resolve_peer

        resolve_result = await resolve_peer(bot.client, target_chat)
        if not resolve_result.get("ok"):
            await bot._safe_reply(
                message,
                f"❌ Не удалось найти `{target_chat}`: {resolve_result.get('error_code', 'PEER_NOT_FOUND')}",
            )
            return
        target_chat = resolve_result["peer_id"]

    try:
        await message.delete()
    except Exception:
        pass

    try:
        await bot.client.send_message(chat_id=target_chat, text=text)
        logger.info("handle_say_sent", target_chat=target_chat, length=len(text))
    except Exception as exc:
        logger.error("handle_say_error", target_chat=target_chat, error=str(exc))
        try:
            await bot.client.send_message(
                chat_id=message.chat.id,
                text=f"❌ Ошибка отправки в `{target_chat}`: {exc}",
            )
        except Exception:
            pass


# =============================================================================
# !listen — режим ответов в чате (active/mention-only/muted)
# =============================================================================


async def _handle_listen_list(bot: "KraabUserbot", message: Message) -> None:
    """Показать все чаты с явными правилами."""
    import datetime as _dt

    from ...core.chat_filter_config import chat_filter_config

    rules = chat_filter_config.list_rules()
    if not rules:
        await message.reply("📭 Нет явных правил. Все чаты используют дефолты.")
        return

    lines = ["🎛️ **Явные правила фильтра:**\n"]
    for r in rules[:30]:
        updated = _dt.datetime.fromtimestamp(r.updated_at).strftime("%Y-%m-%d %H:%M")
        lines.append(f"• `{r.chat_id}` → `{r.mode}` ({updated})")
    if len(rules) > 30:
        lines.append(f"... ещё +{len(rules) - 30}")

    await message.reply("\n".join(lines))


async def _handle_listen_stats(bot: "KraabUserbot", message: Message) -> None:
    """Показать статистику по режимам."""
    from ...core.chat_filter_config import chat_filter_config

    stats = chat_filter_config.stats()
    lines = ["📊 **Статистика фильтра:**\n"]
    lines.append(f"Всего правил: {stats['total_rules']}")
    for mode, count in sorted(stats.get("by_mode", {}).items()):
        lines.append(f"• `{mode}`: {count}")

    await message.reply("\n".join(lines))


async def handle_listen(bot: "KraabUserbot", message: Message) -> None:
    """!listen — управление режимом ответов Краба в чате."""
    from ...core.chat_filter_config import chat_filter_config
    from ...core.command_registry import bump_command

    bump_command("listen")

    args = (bot._get_command_args(message) or "").strip().lower()
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")

    if args == "list":
        return await _handle_listen_list(bot, message)
    if args == "stats":
        return await _handle_listen_stats(bot, message)

    if args == "reload":
        changed = chat_filter_config.reload()
        total = chat_filter_config.stats().get("total_rules", 0)
        status = "🔄 (changed)" if changed else "✅ (no changes)"
        await message.reply(f"{status} Config reloaded. Total rules: {total}")
        return

    if args in ("active", "mention-only", "muted"):
        chat_filter_config.set_mode(chat_id, args)
        mode_name = {
            "active": "все сообщения",
            "mention-only": "@mention и reply",
            "muted": "молчать",
        }[args]
        await message.reply(f"✅ Чат `{chat_id}`: {mode_name}")
        return

    if args == "reset":
        chat_filter_config.reset(chat_id)
        await message.reply(f"🔄 Чат `{chat_id}`: вернулся к дефолту")
        return

    if not args:
        mode = chat_filter_config.get_mode(chat_id, is_group=is_group)
        mode_emoji = {"active": "🟢", "mention-only": "🟡", "muted": "🔴"}[mode]
        await message.reply(f"{mode_emoji} Текущий режим: `{mode}`")
        return

    await message.reply(
        "❌ Неизвестный режим. Используйте: active, mention-only, muted, reset, reload, list, stats",
    )


# =============================================================================
# !filter — per-chat filter mode toggle (Chado §3 P2)
# =============================================================================


async def handle_filter(bot: "KraabUserbot", message: Message) -> None:
    """!filter — алиас !listen для per-chat filter mode (Chado §3 P2)."""
    from ...core.chat_filter_config import chat_filter_config
    from ...core.command_registry import bump_command
    from ...core.message_priority_dispatcher import get_mode_for_chat

    bump_command("filter")

    raw = (bot._get_command_args(message) or "").strip().lower()
    args = "" if raw == "status" else raw
    chat_id = message.chat.id
    is_group = message.chat.type in ("group", "supergroup")

    if args in ("active", "mention-only", "muted"):
        chat_filter_config.set_chat_mode(chat_id, args)
        mode_label = {
            "active": "все сообщения",
            "mention-only": "@mention и reply",
            "muted": "молчать",
        }[args]
        await message.reply(f"✅ Чат `{chat_id}`: режим → `{args}` ({mode_label})")
        return

    if args == "reset":
        chat_filter_config.reset(chat_id)
        await message.reply(f"🔄 Чат `{chat_id}`: режим сброшен к дефолту")
        return

    if not args:
        mode = get_mode_for_chat(chat_id, is_group=is_group)
        emoji = {"active": "🟢", "mention-only": "🟡", "muted": "🔴"}.get(mode, "⚪")
        await message.reply(
            f"{emoji} Текущий режим: `{mode}`\n\n"
            "Команды: `!filter active` · `!filter mention-only` · `!filter muted` · `!filter reset`"
        )
        return

    await message.reply(
        "❌ Неизвестный режим.\nИспользуйте: `status`, `active`, `mention-only`, `muted`, `reset`"
    )


# =============================================================================
# !chado — статус cross-AI sync с Chado
# =============================================================================


async def handle_chado(bot: "KraabUserbot", message: Message) -> None:
    """!chado — статус cross-AI синхронизации с Chado (Chado §9 P2)."""
    from ...core.command_registry import bump_command

    bump_command("chado")

    raw = (bot._get_command_args(message) or "").strip().lower()
    sub = raw.split()[0] if raw else "status"

    if sub in ("", "status"):
        await _handle_chado_status(message)
    elif sub == "ping":
        await _handle_chado_ping(bot, message)
    elif sub == "digest":
        await _handle_chado_digest(message)
    else:
        await message.reply(
            "❌ Неизвестная субкоманда.\n"
            "Используйте: `!chado status` · `!chado ping` · `!chado digest`"
        )


async def _handle_chado_status(message: Message) -> None:
    """Показывает текущее состояние cross-AI sync с Chado."""
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    from ...core.cross_ai_review import parse_review_bullets  # noqa: F401

    db_path = Path.home() / ".openclaw" / "krab_memory" / "archive.db"
    chado_count = 0
    latest_quote = ""
    try:
        if db_path.exists():
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=1.5)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE sender_name LIKE '%Chado%'"
                ).fetchone()
                chado_count = int(row[0]) if row else 0

                latest_row = conn.execute(
                    "SELECT text FROM messages WHERE sender_name LIKE '%Chado%'"
                    " ORDER BY date DESC LIMIT 1"
                ).fetchone()
                if latest_row and latest_row[0]:
                    latest_quote = str(latest_row[0])[:200]
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_status_archive_query_failed", error=str(exc))

    last_sync_ts = ""
    try:
        state_file = Path.home() / ".openclaw" / "krab_runtime_state" / "proactive_watch_state.json"
        if state_file.exists():
            import json as _json

            state = _json.loads(state_file.read_text())
            ts_raw = state.get("last_cross_ai_review_ts") or state.get("cross_ai_review_last_ts")
            if ts_raw:
                try:
                    dt = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                    last_sync_ts = dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception:  # noqa: BLE001
                    last_sync_ts = str(ts_raw)[:20]
    except Exception:  # noqa: BLE001
        pass

    crossteam_link = ""
    try:
        from ...core.swarm_channels import swarm_channels

        forum_id = getattr(swarm_channels, "_forum_chat_id", None)
        topics: dict = getattr(swarm_channels, "_team_topics", {})
        ct_topic = topics.get("crossteam")
        if forum_id and ct_topic:
            crossteam_link = f"t.me/c/{str(forum_id).lstrip('-100')}/{ct_topic}"
    except Exception:  # noqa: BLE001
        pass

    next_trigger = "—"
    try:
        from ...core.scheduler import krab_scheduler

        jobs = krab_scheduler.list_jobs() if hasattr(krab_scheduler, "list_jobs") else []
        for job in jobs:
            name = (getattr(job, "name", None) or "").lower()
            if "chado" in name or "cross_ai" in name:
                next_run = getattr(job, "next_run_time", None)
                if next_run:
                    next_trigger = str(next_run)[:16]
                break
    except Exception:  # noqa: BLE001
        pass

    lines = ["🤝 **Chado Cross-AI Sync — статус**", ""]
    lines.append(f"**Последний sync:** {last_sync_ts or '—'}")
    lines.append(f"**Сообщений Chado в archive.db:** `{chado_count}`")
    if latest_quote:
        lines.append(f"**Последняя цитата:**\n_{latest_quote}_")
    else:
        lines.append("**Последняя цитата:** —")
    lines.append(f"**Crossteam топик:** {crossteam_link or '— (не настроен)'}")
    lines.append(f"**Следующий запуск sync:** {next_trigger}")

    await message.reply("\n".join(lines))


async def _handle_chado_ping(bot: "KraabUserbot", message: Message) -> None:
    """Отправляет ping в Forum Topic crossteam через swarm_channels."""
    from datetime import datetime, timezone

    from ...core.swarm_channels import swarm_channels

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ping_text = (
        f"🤝 [Chado Ping] Cross-AI sync check\n"
        f"Инициатор: owner via `!chado ping`\n"
        f"Время: {ts}\n\n"
        "Chado, ты здесь? Синхронизация активна."
    )

    sent = False
    try:
        chat_id, topic_id = swarm_channels._resolve_destination("crossteam")
        if chat_id:
            await swarm_channels._send_message(chat_id, ping_text, topic_id=topic_id)
            sent = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_ping_broadcast_failed", error=str(exc))

    if sent:
        await message.reply("✅ Ping отправлен в crossteam Forum Topic.")
    else:
        await message.reply(
            "⚠️ Crossteam топик не настроен — ping не отправлен.\n"
            "Используйте `!swarm setup` для настройки Forum Topics."
        )


async def _handle_chado_digest(message: Message) -> None:
    """Dry-run preview cron_chado_sync.py."""
    import importlib.util
    from pathlib import Path

    script_path = Path(__file__).parent.parent.parent.parent / "scripts" / "cron_chado_sync.py"

    if not script_path.exists():
        await message.reply(
            "⚠️ `scripts/cron_chado_sync.py` не найден.\n"
            "Digest недоступен — скрипт sync ещё не создан."
        )
        return

    try:
        spec = importlib.util.spec_from_file_location("cron_chado_sync", script_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        if hasattr(mod, "dry_run_preview"):
            result = mod.dry_run_preview()
            preview = str(result)[:3000] if result else "— нет данных"
        else:
            preview = "⚠️ Функция `dry_run_preview()` не найдена в cron_chado_sync.py"
    except Exception as exc:  # noqa: BLE001
        logger.warning("chado_digest_dry_run_failed", error=str(exc))
        preview = f"❌ Ошибка dry-run: {exc}"

    await message.reply(f"📋 **Chado Digest (dry-run)**\n\n{preview}")


# =============================================================================
# !e2e-smoke — E2E regression smoke tests (owner-only)
# =============================================================================


async def handle_e2e_smoke(bot: "KraabUserbot", message: Message) -> None:
    """!e2e-smoke — запустить E2E regression smoke tests (owner-only)."""
    access_profile = bot._get_access_profile(message.from_user)
    if access_profile.level != AccessLevel.OWNER:
        raise UserInputError(user_message="🔒 `!e2e-smoke` доступен только владельцу.")

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    import importlib.util as _ilu  # noqa: PLC0415
    import pathlib as _pl  # noqa: PLC0415

    _script_path = _pl.Path(__file__).parent.parent.parent.parent / "scripts" / "e2e_smoke_test.py"
    _spec = _ilu.spec_from_file_location("e2e_smoke_test", _script_path)
    if _spec is None or _spec.loader is None:
        await message.reply("❌ e2e_smoke_test.py не найден. Проверьте scripts/e2e_smoke_test.py")
        return
    _e2e = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_e2e)  # type: ignore[union-attr]

    TEST_CASES = _e2e.TEST_CASES  # noqa: N806

    if arg == "list":
        names = "\n".join(f"  • `{c.name}` — {c.description}" for c in TEST_CASES)
        await message.reply(f"**E2E тесты ({len(TEST_CASES)}):**\n{names}")
        return

    owner_chat_id: int | None = None
    try:
        owner_chat_id = message.from_user.id if message.from_user else None
    except Exception:
        pass
    if owner_chat_id is None and hasattr(bot, "owner_user_id"):
        owner_chat_id = bot.owner_user_id

    if not owner_chat_id:
        await message.reply("❌ Не удалось определить owner chat_id для E2E.")
        return

    selected = TEST_CASES
    if arg and arg != "all":
        selected = [c for c in TEST_CASES if c.name == arg]
        if not selected:
            names_list = ", ".join(f"`{c.name}`" for c in TEST_CASES)
            await message.reply(f"❌ Тест `{arg}` не найден.\nДоступные: {names_list}")
            return

    await message.reply(
        f"⚙️ Запускаем E2E smoke tests ({len(selected)}/{len(TEST_CASES)})…\n"
        f"Ожидайте до {len(selected) * 65}s"
    )

    runner = _e2e.E2ESmokeRunner(chat_id=owner_chat_id, timeout=60.0, verbose=False)

    results = await runner.run_all(selected)

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    lines = [f"**E2E Smoke Results: {passed}/{total} passed**\n"]
    for r in results:
        icon = "✅" if r.passed else "❌"
        snippet = (r.actual_text[:60] + "…") if len(r.actual_text) > 60 else r.actual_text
        reason = f" — {r.failure_reason}" if not r.passed else ""
        lines.append(f"{icon} `{r.case.name}` ({r.elapsed:.1f}s){reason}")
        if r.passed and snippet:
            lines.append(f"   _{snippet}_")

    try:
        report = _e2e._render_report(results, sum(r.elapsed for r in results))
        _e2e.save_report(report)
        lines.append("\nОтчёт: `docs/E2E_RESULTS_LATEST.md`")
    except Exception as exc:
        logger.warning("e2e-smoke: save report failed: %s", exc)

    await message.reply("\n".join(lines))

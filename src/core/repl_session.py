# -*- coding: utf-8 -*-
"""
REPLSession — owner-only Live Debug REPL для Krab.

Расширяет одношотовый !eval до интерактивной REPL-сессии с persistent state
между вызовами. Каждый owner получает изолированный namespace; запрещённые
имена / импорты / атрибуты блокируются на уровне AST-walker; каждый запуск
исполняется в отдельном thread с жёстким timeout (default 10s); все попытки
(успешные и заблокированные) уходят в audit-log.

### Инварианты

- **Sandbox-by-default.** Default namespace содержит только whitelisted-модули
  (math, datetime, json, re, hashlib, base64, time, statistics) и safe-builtins.
  Никакого os, sys, subprocess, socket, ctypes, requests, __import__, open,
  eval, exec, compile.
- **AST-валидация перед запуском.** Запрещаем Import, ImportFrom, Global,
  Nonlocal, dunder-имена/атрибуты. Любое такое — REPLSecurityError.
- **Per-owner isolation.** _sessions[owner_id] — отдельный namespace dict.
  Owner A не видит переменные owner B.
- **Timeout enforced.** Запускаем evaluation в отдельном ThreadPoolExecutor.submit,
  ждём timeout_s секунд. Если не успело — REPLTimeoutError. Остановить thread
  Python нативно не может, но новый запуск не блокируется.
- **Audit log append-only.** JSON-lines в
  ~/.openclaw/krab_runtime_state/repl_audit.log. Каждая строка:
  {ts, owner_id, action, code_preview, result_kind, error_type?}.

### Не решает
- Не песочница процесса/контейнера. Изолируем namespace + AST, но любой
  whitelisted-модуль может стать вектором (например, re ReDoS). Owner-only +
  timeout — последняя линия.
- Не отслеживает RAM/CPU. Бесконечная аллокация выжрет память до OOM.
- Не реализует stdin / interactive input. Только code-in / value-out.
"""

from __future__ import annotations

import ast
import base64
import builtins
import concurrent.futures
import contextlib
import datetime as _datetime
import hashlib
import io
import json
import re
import statistics
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Стандартный путь к audit-логу. Можно переопределить через
# configure_default_paths() — паттерн как у chat_ban_cache.
_DEFAULT_AUDIT_LOG = Path.home() / ".openclaw" / "krab_runtime_state" / "repl_audit.log"

# Жёсткий timeout по умолчанию (секунды).
DEFAULT_EXEC_TIMEOUT_S = 10.0

# Лимит длины кода в одной audit-строке (чтобы лог не разрастался от больших dump).
_AUDIT_CODE_PREVIEW_LIMIT = 500


class REPLError(Exception):
    """Базовый класс для всех REPL-ошибок."""


class REPLSecurityError(REPLError):
    """AST-проверка отклонила код (forbidden import / name / dunder и т.п.)."""


class REPLTimeoutError(REPLError):
    """Запуск не уложился в timeout."""


class REPLNotStartedError(REPLError):
    """Попытка запуска в несуществующей сессии."""


# Запрещённые имена. Любое обращение к ним в AST → SecurityError.
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
    }
)

# Запрещённые AST-узлы (statement-level).
_FORBIDDEN_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    # async-формы — отключаем чтобы не плодить вектора через aiohttp/etc.
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
)


def _build_safe_builtins() -> dict[str, Any]:
    """Собирает whitelist builtins — без open/eval/import и пр."""
    whitelist = {
        "abs",
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "bytes",
        "bytearray",
        "callable",
        "chr",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hash",
        "hex",
        "id",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
    }
    safe: dict[str, Any] = {}
    for name in whitelist:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["True"] = True
    safe["False"] = False
    safe["None"] = None
    return safe


def _build_default_namespace() -> dict[str, Any]:
    """Стартовый namespace новой сессии — whitelisted модули + safe builtins."""
    import math as _math

    ns: dict[str, Any] = {
        "__builtins__": _build_safe_builtins(),
        # Whitelisted-модули — импортируются один раз, передаются по ссылке
        # без возможности импортировать что-то ещё.
        "math": _math,
        "datetime": _datetime,
        "json": json,
        "re": re,
        "hashlib": hashlib,
        "base64": base64,
        "time": time,
        "statistics": statistics,
    }
    return ns


def _validate_ast(tree: ast.AST) -> None:
    """Walker, который рейзит REPLSecurityError при первой подозрительной ноде."""
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            raise REPLSecurityError(f"запрещённый узел AST: {type(node).__name__}")
        # dunder-атрибуты — основной побег из sandbox (через .__class__.__bases__).
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise REPLSecurityError(f"запрещён доступ к dunder-атрибуту: {node.attr}")
        if isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES:
                raise REPLSecurityError(f"запрещённое имя: {node.id}")
            if node.id.startswith("__") and node.id.endswith("__"):
                raise REPLSecurityError(f"запрещён dunder-name: {node.id}")


class _ExecResult:
    """Результат одного REPL-запуска — value/stdout/error."""

    __slots__ = ("ok", "value", "stdout", "error_type", "error_message")

    def __init__(
        self,
        ok: bool,
        value: Any = None,
        stdout: str = "",
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.ok = ok
        self.value = value
        self.stdout = stdout
        self.error_type = error_type
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        # repr value — чтобы не светить объект с бинарными/служебными полями.
        try:
            value_repr = None if self.value is None else repr(self.value)
        except Exception as exc:  # noqa: BLE001 — repr может падать
            value_repr = f"<repr failed: {type(exc).__name__}>"
        return {
            "ok": self.ok,
            "value_repr": value_repr,
            "stdout": self.stdout,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


class REPLSession:
    """Owner-only sandboxed REPL с per-owner persistent namespace."""

    def __init__(
        self,
        audit_log_path: Path | None = None,
        default_timeout_s: float = DEFAULT_EXEC_TIMEOUT_S,
        now_fn: Callable[[], _datetime.datetime] | None = None,
    ) -> None:
        # Per-owner namespaces. Создаются на start(), удаляются на stop().
        self._sessions: dict[int, dict[str, Any]] = {}
        # Метаданные сессий — started_at, last_exec_at, exec_count.
        self._meta: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._audit_log_path = audit_log_path or _DEFAULT_AUDIT_LOG
        self._default_timeout_s = default_timeout_s
        self._now_fn = now_fn or (lambda: _datetime.datetime.now(_datetime.timezone.utc))
        # Один executor на все запуски; small thread-pool.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="krab-repl"
        )

    # -------------------- public API --------------------

    def configure_default_paths(self, audit_log_path: Path) -> None:
        """Bootstrap re-init (вызвается из userbot.start() после _runtime_state_dir)."""
        with self._lock:
            self._audit_log_path = audit_log_path

    def is_started(self, owner_id: int) -> bool:
        """True, если сессия для этого owner уже запущена."""
        with self._lock:
            return owner_id in self._sessions

    def start(self, owner_id: int) -> bool:
        """
        Запускает REPL-сессию для owner. Возвращает True если создана новая,
        False если уже существовала (idempotent).
        """
        with self._lock:
            if owner_id in self._sessions:
                self._audit(owner_id, "start_noop", code="", result_kind="noop")
                return False
            self._sessions[owner_id] = _build_default_namespace()
            self._meta[owner_id] = {
                "started_at": self._now_fn().isoformat(),
                "exec_count": 0,
                "last_exec_at": None,
            }
        self._audit(owner_id, "start", code="", result_kind="ok")
        logger.info("repl_session_started", extra={"owner_id": owner_id})
        return True

    def stop(self, owner_id: int) -> bool:
        """
        Останавливает сессию, освобождает namespace. Возвращает True если была.
        """
        with self._lock:
            existed = owner_id in self._sessions
            self._sessions.pop(owner_id, None)
            self._meta.pop(owner_id, None)
        self._audit(
            owner_id,
            "stop" if existed else "stop_noop",
            code="",
            result_kind="ok" if existed else "noop",
        )
        if existed:
            logger.info("repl_session_stopped", extra={"owner_id": owner_id})
        return existed

    def exec_code(
        self,
        code: str,
        owner_id: int,
        timeout_s: float | None = None,
    ) -> _ExecResult:
        """
        Запускает код в сессии owner. Если кода — выражение, возвращает value.
        Если statement — value=None, но stdout сохраняется.

        Raises:
            REPLNotStartedError: если сессия не была start()'нута.
            REPLSecurityError: если AST-валидация отклонила код.
            REPLTimeoutError: если запуск не уложился в timeout.
        """
        with self._lock:
            if owner_id not in self._sessions:
                raise REPLNotStartedError("REPL не запущен — сначала !repl start")
            namespace = self._sessions[owner_id]

        # AST-парсинг + валидация. exec-mode чтобы поддержать assignments.
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            self._audit(
                owner_id,
                "exec",
                code=code,
                result_kind="syntax_error",
                error_type="SyntaxError",
            )
            return _ExecResult(
                ok=False,
                error_type="SyntaxError",
                error_message=str(exc),
            )

        try:
            _validate_ast(tree)
        except REPLSecurityError:
            self._audit(
                owner_id,
                "exec",
                code=code,
                result_kind="security_block",
                error_type="REPLSecurityError",
            )
            raise

        timeout = timeout_s if timeout_s is not None else self._default_timeout_s

        # Если последняя нода — Expression, разделяем: всё кроме последней —
        # exec-режим, последнее — eval-режим ради return value.
        last_value_holder: list[Any] = [None]

        def _runner() -> None:
            body = list(tree.body)
            tail_expr: ast.Expr | None = None
            if body and isinstance(body[-1], ast.Expr):
                tail_expr = body[-1]
                body = body[:-1]

            # statements — exec-режим
            if body:
                module = ast.Module(body=body, type_ignores=[])
                compiled = compile(module, "<repl>", "exec")
                exec(compiled, namespace)  # noqa: S102

            # tail-выражение — eval-режим
            if tail_expr is not None:
                expr_module = ast.Expression(body=tail_expr.value)
                compiled_expr = compile(expr_module, "<repl>", "eval")
                last_value_holder[0] = eval(  # noqa: S307
                    compiled_expr, namespace
                )

        # Перехватываем stdout. Делаем per-exec StringIO, не замещая sys.stdout
        # глобально (это убило бы userbot-логи если что-то напечатает в долгом
        # запуске параллельно).
        stdout_buf = io.StringIO()

        def _wrapper() -> None:
            with contextlib.redirect_stdout(stdout_buf):
                _runner()

        future = self._executor.submit(_wrapper)
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Thread продолжит выполнение в фоне — мы его остановить не можем,
            # но новый запуск не блокируется (max_workers=2 + executor разруливает).
            self._audit(
                owner_id,
                "exec",
                code=code,
                result_kind="timeout",
                error_type="REPLTimeoutError",
            )
            raise REPLTimeoutError(f"запуск не уложился в {timeout:.1f}s") from None
        except Exception as exc:  # noqa: BLE001 — пользовательский код может бросить что угодно
            error_type = type(exc).__name__
            self._audit(
                owner_id,
                "exec",
                code=code,
                result_kind="user_error",
                error_type=error_type,
            )
            self._touch_meta(owner_id)
            return _ExecResult(
                ok=False,
                stdout=stdout_buf.getvalue(),
                error_type=error_type,
                error_message=str(exc),
            )

        self._audit(owner_id, "exec", code=code, result_kind="ok")
        self._touch_meta(owner_id)
        return _ExecResult(
            ok=True,
            value=last_value_holder[0],
            stdout=stdout_buf.getvalue(),
        )

    def get_meta(self, owner_id: int) -> dict[str, Any] | None:
        """Возвращает копию метаданных сессии или None если нет."""
        with self._lock:
            meta = self._meta.get(owner_id)
            return dict(meta) if meta is not None else None

    def list_owners(self) -> list[int]:
        """Список активных owner_id (копия)."""
        with self._lock:
            return list(self._sessions.keys())

    def shutdown(self) -> None:
        """Закрывает executor, очищает все namespace. Вызывается на teardown."""
        with self._lock:
            self._sessions.clear()
            self._meta.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -------------------- internals --------------------

    def _touch_meta(self, owner_id: int) -> None:
        with self._lock:
            meta = self._meta.get(owner_id)
            if meta is None:
                return
            meta["exec_count"] = int(meta.get("exec_count", 0)) + 1
            meta["last_exec_at"] = self._now_fn().isoformat()

    def _audit(
        self,
        owner_id: int,
        action: str,
        code: str,
        result_kind: str,
        error_type: str | None = None,
    ) -> None:
        """Append одной JSON-строки в audit log. Без падений при I/O сбое."""
        preview = code[:_AUDIT_CODE_PREVIEW_LIMIT]
        record = {
            "ts": self._now_fn().isoformat(),
            "owner_id": owner_id,
            "action": action,
            "code_preview": preview,
            "code_len": len(code),
            "result_kind": result_kind,
        }
        if error_type is not None:
            record["error_type"] = error_type
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(
                "repl_audit_write_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "path": str(self._audit_log_path),
                },
            )


# Module-level singleton — паттерн как у chat_ban_cache / memo_service.
repl_session = REPLSession()

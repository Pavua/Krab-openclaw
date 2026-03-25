# -*- coding: utf-8 -*-
"""
Асинхронный запуск внешних CLI-инструментов (codex, gemini, claude, cursor).

Дизайн:
- asyncio.create_subprocess_exec — неблокирующий запуск, без shell (безопасно)
- Жёсткий timeout с SIGTERM → SIGKILL
- Захват stdout+stderr в единый вывод
- Структурированный результат с кодом выхода
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import NamedTuple

from ..core.logger import get_logger

logger = get_logger(__name__)

# Максимальный размер вывода (предотвращает OOM при огромных ответах)
_MAX_OUTPUT_BYTES = 256_000
_KILL_GRACE_SEC = 3.0


class CliResult(NamedTuple):
    exit_code: int
    output: str           # stdout + stderr объединены
    timed_out: bool
    tool: str
    prompt_preview: str   # первые 80 символов промпта — для логов


# Флаги "тихого" (non-interactive) запуска для каждого инструмента.
# asyncio.create_subprocess_exec передаёт аргументы напрямую без shell —
# командная инъекция через prompt невозможна.
_TOOL_FLAGS: dict[str, list[str]] = {
    "codex": ["-q"],         # quiet: без интерактивного UI
    "gemini": ["-p"],        # -p prompt: non-interactive режим
    "claude": ["-p"],        # claude -p: non-interactive с одним запросом
    "opencode": ["--print"], # opencode --print: non-interactive вывод
    "cursor": ["--repl"],    # cursor repl-режим
}


async def run_cli(
    tool: str,
    prompt: str,
    *,
    cwd: str | Path | None = None,
    timeout: float = 120.0,
    extra_args: list[str] | None = None,
) -> CliResult:
    """
    Запускает CLI-инструмент с prompt и возвращает его вывод.

    tool: "codex" | "gemini" | "claude" | "cursor"
    prompt: текстовый запрос (передаётся как аргумент, не через shell)
    cwd: рабочая директория (None = текущая)
    timeout: максимальное время выполнения в секундах
    extra_args: дополнительные флаги, вставляются перед prompt
    """
    prompt_preview = prompt[:80] + ("..." if len(prompt) > 80 else "")
    logger.info("cli_runner_start", tool=tool, prompt_preview=prompt_preview, timeout=timeout)

    bin_path = shutil.which(tool)
    if not bin_path:
        logger.error("cli_runner_tool_not_found", tool=tool)
        return CliResult(
            exit_code=127,
            output=f"❌ Инструмент `{tool}` не найден в PATH.",
            timed_out=False,
            tool=tool,
            prompt_preview=prompt_preview,
        )

    flags = list(extra_args or []) + _TOOL_FLAGS.get(tool, [])
    cmd = [bin_path, *flags, prompt]
    cwd_path = str(Path(cwd).resolve()) if cwd else None

    proc: asyncio.subprocess.Process | None = None
    timed_out = False
    stdout_bytes = b""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd_path,
            limit=_MAX_OUTPUT_BYTES,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning("cli_runner_timeout", tool=tool, timeout=timeout)
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            await asyncio.sleep(_KILL_GRACE_SEC)
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

        exit_code = proc.returncode if proc.returncode is not None else -1

    except (OSError, PermissionError) as exc:
        logger.error("cli_runner_exec_error", tool=tool, error=str(exc))
        return CliResult(
            exit_code=1,
            output=f"❌ Ошибка запуска `{tool}`: {exc}",
            timed_out=False,
            tool=tool,
            prompt_preview=prompt_preview,
        )

    output = stdout_bytes.decode("utf-8", errors="replace").strip()
    if timed_out:
        suffix = f"\n\n⚠️ Таймаут {int(timeout)}с — вывод может быть неполным."
        output = (output[:3800] if output else "(нет вывода)") + suffix

    logger.info(
        "cli_runner_done",
        tool=tool,
        exit_code=exit_code,
        output_len=len(output),
        timed_out=timed_out,
    )
    return CliResult(
        exit_code=exit_code,
        output=output,
        timed_out=timed_out,
        tool=tool,
        prompt_preview=prompt_preview,
    )

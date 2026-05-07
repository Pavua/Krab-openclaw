"""Wave 22-A: CLI subprocess bypass для codex-cli и google-gemini-cli providers.

OpenClaw 2026.5.x broke WebSocket → openresponses HTTP transport. Прямые
CLI вызовы (codex/gemini) работают, но Krab по умолчанию идёт через broken
OpenClaw layer. Этот модуль вызывает CLI binaries как subprocess
(аналогично Wave 18-B для google/*).

Поддерживаемые префиксы:
- 'codex-cli/'         → spawn 'codex -p ...' (subscription via ChatGPT account)
- 'google-gemini-cli/' → spawn 'gemini -p ...' (OAuth, free tier)

Activated only когда модель starts с одним из префиксов AND env
KRAB_CLI_SUBPROCESS_BYPASS_ENABLED=1 (default ON).

Симметрично OpenClawClient._openclaw_completion_once() — возвращает str.

Wave 24-A: Multi-account codex rotation через CODEX_HOME isolation.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from typing import Any

from ..core.logger import get_logger
from ._bypass_perf import record_bypass_call
from ._bypass_sentry import add_bypass_breadcrumb

logger = get_logger(__name__)


# Маппинг префиксов провайдеров на имена CLI-бинарей
CLI_BINARIES: dict[str, str] = {
    "codex-cli/": "codex",
    "google-gemini-cli/": "gemini",
}


def is_cli_subprocess_enabled() -> bool:
    """Включён ли CLI subprocess bypass. Default ON."""
    return str(os.environ.get("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_cli_model(model: str) -> tuple[bool, str | None]:
    """Проверяет, является ли модель CLI-моделью.

    Returns:
        (is_cli_match, binary_name). binary_name=None если не cli model.
    """
    if not model:
        return False, None
    for prefix, binary in CLI_BINARIES.items():
        if model.startswith(prefix):
            return True, binary
    return False, None


def _strip_provider_prefix(model: str) -> str:
    """Убирает провайдер-префикс: 'codex-cli/gpt-5.5' -> 'gpt-5.5'."""
    return model.split("/", 1)[1] if "/" in model else model


def _resolve_binary(binary_name: str) -> str | None:
    """PATH lookup для CLI binary."""
    return shutil.which(binary_name)


def _build_messages_text(messages: list[dict[str, Any]]) -> str:
    """Конвертирует OpenAI-style messages → плоский текст для CLI -p флага.

    Формат:
        [Контекст]: ...
        [Пользователь]: ...
        [Ассистент]: ...
        [Пользователь]: <last>
    """
    parts = []
    for msg in messages:
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content") or ""
        # Поддержка мультимодальных messages (list of content parts)
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and "text" in p]
            content = " ".join(text_parts)
        content_str = str(content).strip()
        if not content_str:
            continue
        if role == "system":
            parts.append(f"[Контекст]: {content_str}")
        elif role == "user":
            parts.append(f"[Пользователь]: {content_str}")
        elif role == "assistant":
            parts.append(f"[Ассистент]: {content_str}")
    return "\n\n".join(parts)


def _build_cmd(
    binary_path: str,
    binary_name: str,
    model_id: str,
    prompt_text: str,
) -> list[str]:
    """Строит команду для spawn subprocess с учётом специфики каждой binary.

    codex: использует subcommand `exec` + positional prompt (`-p` зарезервирован
    под `--profile` в codex CLI, поэтому prompt идёт ПОСЛЕДНИМ аргументом).
    gemini: `-p/--prompt` флаг для non-interactive headless mode.
    """
    if binary_name == "codex":
        # Wave 25-D-fix-2: codex требует `exec` + positional prompt
        cmd = [binary_path, "exec"]
        if model_id and model_id not in ("default", ""):
            cmd.extend(["--model", model_id])
        cmd.append(prompt_text)
        return cmd
    # gemini (default): -p/--prompt is the headless flag
    if model_id and model_id not in ("default", ""):
        return [binary_path, "--model", model_id, "-p", prompt_text]
    return [binary_path, "-p", prompt_text]


async def complete_via_cli(
    *,
    model: str,
    messages: list[dict[str, Any]],
    timeout_sec: float = 300.0,
    max_output_tokens: int | None = None,  # noqa: ARG001
) -> str:
    """Спавнит CLI binary и возвращает text response.

    Args:
        model: e.g. 'codex-cli/gpt-5.5' или 'google-gemini-cli/gemini-3.1-pro-preview'
        messages: OpenAI-style messages list
        timeout_sec: kill subprocess после этого таймаута (default 300s)
        max_output_tokens: не используется (CLI binaries не имеют такого флага)

    Returns:
        text response (str). Пустая строка если CLI вернул empty.

    Raises:
        RuntimeError: если binary не найден или subprocess fails
    """
    is_cli, binary_name = is_cli_model(model)
    if not is_cli or binary_name is None:
        raise RuntimeError(f"Not a CLI model: {model}")

    binary_path = _resolve_binary(binary_name)
    if not binary_path:
        raise RuntimeError(
            f"CLI binary '{binary_name}' не найден в PATH. Установка: brew install {binary_name}"
        )

    model_id = _strip_provider_prefix(model)
    prompt_text = _build_messages_text(messages)

    if not prompt_text.strip():
        logger.warning("cli_subprocess_no_messages", model=model_id, binary=binary_name)
        return ""

    # Wave 24-A: Multi-account rotation для codex — выбираем CODEX_HOME из пула
    env_overrides: dict[str, str] = {}
    _rotator_account: str | None = None
    if binary_name == "codex":
        try:
            from .codex_account_rotator import get_account_name_from_home, get_next_codex_home

            _codex_home = get_next_codex_home()
            if _codex_home:
                env_overrides["CODEX_HOME"] = _codex_home
                _rotator_account = get_account_name_from_home(_codex_home)
                logger.info(
                    "codex_multi_account_selected",
                    home=_codex_home,
                    account=_rotator_account,
                )
        except Exception as _rot_exc:  # noqa: BLE001
            logger.debug("codex_rotator_skipped", error=str(_rot_exc))

    logger.info(
        "cli_subprocess_complete_start",
        model=model_id,
        binary=binary_name,
        prompt_len=len(prompt_text),
    )
    # Breadcrumb: старт CLI bypass — для post-mortem trace (Wave 30-B)
    add_bypass_breadcrumb(
        bypass_kind="cli",
        event="engaged",
        model=model_id,
        extra={"binary": binary_name, "prompt_len": len(prompt_text)},
    )

    cmd = _build_cmd(binary_path, binary_name, model_id, prompt_text)

    # Строим env для subprocess: текущий env + overrides
    _env: dict[str, str] | None = None
    if env_overrides:
        _env = {**os.environ, **env_overrides}

    # Wave 31-A: замер latency bypass call
    _perf_start = time.time()
    _perf_success = False
    _perf_response_len = 0
    _perf_error_type: str | None = None
    _perf_error_message: str | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env,
        )
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            # Принудительно завершаем процесс при таймауте
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            # Breadcrumb: таймаут CLI subprocess (Wave 30-B)
            add_bypass_breadcrumb(
                bypass_kind="cli",
                event="timeout",
                model=model_id,
                extra={"binary": binary_name, "timeout_sec": timeout_sec},
                level="warning",
            )
            raise RuntimeError(f"CLI subprocess timeout после {timeout_sec}s: {binary_name}")

        stderr_text = stderr_data.decode("utf-8", errors="ignore")[:500]

        if proc.returncode != 0:
            logger.warning(
                "cli_subprocess_nonzero_exit",
                model=model_id,
                binary=binary_name,
                returncode=proc.returncode,
                stderr=stderr_text,
            )
            # Breadcrumb: non-zero exit — returncode + stderr preview для диагностики (Wave 30-B)
            add_bypass_breadcrumb(
                bypass_kind="cli",
                event="failure",
                model=model_id,
                extra={
                    "binary": binary_name,
                    "returncode": proc.returncode,
                    "stderr_preview": stderr_text[:200],
                },
                level="warning",
            )
            # codex/gemini могут вернуть текст в stdout даже при non-zero exit (warnings)

        text = stdout_data.decode("utf-8", errors="ignore").strip()

        # Wave 24-A: фиксируем результат вызова в rotator state
        if _rotator_account and binary_name == "codex":
            try:
                from .codex_account_rotator import record_call

                # Quota error по stderr и returncode
                _is_quota_err = proc.returncode != 0 and any(
                    k in stderr_text.lower() for k in ("quota", "rate limit", "429", "exceeded")
                )
                record_call(
                    _rotator_account,
                    success=not _is_quota_err,
                    error=stderr_text if _is_quota_err else None,
                )
            except Exception as _rec_exc:  # noqa: BLE001
                logger.debug("codex_rotator_record_failed", error=str(_rec_exc))

        logger.info(
            "cli_subprocess_complete_done",
            model=model_id,
            binary=binary_name,
            response_len=len(text),
            returncode=proc.returncode,
        )
        # Breadcrumb: успешный CLI bypass — response_len для post-mortem (Wave 30-B)
        add_bypass_breadcrumb(
            bypass_kind="cli",
            event="success",
            model=model_id,
            extra={"binary": binary_name, "response_len": len(text)},
        )
        _perf_success = True
        _perf_response_len = len(text)
        return text

    except RuntimeError as exc:
        # Перебрасываем RuntimeError (timeout, binary not found) без wrap
        _perf_error_type = type(exc).__name__
        _perf_error_message = str(exc)[:300]
        raise
    except Exception as exc:
        logger.warning(
            "cli_subprocess_failed",
            model=model_id,
            binary=binary_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _perf_error_type = type(exc).__name__
        _perf_error_message = str(exc)[:300]
        raise
    finally:
        # Wave 31-A: записываем latency в JSONL (graceful — не крашит bypass)
        record_bypass_call(
            kind="cli",
            model=model,
            duration_sec=time.time() - _perf_start,
            success=_perf_success,
            response_len=_perf_response_len,
            error_type=_perf_error_type,
            error_message=_perf_error_message,
        )

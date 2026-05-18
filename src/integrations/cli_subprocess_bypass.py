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
from ..userbot.llm_retry import LLMRetryableError
from ._bypass_perf import record_bypass_call
from ._bypass_sentry import add_bypass_breadcrumb
from ._observability_log import record_agent_run

logger = get_logger(__name__)


# S62 W4: idle observability — mirror S55 D bypass / S56 C vision / S61 W2
# translator pattern. Module-level rate-limit state (complete_via_cli — module
# function, не метод класса), keyed by reason string, value = last-log
# timestamp (float seconds). Помогает owner'у видеть когда codex armed но
# spinning idle (weekly quota exhausted, disabled via env, binary missing).
_codex_idle_last_log_ts: dict[str, float] = {}


def _log_codex_idle_skip(reason: str, *, model: str = "") -> None:
    """Rate-limited idle log: once per ``KRAB_CODEX_IDLE_LOG_INTERVAL_SEC``
    per reason (default 60s).

    Reasons:
        - ``weekly_quota_exhausted`` — Wave 62-G preempt (is_codex_disabled)
        - ``disabled_via_env``      — KRAB_CODEX_DISABLED_MODELS / bypass off
        - ``subprocess_unavailable`` — codex binary missing in PATH

    S63 W1: каждый skip также инкрементирует Prometheus counter
    ``krab_codex_idle_skip_total{reason}`` (best-effort, never raises).
    Counter не rate-limited — отдельный сигнал для observability.
    """
    # S63 W1: Prometheus counter inc — independent of rate-limit ниже.
    try:
        from src.core.metrics.idle_skip import inc_codex_idle_skip

        inc_codex_idle_skip(reason)
    except Exception:  # noqa: BLE001 — metrics best-effort
        pass

    now_ts = time.time()
    try:
        interval_sec = float(os.getenv("KRAB_CODEX_IDLE_LOG_INTERVAL_SEC", "60"))
    except (TypeError, ValueError):
        interval_sec = 60.0
    last = _codex_idle_last_log_ts.get(reason, 0.0)
    if now_ts - last < interval_sec:
        return
    _codex_idle_last_log_ts[reason] = now_ts
    logger.info(
        "codex_cli_idle_skip",
        reason=reason,
        model=model,
        interval_sec=interval_sec,
    )


def _get_idle_timeout_sec() -> float:
    """Wave 44-W: idle gap threshold перед kill subprocess'a."""
    raw = os.environ.get("KRAB_LLM_IDLE_TIMEOUT_SEC", "180")
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 180.0


def _get_codex_hard_cap_sec() -> float:
    """Wave 44-W: hard cap fallback. 0 = disabled."""
    raw = os.environ.get("KRAB_CODEX_AGENT_HARD_CAP_SEC", "7200")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 7200.0


async def _stream_with_stagnation(
    proc: Any,
    *,
    idle_timeout_sec: float,
    hard_cap_sec: float,
    quota_check: bool = False,
) -> tuple[int | None, bytes, bytes]:
    """Wave 44-W: stream stdout/stderr line-by-line tracking last_output time.

    Kill при idle gap > idle_timeout_sec → LLMRetryableError("stagnation_timeout").
    Kill при total elapsed > hard_cap_sec (если > 0) → LLMRetryableError("hard_cap_timeout").
    Любой byte stdout или stderr resets idle clock.

    quota_check: для codex — early bail if stderr matches quota patterns
        (Wave 44-V quota detection приоритет over stagnation).

    Tests: если ``proc.stdout.readline`` не возвращает awaitable (mock без AsyncMock),
    fallback на ``proc.communicate()`` с asyncio.wait_for под hard_cap или idle*4.
    """
    # Tests-friendly fallback: если readline не awaitable — используем communicate
    is_streaming = True
    try:
        _readline_test = proc.stdout.readline()
        if asyncio.iscoroutine(_readline_test):
            _readline_test.close()
        elif not hasattr(_readline_test, "__await__"):
            is_streaming = False
    except Exception:  # noqa: BLE001
        is_streaming = False

    if not is_streaming:
        timeout = hard_cap_sec if hard_cap_sec > 0 else max(idle_timeout_sec * 4, 60.0)
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            raise
        return proc.returncode, stdout_data, stderr_data

    start = time.time()
    last_output = start
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    quota_hit = False

    async def _read_stream(stream: Any, chunks: list[bytes], is_stderr: bool) -> None:
        nonlocal last_output, quota_hit
        while True:
            try:
                line = await stream.readline()
            except Exception:  # noqa: BLE001
                break
            if not line:
                break
            chunks.append(line)
            last_output = time.time()
            if quota_check and is_stderr:
                try:
                    from .codex_quota_state import is_quota_error

                    accumulated = b"".join(chunks).decode("utf-8", errors="ignore")
                    if is_quota_error(stderr=accumulated):
                        quota_hit = True
                        break
                except Exception:  # noqa: BLE001
                    pass

    out_task = asyncio.create_task(_read_stream(proc.stdout, stdout_chunks, False))
    err_task = asyncio.create_task(_read_stream(proc.stderr, stderr_chunks, True))

    try:
        while True:
            now = time.time()
            elapsed_total = now - start
            elapsed_idle = now - last_output

            if proc.returncode is not None or (out_task.done() and err_task.done()):
                break

            if quota_hit:
                break

            if elapsed_idle > idle_timeout_sec:
                logger.warning(
                    "codex_stagnation_killed",
                    idle_sec=round(elapsed_idle, 1),
                    total_sec=round(elapsed_total, 1),
                    idle_threshold=idle_timeout_sec,
                )
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                raise LLMRetryableError(
                    f"codex stagnation: no output for {elapsed_idle:.0f}s",
                    error_text=f"stagnation_timeout idle={elapsed_idle:.0f}s",
                )

            if hard_cap_sec > 0 and elapsed_total > hard_cap_sec:
                logger.warning(
                    "codex_hard_cap_killed",
                    total_sec=round(elapsed_total, 1),
                    hard_cap=hard_cap_sec,
                )
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                raise LLMRetryableError(
                    f"codex hard cap: total {elapsed_total:.0f}s > {hard_cap_sec}s",
                    error_text=f"hard_cap_timeout total={elapsed_total:.0f}s",
                )

            await asyncio.sleep(min(5.0, max(0.5, idle_timeout_sec / 10)))
    finally:
        for task in (out_task, err_task):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except Exception:  # noqa: BLE001
                    task.cancel()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass

    return proc.returncode, b"".join(stdout_chunks), b"".join(stderr_chunks)


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


async def _run_codex_subprocess_once(
    *,
    binary_path: str,
    model_id: str,
    prompt_text: str,
    timeout_sec: float,
    codex_home: str | None,
) -> tuple[int | None, str, str]:
    """Один прогон codex subprocess. Возвращает (returncode, stdout, stderr).

    Wave 44-V helper. Не записывает rotator state — caller сам решает.
    """
    cmd = _build_cmd(binary_path, "codex", model_id, prompt_text)
    _env: dict[str, str] | None = None
    if codex_home:
        _env = {**os.environ, "CODEX_HOME": codex_home}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_env,
    )
    # Wave 44-W: streaming reader с stagnation detection.
    # idle_timeout — primary trigger (быстрый kill зависшего codex);
    # hard_cap_sec — fallback (для случаев когда codex эмитит byte раз в N сек).
    # timeout_sec (legacy param) используется как hard_cap если он < codex_hard_cap,
    # либо если codex_hard_cap отключён (=0). По умолчанию 7200s = 2 часа.
    idle_timeout = _get_idle_timeout_sec()
    codex_cap = _get_codex_hard_cap_sec()
    if codex_cap == 0:
        # Hard cap отключён → используем legacy timeout_sec как safety net
        hard_cap = max(timeout_sec, 1.0)
    else:
        # Берём minimum чтобы не превышать caller's timeout_sec
        hard_cap = min(codex_cap, max(timeout_sec, 1.0))
    try:
        returncode, stdout_data, stderr_data = await _stream_with_stagnation(
            proc,
            idle_timeout_sec=idle_timeout,
            hard_cap_sec=hard_cap,
            quota_check=True,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"CLI subprocess timeout после {timeout_sec}s: codex") from None
    except LLMRetryableError:
        # Wave 44-W: stagnation/hard_cap уже kill'нул proc, передаём наверх
        raise

    stdout_text = stdout_data.decode("utf-8", errors="ignore")
    stderr_text = stderr_data.decode("utf-8", errors="ignore")
    return returncode, stdout_text, stderr_text


async def _complete_codex_with_account_rotation(
    *,
    binary_path: str,
    model_id: str,
    prompt_text: str,
    timeout_sec: float,
) -> str:
    """Wave 44-V: codex CLI с авто-сменой аккаунта при quota errors.

    Если все accounts исчерпали квоту, поднимает CodexQuotaExhaustedError
    чтобы caller fall back на следующую модель из chain.
    """
    from .codex_account_rotator import (
        get_account_name_from_home,
        get_next_codex_home,
        list_accounts,
        record_call,
    )
    from .codex_quota_state import (
        CodexQuotaExhaustedError,
        classify_quota,
        cooldown_for_kind,
        is_codex_disabled,
        is_quota_error,
    )

    _perf_start = time.time()
    _perf_success = False
    _perf_response_len = 0
    _perf_error_type: str | None = None
    _perf_error_message: str | None = None
    full_model = f"codex-cli/{model_id}"

    # Wave 62-G (2026-05-11): preempt — если codex marked weekly-disabled
    # через mark_codex_disabled (например через !quota команду или вручную),
    # не делаем subprocess attempt вообще. Сохраняем 2-3s per request
    # пока quota не recover (WEEKLY_COOLDOWN=7d).
    # До Wave 62-G: is_codex_disabled() нигде не читался → state file был dead-letter.
    if is_codex_disabled():
        logger.info(
            "codex_preempted_weekly_disabled",
            model=model_id,
            reason="is_codex_disabled() returned True — see codex_quota_state.json",
        )
        # S62 W4: idle observability — log skip reason (rate-limited)
        _log_codex_idle_skip("weekly_quota_exhausted", model=model_id)
        add_bypass_breadcrumb(
            bypass_kind="cli",
            event="preempted_weekly_disabled",
            model=model_id,
            extra={"binary": "codex"},
            level="info",
        )
        raise CodexQuotaExhaustedError(
            "Codex preempted (weekly quota state) — falling back to next model in chain",
            kind="weekly",
        )

    max_attempts = max(2, len([a for a in list_accounts() if a.get("logged_in")]) + 1)

    add_bypass_breadcrumb(
        bypass_kind="cli",
        event="engaged",
        model=model_id,
        extra={"binary": "codex", "prompt_len": len(prompt_text)},
    )

    try:
        for attempt_idx in range(max_attempts):
            codex_home = get_next_codex_home()
            if not codex_home:
                logger.warning(
                    "codex_all_accounts_exhausted",
                    attempt=attempt_idx + 1,
                    max_attempts=max_attempts,
                )
                add_bypass_breadcrumb(
                    bypass_kind="cli",
                    event="quota_exhausted",
                    model=model_id,
                    extra={"binary": "codex"},
                    level="warning",
                )
                _perf_error_type = "CodexQuotaExhaustedError"
                _perf_error_message = "all codex accounts exhausted"
                raise CodexQuotaExhaustedError(
                    "All codex accounts hit quota — falling back to next model in chain",
                    kind="weekly",
                )

            account_name = get_account_name_from_home(codex_home)
            logger.info(
                "codex_multi_account_selected",
                home=codex_home,
                account=account_name,
                attempt=attempt_idx + 1,
            )

            try:
                returncode, stdout_text, stderr_text = await _run_codex_subprocess_once(
                    binary_path=binary_path,
                    model_id=model_id,
                    prompt_text=prompt_text,
                    timeout_sec=timeout_sec,
                    codex_home=codex_home,
                )
            except RuntimeError as _exc:
                _perf_error_type = type(_exc).__name__
                _perf_error_message = str(_exc)[:300]
                raise
            except LLMRetryableError as _exc:
                # Wave 44-W: stagnation/hard_cap → caller увидит retryable.
                # НЕ помечаем account как exhausted (это codex hang, не quota).
                _perf_error_type = type(_exc).__name__
                _perf_error_message = str(_exc)[:300]
                raise

            stderr_preview = stderr_text[:500]
            text = stdout_text.strip()
            _is_quota = is_quota_error(stderr=stderr_text, stdout=stdout_text)

            if _is_quota:
                kind = classify_quota(stderr=stderr_text, stdout=stdout_text)
                cooldown = cooldown_for_kind(kind)
                logger.warning(
                    "codex_quota_detected",
                    provider=full_model,
                    account=account_name,
                    kind=kind,
                    cooldown_hours=int(cooldown.total_seconds() // 3600),
                    stderr_preview=stderr_preview[:200],
                )
                try:
                    record_call(
                        account_name,
                        success=False,
                        error=stderr_preview or "quota_exhausted",
                        cooldown=cooldown,
                    )
                except Exception as _rec_exc:  # noqa: BLE001
                    logger.debug("codex_rotator_record_failed", error=str(_rec_exc))
                continue

            if returncode != 0:
                logger.warning(
                    "cli_subprocess_nonzero_exit",
                    model=model_id,
                    binary="codex",
                    returncode=returncode,
                    stderr=stderr_preview,
                )
                add_bypass_breadcrumb(
                    bypass_kind="cli",
                    event="failure",
                    model=model_id,
                    extra={
                        "binary": "codex",
                        "returncode": returncode,
                        "stderr_preview": stderr_preview[:200],
                    },
                    level="warning",
                )
                try:
                    record_call(account_name, success=False, error=stderr_preview)
                except Exception as _rec_exc:  # noqa: BLE001
                    logger.debug("codex_rotator_record_failed", error=str(_rec_exc))

            try:
                record_call(account_name, success=True)
            except Exception as _rec_exc:  # noqa: BLE001
                logger.debug("codex_rotator_record_failed", error=str(_rec_exc))

            logger.info(
                "cli_subprocess_complete_done",
                model=model_id,
                binary="codex",
                response_len=len(text),
                returncode=returncode,
                account=account_name,
                attempt=attempt_idx + 1,
            )
            add_bypass_breadcrumb(
                bypass_kind="cli",
                event="success",
                model=model_id,
                extra={
                    "binary": "codex",
                    "response_len": len(text),
                    "account": account_name,
                },
            )
            _perf_success = True
            _perf_response_len = len(text)
            return text

        _perf_error_type = "CodexQuotaExhaustedError"
        _perf_error_message = "max_attempts exhausted"
        raise CodexQuotaExhaustedError(
            "Codex max_attempts exceeded — all retried accounts hit quota",
            kind="weekly",
        )
    except CodexQuotaExhaustedError as exc:
        _perf_error_type = type(exc).__name__
        _perf_error_message = str(exc)[:300]
        raise
    except Exception as exc:
        logger.warning(
            "cli_subprocess_failed",
            model=model_id,
            binary="codex",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _perf_error_type = type(exc).__name__
        _perf_error_message = str(exc)[:300]
        raise
    finally:
        _duration = time.time() - _perf_start
        record_bypass_call(
            kind="cli",
            model=full_model,
            duration_sec=_duration,
            success=_perf_success,
            response_len=_perf_response_len,
            error_type=_perf_error_type,
            error_message=_perf_error_message,
        )
        # S69 W6: per-model latency p50/p95/p99 gauges.
        try:
            from ..core.metrics.model_latency import record_latency_seconds

            record_latency_seconds(full_model, _duration)
        except Exception:  # noqa: BLE001
            pass
        # Wave 44-U observability: telemetry для Owner panel /observability dashboard
        try:
            record_agent_run(
                model=full_model,
                kind="krab-bypass",
                prompt_text=prompt_text,
                response_text="",  # response не сохраняем тут — только excerpt в not-codex
                started_at=_perf_start,
                completed_at=time.time(),
                duration_sec=_duration,
                status="ok"
                if _perf_success
                else (
                    "timeout"
                    if _perf_error_type == "RuntimeError"
                    and "timeout" in (_perf_error_message or "").lower()
                    else "error"
                ),
                exit_code=None,
                stderr_excerpt=_perf_error_message or "",
                extra={"binary": "codex", "response_len": _perf_response_len},
            )
        except Exception:  # noqa: BLE001
            pass


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
        RuntimeError: если binary не найден или subprocess fails.
        CodexQuotaExhaustedError: для codex — когда ВСЕ accounts исчерпали квоту
            (caller должен fall back на следующую модель из chain). Wave 44-V.
    """
    is_cli, binary_name = is_cli_model(model)
    if not is_cli or binary_name is None:
        raise RuntimeError(f"Not a CLI model: {model}")

    # S62 W4: idle observability — bypass off via env, но caller всё равно
    # дошёл до complete_via_cli. Логируем skip reason (rate-limited) для
    # codex, чтобы видеть spinning idle bypass armed-but-disabled.
    if binary_name == "codex" and not is_cli_subprocess_enabled():
        _log_codex_idle_skip("disabled_via_env", model=model)

    binary_path = _resolve_binary(binary_name)
    if not binary_path:
        # S62 W4: codex binary отсутствует в PATH → idle skip (subprocess_unavailable)
        if binary_name == "codex":
            _log_codex_idle_skip("subprocess_unavailable", model=model)
        raise RuntimeError(
            f"CLI binary '{binary_name}' не найден в PATH. Установка: brew install {binary_name}"
        )

    model_id = _strip_provider_prefix(model)
    prompt_text = _build_messages_text(messages)

    if not prompt_text.strip():
        logger.warning("cli_subprocess_no_messages", model=model_id, binary=binary_name)
        return ""

    # Wave 44-V: для codex — внутренний retry loop по аккаунтам при quota errors.
    # Каждый exhausted account помечается соответствующим cooldown'ом
    # (weekly=7d / transient=1h). Если accounts закончились → CodexQuotaExhaustedError.
    if binary_name == "codex":
        return await _complete_codex_with_account_rotation(
            binary_path=binary_path,
            model_id=model_id,
            prompt_text=prompt_text,
            timeout_sec=timeout_sec,
        )

    # Не-codex CLI (gemini) — single-shot вызов без rotation
    env_overrides: dict[str, str] = {}
    _rotator_account: str | None = None

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
        # Wave 44-W: streaming с stagnation detection и для gemini.
        # idle_timeout — primary, hard_cap = timeout_sec (legacy contract сохраняется).
        idle_timeout = _get_idle_timeout_sec()
        try:
            returncode, stdout_data, stderr_data = await _stream_with_stagnation(
                proc,
                idle_timeout_sec=idle_timeout,
                hard_cap_sec=max(timeout_sec, 1.0),
                quota_check=False,
            )
            # `returncode` matches proc.returncode after _stream_with_stagnation
        except asyncio.TimeoutError:
            add_bypass_breadcrumb(
                bypass_kind="cli",
                event="timeout",
                model=model_id,
                extra={"binary": binary_name, "timeout_sec": timeout_sec},
                level="warning",
            )
            raise RuntimeError(f"CLI subprocess timeout после {timeout_sec}s: {binary_name}")
        except LLMRetryableError as _exc:
            add_bypass_breadcrumb(
                bypass_kind="cli",
                event="stagnation",
                model=model_id,
                extra={"binary": binary_name, "reason": str(_exc)[:200]},
                level="warning",
            )
            raise

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

        # Wave 44-V: codex теперь обрабатывается в _complete_codex_with_account_rotation,
        # сюда падают только non-codex (gemini) — без rotator state записи.

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
        _duration = time.time() - _perf_start
        record_bypass_call(
            kind="cli",
            model=model,
            duration_sec=_duration,
            success=_perf_success,
            response_len=_perf_response_len,
            error_type=_perf_error_type,
            error_message=_perf_error_message,
        )
        # S69 W6: per-model latency p50/p95/p99 gauges (non-codex CLI).
        try:
            from ..core.metrics.model_latency import record_latency_seconds

            record_latency_seconds(model, _duration)
        except Exception:  # noqa: BLE001
            pass
        # Wave 44-U observability: telemetry для Owner panel /observability dashboard
        try:
            _status = (
                "ok"
                if _perf_success
                else (
                    "timeout"
                    if (
                        _perf_error_type == "RuntimeError"
                        and "timeout" in (_perf_error_message or "").lower()
                    )
                    else "error"
                )
            )
            record_agent_run(
                model=model,
                kind="krab-bypass",
                prompt_text=prompt_text,
                response_text="",
                started_at=_perf_start,
                completed_at=time.time(),
                duration_sec=_duration,
                status=_status,
                stderr_excerpt=_perf_error_message or "",
                extra={"binary": binary_name, "response_len": _perf_response_len},
            )
        except Exception:  # noqa: BLE001
            pass

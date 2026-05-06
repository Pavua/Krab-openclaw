# -*- coding: utf-8 -*-
"""
src/core/swarm.py
~~~~~~~~~~~~~~~~~
Роевой оркестратор и Multi-Agent Room для кооперативного решения задач.

Зачем нужен модуль:
- сохранить совместимость с R17-тестами и утраченной функциональностью после рефакторинга;
- дать стабильный контур «аналитик -> критик -> интегратор» для сложных запросов;
- изолировать логику роя от transport/runtime-слоя (router передается извне);
- поддерживать межкомандное делегирование через SwarmBus ([DELEGATE: team]).
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from typing import Any, Callable

from .logger import get_logger
from .swarm_channels import swarm_channels
from .swarm_memory import swarm_memory
from .swarm_team_prompts import get_team_system_prompt
from .swarm_tool_scope import format_tool_hint

# ---------------------------------------------------------------------------
# Wave 38-B: Engine dispatch helper
# Lazy import чтобы избежать циклических зависимостей при старте.
# При KRAB_AGENT_ENGINE_DISPATCH_ENABLED=0 (дефолт) — нулевое изменение поведения.
# ---------------------------------------------------------------------------


async def _dispatch_route_query(
    prompt: str,
    router: Any,
    *,
    team_name: str = "",
    chat_id: Any = None,
) -> str:
    """Wave 38-B: Route через AgentEngine если dispatch включён, иначе через router.

    Возвращает строку-ответ. Записывает run в agent_engine_runs после завершения.
    Backward compat: при dispatch OFF — прямо вызывает router.route_query().
    """
    import os

    dispatch_on = os.environ.get("KRAB_AGENT_ENGINE_DISPATCH_ENABLED", "0").strip() in {
        "1",
        "true",
        "yes",
    }

    if not dispatch_on:
        # Нулевое изменение поведения — прямой вызов router
        return await router.route_query(prompt, skip_swarm=True)

    # -- dispatch включён: resolve engine ----------------------------------
    t0 = time.monotonic()
    engine = None
    requested_kind = "openclaw"
    actual_kind = "openclaw"
    success = False
    response_text = ""

    try:
        from .agent_engine_resolver import get_engine_for_route  # noqa: PLC0415

        # openclaw_client берём из router для fallback
        _openclaw_client = getattr(router, "_openclaw_client", None) or getattr(
            router, "client", None
        )
        engine, requested_kind, actual_kind = await get_engine_for_route(
            chat_id=chat_id,
            room=team_name or None,
            openclaw_client=_openclaw_client,
        )
        logger.info(
            "swarm_engine_dispatched",
            team=team_name,
            requested=requested_kind,
            actual=actual_kind,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("swarm_engine_dispatch_resolve_failed", error=str(exc))
        # Fallback на router при ошибке resolver
        return await router.route_query(prompt, skip_swarm=True)

    # -- вызываем engine: если это OpenClawAdapter — stream() делегирует
    # в openclaw_client.send_message_stream(); Hermes — в subprocess ACP.
    # Но route_query() может быть удобнее для OpenClaw (возвращает полный текст).
    # Детектируем: если actual_kind == "openclaw" — идём через router для
    # сохранения всей OpenClaw логики (tools, memory, etc.). Только при
    # actual_kind == "hermes" используем engine.stream().
    try:
        if actual_kind == "hermes" and engine is not None:
            # Hermes engine — stream() через ACP subprocess
            chunks: list[str] = []
            async for chunk in engine.stream(
                prompt,
                ctx={"chat_id": str(chat_id) if chat_id else "_swarm_", "room": team_name},
            ):
                if chunk.text and chunk.chunk_type != "finish":
                    chunks.append(chunk.text)
            response_text = "".join(chunks)
            success = bool(response_text)
        else:
            # openclaw actual — через router (full feature-set)
            response_text = await router.route_query(prompt, skip_swarm=True)
            success = bool(response_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("swarm_engine_dispatch_call_failed", engine=actual_kind, error=str(exc))
        # Fallback на router при ошибке engine
        try:
            response_text = await router.route_query(prompt, skip_swarm=True)
            success = bool(response_text)
            actual_kind = "openclaw"  # зафиксируем что использовали fallback
        except Exception as exc2:  # noqa: BLE001
            response_text = f"[Ошибка engine и fallback: {exc2}]"

    # -- записываем run в archive.db (fail-safe) ----------------------------
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    try:
        from .agent_engine_runs import record_engine_run  # noqa: PLC0415

        fallback_engine = "openclaw" if actual_kind != requested_kind else None
        record_engine_run(
            engine=actual_kind,
            chat_id=str(chat_id) if chat_id else None,
            room=team_name or None,
            latency_ms_total=elapsed_ms,
            success=success,
            fallback_engine=fallback_engine,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("swarm_engine_run_record_failed", error=str(exc))

    return response_text


# Реестр прогресса — подключается лениво чтобы избежать циклических импортов
def _get_swarm_progress():  # noqa: ANN202
    """Lazy getter для SwarmProgressRegistry singleton."""
    from .swarm_bus import swarm_progress  # noqa: PLC0415

    return swarm_progress


# Wave 16-D: lazy-импорт SkillCurator для A/B wire-up.
# Если импорт не удался — логируем предупреждение и продолжаем без A/B.
try:
    from .skill_curator import skill_curator as _skill_curator
except Exception as _sc_import_exc:  # noqa: BLE001
    _skill_curator = None  # type: ignore[assignment]
    logger_import = __import__("logging").getLogger(__name__)
    logger_import.warning("skill_curator_import_failed: %s", _sc_import_exc)

logger = get_logger(__name__)

# Паттерн для детектирования директив делегирования в ответе роли.
# Форматы: [DELEGATE: coders], [DELEGATE:traders], [DELEGATE: аналитика]
_DELEGATE_PATTERN = re.compile(
    r"\[DELEGATE:\s*([a-zA-Zа-яА-Я_\-]+)\]",
    re.IGNORECASE,
)


class SwarmTask:
    """Описание отдельной задачи для параллельного выполнения в рое."""

    def __init__(self, name: str, func: Callable, *args: Any, **kwargs: Any) -> None:
        self.name = name
        self.func = func
        self.args = args
        self.kwargs = kwargs


class SwarmOrchestrator:
    """
    Базовый оркестратор параллельных задач.

    Оставлен для обратной совместимости и будущего расширения.
    """

    def __init__(self, tool_handler: Any, router: Any | None = None) -> None:
        self.tools = tool_handler
        self.router = router
        logger.info("swarm_orchestrator_initialized")

    async def execute_parallel(self, tasks: list[SwarmTask]) -> dict[str, Any]:
        """Запускает список задач параллельно и возвращает агрегированные результаты."""
        logger.info("swarm_parallel_started", tasks=len(tasks))

        async def _run_safe(task: SwarmTask) -> tuple[str, Any]:
            try:
                result = task.func(*task.args, **task.kwargs)
                return task.name, await self._resolve_maybe_awaitable(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("swarm_task_failed", task=task.name, error=str(exc))
                return task.name, f"Error: {exc}"

        results = await asyncio.gather(*[_run_safe(task) for task in tasks])
        return dict(results)

    @staticmethod
    async def _resolve_maybe_awaitable(value: Any) -> Any:
        """Дожидается awaitable-значений, но пропускает обычные типы без накладных расходов."""
        if inspect.isawaitable(value):
            return await value
        return value


# ---------------------------------------------------------------------------
# R17: Multi-Agent Room MVP  |  R18: delegation support via SwarmBus
# ---------------------------------------------------------------------------

DEFAULT_AGENT_ROLES = [
    {
        "name": "analyst",
        "emoji": "🔬",
        "title": "Аналитик",
        "system_hint": (
            "Ты — аналитик. Разбери тему детально: выдели ключевые факты, "
            "тренды и цифры. Без лишних слов, только суть."
        ),
    },
    {
        "name": "critic",
        "emoji": "🎯",
        "title": "Критик",
        "system_hint": (
            "Ты — критик. Учитывая анализ выше, найди слабые стороны, "
            "риски и упущенные нюансы. Будь конкретен."
        ),
    },
    {
        "name": "integrator",
        "emoji": "🧠",
        "title": "Интегратор",
        "system_hint": (
            "Ты — интегратор. Учитывая анализ и критику выше, сформулируй "
            "финальный вывод с четкими рекомендациями."
        ),
    },
]


class AgentRoom:
    """
    Последовательный оркестратор «комнаты агентов».

    Контракт с роутером:
    - должен поддерживать `await route_query(prompt, skip_swarm=True)`.
    - сам роутер отвечает за transport, модель и retry policy.

    R18: Если ответ роли содержит [DELEGATE: <team>], AgentRoom диспатчит
    подзадачу в указанную команду через SwarmBus и инжектирует результат
    в контекст следующей роли.
    """

    def __init__(
        self, roles: list[dict[str, str]] | None = None, *, role_context_clip: int = 3000
    ) -> None:
        self.roles = roles or DEFAULT_AGENT_ROLES
        self.role_context_clip = max(200, int(role_context_clip))
        logger.info("agent_room_initialized", roles=[r.get("name", "agent") for r in self.roles])

    async def run_round(
        self,
        topic: str,
        router: Any,
        *,
        _bus: Any = None,
        _depth: int = 0,
        _router_factory: Any = None,
        _team_name: str = "",
        _track_progress: bool = True,
    ) -> str:
        """
        Запускает полный роевой раунд по теме `topic`.

        Если роль возвращает [DELEGATE: <team>] и предоставлен _bus (SwarmBus),
        задача диспатчится в указанную команду. Результат инжектируется в
        накопленный контекст для следующих ролей.
        """
        t0 = time.monotonic()
        started_at_iso = (
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(timespec="seconds")
        )
        accumulated_context = ""
        round_results: list[dict[str, str]] = []
        delegation_results: list[str] = []

        # -- Wave 16-D: A/B variant selection для team-specific промпта --------
        # Если активен A/B-тест для данной команды — выбираем вариант детерминированно.
        # Нет теста / импорт не удался → variant=None (baseline behaviour unchanged).
        _ab_id: str | None = None
        _ab_variant: str | None = None
        _ab_team_prompt: str | None = None
        if _team_name and _skill_curator is not None:
            try:
                # get_active_ab_test возвращает dict с данными теста (или None)
                _ab_data_active = _skill_curator.get_active_ab_test(_team_name)
                if _ab_data_active:
                    _ab_id = _ab_data_active.get("ab_id")
                    # round_id строится из topic + timestamp для детерминированности
                    _round_id = f"{_team_name}:{topic[:60]}:{started_at_iso}"
                    _ab_variant = _skill_curator.select_variant(_ab_id, _round_id)
                    if _ab_variant == "candidate":
                        _ab_team_prompt = _ab_data_active.get(
                            "candidate_prompt"
                        ) or get_team_system_prompt(_team_name)
                    else:
                        _ab_team_prompt = get_team_system_prompt(_team_name)
                    logger.info(
                        "agent_room_ab_variant_selected",
                        team=_team_name,
                        ab_id=_ab_id,
                        variant=_ab_variant,
                    )
            except Exception as _ab_exc:  # noqa: BLE001
                # A/B ошибка не должна ломать раунд
                logger.warning("agent_room_ab_select_failed", team=_team_name, error=str(_ab_exc))
                _ab_id = None
                _ab_variant = None
                _ab_team_prompt = None
        # -----------------------------------------------------------------------

        # Inject контекста из памяти предыдущих прогонов
        memory_context = ""
        if _team_name:
            memory_context = swarm_memory.get_context_for_injection(_team_name)
            if memory_context:
                accumulated_context = memory_context + "\n\n"
                logger.info(
                    "agent_room_memory_injected", team=_team_name, context_len=len(memory_context)
                )

        logger.info("agent_room_round_started", topic=topic, roles=len(self.roles), depth=_depth)

        # Регистрируем single-round сессию только для top-level вызовов.
        # _track_progress=False передаётся из run_loop (там уже есть loop-level sid).
        # _depth>0 — делегированные раунды не регистрируем.
        _single_round_sid: str | None = None
        if _track_progress and _depth == 0:
            _progress = _get_swarm_progress()
            _single_round_sid = _progress.start_session(
                team=_team_name or "default",
                topic=str(topic or "")[:120],
                rounds_total=1,
            )

        # Live broadcast: анонс начала раунда в swarm-группу
        # Для delegated rounds (depth>0) используем target_team для broadcast в его топик
        broadcast_team = _team_name
        if _team_name:
            swarm_channels.mark_round_active(_team_name)
            await swarm_channels.broadcast_round_start(team=_team_name, topic=topic)

        for role_idx, role in enumerate(self.roles):
            name = str(role.get("name", "agent"))
            emoji = str(role.get("emoji", "🤖"))
            title = str(role.get("title", name))
            hint = str(role.get("system_hint", "")).strip()

            # Проверяем intervention от владельца перед каждой ролью
            if _team_name:
                intervention = swarm_channels.get_pending_intervention(_team_name)
                if intervention:
                    accumulated_context += intervention
                    logger.info("agent_room_intervention_applied", team=_team_name, role=name)

            # Tool awareness: per-team tool scoping через swarm_tool_scope
            _tor_enabled = False
            try:
                from ..config import config as _cfg  # noqa: PLC0415

                _tor_enabled = getattr(_cfg, "TOR_ENABLED", False)
            except Exception:  # noqa: BLE001
                pass
            tool_hint = format_tool_hint(
                _team_name or "default",
                tor_enabled=_tor_enabled,
                role_idx=role_idx,
            )

            # Wave 16-D: первая роль (role_idx==0) получает A/B team_prompt как prefix.
            # Остальные роли используют свои role-specific hint'ы без изменений.
            ab_prefix = ""
            if role_idx == 0 and _ab_team_prompt:
                ab_prefix = f"{_ab_team_prompt}\n\n"

            if accumulated_context:
                prompt = (
                    f"{ab_prefix}{hint}{tool_hint}\n\n"
                    f"--- Контекст предыдущих ролей ---\n{accumulated_context}\n"
                    f"---\n\nТема: {topic}"
                )
            else:
                prompt = f"{ab_prefix}{hint}{tool_hint}\n\nТема: {topic}"

            try:
                # Wave 38-B: route через engine dispatcher (при dispatch OFF — прямой router.route_query)
                response = await _dispatch_route_query(
                    prompt,
                    router,
                    team_name=_team_name,
                    chat_id=None,  # swarm не знает chat_id — передаём None (resolver использует room)
                )
            except Exception as exc:  # noqa: BLE001
                response = f"[Ошибка роли {name}: {exc}]"
                logger.warning("agent_room_role_failed", role=name, error=str(exc))

            clipped = str(response or "").strip()[: self.role_context_clip]
            if not clipped:
                clipped = "[Пустой ответ роли: проверьте контекст, лимиты или состояние модели]"
                logger.warning("agent_room_role_empty_response", role=name, topic=topic)

            # Live broadcast: публикуем ответ роли в swarm-группу (все уровни depth)
            if broadcast_team:
                await swarm_channels.broadcast_role_step(
                    team=broadcast_team,
                    role_name=name,
                    role_emoji=emoji,
                    role_title=title,
                    text=clipped,
                )

            # R18: Детектируем директиву делегирования [DELEGATE: team]
            if _bus is not None and _router_factory is not None:
                m = _DELEGATE_PATTERN.search(clipped)
                if m:
                    delegate_team = m.group(1).strip()
                    # Извлекаем задачу: текст после [DELEGATE: team] или весь ответ
                    delegate_topic = _DELEGATE_PATTERN.sub("", clipped).strip() or topic
                    logger.info(
                        "agent_room_delegation_detected",
                        role=name,
                        target_team=delegate_team,
                        depth=_depth,
                    )
                    # Live broadcast: уведомление о делегировании (все уровни depth)
                    if broadcast_team:
                        await swarm_channels.broadcast_delegation(
                            source_team=broadcast_team,
                            target_team=delegate_team,
                            topic=delegate_topic,
                        )
                    # Phase 8: delegation checkpoint — фиксируем в task board
                    try:
                        from .swarm_task_board import swarm_task_board  # noqa: PLC0415

                        swarm_task_board.create_task(
                            team=delegate_team,
                            title=f"Delegation: {delegate_topic[:80]}",
                            description=f"Delegated from {_team_name or 'default'} role {name}",
                            priority="high",
                            created_by=f"delegation:{_team_name or 'default'}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

                    delegate_result = await _bus.dispatch(
                        source_team=_team_name or "default",
                        target_team=delegate_team,
                        topic=delegate_topic,
                        router_factory=_router_factory,
                        depth=_depth,
                    )
                    # Инжектируем результат делегирования в контекст
                    delegation_summary = (
                        f"\n\n📬 **Результат от команды {delegate_team}:**\n{delegate_result[:800]}"
                    )
                    clipped += delegation_summary
                    delegation_results.append(f"→ {delegate_team}: задача выполнена")
                    logger.info("agent_room_delegation_injected", role=name, target=delegate_team)

            round_results.append({"role": name, "emoji": emoji, "title": title, "text": clipped})
            accumulated_context += f"[{emoji} {title}]:\n{clipped}\n\n"

        header = f"🐝 **Swarm Room: {topic}**\n\n"
        body = ""
        for result in round_results:
            body += f"**{result['emoji']} {result['title']}:**\n{result['text']}\n\n"

        if delegation_results:
            body += f"📡 **Делегирование:** {', '.join(delegation_results)}\n"

        full_result = header + body.strip()

        # Live broadcast: итог раунда + снимаем active
        if broadcast_team:
            last_role_text = round_results[-1]["text"] if round_results else ""
            await swarm_channels.broadcast_round_end(team=broadcast_team, summary=last_role_text)
            swarm_channels.mark_round_done(broadcast_team)

        # Сохраняем результат в персистентную память (только top-level раунды)
        if _team_name and _depth == 0:
            duration = time.monotonic() - t0
            swarm_memory.save_run(
                team=_team_name,
                topic=topic,
                result=full_result,
                delegations=delegation_results,
                duration_sec=duration,
            )
            # Phase 8: save artifact
            _artifact_verification: dict | None = None
            try:
                from .swarm_artifact_store import swarm_artifact_store  # noqa: PLC0415

                swarm_artifact_store.save_round_artifact(
                    team=_team_name,
                    topic=topic,
                    result=full_result,
                    delegations=delegation_results,
                    duration_sec=time.monotonic() - t0,
                )
                # Phase 7: auto-save markdown report для analysts/research rounds
                if _team_name in {"analysts", "traders", "coders", "creative"}:
                    swarm_artifact_store.save_report(
                        team=_team_name,
                        topic=topic,
                        result=full_result,
                    )
            except Exception:  # noqa: BLE001
                pass

            # Phase 8: quick heuristic verification of round result
            try:
                from .swarm_verifier import quick_heuristic_check  # noqa: PLC0415

                verification = quick_heuristic_check(full_result)
                if not verification.passed:
                    logger.warning(
                        "swarm_round_quality_check_failed",
                        team=_team_name,
                        score=verification.score,
                        issues=verification.issues[:3],
                    )
            except Exception:  # noqa: BLE001
                pass

            # Task board: автоматическая фиксация раунда как completed task
            try:
                from .swarm_task_board import swarm_task_board  # noqa: PLC0415

                task = swarm_task_board.create_task(
                    team=_team_name,
                    title=topic[:100],
                    description=f"Swarm round ({len(self.roles)} roles, {len(delegation_results)} delegations)",
                    priority="medium",
                    created_by="swarm_auto",
                )
                swarm_task_board.complete_task(
                    task.task_id,
                    result=full_result[:500],
                )
            except Exception:  # noqa: BLE001
                pass  # task board — не критично

            # Wave 16-D: записываем метрики раунда в A/B-тест (если активен)
            if _ab_id and _ab_variant and _skill_curator is not None:
                try:
                    _finished_at_iso = (
                        __import__("datetime")
                        .datetime.now(__import__("datetime").timezone.utc)
                        .isoformat(timespec="seconds")
                    )
                    _latency_s = round(time.monotonic() - t0, 3)
                    # Считаем tool_calls из ответов ролей (упрощённо: ищем «Tool:»)
                    _tool_calls_count = sum(r.get("text", "").count("Tool:") for r in round_results)
                    # verifier_pass из heuristic verification (если был)
                    _verifier_pass = False
                    try:
                        from .swarm_verifier import quick_heuristic_check as _qhc  # noqa: PLC0415

                        _vr = _qhc(full_result)
                        _verifier_pass = bool(_vr.passed)
                    except Exception:  # noqa: BLE001
                        pass
                    _round_metric_id = f"{_team_name}:{topic[:60]}:{started_at_iso}"
                    _skill_curator.record_round_metric(
                        _ab_id,
                        _round_metric_id,
                        {
                            "round_id": _round_metric_id,
                            "variant": _ab_variant,
                            "started_at": started_at_iso,
                            "finished_at": _finished_at_iso,
                            "cost_usd": None,  # cost_analytics not wired (future)
                            "latency_s": _latency_s,
                            "tool_calls": _tool_calls_count,
                            "verifier_pass": _verifier_pass,
                            "user_reaction": None,  # обновляется позже через reaction tracker
                            "success": len(round_results) == len(self.roles),
                        },
                    )
                    logger.info(
                        "agent_room_ab_metric_recorded",
                        team=_team_name,
                        ab_id=_ab_id,
                        variant=_ab_variant,
                    )
                except Exception as _ab_metric_exc:  # noqa: BLE001
                    logger.warning(
                        "agent_room_ab_record_metric_failed",
                        team=_team_name,
                        error=str(_ab_metric_exc),
                    )

        logger.info("agent_room_round_completed", topic=topic, delegations=len(delegation_results))
        # Завершаем single-round сессию если была зарегистрирована
        if _single_round_sid is not None:
            _get_swarm_progress().record_round_done(_single_round_sid)
            _get_swarm_progress().end_session(_single_round_sid)
        return full_result

    async def run_loop(
        self,
        topic: str,
        router: Any,
        *,
        rounds: int = 2,
        max_rounds: int = 3,
        next_round_clip: int = 4000,
        _bus: Any = None,
        _router_factory: Any = None,
        _team_name: str = "",
    ) -> str:
        """
        Запускает несколько раундов роя с итеративной доработкой результата.

        Идея:
        - Раунд 1 создает первичное решение.
        - Следующие раунды перерабатывают решение с учетом критики из предыдущего шага.
        """
        safe_max = max(1, int(max_rounds))
        safe_rounds = max(1, min(int(rounds), safe_max))
        safe_clip = max(500, int(next_round_clip))

        logger.info(
            "agent_room_loop_started",
            topic=topic,
            rounds=safe_rounds,
            max_rounds=safe_max,
        )

        base_topic = str(topic or "").strip()
        current_topic = base_topic
        sections: list[str] = []

        # Регистрируем сессию в реестре прогресса
        _progress = _get_swarm_progress()
        _sid = _progress.start_session(
            team=_team_name or "default",
            topic=base_topic,
            rounds_total=safe_rounds,
        )
        try:
            for idx in range(safe_rounds):
                round_no = idx + 1
                round_result = await self.run_round(
                    current_topic,
                    router,
                    _bus=_bus,
                    _depth=0,
                    _router_factory=_router_factory,
                    _team_name=_team_name,
                    _track_progress=False,  # loop уже управляет своим sid
                )
                sections.append(f"## Раунд {round_no}/{safe_rounds}\n{round_result}")
                # Фиксируем завершение раунда
                _progress.record_round_done(_sid)

                if round_no >= safe_rounds:
                    continue

                # Для следующего раунда даем сжатый контекст предыдущего результата.
                clipped = round_result[:safe_clip]
                current_topic = (
                    "Улучши и уточни предыдущее решение. "
                    "Сделай более практичный, проверяемый и устойчивый план.\n\n"
                    f"Исходная тема:\n{base_topic}\n\n"
                    f"Результат предыдущего раунда:\n{clipped}"
                )
        finally:
            _progress.end_session(_sid)

        logger.info("agent_room_loop_completed", topic=topic, rounds=safe_rounds)
        return f"🐝 **Swarm Loop: {base_topic}**\n\n" + "\n\n".join(sections)

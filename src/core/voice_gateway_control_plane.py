# -*- coding: utf-8 -*-
"""
voice_gateway_control_plane.py — официальный интерфейс control-plane для Voice Gateway.

Что это:
- `Protocol`, который описывает методы, на которые реально опирается
  translator backend в owner panel;
- контракт между web layer и любым concrete-клиентом Voice Gateway:
  HTTP-клиентом, тестовым fake-клиентом или future adapter'ом.

Зачем нужно:
- web_app больше не должен неявно зависеть от случайного набора методов,
  существующих только у test double;
- это минимальная защита от дрейфа между fake-клиентами тестов и production
  клиентом;
- master-plan прямо требует явный repo-native control-plane interface.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VoiceGatewayControlPlane(Protocol):
    """Официальный async-контракт Voice Gateway control-plane."""

    async def health_check(self) -> bool: ...

    async def health_report(self) -> dict[str, Any]: ...

    async def capabilities_report(self) -> dict[str, Any]: ...

    async def list_sessions(self, *, status: str | None = None, source: str | None = None, limit: int = 20) -> dict[str, Any]: ...

    async def get_diagnostics(self, session_id: str) -> dict[str, Any]: ...

    async def get_diagnostics_why(self, session_id: str) -> dict[str, Any]: ...

    async def get_timeline_summary(self, session_id: str) -> dict[str, Any]: ...

    async def get_timeline(self, session_id: str, *, limit: int = 8) -> dict[str, Any]: ...

    async def get_timeline_stats(self, session_id: str, *, limit: int = 200) -> dict[str, Any]: ...

    async def export_timeline(self, session_id: str, *, format: str = "md", limit: int = 40) -> dict[str, Any]: ...

    async def list_quick_phrases(self, *, source_lang: str = "", target_lang: str = "") -> dict[str, Any]: ...

    async def start_session(
        self,
        *,
        source: str,
        translation_mode: str,
        notify_mode: str,
        tts_mode: str,
        src_lang: str,
        tgt_lang: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def patch_session(self, session_id: str, **patch: Any) -> dict[str, Any]: ...

    async def stop_session(self, session_id: str) -> dict[str, Any]: ...

    async def tune_runtime(self, session_id: str, **patch: Any) -> dict[str, Any]: ...

    async def send_quick_phrase(
        self,
        session_id: str,
        *,
        text: str,
        source_lang: str = "",
        target_lang: str = "",
    ) -> dict[str, Any]: ...

    async def build_summary(self, session_id: str, *, max_items: int = 12) -> dict[str, Any]: ...

    async def list_mobile_devices(self, *, limit: int = 8) -> dict[str, Any]: ...

    async def get_mobile_session_snapshot(self, device_id: str) -> dict[str, Any]: ...

    async def register_mobile_device(
        self,
        *,
        device_id: str,
        voip_push_token: str,
        apns_environment: str,
        app_version: str,
        locale: str,
        preferred_source_lang: str,
        preferred_target_lang: str,
        notify_default: bool,
    ) -> dict[str, Any]: ...

    async def bind_mobile_device(self, device_id: str, *, session_id: str) -> dict[str, Any]: ...

    async def delete_mobile_device(self, device_id: str) -> dict[str, Any]: ...

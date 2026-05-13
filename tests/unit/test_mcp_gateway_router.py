# -*- coding: utf-8 -*-
"""Тесты mcp_gateway_router (Wave 236)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.mcp_gateway_router import (
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    build_mcp_gateway_router,
)


class FakeMCPManager:
    """Заглушка под mcp_manager: задаёт manifest + call_tool_unified."""

    def __init__(
        self,
        *,
        manifest: list[dict[str, Any]] | None = None,
        manifest_raises: Exception | None = None,
        call_result: Any = "ok",
        call_raises: Exception | None = None,
    ) -> None:
        self._manifest = manifest or []
        self._manifest_raises = manifest_raises
        self._call_result = call_result
        self._call_raises = call_raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tool_manifest(self) -> list[dict[str, Any]]:
        if self._manifest_raises is not None:
            raise self._manifest_raises
        return list(self._manifest)

    async def call_tool_unified(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self._call_raises is not None:
            raise self._call_raises
        return self._call_result


def _make_client(
    *,
    manager: FakeMCPManager | None = None,
    write_raises: Exception | None = None,
    web_api_key: str | None = None,
) -> tuple[TestClient, FakeMCPManager]:
    mgr = manager or FakeMCPManager()
    deps: dict[str, Any] = {"mcp_manager": mgr}

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_raises is not None:
            raise write_raises

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: web_api_key,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_mcp_gateway_router(ctx))
    return TestClient(app), mgr


# Sample OpenAI-style manifest used in multiple tests.
_SAMPLE_MANIFEST: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "peekaboo",
            "description": "Screenshot",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def test_server_info_returns_protocol_metadata() -> None:
    client, _ = _make_client()
    r = client.get("/api/mcp/server/info")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == SERVER_NAME
    assert body["version"] == SERVER_VERSION
    assert body["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert "tools" in body["capabilities"]


def test_tools_list_post_returns_mcp_native_shape() -> None:
    mgr = FakeMCPManager(manifest=_SAMPLE_MANIFEST)
    client, _ = _make_client(manager=mgr)
    r = client.post("/api/mcp/tools/list", json={})
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body
    assert len(body["tools"]) == 2
    first = body["tools"][0]
    # MCP spec: name, description, inputSchema
    assert set(first.keys()) >= {"name", "description", "inputSchema"}
    assert first["name"] == "web_search"
    assert first["inputSchema"]["properties"]["query"]["type"] == "string"


def test_tools_list_get_also_works() -> None:
    mgr = FakeMCPManager(manifest=_SAMPLE_MANIFEST)
    client, _ = _make_client(manager=mgr)
    r = client.get("/api/mcp/tools/list")
    assert r.status_code == 200
    assert len(r.json()["tools"]) == 2


def test_tools_list_empty_manifest() -> None:
    client, _ = _make_client()
    r = client.post("/api/mcp/tools/list", json={})
    assert r.status_code == 200
    assert r.json()["tools"] == []


def test_tools_list_manifest_failure_returns_500() -> None:
    mgr = FakeMCPManager(manifest_raises=RuntimeError("boom"))
    client, _ = _make_client(manager=mgr)
    r = client.post("/api/mcp/tools/list", json={})
    assert r.status_code == 500
    assert "tool_manifest_failed" in r.json()["detail"]


def test_tools_call_ok_text_result() -> None:
    mgr = FakeMCPManager(manifest=_SAMPLE_MANIFEST, call_result="hello world")
    client, _ = _make_client(manager=mgr)
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "web_search", "arguments": {"query": "hi"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["isError"] is False
    assert body["content"][0]["type"] == "text"
    assert body["content"][0]["text"] == "hello world"
    assert mgr.calls == [("web_search", {"query": "hi"})]


def test_tools_call_error_text_marks_is_error_true() -> None:
    mgr = FakeMCPManager(call_result="❌ tool failed")
    client, _ = _make_client(manager=mgr)
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["isError"] is True


def test_tools_call_exception_caught_as_error_content() -> None:
    mgr = FakeMCPManager(call_raises=RuntimeError("kaboom"))
    client, _ = _make_client(manager=mgr)
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["isError"] is True
    assert "kaboom" in body["content"][0]["text"]


def test_tools_call_timeout_returns_isError() -> None:
    mgr = FakeMCPManager(call_raises=TimeoutError("slow"))
    client, _ = _make_client(manager=mgr)
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["isError"] is True
    assert "timeout" in body["content"][0]["text"]


def test_tools_call_missing_name_400() -> None:
    client, _ = _make_client()
    r = client.post("/api/mcp/tools/call", json={"arguments": {}})
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_tool_name"


def test_tools_call_invalid_arguments_400() -> None:
    client, _ = _make_client()
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": "not-a-dict"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "arguments_must_be_object"


def test_tools_call_write_access_denied() -> None:
    client, _ = _make_client(write_raises=HTTPException(status_code=403, detail="nope"))
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
    )
    assert r.status_code == 403


def test_tools_call_bearer_bypasses_legacy_check() -> None:
    """Когда WEB_API_KEY установлен и Bearer совпадает, legacy check не вызывается."""
    mgr = FakeMCPManager(call_result="ok")
    client, _ = _make_client(
        manager=mgr,
        # legacy check бы упал, но Bearer-match должен сработать раньше:
        write_raises=HTTPException(status_code=403),
        web_api_key="secret",
    )
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "ok"


def test_tools_call_bad_bearer_falls_through_to_legacy() -> None:
    client, _ = _make_client(
        write_raises=HTTPException(status_code=403),
        web_api_key="secret",
    )
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403


def test_admin_mcp_html_page_lists_tools() -> None:
    mgr = FakeMCPManager(manifest=_SAMPLE_MANIFEST)
    client, _ = _make_client(manager=mgr)
    r = client.get("/admin/mcp")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert "web_search" in html
    assert "peekaboo" in html
    assert "Krab MCP gateway" in html
    assert MCP_PROTOCOL_VERSION in html


def test_sse_endpoint_streams_endpoint_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE streamer должен сначала отдавать endpoint+tools events."""
    monkeypatch.setenv("KRAB_MCP_SSE_MAX_SECONDS", "1")
    mgr = FakeMCPManager(manifest=_SAMPLE_MANIFEST)
    client, _ = _make_client(manager=mgr)
    with client.stream("GET", "/api/mcp/sse") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # Прочитаем первые куски — достаточно увидеть event: endpoint и tools.
        buffer = ""
        for chunk in resp.iter_text():
            buffer += chunk
            if "event: tools" in buffer and "event: endpoint" in buffer:
                break
        assert "event: endpoint" in buffer
        assert "event: tools" in buffer
        # tools event несёт сериализованный список tool definitions
        assert "web_search" in buffer


def test_mcp_native_passthrough_for_already_native_items() -> None:
    """Если manifest содержит element уже в MCP shape (name+inputSchema), pass through."""
    mgr = FakeMCPManager(
        manifest=[
            {
                "name": "raw_tool",
                "description": "already MCP",
                "inputSchema": {"type": "object"},
            }
        ]
    )
    client, _ = _make_client(manager=mgr)
    r = client.post("/api/mcp/tools/list", json={})
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "raw_tool"
    assert tools[0]["inputSchema"] == {"type": "object"}


def test_invalid_manifest_items_skipped() -> None:
    mgr = FakeMCPManager(
        manifest=[
            None,  # type: ignore[list-item]
            "bad",  # type: ignore[list-item]
            {"function": {}},  # no name
            {
                "type": "function",
                "function": {
                    "name": "good",
                    "description": "ok",
                    "parameters": {"type": "object"},
                },
            },
        ]
    )
    client, _ = _make_client(manager=mgr)
    r = client.post("/api/mcp/tools/list", json={})
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "good"


def test_tools_call_serializes_dict_result_to_json() -> None:
    mgr = FakeMCPManager(call_result={"a": 1, "b": [2, 3]})
    client, _ = _make_client(manager=mgr)
    r = client.post(
        "/api/mcp/tools/call",
        json={"name": "x", "arguments": {}},
    )
    body = r.json()
    text = body["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed == {"a": 1, "b": [2, 3]}
    assert body["isError"] is False

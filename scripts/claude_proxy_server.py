#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Pro web proxy — OpenAI-compatible API server backed by claude.ai session.

Exposes:
  GET  /v1/models              — list available Claude models
  POST /v1/chat/completions    — proxy to claude.ai (streaming + non-streaming)
  GET  /health                 — liveness probe

Listens on localhost:18791 (configurable via --port).

Session key stored in ~/.openclaw/claude_proxy_config.json (NOT in repo).

WARNING: Uses claude.ai unofficial internal API.
  - Fragile: may break when claude.ai updates its interface.
  - Violates Anthropic ToS for automated/third-party use.
  - For personal use only with your own Claude Pro subscription.
  - Session key expires (typically ~7 days); must be refreshed manually.

Setup:
  1. Log in to claude.ai in Safari or Chrome.
  2. Open DevTools (⌥⌘I) → Application → Cookies → https://claude.ai
  3. Find the cookie named 'sessionKey' and copy its value.
  4. Run once to store:
       python scripts/claude_proxy_server.py --set-session sk-ant-sid01-...
  5. Start the proxy:
       python scripts/claude_proxy_server.py
  6. Add to OpenClaw models.json (see docs/CLAUDE_PROXY_SETUP.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

# ── Config ─────────────────────────────────────────────────────────────────────

_CONFIG_FILE = Path.home() / ".openclaw" / "claude_proxy_config.json"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 17191

CLAUDE_AI_BASE = "https://claude.ai/api"

# Map OpenAI-style IDs → claude.ai internal model IDs
# These are the model IDs as claude.ai sends them in its API
SUPPORTED_MODELS: dict[str, str] = {
    "claude-proxy/claude-opus-4-6": "claude-opus-4-6",
    "claude-proxy/claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-proxy/claude-haiku-4-5": "claude-haiku-4-5",
    "claude-proxy/claude-3-7-sonnet": "claude-3-7-sonnet",
    "claude-proxy/claude-3-5-sonnet": "claude-3-5-sonnet",
    "claude-proxy/claude-3-5-haiku": "claude-3-5-haiku",
}
DEFAULT_MODEL_KEY = "claude-proxy/claude-opus-4-6"


# ── Session / config helpers ───────────────────────────────────────────────────

def _load_config() -> dict[str, Any]:
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config(data: dict[str, Any]) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_session_key() -> str:
    key = _load_config().get("session_key", "")
    if not key:
        raise RuntimeError(
            "No session key configured.\n"
            "Run: python scripts/claude_proxy_server.py --set-session <sessionKey>"
        )
    return key


# ── claude.ai API client ───────────────────────────────────────────────────────

def _headers(session_key: str) -> dict[str, str]:
    return {
        "Cookie": f"sessionKey={session_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": "https://claude.ai/",
        "Origin": "https://claude.ai",
        "anthropic-client-platform": "web_claude_ai",
    }


async def _fetch_org_id(client: httpx.AsyncClient, session_key: str) -> str:
    """Returns the first organization UUID for the session."""
    resp = await client.get(
        f"{CLAUDE_AI_BASE}/organizations",
        headers=_headers(session_key),
        timeout=15.0,
    )
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired session key. Refresh it.")
    resp.raise_for_status()
    orgs = resp.json()
    if not orgs:
        raise HTTPException(status_code=503, detail="No Claude organizations found for this session.")
    return orgs[0]["uuid"]


async def _create_conversation(
    client: httpx.AsyncClient, session_key: str, org_id: str
) -> str:
    """Creates a fresh conversation, returns its UUID."""
    conv_id = str(uuid.uuid4())
    resp = await client.post(
        f"{CLAUDE_AI_BASE}/organizations/{org_id}/chat_conversations",
        headers=_headers(session_key),
        json={"uuid": conv_id, "name": "krab-proxy"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json().get("uuid", conv_id)


def _build_prompt(messages: list[dict]) -> str:
    """
    Convert OpenAI chat messages list to claude.ai Human/Assistant prompt format.
    System messages are prepended before the first Human turn.
    """
    system_parts: list[str] = []
    turn_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content") or ""
        # Handle multipart content (vision, tool use, etc.)
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content = str(content).strip()

        if role == "system":
            system_parts.append(content)
        elif role == "user":
            turn_parts.append(f"\n\nHuman: {content}")
        elif role == "assistant":
            turn_parts.append(f"\n\nAssistant: {content}")

    system_prefix = ""
    if system_parts:
        system_prefix = " ".join(system_parts) + "\n\n"

    # Ensure the prompt ends with "\n\nAssistant:" to cue the model
    prompt = system_prefix + "".join(turn_parts) + "\n\nAssistant:"
    return prompt


async def _stream_claude_response(
    session_key: str,
    org_id: str,
    conv_id: str,
    prompt: str,
    model_internal: str,
    completion_id: str,
    created: int,
    model_key: str,
) -> AsyncIterator[str]:
    """
    Streams SSE chunks from claude.ai and converts them to OpenAI delta format.

    claude.ai SSE can send events in two formats:
      - Older: {"type": "completion", "completion": "...", "stop_reason": null|"end_turn"}
      - Newer: Anthropic Messages API format with content_block_delta events
    Both are handled.
    """
    payload: dict[str, Any] = {
        "prompt": prompt,
        "model": model_internal,
        "timezone": "UTC",
        "rendering_mode": "raw",
        "attachments": [],
        "files": [],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=120.0)) as client:
        async with client.stream(
            "POST",
            f"{CLAUDE_AI_BASE}/organizations/{org_id}/chat_conversations/{conv_id}/completion",
            headers=_headers(session_key),
            json=payload,
        ) as resp:
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Session key expired. Please refresh.")
            if resp.status_code == 403:
                raise HTTPException(status_code=403, detail="Access denied by claude.ai.")
            if resp.status_code == 429:
                raise HTTPException(status_code=429, detail="Rate limited by claude.ai.")
            if resp.status_code != 200:
                body = await resp.aread()
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"claude.ai error {resp.status_code}: {body.decode()[:300]}",
                )

            async for raw_line in resp.aiter_lines():
                if not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                # ── Older completion format ────────────────────────────────
                if event_type == "completion":
                    text = event.get("completion", "")
                    stop = event.get("stop_reason")
                    if text:
                        yield _openai_chunk(completion_id, created, model_key, text, None)
                    if stop:
                        yield _openai_chunk(completion_id, created, model_key, "", "stop")
                        yield "data: [DONE]\n\n"
                        return

                # ── Newer Messages API format ──────────────────────────────
                elif event_type == "content_block_delta":
                    text = event.get("delta", {}).get("text", "")
                    if text:
                        yield _openai_chunk(completion_id, created, model_key, text, None)

                elif event_type == "message_delta":
                    stop = event.get("delta", {}).get("stop_reason")
                    if stop:
                        yield _openai_chunk(completion_id, created, model_key, "", "stop")
                        yield "data: [DONE]\n\n"
                        return

                elif event_type == "message_stop":
                    yield _openai_chunk(completion_id, created, model_key, "", "stop")
                    yield "data: [DONE]\n\n"
                    return


def _openai_chunk(
    cid: str, created: int, model: str, text: str, finish_reason: str | None
) -> str:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": text} if text else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ── FastAPI application ────────────────────────────────────────────────────────

app = FastAPI(
    title="Claude Proxy",
    description="OpenAI-compatible proxy for claude.ai (Claude Pro subscription)",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "claude-proxy", "port": PROXY_PORT}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": k,
                "object": "model",
                "created": 1740000000,
                "owned_by": "claude-proxy",
            }
            for k in SUPPORTED_MODELS
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body: dict[str, Any] = await request.json()
    messages: list[dict] = body.get("messages", [])
    model_key: str = body.get("model") or DEFAULT_MODEL_KEY
    do_stream: bool = bool(body.get("stream", False))

    # Resolve model ID
    model_internal = SUPPORTED_MODELS.get(model_key)
    if model_internal is None:
        # Try partial match (e.g. "claude-opus-4-6" matches "claude-proxy/claude-opus-4-6")
        model_internal = next(
            (v for k, v in SUPPORTED_MODELS.items() if model_key in k),
            SUPPORTED_MODELS[DEFAULT_MODEL_KEY],
        )

    try:
        session_key = _get_session_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    prompt = _build_prompt(messages)

    # Fetch org + create conversation (shared between streaming and non-streaming)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            org_id = await _fetch_org_id(client, session_key)
            conv_id = await _create_conversation(client, session_key, org_id)
        except HTTPException:
            raise
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"claude.ai setup error: {exc}",
            ) from exc

    if do_stream:
        async def _gen() -> AsyncIterator[str]:
            async for chunk in _stream_claude_response(
                session_key, org_id, conv_id, prompt, model_internal,
                completion_id, created, model_key,
            ):
                yield chunk

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # Non-streaming: collect full text
    full_text = ""
    async for chunk in _stream_claude_response(
        session_key, org_id, conv_id, prompt, model_internal,
        completion_id, created, model_key,
    ):
        if chunk.startswith("data: {"):
            try:
                obj = json.loads(chunk[6:].strip())
                full_text += obj["choices"][0]["delta"].get("content", "")
            except Exception:
                pass

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_key,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude Pro web proxy — OpenAI-compatible API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--set-session",
        metavar="SESSION_KEY",
        help="Store claude.ai session key and exit",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify session key is set and fetch org ID, then exit",
    )
    parser.add_argument("--port", type=int, default=PROXY_PORT)
    args = parser.parse_args()

    if args.set_session:
        cfg = _load_config()
        cfg["session_key"] = args.set_session
        _save_config(cfg)
        print(f"Session key saved to {_CONFIG_FILE}")
        print("Start the proxy with: python scripts/claude_proxy_server.py")
        sys.exit(0)

    if args.check:
        session_key = _get_session_key()
        print("Session key is set.")

        async def _check() -> None:
            async with httpx.AsyncClient() as client:
                org_id = await _fetch_org_id(client, session_key)
                print(f"Organization ID: {org_id}")
                print("Session key is valid.")

        asyncio.run(_check())
        sys.exit(0)

    print(f"Starting Claude proxy on http://{PROXY_HOST}:{args.port}")
    print(f"  Config: {_CONFIG_FILE}")
    print(f"  OpenAI-compatible endpoint: http://localhost:{args.port}/v1/chat/completions")
    uvicorn.run(app, host=PROXY_HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

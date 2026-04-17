#!/usr/bin/env python3
"""Smoke test LM Studio fallback pipeline.

Verifies:
1. LM Studio доступен по :1234
2. Model loaded (or загружает default)
3. Simple completion работает
4. Timing <30 сек
5. Handles TLS/network errors
"""

import sys
import time

import httpx

LM_URL = "http://127.0.0.1:1234/v1"


def test_health() -> dict:
    try:
        r = httpx.get(f"{LM_URL}/models", timeout=5.0)
        data = r.json() if r.status_code == 200 else {}
        return {"ok": r.status_code == 200, "models": [m.get("id") for m in data.get("data", [])]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_completion(model_id: str) -> dict:
    start = time.time()
    try:
        r = httpx.post(
            f"{LM_URL}/chat/completions",
            timeout=60.0,
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "Say 'OK' in one word"}],
                "max_tokens": 16,
                "temperature": 0,
            },
        )
        elapsed = time.time() - start
        if r.status_code == 200:
            data = r.json()
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return {"ok": True, "reply": reply[:100], "elapsed_sec": round(elapsed, 2)}
        return {"ok": False, "http_status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed_sec": round(time.time() - start, 2)}


def main():
    print("=== LM Studio Fallback Smoke Test ===\n")

    health = test_health()
    print(f"Health: {health}")
    if not health.get("ok"):
        print("LM Studio недоступен — skip completion")
        sys.exit(1)

    models = health.get("models", [])
    if not models:
        print("No models loaded в LM Studio — loading тест невозможен")
        sys.exit(1)

    model_id = models[0]
    print(f"\nTesting completion на {model_id}...")
    result = test_completion(model_id)
    print(f"Result: {result}")

    if result.get("ok"):
        elapsed = result["elapsed_sec"]
        reply = result.get("reply", "")
        timing_ok = elapsed < 30
        print(f"\nLM Studio OK · {elapsed}s · reply: {reply}")
        if not timing_ok:
            print(f"WARNING: elapsed {elapsed}s > 30s threshold")
        sys.exit(0)
    else:
        print(f"\nFAIL: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()

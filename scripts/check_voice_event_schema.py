#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Voice Event Schema Checker.

Назначение:
1) Нормализовать и проверить события voice stream по schema v1.0.
2) Быстро выявлять несовместимые payload между Krab и Voice Gateway.

Использование:
1) python scripts/check_voice_event_schema.py '{"type":"stt.partial","data":{"session_id":"vs_1","latency_ms":120}}'
2) python scripts/check_voice_event_schema.py --file /path/to/events.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.voice_gateway_client import VoiceGatewayClient


def _validate(event: dict) -> list[str]:
    required = ["schema_version", "session_id", "event_type", "source", "severity", "latency_ms", "ts", "data"]
    missing = [key for key in required if key not in event]
    return missing


def _process_one(raw: dict) -> dict:
    normalized = VoiceGatewayClient.normalize_stream_event(raw)
    missing = _validate(normalized)
    return {
        "ok": len(missing) == 0,
        "missing": missing,
        "normalized": normalized,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check/normalize voice event schema")
    parser.add_argument("event_json", nargs="?", help="raw event JSON string")
    parser.add_argument("--file", dest="file_path", help="path to JSON or JSONL file")
    args = parser.parse_args()

    if not args.event_json and not args.file_path:
        parser.error("provide event_json or --file")

    rows: list[dict] = []
    if args.event_json:
        rows.append(json.loads(args.event_json))
    elif args.file_path:
        path = Path(args.file_path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        else:
            payload = json.loads(text)
            if isinstance(payload, list):
                rows.extend(payload)
            else:
                rows.append(payload)

    results = [_process_one(item if isinstance(item, dict) else {}) for item in rows]
    failed = sum(1 for item in results if not item["ok"])
    out = {
        "ok": failed == 0,
        "total": len(results),
        "failed": failed,
        "results": results,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

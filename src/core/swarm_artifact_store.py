# -*- coding: utf-8 -*-
"""
swarm_artifact_store.py — persist swarm round artifacts to files.

Phase 8 Master Plan: artifact store для swarm execution layer.
Сохраняет результаты раундов, delegation chains, verifier reports.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("krab.swarm_artifact_store")

_DEFAULT_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_artifacts"


class SwarmArtifactStore:
    """Файловое хранилище артефактов swarm rounds."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or _DEFAULT_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save_round_artifact(
        self,
        *,
        team: str,
        topic: str,
        result: str,
        delegations: list[str] | None = None,
        verification: dict[str, Any] | None = None,
        duration_sec: float = 0.0,
    ) -> Path:
        """Сохраняет артефакт раунда. Возвращает путь к файлу."""
        ts = int(time.time())
        safe_team = team.lower().replace("/", "_")[:20]
        filename = f"{safe_team}_{ts}.json"
        path = self._base_dir / filename

        payload = {
            "team": team,
            "topic": topic[:200],
            "result": result[:5000],
            "delegations": delegations or [],
            "verification": verification,
            "duration_sec": round(duration_sec, 2),
            "timestamp": ts,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        }

        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("swarm_artifact_saved", team=team, path=str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_artifact_save_failed", error=str(exc))

        return path

    def list_artifacts(self, *, team: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Список артефактов (новые первые)."""
        files = sorted(self._base_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        results: list[dict[str, Any]] = []
        for f in files:
            if len(results) >= limit:
                break
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if team and data.get("team", "").lower() != team.lower():
                    continue
                data["_path"] = str(f)
                results.append(data)
            except Exception:  # noqa: BLE001
                continue
        return results

    def get_artifact(self, filename: str) -> dict[str, Any] | None:
        """Читает конкретный артефакт по имени файла."""
        path = self._base_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def save_report(
        self,
        *,
        team: str,
        topic: str,
        result: str,
        report_dir: Path | None = None,
    ) -> Path:
        """Сохраняет результат как markdown report для service workflows (Phase 7)."""
        ts = int(time.time())
        safe_team = team.lower().replace("/", "_")[:20]
        safe_topic = (
            "".join(c if c.isalnum() or c in " _-" else "_" for c in topic[:50])
            .strip()
            .replace(" ", "_")
        )
        filename = f"{safe_team}_{safe_topic}_{ts}.md"
        dest = report_dir or self._base_dir.parent / "reports"
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / filename

        content = (
            f"# Swarm Report: {topic}\n\n"
            f"**Team:** {team}\n"
            f"**Generated:** {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))}\n\n"
            f"---\n\n"
            f"{result}\n"
        )

        try:
            path.write_text(content, encoding="utf-8")
            logger.info("swarm_report_saved", team=team, path=str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_report_save_failed", error=str(exc))

        return path

    def cleanup_old(self, max_files: int = 100) -> int:
        """Удаляет старые артефакты сверх лимита."""
        files = sorted(self._base_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        removed = 0
        while len(files) > max_files:
            old = files.pop(0)
            try:
                old.unlink()
                removed += 1
            except Exception:  # noqa: BLE001
                pass
        return removed


# Синглтон
swarm_artifact_store = SwarmArtifactStore()

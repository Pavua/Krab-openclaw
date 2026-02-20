# -*- coding: utf-8 -*-
"""
Проверка parity JS-рантайма между боевым index.html и frontend-прототипом.

Зачем:
1) Отсекать «красивые», но нефункциональные прототипы.
2) Гарантировать, что в прототипе сохранены ключевые API-вызовы и обработчики.
3) Не допускать интеграцию заглушек вместо рабочего JS.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)

# Маркеры заглушек, которые не должны попадать в production-кандидат.
STUB_MARKERS = (
    "Placeholder, real JS gets transferred here in Production",
    "Prototype showcase setup",
    "Executing requested task...",
    "Krab Analysis",
)

# Ключевые признаки рабочего JS-контура панели.
REQUIRED_PATTERNS = (
    r"async\s+function\s+updateStats\s*\(",
    r"async\s+function\s+assistantQuery\s*\(",
    r"async\s+function\s+loadModelFeedbackStats\s*\(",
    r"async\s+function\s+runQuickDeepResearch\s*\(",
    r"document\.getElementById\((['\"])quickDeepBtn\1\)\.addEventListener\((['\"])click\2,\s*runQuickDeepResearch\)",
    r"document\.getElementById\((['\"])feedbackStatsBtn\1\)\.addEventListener\((['\"])click\2,\s*loadModelFeedbackStats\)",
    r"/api/model/catalog",
    r"/api/assistant/query",
    r"/api/ops/report",
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_inline_js(text: str) -> str:
    chunks: list[str] = []
    for block in SCRIPT_BLOCK_RE.findall(text):
        chunks.append(block.strip())
    return "\n".join(chunks).strip()


def _missing_required_patterns(js_text: str) -> list[str]:
    missing: list[str] = []
    for pattern in REQUIRED_PATTERNS:
        if re.search(pattern, js_text, flags=re.IGNORECASE) is None:
            missing.append(pattern)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка runtime parity web-прототипа")
    parser.add_argument("--base", default="src/web/index.html", help="Путь к боевому index.html")
    parser.add_argument(
        "--prototype",
        default="src/web/prototypes/nano/index_redesign.html",
        help="Путь к проверяемому прототипу",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.60,
        help="Минимальная доля длины JS прототипа от боевого JS (по умолчанию 0.60)",
    )
    args = parser.parse_args()

    base_path = Path(args.base)
    prototype_path = Path(args.prototype)

    if not base_path.exists():
        print(f"❌ Базовый файл не найден: {base_path}")
        return 2
    if not prototype_path.exists():
        print(f"❌ Прототип не найден: {prototype_path}")
        return 2

    base_text = _read_text(base_path)
    prototype_text = _read_text(prototype_path)

    base_js = _extract_inline_js(base_text)
    prototype_js = _extract_inline_js(prototype_text)

    if not base_js:
        print("❌ В базовом файле не найден inline JS для сравнения.")
        return 2
    if not prototype_js:
        print("❌ В прототипе не найден inline JS.")
        return 1

    ratio = len(prototype_js) / max(len(base_js), 1)
    stub_hits = [marker for marker in STUB_MARKERS if marker in prototype_js]
    missing_patterns = _missing_required_patterns(prototype_js)

    print("🧪 Web Runtime Parity Check")
    print(f"- base js chars: {len(base_js)}")
    print(f"- prototype js chars: {len(prototype_js)}")
    print(f"- js size ratio: {ratio:.3f}")
    print(f"- required pattern misses: {len(missing_patterns)}")
    print(f"- stub markers: {len(stub_hits)}")

    failed = False

    if ratio < args.min_ratio:
        failed = True
        print(f"\n🚨 JS-контур слишком короткий: ratio={ratio:.3f} < min_ratio={args.min_ratio:.3f}")

    if missing_patterns:
        failed = True
        print("\n🚨 В JS прототипа не найдены обязательные паттерны:")
        for pattern in missing_patterns:
            print(f"  - {pattern}")

    if stub_hits:
        failed = True
        print("\n🚨 Найдены маркеры заглушек:")
        for marker in stub_hits:
            print(f"  - {marker}")

    if failed:
        print("\n❌ Runtime parity check не пройден.")
        return 1

    print("\n✅ Runtime parity check пройден.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

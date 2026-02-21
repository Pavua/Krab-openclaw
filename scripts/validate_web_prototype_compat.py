# -*- coding: utf-8 -*-
"""
Проверка совместимости frontend-прототипа с боевым index.html.

Зачем:
1) Не сломать текущую Web-панель при интеграции нового UI.
2) Гарантировать, что все критичные DOM id сохранены.
3) Запретить случайный перенос мок-скриптов в production-файл.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ID_PATTERN = re.compile(r'id="([^"]+)"')
MOCK_MARKERS = (
    "Mocked for Prototype View",
    "Simulating the environment for the prototype showcase",
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_ids(text: str) -> set[str]:
    return set(ID_PATTERN.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка совместимости web-прототипа")
    parser.add_argument(
        "--base",
        default="src/web/index.html",
        help="Путь к боевому index.html",
    )
    parser.add_argument(
        "--prototype",
        default="src/web/prototypes/nano/index_redesign.html",
        help="Путь к проверяемому прототипу",
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

    base_ids = _extract_ids(base_text)
    prototype_ids = _extract_ids(prototype_text)

    missing_ids = sorted(base_ids - prototype_ids)
    mock_hits = [marker for marker in MOCK_MARKERS if marker in prototype_text]

    print("🧪 Web Prototype Compatibility Check")
    print(f"- base ids: {len(base_ids)}")
    print(f"- prototype ids: {len(prototype_ids)}")
    print(f"- missing ids: {len(missing_ids)}")
    print(f"- mock markers: {len(mock_hits)}")

    if missing_ids:
        print("\n🚨 Отсутствуют критичные id из боевого файла:")
        for item in missing_ids:
            print(f"  - {item}")

    if mock_hits:
        print("\n🚨 Найдены мок-маркеры в прототипе:")
        for item in mock_hits:
            print(f"  - {item}")

    if missing_ids or mock_hits:
        print("\n❌ Прототип пока не готов к прямой интеграции.")
        return 1

    print("\n✅ Прототип совместим для интеграции.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


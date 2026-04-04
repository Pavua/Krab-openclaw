#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Создаёт Apple Shortcut "Краб: Быстрый ввод" и открывает его для импорта в Shortcuts.app.

Запуск (один раз):
    python3 scripts/create_krab_shortcut.py

После выполнения откроется диалог "Добавить шорткат" в Shortcuts.app.
Нажми "Добавить".

Назначение клавиши (после импорта):
  System Settings → Keyboard → Keyboard Shortcuts → App Shortcuts → +
  App: Shortcuts.app | Title: "Краб: Быстрый ввод" | Shortcut: ⌘⇧K
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).parent / "krab_quick_input.sh")
SHORTCUT_NAME = "Краб: Быстрый ввод"
OUTPUT_FILE = Path(__file__).parent / "Кrab_Quick_Input.shortcut"


def make_shortcut_plist() -> bytes:
    """Создаёт бинарный plist Apple Shortcut с двумя действиями:
    1. Ask for Input (текстовый ввод)
    2. Run Shell Script (отправка в Krab)
    """
    workflow = {
        "WFWorkflowActions": [
            # Действие 1: Спросить текст
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
                "WFWorkflowActionParameters": {
                    "WFAskActionPrompt": "Сообщение для Краба:",
                    "WFInputType": "Text",
                    "WFAskActionDefaultAnswerValue": "",
                },
            },
            # Действие 2: Run Shell Script с результатом
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
                "WFWorkflowActionParameters": {
                    "WFShellScriptActionScript": (
                        f'INPUT="$1"\n'
                        f'[ -z "$INPUT" ] && exit 0\n'
                        f'PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({{\'text\':\'💬 \'+sys.argv[1], \'chat_id\':\'@p0lrd\'}}))"\
 "$INPUT")\n'
                        f'curl -s -X POST http://127.0.0.1:8080/api/notify -H "Content-Type: application/json" -d "$PAYLOAD"\n'
                    ),
                    "WFShellType": "/bin/bash",
                    "WFInput": {
                        "Value": {
                            "attachments": [
                                {
                                    "OutputUUID": "PREV_OUTPUT",
                                    "Type": "ActionOutput",
                                }
                            ],
                            "string": "{\uFFFC}",
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                },
            },
        ],
        "WFWorkflowClientVersion": "1280.0.0",
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 61440,
            "WFWorkflowIconStartColor": 431817727,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowName": SHORTCUT_NAME,
        "WFWorkflowTypes": [],
    }
    return plistlib.dumps(workflow, fmt=plistlib.FMT_BINARY)


def main() -> None:
    plist_data = make_shortcut_plist()
    OUTPUT_FILE.write_bytes(plist_data)
    print(f"✅ Shortcut файл создан: {OUTPUT_FILE}")

    # Подписываем (требуется macOS 15+)
    signed_file = OUTPUT_FILE.with_stem(OUTPUT_FILE.stem + "_signed")
    sign_result = subprocess.run(
        ["shortcuts", "sign", "--mode", "people-who-know-me",
         "--input", str(OUTPUT_FILE), "--output", str(signed_file)],
        capture_output=True, text=True,
    )

    import_target = signed_file if sign_result.returncode == 0 else OUTPUT_FILE
    if sign_result.returncode == 0:
        print(f"🔏 Подписан: {signed_file}")
    else:
        print(f"⚠️ Подпись не удалась, пробуем без подписи")

    # Открываем в Shortcuts.app для импорта
    result = subprocess.run(["open", "-a", "Shortcuts", str(import_target)], capture_output=True)
    if result.returncode == 0:
        print(f"🚀 Открыт в Shortcuts.app — нажми 'Добавить' в диалоге")
    else:
        print(f"⚠️ Открой вручную: open '{import_target}'")

    print()
    print("После импорта назначь клавишу:")
    print("  Sys Settings → Keyboard → Shortcuts → App Shortcuts → +")
    print(f"  App: Shortcuts.app | Menu Title: {SHORTCUT_NAME} | Key: ⌘⇧K")


if __name__ == "__main__":
    main()

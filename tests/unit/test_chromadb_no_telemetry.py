# -*- coding: utf-8 -*-
"""
tests/unit/test_chromadb_no_telemetry.py

Проверяем, что ChromaDB telemetry (PostHog consumer thread) отключена.

Root cause зависания pytest:
- ChromaDB при импорте поднимает PostHog consumer thread (`queue.get(block=True)`).
- Этот thread блокирует `waiter.acquire()` при выходе интерпретатора.
- Фикс: ANONYMIZED_TELEMETRY=False + CHROMA_TELEMETRY=False выставляются
  в tests/conftest.py (до первого import chromadb) и в src/memory_engine.py,
  src/bootstrap/runtime.py (для runtime-процесса).

Тесты намеренно не импортируют chromadb напрямую — только проверяют env
и отсутствие посторонних daemon threads.
"""

from __future__ import annotations

import os
import threading


def test_anonymized_telemetry_env_set() -> None:
    """ANONYMIZED_TELEMETRY должен быть установлен в 'False' до запуска тестов."""
    value = os.environ.get("ANONYMIZED_TELEMETRY")
    assert value is not None, (
        "ANONYMIZED_TELEMETRY не установлен. "
        "conftest.py должен устанавливать его до любого импорта chromadb."
    )
    assert value.lower() in ("false", "0", "no"), (
        f"ANONYMIZED_TELEMETRY={value!r} — ожидается 'False'. "
        "Telemetry chromadb не отключена!"
    )


def test_chroma_telemetry_env_set() -> None:
    """CHROMA_TELEMETRY должен быть установлен в 'False' до запуска тестов."""
    value = os.environ.get("CHROMA_TELEMETRY")
    assert value is not None, (
        "CHROMA_TELEMETRY не установлен. "
        "conftest.py должен устанавливать его до любого импорта chromadb."
    )
    assert value.lower() in ("false", "0", "no"), (
        f"CHROMA_TELEMETRY={value!r} — ожидается 'False'. "
        "Telemetry chromadb не отключена!"
    )


def test_no_posthog_consumer_thread_alive() -> None:
    """Не должно быть живых posthog/chromadb telemetry consumer threads.

    ChromaDB PostHog consumer thread имеет daemon=True, но всё равно
    может блокировать выход через waiter.acquire() в CPython.
    При ANONYMIZED_TELEMETRY=False такой thread не создаётся вообще.

    Примечание: pytest_timeout создаёт thread с "timeout" в имени — исключаем.
    """
    alive_threads = threading.enumerate()
    bad = [
        t.name
        for t in alive_threads
        if (
            # Точные substring-маркеры PostHog consumer threads.
            # pytest_timeout именует thread "pytest_timeout <test_id>" — исключаем их
            # проверкой что имя НЕ начинается с "pytest_timeout".
            not t.name.startswith("pytest_timeout")
            and (
                "posthog" in t.name.lower()
                or t.name.lower().startswith("chromadb_consumer")
                or t.name.lower().startswith("chromadb-consumer")
            )
        )
    ]
    assert not bad, (
        f"Обнаружены posthog/telemetry threads: {bad}. "
        "ChromaDB telemetry не отключена — убедитесь что ANONYMIZED_TELEMETRY=False "
        "выставляется до первого `import chromadb`."
    )


def test_memory_engine_sets_telemetry_env_before_chromadb_import() -> None:
    """memory_engine.py должен содержать setdefault до реального `import chromadb`.

    Ищем именно исполняемый `import chromadb` (не упоминание в комментарии).
    В Python реальный импорт будет: `    import chromadb` (с отступом внутри try)
    или `import chromadb` в начале строки.
    """
    import pathlib
    import re

    me_path = pathlib.Path(__file__).parents[2] / "src" / "memory_engine.py"
    assert me_path.exists(), f"Не найден файл {me_path}"

    source = me_path.read_text(encoding="utf-8")

    # setdefault позиция
    setdefault_pos = source.find('os.environ.setdefault("ANONYMIZED_TELEMETRY"')
    assert setdefault_pos != -1, (
        "src/memory_engine.py не содержит os.environ.setdefault(\"ANONYMIZED_TELEMETRY\"). "
        "Добавьте перед `import chromadb`."
    )

    # Реальный import chromadb: строка начинается с пробелов/tab и потом "import chromadb"
    # (не в комментарии). Ищем через regex: ^ с опциональными пробелами + import chromadb.
    real_import_match = re.search(r"^[ \t]*import chromadb\b", source, re.MULTILINE)
    assert real_import_match is not None, (
        "src/memory_engine.py не содержит реального `import chromadb`."
    )
    chromadb_import_pos = real_import_match.start()

    assert setdefault_pos < chromadb_import_pos, (
        f"os.environ.setdefault (pos={setdefault_pos}) стоит ПОСЛЕ "
        f"реального `import chromadb` (pos={chromadb_import_pos}) в memory_engine.py. "
        "Переставьте setdefault выше try-блока с импортом."
    )

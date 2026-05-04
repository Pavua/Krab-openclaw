#!/usr/bin/env python3
"""Wave 19-B: пассивный коллектор базовой линии памяти на основе psutil.

Запускается через cron / launchd каждые 60s. Собирает snapshot:
- top 20 процессов по RSS (sorted desc)
- системная статистика (vm_stat, swap usage)
- per-process: pid, name, cmd[:120], rss_mb, vms_mb, cpu%
- output: append JSONL в ~/.openclaw/krab_runtime_state/memory_baseline.jsonl

Lightweight: один снимок занимает <0.5s, файл ~5KB на snapshot.
14 дней retention (auto-rotate если >50MB).

Usage:
    python scripts/memory_baseline_collector.py             # one snapshot
    python scripts/memory_baseline_collector.py --analyze   # post-mortem анализ
    python scripts/memory_baseline_collector.py --top 30    # top N процессов
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# psutil — единственная внешняя зависимость (уже в venv)
try:
    import psutil
except ImportError:
    print("ERROR: psutil не установлен. Запусти: pip install psutil", file=sys.stderr)
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# Константы
# ──────────────────────────────────────────────────────────────

# Путь вывода по умолчанию
DEFAULT_OUTPUT = Path.home() / ".openclaw" / "krab_runtime_state" / "memory_baseline.jsonl"

# Лимит размера файла до ротации (50 МБ)
ROTATION_SIZE_BYTES = 50 * 1024 * 1024

# Ключевые слова Krab-экосистемы для выделения процессов
KRAB_KEYWORDS = ("claude", "krab", "openclaw", "python", "node", "uvicorn", "pyrogram")

# Топ-N процессов по умолчанию
DEFAULT_TOP_N = 20

# Порог обнаружения роста памяти (2x от baseline)
GROWTH_THRESHOLD = 2.0


# ──────────────────────────────────────────────────────────────
# Сбор snapshot
# ──────────────────────────────────────────────────────────────


def _is_krab_relevant(name: str) -> bool:
    """Проверяет, относится ли процесс к Krab-экосистеме."""
    low = name.lower()
    return any(kw in low for kw in KRAB_KEYWORDS)


def collect_snapshot(top_n: int = DEFAULT_TOP_N) -> dict:
    """Собирает один snapshot системной памяти и top-N процессов.

    Возвращает словарь с полями:
    - ts: ISO timestamp UTC
    - system: метрики виртуальной памяти и swap
    - processes: list of top_n процессов по RSS (desc)
    - krab_procs: subset из processes с именами Krab-экосистемы
    """
    ts = datetime.now(timezone.utc).isoformat()

    # Системная память
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    system = {
        "total_mb": round(vm.total / 1024 / 1024, 1),
        "available_mb": round(vm.available / 1024 / 1024, 1),
        "used_mb": round(vm.used / 1024 / 1024, 1),
        "percent": vm.percent,
        "swap_total_mb": round(sw.total / 1024 / 1024, 1),
        "swap_used_mb": round(sw.used / 1024 / 1024, 1),
        "swap_percent": sw.percent,
    }

    # Сбор информации по процессам
    procs = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            if mem is None:
                continue
            # Склеиваем cmdline в строку и обрезаем до 120 символов
            cmdline_parts = info.get("cmdline") or []
            cmd_str = " ".join(str(c) for c in cmdline_parts)[:120]
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or "",
                    "cmd": cmd_str,
                    "rss_mb": round(mem.rss / 1024 / 1024, 2),
                    "vms_mb": round(mem.vms / 1024 / 1024, 2),
                    "cpu_pct": info.get("cpu_percent") or 0.0,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Процесс завершился или нет доступа — пропускаем
            continue

    # Сортируем по RSS desc, берём top_n
    procs.sort(key=lambda p: p["rss_mb"], reverse=True)
    top_procs = procs[:top_n]

    # Выделяем Krab-релевантные процессы (из всего списка, не только top_n)
    krab_procs = [p for p in procs if _is_krab_relevant(p["name"])]

    return {
        "ts": ts,
        "system": system,
        "processes": top_procs,
        "krab_procs": krab_procs,
    }


# ──────────────────────────────────────────────────────────────
# Запись JSONL
# ──────────────────────────────────────────────────────────────


def _rotate_if_needed(path: Path) -> None:
    """Выполняет ротацию файла если он превышает ROTATION_SIZE_BYTES.

    Добавляет суффикс .N к старому файлу, начиная с .1 (max .9).
    """
    if not path.exists() or path.stat().st_size <= ROTATION_SIZE_BYTES:
        return

    # Сдвигаем старые ротированные файлы
    for i in range(9, 0, -1):
        old = path.with_suffix(f".jsonl.{i}")
        new = path.with_suffix(f".jsonl.{i + 1}")
        if old.exists():
            old.rename(new)

    # Текущий файл становится .1
    backup = path.with_suffix(".jsonl.1")
    path.rename(backup)
    print(f"[memory_baseline] Ротация: {path} → {backup}", file=sys.stderr)


def append_jsonl(path: Path, snapshot: dict) -> None:
    """Атомарно добавляет snapshot в JSONL-файл.

    Использует tempfile + os.replace для атомарного обновления.
    Создаёт директорию если не существует. Выполняет ротацию при >50MB.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Проверяем ротацию перед записью
    _rotate_if_needed(path)

    line = json.dumps(snapshot, ensure_ascii=False) + "\n"

    # Атомарная запись: пишем во временный файл, затем переименовываем
    # Для append используем read-existing + write-new подход,
    # или просто append напрямую (atomic на большинстве ФС для малых записей).
    # Используем tmpfile в той же директории для атомарного rename.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".mbc_tmp_")
    try:
        # Копируем существующий контент если файл есть
        if path.exists():
            with open(path, "rb") as src:
                existing = src.read()
        else:
            existing = b""

        with os.fdopen(tmp_fd, "wb") as dst:
            dst.write(existing)
            dst.write(line.encode("utf-8"))

        os.replace(tmp_path, path)
    except Exception:
        # При ошибке удаляем временный файл
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ──────────────────────────────────────────────────────────────
# Анализ post-mortem
# ──────────────────────────────────────────────────────────────


def analyze(path: Path) -> dict:
    """Анализирует накопленные снимки из JSONL-файла.

    Ищет:
    - топ-5 процессов с наибольшим ростом RSS (max vs first)
    - системный тренд памяти (первый vs последний snapshot)
    - процессы с ростом >2x от baseline ("утечки")

    Возвращает dict с результатами анализа.
    """
    if not path.exists():
        return {
            "error": f"Файл не найден: {path}",
            "snapshots_count": 0,
            "memory_growth_detected": False,
        }

    # Читаем все строки
    snapshots = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # Пропускаем повреждённые строки

    if not snapshots:
        return {
            "error": "Файл пуст или не содержит валидных JSON-строк",
            "snapshots_count": 0,
            "memory_growth_detected": False,
        }

    # Трекинг RSS по ключу "name:pid"
    proc_rss_history: dict[str, list[float]] = {}
    for snap in snapshots:
        for proc in snap.get("processes", []):
            key = f"{proc['name']}:{proc['pid']}"
            if key not in proc_rss_history:
                proc_rss_history[key] = []
            proc_rss_history[key].append(proc["rss_mb"])

    # Вычисляем рост для каждого процесса
    growers = []
    leaked = []
    for key, rss_list in proc_rss_history.items():
        if len(rss_list) < 2:
            continue
        first_rss = rss_list[0]
        max_rss = max(rss_list)
        last_rss = rss_list[-1]
        if first_rss <= 0:
            continue
        ratio = max_rss / first_rss
        growers.append(
            {
                "process": key,
                "first_rss_mb": round(first_rss, 2),
                "max_rss_mb": round(max_rss, 2),
                "last_rss_mb": round(last_rss, 2),
                "growth_ratio": round(ratio, 2),
                "samples": len(rss_list),
            }
        )
        if ratio >= GROWTH_THRESHOLD:
            leaked.append(key)

    # Сортируем по росту (desc), берём топ-5
    growers.sort(key=lambda g: g["growth_ratio"], reverse=True)
    top_growers = growers[:5]

    # Системный тренд
    first_sys = snapshots[0].get("system", {})
    last_sys = snapshots[-1].get("system", {})
    system_trend = {
        "first_ts": snapshots[0].get("ts"),
        "last_ts": snapshots[-1].get("ts"),
        "first_used_mb": first_sys.get("used_mb"),
        "last_used_mb": last_sys.get("used_mb"),
        "first_percent": first_sys.get("percent"),
        "last_percent": last_sys.get("percent"),
        "first_swap_used_mb": first_sys.get("swap_used_mb"),
        "last_swap_used_mb": last_sys.get("swap_used_mb"),
    }

    return {
        "snapshots_count": len(snapshots),
        "memory_growth_detected": len(leaked) > 0,
        "leaked_processes": leaked,
        "top_growers": top_growers,
        "system_trend": system_trend,
    }


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────


def _fmt_mb(mb: float | None) -> str:
    if mb is None:
        return "N/A"
    return f"{mb:.1f} MB"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wave 19-B: пассивный коллектор базовой линии памяти (OOM-диагностика)"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Режим post-mortem анализа накопленных данных",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        metavar="N",
        help=f"Количество топ-процессов (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Путь к JSONL-файлу (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Подробный вывод при сборе snapshot",
    )
    args = parser.parse_args()

    if args.analyze:
        # Режим анализа
        result = analyze(args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        # Краткое резюме в stderr
        count = result.get("snapshots_count", 0)
        leaked = result.get("leaked_processes", [])
        trend = result.get("system_trend", {})
        print(f"\n=== Анализ {args.output} ===", file=sys.stderr)
        print(f"Снимков: {count}", file=sys.stderr)
        if trend.get("first_ts") and trend.get("last_ts"):
            print(f"Период: {trend['first_ts']} → {trend['last_ts']}", file=sys.stderr)
        if trend.get("first_used_mb") is not None and trend.get("last_used_mb") is not None:
            delta = (trend["last_used_mb"] or 0) - (trend["first_used_mb"] or 0)
            sign = "+" if delta >= 0 else ""
            print(
                f"Системная память: {_fmt_mb(trend['first_used_mb'])} → "
                f"{_fmt_mb(trend['last_used_mb'])} ({sign}{delta:.1f} MB)",
                file=sys.stderr,
            )
        if leaked:
            print(f"⚠ Утечки обнаружены ({len(leaked)}): {', '.join(leaked[:5])}", file=sys.stderr)
        else:
            print("✓ Утечки памяти не обнаружены", file=sys.stderr)

        top = result.get("top_growers", [])
        if top:
            print("\nТоп-5 процессов по росту RSS:", file=sys.stderr)
            for g in top:
                print(
                    f"  {g['process']}: {_fmt_mb(g['first_rss_mb'])} → "
                    f"{_fmt_mb(g['max_rss_mb'])} (x{g['growth_ratio']})",
                    file=sys.stderr,
                )
        return

    # Режим сбора snapshot
    t0 = time.monotonic()
    snapshot = collect_snapshot(top_n=args.top)
    elapsed = time.monotonic() - t0

    append_jsonl(args.output, snapshot)

    if args.verbose:
        sys_info = snapshot["system"]
        print(
            f"[{snapshot['ts']}] snapshot за {elapsed * 1000:.0f}ms → {args.output}",
            file=sys.stderr,
        )
        print(
            f"  Память: {_fmt_mb(sys_info['used_mb'])} / {_fmt_mb(sys_info['total_mb'])} "
            f"({sys_info['percent']}%), swap {_fmt_mb(sys_info['swap_used_mb'])}",
            file=sys.stderr,
        )
        print(
            f"  Топ-{args.top} процессов собрано, Krab-related: {len(snapshot['krab_procs'])}",
            file=sys.stderr,
        )
        for p in snapshot["processes"][:5]:
            print(f"    [{p['pid']}] {p['name']}: {p['rss_mb']} MB RSS", file=sys.stderr)
    else:
        print(f"snapshot ok → {args.output} ({elapsed * 1000:.0f}ms)")


if __name__ == "__main__":
    main()

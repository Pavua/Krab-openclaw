"""Generate docs/SKILLS.md — inventory of all skills, commands, capabilities
with their stage (experimental/beta/production), category, and grading.

Usage: venv/bin/python scripts/build_skill_manifest.py [--output docs/SKILLS.md]
"""

from __future__ import annotations

import argparse
import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

# Убеждаемся, что src/ в пути для импорта
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))


# ---------------------------------------------------------------------------
# Сборщики
# ---------------------------------------------------------------------------


def collect_skill_modules() -> list[dict]:
    """Сканирует src/skills/*.py — имя файла + первая строка docstring."""
    skills_dir = _ROOT / "src" / "skills"
    results: list[dict] = []
    for path in sorted(skills_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = path.stem
        docstring = _extract_module_docstring(path)
        summary = _first_line(docstring) if docstring else "(нет описания)"
        results.append({"name": name, "file": f"src/skills/{path.name}", "summary": summary})
    return results


def collect_commands() -> list[dict]:
    """Импортирует CommandRegistry и возвращает список команд с метаданными."""
    try:
        from core.command_registry import _COMMANDS  # noqa: PLC0415
    except ImportError as exc:
        # Упрощённый fallback — парсим файл напрямую через AST
        return _collect_commands_ast_fallback(exc)

    results: list[dict] = []
    for cmd in _COMMANDS:
        results.append(
            {
                "name": cmd.name,
                "category": cmd.category,
                "description": cmd.description,
                "usage": cmd.usage,
                "owner_only": cmd.owner_only,
                "aliases": list(cmd.aliases),
                "stage": cmd.stage,
            }
        )
    return results


def _collect_commands_ast_fallback(original_exc: Exception) -> list[dict]:
    """Парсит command_registry.py через AST без импорта — fallback при ImportError."""
    registry_path = _ROOT / "src" / "core" / "command_registry.py"
    try:
        tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "CommandInfo"):
            continue
        kw = {k.arg: _ast_const(k.value) for k in node.keywords if k.arg is not None}
        results.append(
            {
                "name": kw.get("name", ""),
                "category": kw.get("category", ""),
                "description": kw.get("description", ""),
                "usage": kw.get("usage", ""),
                "owner_only": bool(kw.get("owner_only", False)),
                "aliases": [],
                "stage": kw.get("stage", "production"),
            }
        )
    return results


def collect_capabilities() -> list[dict]:
    """Импортирует capability_registry и возвращает role × flag матрицу."""
    try:
        from core.capability_registry import _ROLE_CAPABILITIES  # noqa: PLC0415

        return [
            {"role": role, "capabilities": dict(caps)} for role, caps in _ROLE_CAPABILITIES.items()
        ]
    except ImportError:
        return _collect_capabilities_ast_fallback()


def _collect_capabilities_ast_fallback() -> list[dict]:
    """Парсит capability_registry.py через AST без импорта.

    Ключи _ROLE_CAPABILITIES — это AccessLevel.OWNER.value и т.д.
    Мы резолвим их через маппинг имён констант → строки.
    """
    cap_path = _ROOT / "src" / "core" / "capability_registry.py"
    try:
        tree = ast.parse(cap_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    # Подбираем маппинг AccessLevel.<NAME>.value → строку из enum-определения.
    # AccessLevel — это Enum; смотрим на присваивания вида `OWNER = "owner"`.
    access_level_map: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AccessLevel":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name):
                            val = _ast_const(stmt.value)
                            if val is not None:
                                access_level_map[t.id] = str(val)

    # Если у нас нет маппинга из самого файла, используем статические значения.
    if not access_level_map:
        access_level_map = {
            "OWNER": "owner",
            "FULL": "full",
            "PARTIAL": "partial",
            "GUEST": "guest",
        }

    def _resolve_role_key(node: ast.expr) -> str | None:
        """AccessLevel.OWNER.value → 'owner'."""
        # ast.Attribute: node.value = Attribute(value=Name('AccessLevel'), attr='OWNER'), attr='value'
        if isinstance(node, ast.Attribute) and node.attr == "value":
            inner = node.value
            if isinstance(inner, ast.Attribute):
                return access_level_map.get(inner.attr)
        # Простая строка — тоже принимаем
        return _ast_const(node)  # type: ignore[return-value]

    # Ищем dict_node — значение _ROLE_CAPABILITIES (AnnAssign или Assign)
    dict_node: ast.Dict | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            t = node.target
            if isinstance(t, ast.Name) and t.id == "_ROLE_CAPABILITIES":
                if isinstance(node.value, ast.Dict):
                    dict_node = node.value
                    break
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_ROLE_CAPABILITIES":
                    if isinstance(node.value, ast.Dict):
                        dict_node = node.value
                    break
            if dict_node is not None:
                break

    if dict_node is None:
        return []

    results: list[dict] = []
    for role_node, caps_node in zip(dict_node.keys, dict_node.values):
        if role_node is None:
            continue
        role = _resolve_role_key(role_node)
        if role is None:
            continue
        if not isinstance(caps_node, ast.Dict):
            continue
        caps: dict[str, bool] = {}
        for k, v in zip(caps_node.keys, caps_node.values):
            key = _ast_const(k)
            val = _ast_const(v)
            if key is not None:
                caps[str(key)] = bool(val)
        results.append({"role": role, "capabilities": caps})
    return results


# ---------------------------------------------------------------------------
# Рендерер Markdown
# ---------------------------------------------------------------------------

_STAGE_BADGE: dict[str, str] = {
    "production": "🟢 production",
    "beta": "🟡 beta",
    "experimental": "🔴 experimental",
}


def render_markdown(
    skills: list[dict],
    commands: list[dict],
    capabilities: list[dict],
) -> str:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines += [
        f"<!-- AUTO-GENERATED — do not edit manually. Regenerate: venv/bin/python scripts/build_skill_manifest.py -->",
        f"# Krab Skill Manifest",
        f"",
        f"> Generated: **{now}**",
        f"",
    ]

    # ── Skills ──────────────────────────────────────────────────────────────
    lines += ["## Skills", "", "Модули в `src/skills/` — специализированные интеграции.", ""]
    if skills:
        lines += ["| Module | File | Description |", "|--------|------|-------------|"]
        for s in skills:
            lines.append(f"| `{s['name']}` | `{s['file']}` | {s['summary']} |")
    else:
        lines.append("_(нет модулей)_")
    lines.append("")

    # ── Commands ─────────────────────────────────────────────────────────────
    lines += [
        "## Commands",
        "",
        "Команды из `CommandRegistry` (`src/core/command_registry.py`).",
        "",
    ]

    # Группируем по категории
    categories: dict[str, list[dict]] = {}
    for cmd in commands:
        cat = cmd.get("category", "other")
        categories.setdefault(cat, []).append(cmd)

    for cat in sorted(categories):
        lines += [f"### {cat}", ""]
        lines += [
            "| Command | Stage | Owner-only | Description | Usage |",
            "|---------|-------|------------|-------------|-------|",
        ]
        for cmd in sorted(categories[cat], key=lambda c: c["name"]):
            stage_label = _STAGE_BADGE.get(cmd.get("stage", "production"), cmd.get("stage", ""))
            owner = "✓" if cmd.get("owner_only") else ""
            desc = cmd.get("description", "").replace("|", "\\|")
            usage = cmd.get("usage", "").replace("|", "\\|")
            lines.append(f"| `!{cmd['name']}` | {stage_label} | {owner} | {desc} | `{usage}` |")
        lines.append("")

    # ── Capabilities ─────────────────────────────────────────────────────────
    lines += [
        "## Capabilities",
        "",
        "Role × capability matrix из `src/core/capability_registry.py`.",
        "",
    ]

    if capabilities:
        all_caps = sorted({cap for role_data in capabilities for cap in role_data["capabilities"]})
        roles = [r["role"] for r in capabilities]

        header = "| Capability | " + " | ".join(f"`{r}`" for r in roles) + " |"
        sep = "|------------|" + "|".join(["-----"] * len(roles)) + "|"
        lines += [header, sep]

        for cap in all_caps:
            row_parts = []
            for role_data in capabilities:
                val = role_data["capabilities"].get(cap)
                row_parts.append("✓" if val else "✗")
            lines.append(f"| `{cap}` | " + " | ".join(row_parts) + " |")
        lines.append("")
    else:
        lines.append("_(данные недоступны)_\n")

    # ── Footer ───────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        f"*Сгенерировано {now}. Команда регенерации:*",
        "",
        "```bash",
        "venv/bin/python scripts/build_skill_manifest.py",
        "```",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def _extract_module_docstring(path: Path) -> str | None:
    """Читает первый строчный docstring модуля через AST."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return ast.get_docstring(tree)
    except Exception:  # noqa: BLE001
        return None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return text.strip()


def _ast_const(node: ast.expr | None) -> object:
    """Безопасно извлекает константу из AST-узла."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.NameConstant):  # Python <3.8 fallback
        return node.value  # type: ignore[attr-defined]
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Генерирует docs/SKILLS.md — инвентарь skills/commands/capabilities."
    )
    parser.add_argument("--output", default="docs/SKILLS.md", help="Путь к выходному файлу")
    args = parser.parse_args()

    output_path = Path(args.output)

    print("Сбор skill modules...")
    skills = collect_skill_modules()
    print(f"  → {len(skills)} модулей")

    print("Сбор commands из CommandRegistry...")
    commands = collect_commands()
    print(f"  → {len(commands)} команд")

    print("Сбор capabilities...")
    capabilities = collect_capabilities()
    print(f"  → {len(capabilities)} ролей")

    print(f"Рендеринг {output_path}...")
    content = render_markdown(skills, commands, capabilities)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    lines = content.count("\n")
    print(f"Готово: {output_path} ({lines} строк)")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
R24 Smoke Orchestrator.
Координирует запуск тестов и генерирует финальный отчет MD с маскировкой секретов.
"""

import subprocess
import os
import sys
import re
import json
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(ROOT, "output", "reports", "R24_SMOKE_REPORT.md")

# Шаблоны для маскировки секретов
SECRETS_PATTERNS = [
    (r"AIzaSy[a-zA-Z0-9_-]{33}", "GEMINI_KEY_MASKED"),
    (r"oc-[a-zA-Z0-9]{20,}", "OPENCLAW_KEY_MASKED"),
    (r"[0-9]{10}:[a-zA-Z0-9_-]{35}", "TELEGRAM_TOKEN_MASKED"),
    (r"\"api_key\":\s*\"[^\"]+\"", "\"api_key\": \"***MASKED***\""),
    (r"\"OPENCLAW_API_KEY\":\s*\"[^\"]+\"", "\"OPENCLAW_API_KEY\": \"***MASKED***\""),
]

def mask_text(text):
    masked = text
    for pattern, replacement in SECRETS_PATTERNS:
        masked = re.sub(pattern, replacement, masked)
    return masked

def run_script(script_name):
    script_path = os.path.join(ROOT, "scripts", script_name)
    python_bin = sys.executable
    print(f"🚀 Запуск {script_name}...")
    try:
        result = subprocess.run(
            [python_bin, script_path],
            capture_output=True,
            text=True,
            check=False
        )
        return {
            "name": script_name,
            "ok": result.returncode == 0,
            "stdout": mask_text(result.stdout),
            "stderr": mask_text(result.stderr),
            "exit_code": result.returncode
        }
    except Exception as e:
        return {
            "name": script_name,
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1
        }

def main():
    print("🛠️ Начинаю цикл проверок R24...")
    
    scripts = [
        "routing_smoke.py",
        "cloud_tier_smoke.py",
        "runtime_snapshot.py"
    ]
    
    all_results = []
    for s in scripts:
        all_results.append(run_script(s))
        
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(f"# R24 SMOKE REPORT\n")
        f.write(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        overall_ok = all(r["ok"] for r in all_results)
        status_emoji = "✅ PASS" if overall_ok else "❌ FAIL"
        f.write(f"## Overall Status: {status_emoji}\n\n")
        
        f.write("### Summary\n")
        for r in all_results:
            res_str = "OK" if r["ok"] else "FAILED"
            f.write(f"- **{r['name']}**: {res_str}\n")
            
        f.write("\n### Details\n")
        for r in all_results:
            f.write(f"#### {r['name']}\n")
            f.write(f"Exit Code: {r['exit_code']}\n")
            if r["stdout"]:
                f.write(f"**Stdout:**\n```\n{r['stdout']}\n```\n")
            if r["stderr"]:
                f.write(f"**Stderr:**\n```\n{r['stderr']}\n```\n")
            f.write("---\n")
            
    print(f"✅ Отчет создан: {REPORT_PATH}")
    return 0 if overall_ok else 1

if __name__ == "__main__":
    sys.exit(main())

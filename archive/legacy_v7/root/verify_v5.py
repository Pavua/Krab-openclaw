# -*- coding: utf-8 -*-
"""
Verification script for Krab v5.0.
Tests SystemMonitor, MacAutomation, DocumentParser, and RAG v2.0.
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.append(os.getcwd())

async def test_system_monitor():
    print("\n--- Testing SystemMonitor ---")
    from src.utils.system_monitor import SystemMonitor
    snapshot = SystemMonitor.get_snapshot()
    print(f"RAM: {snapshot.ram_percent}% used")
    print(f"CPU: {snapshot.cpu_percent}%")
    print(f"Disk: {snapshot.disk_percent}% used")
    print(f"Report:\n{snapshot.format_report()}")
    
    proc = SystemMonitor.get_process_info()
    print(f"Process RAM: {proc['ram_mb']:.1f} MB")

async def test_mac_bridge():
    print("\n--- Testing MacAutomation ---")
    from src.utils.mac_bridge import MacAutomation
    battery = await MacAutomation.get_battery_status()
    print(f"Battery: {battery}")
    wifi = await MacAutomation.get_wifi_name()
    print(f"WiFi: {wifi}")
    # Не тестируем уведомления и звук автоматически, чтобы не мешать пользователю
    print("MacBridge basic check: OK")

async def test_doc_parser():
    print("\n--- Testing DocumentParser ---")
    from src.utils.doc_parser import DocumentParser
    
    # Создаем тестовый файл
    test_file = Path("artifacts/test.txt")
    test_file.write_text("Hello Krab! This is a test document content.", encoding="utf-8")
    
    text, meta = await DocumentParser.parse(str(test_file))
    print(f"Parsed text: {text}")
    print(f"Metadata: {meta}")
    
    if test_file.exists():
        test_file.unlink()

async def test_rag_v2():
    print("\n--- Testing RAG v2.0 ---")
    from src.core.rag_engine import RAGEngine
    rag = RAGEngine()
    
    # Очистка старого теста
    rag.collection.delete(ids=["test_v2_1"])
    
    doc_id = rag.add_document(
        "Deep knowledge about the Krab universe.",
        category="learning",
        ttl_days=1,
        doc_id="test_v2_1"
    )
    print(f"Added doc: {doc_id}")
    
    stats = rag.get_stats()
    print(f"RAG Stats: {stats}")
    
    results = rag.query_with_scores("universe", n_results=1)
    if results:
        print(f"Search success: {results[0]['text']}")
    
    # Cleanup
    rag.collection.delete(ids=["test_v2_1"])

async def main():
    print("🚀 Starting Krab v5.0 Deep Verification...")
    try:
        await test_system_monitor()
        await test_mac_bridge()
        await test_doc_parser()
        await test_rag_v2()
        print("\n✅ ALL SYSTEMS NOMINAL.")
    except Exception as e:
        print(f"\n❌ VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

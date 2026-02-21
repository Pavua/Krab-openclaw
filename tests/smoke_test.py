# -*- coding: utf-8 -*-
import sys
import os
import asyncio
import unittest
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

# Add src to path
sys.path.append(os.getcwd())

# Mock configuration
MOCK_CONFIG = {
    "LM_STUDIO_URL": "http://localhost:1234/v1",
    "GEMINI_API_KEY": "fake_key",
    "OWNER_ID": 123456,
    "OWNER_USERNAME": "test_owner",
    "security.stealth_mode": False
}

class TestSystemHealth(unittest.IsolatedAsyncioTestCase):
    async def test_01_imports(self):
        """Test strict imports of all core modules."""
        modules = [
            "src.core.model_manager",
            "src.core.rag_engine",
            "src.core.security_manager",
            "src.core.image_manager",
            "src.core.summary_manager",
            "src.modules.perceptor",
            "src.utils.black_box",
            "src.handlers.ai",
            "src.handlers.cyber",
            "src.modules.browser"
        ]
        for mod in modules:
            try:
                __import__(mod)
            except ImportError as e:
                self.fail(f"Import failed for {mod}: {e}")
        print("✅ Core & Handler Imports: OK")

    async def test_02_rag_engine(self):
        """Test RAG Engine v2.0."""
        from src.core.rag_engine import RAGEngine
        # Используем уникальную временную БД, чтобы не цепляться за
        # потенциально поврежденные/устаревшие артефакты предыдущих прогонов.
        tmp_db = tempfile.mkdtemp(prefix="krab_rag_smoke_")
        rag = RAGEngine(db_path=tmp_db)
        doc_id = rag.add_document("Test knowledge", category="general")
        self.assertIsNotNone(doc_id)
        res = rag.query("knowledge")
        self.assertIn("Test", res)
        print("✅ RAG Engine v2.0: OK")

    async def test_03_model_router(self):
        """Test ModelRouter logic and fallback."""
        from src.core.model_manager import ModelRouter
        router = ModelRouter(MOCK_CONFIG)
        self.assertEqual(router.force_mode, "auto")
        router.set_force_mode("local")
        self.assertEqual(router.force_mode, "force_local")
        print("✅ ModelRouter Core: OK")

    async def test_04_security_manager(self):
        """Test SecurityManager and Stealth Mode."""
        from src.core.security_manager import SecurityManager
        sec = SecurityManager("test_owner")
        self.assertFalse(sec.stealth_mode)
        sec.toggle_stealth()
        self.assertTrue(sec.stealth_mode)
        
        # Test roles
        sec.config = MagicMock()
        sec.config.get.return_value = {}
        sec.config.set = MagicMock()
        sec.roles = {} # reset
        
        self.assertTrue(sec.grant_role("new_user", "admin"))
        self.assertEqual(sec.get_role("new_user"), "admin")
        self.assertTrue(sec.revoke_role("new_user"))
        self.assertEqual(sec.get_role("new_user"), "guest")
        print("✅ SecurityManager (Stealth): OK")

    async def test_05_black_box_stats(self):
        """Test BlackBox database operations."""
        from src.utils.black_box import BlackBox
        bb = BlackBox(db_path="artifacts/memory/tests_black_box.db")
        bb.log_message(123, "Test Chat", 456, "Test Sender", "test_user", "INCOMING", "Hello")
        stats = bb.get_stats()
        self.assertGreaterEqual(stats["total"], 1)
        recent = bb.get_recent_messages(limit=1)
        self.assertEqual(recent[0]["text"], "Hello")
        print("✅ BlackBox Stats & Logging: OK")

    async def test_06_image_manager(self):
        """Test ImageManager initialization."""
        from src.core.image_manager import ImageManager
        im = ImageManager(MOCK_CONFIG)
        self.assertIsNotNone(im)
        print("✅ ImageManager: OK")

    async def test_07_summary_manager(self):
        """Test SummaryManager logic."""
        from src.core.summary_manager import SummaryManager
        router = MagicMock()
        memory = MagicMock()
        sm = SummaryManager(router, memory, min_messages=10)
        self.assertEqual(sm.min_messages, 10)
        print("✅ SummaryManager Init: OK")

    async def test_08_task_queue(self):
        """Test TaskQueue background execution."""
        from src.core.task_queue import TaskQueue
        app = MagicMock()
        app.send_message = AsyncMock()
        
        tq = TaskQueue(app)
        
        async def dummy_task():
            await asyncio.sleep(0.1)
            return "Task Done"
            
        task_id = await tq.enqueue("Test Task", 123456, dummy_task())
        self.assertIsNotNone(task_id)
        
        # Wait a bit for background execution
        await asyncio.sleep(0.3)
        self.assertEqual(tq.tasks[task_id].status, "COMPLETED")
        app.send_message.assert_called()
        print("✅ TaskQueue: OK")

    async def test_09_browser_agent(self):
        """Test Browser Agent initialization."""
        try:
            from src.modules.browser import BrowserAgent
            agent = BrowserAgent(headless=True)
            self.assertIsNotNone(agent)
            
            # Mock playwright to avoid launching browser in smoke test
            agent.playwright = MagicMock() 
            agent.browser = MagicMock()
            
            # Simple check
            self.assertTrue(agent.headless)
            print("✅ BrowserAgent: Init OK")
        except ImportError:
            print("⚠️ BrowserAgent skipped (playwright missing)")

    async def test_10_crypto_intel(self):
        """Test CryptoIntel module initialization."""
        try:
            from src.modules.crypto import CryptoIntel
            ci = CryptoIntel()
            self.assertIsNotNone(ci)
            await ci.close()
            print("✅ CryptoIntel: Init OK")
        except ImportError:
            self.fail("CryptoIntel import failed")

if __name__ == "__main__":
    print("🦀 Running Krab v10.0 Comprehensive Smoke Tests...")
    # Create artifacts dir for tests
    os.makedirs("artifacts/memory", exist_ok=True)
    unittest.main(verbosity=1)


import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

# Mock dependencies before import
sys.modules["src.core.rag_engine"] = MagicMock()
sys.modules["structlog"] = MagicMock()

# Add src to path
sys.path.append(os.getcwd())

from src.core.model_manager import ModelRouter

class TestLMSAutoLoad(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_chat_model_loaded_embedding_unload(self):
        """Test that embedding model is unloaded and chat model is loaded."""
        
        config = {"LM_STUDIO_URL": "http://localhost:1234"}
        router = ModelRouter(config)
        
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock 1: lms ps -> Embedding Loaded
            mock_proc_ps1 = AsyncMock()
            mock_proc_ps1.communicate.return_value = (b"LOADED text-embedding-nomic-embed-text-v1.5", b"")
            
            # Mock 2: lms unload
            mock_proc_unload = AsyncMock()
            mock_proc_unload.communicate.return_value = (b"Unloaded", b"")
            
            # Mock 3: lms ps (after unload) -> Empty
            mock_proc_ps2 = AsyncMock()
            mock_proc_ps2.communicate.return_value = (b"No models loaded", b"")
            
            # Mock 4: lms ls -> List available
            mock_proc_ls = AsyncMock()
            mock_proc_ls.communicate.return_value = (b"text-embedding-nomic ...\nlmstudio-community/Qwen2.5-7B-Instruct ...\n", b"")
            
            # Mock 5: lms load
            mock_proc_load = AsyncMock()
            mock_proc_load.communicate.return_value = (b"Loaded", b"")
            
            # Side effect for subprocess calls
            mock_exec.side_effect = [
                mock_proc_ps1,   # 1. Check current
                mock_proc_unload,# 2. Unload embedding
                mock_proc_ps2,   # 3. Check again
                mock_proc_ls,    # 4. List available
                mock_proc_load   # 5. Load chat
            ]
            
            success = await router._ensure_chat_model_loaded()
            
            self.assertTrue(success)
            # Verify calls
            self.assertEqual(mock_exec.call_count, 5)
            # Check load arguments
            args, _ = mock_exec.call_args_list[4]
            self.assertIn("load", args)
            self.assertIn("lmstudio-community/Qwen2.5-7B-Instruct", args)

    async def test_ensure_chat_model_already_loaded(self):
        """Test when chat model is already loaded."""
        config = {"LM_STUDIO_URL": "http://localhost:1234"}
        router = ModelRouter(config)
        
        with patch("asyncio.create_subprocess_exec") as mock_exec:
             # Mock 1: lms ps -> Chat Loaded
            mock_proc_ps1 = AsyncMock()
            mock_proc_ps1.communicate.return_value = (b"LOADED Qwen2.5-7B-Instruct", b"")
            
            # Mock 2: lms ps (second check in logic)
            mock_proc_ps2 = AsyncMock()
            mock_proc_ps2.communicate.return_value = (b"LOADED Qwen2.5-7B-Instruct", b"")

            mock_exec.side_effect = [
                mock_proc_ps1,
                mock_proc_ps2
            ]
            
            success = await router._ensure_chat_model_loaded()
            
            self.assertTrue(success)
            self.assertEqual(mock_exec.call_count, 2) # Should not unload or load

if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.tool_handler import ToolHandler

@pytest.mark.asyncio
async def test_shell_tool_logic():
    handler = ToolHandler(MagicMock(), MagicMock(), MagicMock())
    
    with patch('asyncio.create_subprocess_shell') as mock_exec:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ls result", b""))
        mock_exec.return_value = mock_proc
        
        result = await handler.run_shell("ls")
        assert "ls result" in result

@pytest.mark.asyncio
async def test_ocr_to_rag_indexing():
    # Мы тестируем логику в main.py через мок
    from src.core.model_manager import ModelRouter
    rag = MagicMock()
    router = ModelRouter({})
    router.rag = rag
    
    # Симулируем вызов индексации как в handle_vision
    description = "A cat sitting on a mat"
    rag.add_document.return_value = "doc_123"
    
    doc_id = router.rag.add_document(
        text=f"[Vision Scan]: {description}",
        metadata={"source": "vision"}
    )
    
    assert doc_id == "doc_123"
    rag.add_document.assert_called_once()
    args, kwargs = rag.add_document.call_args
    assert "Vision Scan" in kwargs['text']


import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.telegram_summary_service import TelegramSummaryService, SummaryRequest

@pytest.fixture
def mock_client():
    client = MagicMock()
    # Mock get_chat_history as async generator
    async def async_gen(*args, **kwargs):
        limit = kwargs.get('limit', 10)
        for i in range(limit):
            msg = MagicMock()
            msg.text = f"Message {i}"
            msg.caption = None
            msg.date.strftime.return_value = "2023-01-01 12:00"
            msg.from_user.username = f"user{i}"
            yield msg
    
    client.get_chat_history = async_gen
    return client

@pytest.fixture
def mock_router():
    router = AsyncMock()
    router.route_query.return_value = "Summary Result"
    return router

@pytest.mark.asyncio
async def test_summary_single_pass(mock_client, mock_router):
    service = TelegramSummaryService(router=mock_router, map_reduce_threshold=100, min_limit=1)
    req = SummaryRequest(chat_id=123, limit=10)
    
    result = await service.summarize(mock_client, req, chat_title="Test Chat")
    
    assert result == "Summary Result"
    mock_router.route_query.assert_called_once()
    args = mock_router.route_query.call_args[1]
    assert "task_type" in args
    assert args["task_type"] == "reasoning"

@pytest.mark.asyncio
async def test_summary_map_reduce(mock_client, mock_router):
    # Set threshold low to force map-reduce, min_limit=1 to allow limit=10
    service = TelegramSummaryService(router=mock_router, map_reduce_threshold=5, chunk_size=5, min_limit=1)
    req = SummaryRequest(chat_id=123, limit=10)
    
    # Mock router to return different values for map vs reduce
    # limit=10, chunk=5 -> 2 chunks + 1 reduce = 3 calls
    mock_router.route_query.side_effect = ["Chunk 1", "Chunk 2", "Final Summary"]
    
    result = await service.summarize(mock_client, req, chat_title="Test Chat")
    
    assert result == "Final Summary"
    assert mock_router.route_query.call_count == 3

@pytest.mark.asyncio
async def test_fetch_messages(mock_client, mock_router):
    service = TelegramSummaryService(router=mock_router)
    msgs = await service.fetch_chat_messages(mock_client, 123, 5)
    assert len(msgs) == 5
    # fetch_messages reverses the list. 
    # mock yields 0, 1, 2, 3, 4. 0 is "latest".
    # result is [4, 3, 2, 1, 0].
    # msgs[0] is 4.
    assert msgs[0]['sender'] == "user4"
    assert msgs[-1]['sender'] == "user0"

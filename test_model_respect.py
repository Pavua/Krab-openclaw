import asyncio
from src.openclaw_client import openclaw_client

async def main():
    # Проверим, что отдаст клиент
    stream = openclaw_client.send_message_stream(
        chat_id="test",
        user_message="Tell me a very short joke with 1 word.",
        model_id="google-antigravity/gemini-3-flash",
        is_first_message=True,
        has_photo=False
    )
    async for chunk in stream:
        print(chunk, end="", flush=True)
    print("\n---")
    
if __name__ == "__main__":
    asyncio.run(main())

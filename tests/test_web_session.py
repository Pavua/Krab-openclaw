import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))


import structlog

from web_session import web_manager

structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
    logger_factory=structlog.PrintLoggerFactory(),
)


async def test_web():
    print("🌍 Testing Web Session Manager...")
    try:
        await web_manager.start(headless=True)
        if web_manager.is_active:
            print("✅ Browser started successfully.")
            page = web_manager.page
            await page.goto("https://www.google.com")
            title = await page.title()
            print(f"📄 Page Title: {title}")
        else:
            print("❌ Browser failed to start.")

        await web_manager.stop()
        print("✅ Browser stopped.")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(test_web())

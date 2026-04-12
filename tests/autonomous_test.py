import asyncio
import logging
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))
from web_session import web_manager

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("health_check.log"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AutoTest")


async def run_cycle():
    logger.info("🚀 Starting Autonomous Health Check Cycle...")

    # 1. Start Browser (Headless for automation, but maybe Headful for debugging first)
    # User asked to "open page and check", implying they might want to see it or acceptable to be headless.
    # We'll use headless=False for the script, but user can change it.
    await web_manager.start(
        headless=False
    )  # Headful to see what happens as user requested "check via chrome"
    page = web_manager.page

    try:
        # 2. Open Telegram Web
        # 2. Open Telegram Web
        logger.info("🌍 Navigating to Telegram Web...")
        await page.goto("https://web.telegram.org/k/", timeout=60000)

        # 3. Check Login Status
        try:
            # Look for chat list or search bar
            logger.info("⏳ Waiting for chat list...")
            await page.wait_for_selector(".chat-list", timeout=30000)
            logger.info("✅ Login detected. Chat list found.")
        except:
            logger.warning("⚠️ Login NOT detected or page loading slow.")
            # Screenshot for debug
            await web_manager.take_screenshot("debug_login.png")
            logger.info("📸 Screenshot saved to debug_login.png")

            # Check if we are on login page
            if await page.query_selector(".login-header") or await page.query_selector("button"):
                logger.error(
                    "🛑 Action Required: Please use '!web login' to log in to Telegram Web first!"
                )
            return

        # 4. Find Saved Messages (Self)
        logger.info("🔍 Searching for 'Saved Messages'...")
        # Review: TG Web K class names might change.
        # Strategy: Search input field.
        search_input = page.locator("input.input-field-input")  # Common in K version
        if not await search_input.count():
            # Fallback selector
            search_input = page.locator(".input-search input")

        await search_input.click()
        await search_input.fill("Saved Messages")
        await asyncio.sleep(2)

        # Click first result
        # Usually checking for "Saved Messages" text
        await page.locator(".chat-list .chat-item").first.click()
        logger.info("📂 Opened Saved Messages.")

        # 5. Send Command
        msg = "!sysinfo"
        logger.info(f"📤 Sending command: {msg}")

        # TG Web K input field
        # .input-message-input
        input_box = page.locator(".input-message-input")
        await input_box.click()
        await input_box.fill(msg)
        await page.keyboard.press("Enter")

        # 6. Wait for Reply
        logger.info("⏳ Waiting for Krab response...")
        await asyncio.sleep(5)  # Wait for bot to reply

        # Read last message
        last_msg = page.locator(".message").last
        text = await last_msg.inner_text()

        logger.info(f"📥 Received: {text[:50]}...")

        # 7. Validate
        if "Krab System Info" in text or "RAM" in text:
            logger.info("✅ TEST PASSED: Bot is alive and responding.")
        else:
            logger.error("❌ TEST FAILED: Unexpected response.")

    except Exception as e:
        logger.error(f"❌ Error during cycle: {e}")
        await web_manager.take_screenshot("error_screenshot.png")

    finally:
        await web_manager.stop()
        logger.info("🏁 Cycle finished.")


if __name__ == "__main__":
    asyncio.run(run_cycle())

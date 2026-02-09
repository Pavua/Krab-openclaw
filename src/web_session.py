
import asyncio
import os
from playwright.async_api import async_playwright, BrowserContext, Page
from structlog import get_logger

logger = get_logger(__name__)

USER_DATA_DIR = os.path.join(os.getcwd(), "browser_data")
os.makedirs(USER_DATA_DIR, exist_ok=True)

class WebSessionManager:
    """
    Manages persistent browser sessions for Web Access (ChatGPT/Gemini).
    Uses Playwright with a persistent context to keep login cookies.
    """
    def __init__(self):
        self.playwright = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.is_active = False

    async def start(self, headless: bool = True):
        """Starts the browser session."""
        try:
            self.playwright = await async_playwright().start()
            # Launch persistent context
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.is_active = True
            logger.info("web_session_started", headless=headless)
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error("web_session_start_failed", error=str(e))

    async def stop(self):
        """Stops the browser session."""
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.is_active = False
        logger.info("web_session_stopped")

    async def chatgpt_query(self, prompt: str) -> str:
        """Sends a query to ChatGPT Web."""
        if not self.is_active:
            await self.start()
        
        try:
            page = self.page
            await page.goto("https://chatgpt.com/")
            
            # Wait for input (simplified selector, subject to change)
            # Need strict error handling here
            try:
                await page.wait_for_selector("#prompt-textarea", timeout=5000)
            except:
                return "❌ ChatGPT Access Failed. Please login manually first using `!web login`."

            await page.fill("#prompt-textarea", prompt)
            await page.keyboard.press("Enter")
            
            # Wait for response (very tricky dynamically)
            # For POC: wait 5s then grab last message
            await asyncio.sleep(5) 
            
            # Validating if still generating...
            # This is a very rough implementation
            responses = await page.locator("div[data-message-author-role='assistant']").all_inner_texts()
            if responses:
                return responses[-1]
            return "❌ No response found."

        except Exception as e:
            logger.error("chatgpt_error", error=str(e))
            return f"❌ Error: {str(e)}"

    async def take_screenshot(self, filename: str = "screenshot.png") -> str:
        """Takes a screenshot of the current page."""
        if not self.is_active or not self.page:
            return ""
        try:
            path = os.path.join(os.getcwd(), filename)
            await self.page.screenshot(path=path)
            return path
        except Exception as e:
            logger.error("screenshot_failed", error=str(e))
            return ""

    async def open_url(self, url: str):
        """Navigates to a URL."""
        if not self.is_active:
            await self.start(headless=False)
        if self.page:
            await self.page.goto(url)
            await self.page.bring_to_front()

    async def login_mode(self):
        """Starts browser in HEADFUL mode for manual login."""
        if self.is_active:
             # If already active but headless, we might need to restart or just use it?
             # For simplicity, if headless, restart headful.
             # If already headful, just return.
             pass 
             
        await self.stop()
        await self.start(headless=False)
        return "🌍 Browser opened. Please login to ChatGPT/Gemini manually inside the window."

web_manager = WebSessionManager()

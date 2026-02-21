
import asyncio
import os
from typing import Optional
from playwright.async_api import async_playwright, BrowserContext, Page
import structlog

logger = structlog.get_logger("SubscriptionPortal")

class SubscriptionPortal:
    """
    Experimental Module: Uses Browser Automation to access Gemini Advanced / ChatGPT Plus.
    Requires: 'playwright' installed & manual login via 'setup_browser.py'.
    """
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.user_data_dir = os.path.join(os.getcwd(), "data/browser_profile")
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

    async def start(self):
        """Initializes the browser context."""
        if not os.path.exists(self.user_data_dir):
            raise FileNotFoundError(f"Browser profile not found at {self.user_data_dir}. Run setup_browser.py first!")

        self.playwright = await async_playwright().start()
        
        # Use existing profile
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        logger.info("🌐 Subscription Browser Launched")

    async def query_gemini(self, prompt: str) -> str:
        """Sends a prompt to Gemini Web and returns the response."""
        if not self.context:
            await self.start()
        
        try:
            page = await self.context.new_page()
            await page.goto("https://gemini.google.com/app")
            
            # Wait for input box
            # Note: Selectors for Gemini might change. Need robust strategy.
            # Usually: div[contenteditable="true"] or aria-label="Enter a prompt here"
            input_selector = "div[contenteditable='true']"
            await page.wait_for_selector(input_selector, timeout=10000)
            
            # Type prompt
            await page.fill(input_selector, prompt)
            await page.press(input_selector, "Enter")
            
            # Wait for response completion
            # Heuristic: Wait for "stop generating" button to disappear OR specific element
            # This is tricky. Let's wait for network idle or specific time + selector stability
            await asyncio.sleep(2) # Initial wait
            
            # TODO: Improve response detection. For now, simple wait.
            # Real solution: observe DOM changes.
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(5) 
            
            # Extract last response
            # Strategy: Get all message containers, pick the last one that isn't the user's
            # Ищем последний ответ по нескольким DOM-стратегиям.
            last_response = await page.evaluate("""() => {
                const candidates = [
                    'message-content',
                    '[data-message-author-role=\"model\"]',
                    'div[data-message-id]'
                ];

                for (const selector of candidates) {
                    const nodes = document.querySelectorAll(selector);
                    if (nodes && nodes.length > 0) {
                        const text = (nodes[nodes.length - 1].innerText || '').trim();
                        if (text.length > 0) return text;
                    }
                }

                return 'Error: Could not extract response.';
            }""")
            
            await page.close()
            return last_response
            
        except Exception as e:
            logger.error(f"Browser Query Failed: {e}")
            return f"Browser Error: {e}"

    async def close(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


import asyncio
from playwright.async_api import async_playwright
import os

USER_DATA_DIR = os.path.join(os.getcwd(), "data/browser_profile")

async def setup():
    print("🦀 Krab Subscription Portal Setup")
    print(f"📂 Profile Path: {USER_DATA_DIR}")
    print("-----------------------------------")
    print("Launching browser... Please login to Gemini/ChatGPT manually.")
    
    async with async_playwright() as p:
        # Launch persistent context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False, # Must be visible for login
            args=["--disable-blink-features=AutomationControlled"] # Stealth-ish
        )
        
        page = await context.new_page()
        await page.goto("https://gemini.google.com/app")
        
        print("\n👇 ACTION REQUIRED:")
        print("1. Login to your Google Account in the opened browser.")
        print("2. Verify you can send a message in Gemini.")
        print("3. Return here and press ENTER to save & exit.")
        
        input()
        
        print("Saving state...")
        await context.close()
        print("✅ Setup Complete! Browser profile saved.")

if __name__ == "__main__":
    asyncio.run(setup())

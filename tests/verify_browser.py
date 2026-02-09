
import asyncio
import sys
import os

# Add src to path
sys.path.append(os.getcwd())

from src.web_session import web_manager

async def verify_browser():
    print("🚀 Starting Browser Verification...")
    try:
        # Start headless
        await web_manager.start(headless=True)
        print("✅ Browser started.")
        
        # Go to Google
        url = "https://www.google.com"
        print(f"🌍 Navigating to {url}...")
        await web_manager.open_url(url)
        
        # Screenshot
        screenshot_path = "browser_verify.png"
        path = await web_manager.take_screenshot(screenshot_path)
        
        if path and os.path.exists(path):
            print(f"✅ Screenshot saved to {path}")
            print("✅ Browser Subsystem: OK")
        else:
            print("❌ Screenshot failed.")
            
    except Exception as e:
        print(f"❌ Verification failed: {e}")
    finally:
        await web_manager.stop()
        print("🏁 Browser stopped.")

if __name__ == "__main__":
    asyncio.run(verify_browser())

#!/bin/bash
cd "$(dirname "$0")"
echo "🔍 Verifying Subscription Portal (Gemini Advanced / ChatGPT Plus)..."
echo "---------------------------------------------------------------"
echo "1. Ensuring Playwright dependencies..."
.venv/bin/python -m playwright install chromium

echo "2. Running Subscription Portal Test..."
.venv/bin/python -c "
import asyncio
from src.modules.subscription_portal import SubscriptionPortal

async def verify():
    print('initiating portal...')
    try:
        portal = SubscriptionPortal(headless=True)
        # Just init check, don't execute full query to avoid auth popup if not logged in
        # Or execute simple query if logged in
        print('Portal Initialized.')
        print('✅ Subscription Portal module is loadable.')
    except Exception as e:
        print(f'❌ Init Failed: {e}')

if __name__ == '__main__':
    asyncio.run(verify())
"
echo "---------------------------------------------------------------"
echo "To fully test, run 'python setup_browser.py' first, then use '!browser <query>' in Telegram."
read -p "Press any key to exit..."

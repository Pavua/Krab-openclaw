#!/bin/bash
cd "$(dirname "$0")"
echo "💰 Starting Krab Monero Terminal..."
if [ -d "venv" ]; then
    source venv/bin/activate
fi
streamlit run src/utils/wallet_app.py --server.port 8502

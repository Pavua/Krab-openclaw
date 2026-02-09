#!/bin/bash

echo "🚀 Initializing Nexus..."

# Check for .env
if [ ! -f .env ]; then
    echo "⚠️  .env file not found! Copying template if available or creating one."
    # In a real scenario, we might copy .env.example
fi

# Install dependencies (optional check)
if [ -f requirements.txt ]; then
    echo "📦 Checking dependencies..."
    pip install -r requirements.txt
fi

echo "🤖 Starting Agents..."
python main.py

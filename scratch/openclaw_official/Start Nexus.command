#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Nexus Userbot Bridge..."
echo "Ensure OpenClaw Gateway is ALREADY running!"
python3 nexus_bridge.py

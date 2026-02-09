#!/bin/bash
cd "$(dirname "$0")/.."
DIR="$(pwd)"

# Check if already running
if [ -f "nexus.pid" ]; then
    PID=$(cat nexus.pid)
    if ps -p $PID > /dev/null; then
        echo "Nexus is already running (PID: $PID)"
        exit 0
    fi
fi

# Run in background
nohup ./run.sh > nexus.log 2>&1 &
PID=$!
echo $PID > nexus.pid
echo "Nexus started (PID: $PID). Logs in nexus.log"

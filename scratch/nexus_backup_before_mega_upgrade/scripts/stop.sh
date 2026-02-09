#!/bin/bash
cd "$(dirname "$0")/.."
DIR="$(pwd)"

if [ -f "nexus.pid" ]; then
    PID=$(cat nexus.pid)
    if ps -p $PID > /dev/null; then
        echo "Stopping Nexus (PID: $PID)..."
        kill $PID
        rm nexus.pid
        echo "Nexus stopped."
    else
        echo "Nexus PID found but process is dead. Cleaning up."
        rm nexus.pid
    fi
else
    echo "Nexus is not running (no pid file)."
fi

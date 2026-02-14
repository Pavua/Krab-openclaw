#!/bin/zsh

# Krab Conflict Defense Script
# Checks for command collisions and duplicate handlers.

CDIR=$(dirname "$0")
PDIR=$(dirname "$CDIR")
LOG_FILE="$PDIR/artifacts/logs/conflict_defense.log"

mkdir -p "$(dirname "$LOG_FILE")"

echo "🛡 [$(date)] Running Krab Conflict Defense..." | tee -a "$LOG_FILE"

# 1. Check for duplicate command registrations in handlers/
echo "🔍 Checking for duplicate command prefixes..."
grep -r "filters.command(\"" "$PDIR/src/handlers" | awk -F'"' '{print $2}' | sort | uniq -c | grep -v " 1 " > "$PDIR/artifacts/tmp_conflicts.txt"

if [ -s "$PDIR/artifacts/tmp_conflicts.txt" ]; then
    echo "❌ CONFLICTS DETECTED:" | tee -a "$LOG_FILE"
    cat "$PDIR/artifacts/tmp_conflicts.txt" | tee -a "$LOG_FILE"
else
    echo "✅ No command collisions found in static analysis." | tee -a "$LOG_FILE"
fi

# 2. Check for overlapping message group priorities
echo "🔍 Checking message group priorities..."
grep -r "group=" "$PDIR/src/handlers" | tee -a "$LOG_FILE"

# 3. Clean up
rm -f "$PDIR/artifacts/tmp_conflicts.txt"

echo "🛡 Conflict Defense Complete."

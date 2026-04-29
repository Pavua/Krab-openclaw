#!/usr/bin/env bash
# Rotate Krab logs. Compresses logs > 50MB, deletes > 30 days old.
set -euo pipefail

LOG_DIR="$HOME/Antigravity_AGENTS/Краб/logs"
MAX_AGE_DAYS=30
MIN_SIZE_MB=50

cd "$LOG_DIR"

# Step 1: gzip large files (not currently open ones — Krab writes *.out.log *.err.log continuously)
# Only gzip rotated suffix files like *.1.log, or files older than 1 day that aren't latest
for f in *.log; do
    [ -f "$f" ] || continue
    # Skip live krab_launchd.* files if Krab running
    SIZE_MB=$(du -m "$f" | cut -f1)
    MTIME_DAYS=$(( ( $(date +%s) - $(stat -f %m "$f") ) / 86400 ))
    if [ "$MTIME_DAYS" -gt 1 ] && [ "$SIZE_MB" -gt "$MIN_SIZE_MB" ]; then
        gzip -f "$f"
        echo "[log_rotation] gzipped $f (${SIZE_MB}MB)"
    fi
done

# Step 2: delete old gzipped files
find "$LOG_DIR" -name "*.gz" -mtime +${MAX_AGE_DAYS} -print -delete

# Step 3: rotate live logs if they exceed 500MB (using logrotate-style copytruncate)
for live in krab_launchd.out.log krab_launchd.err.log; do
    [ -f "$live" ] || continue
    SIZE_MB=$(du -m "$live" | cut -f1)
    if [ "$SIZE_MB" -gt 500 ]; then
        cp "$live" "${live%.log}.$(date +%Y%m%d).log"
        : > "$live"  # truncate in place
        echo "[log_rotation] truncated live $live (was ${SIZE_MB}MB)"
    fi
done

TOTAL=$(du -sh "$LOG_DIR" | cut -f1)
echo "[log_rotation] done. Total log dir: $TOTAL"

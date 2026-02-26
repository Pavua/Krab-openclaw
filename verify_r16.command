#!/bin/bash
# R16 Verification Script

echo "🚀 Starting Sprint R16 Backend Stability Verification..."
cd "$(dirname "$0")"

# 1. SLA & Anti-Stuck Verification
echo "--- Checking TaskQueue SLA Aborts ---"
pytest tests/test_r16_queue_sla_abort.py -v

# 2. Cloud Tiered Fallback Verification
echo "--- Checking Cloud Tiered Fallback (Free -> Paid) ---"
pytest tests/test_r16_cloud_tier_fallback.py -v

echo "✅ R16 Verification Completed!"

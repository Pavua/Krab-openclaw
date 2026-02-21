#!/bin/bash
cd "$(dirname "$0")"
echo "🦀 Running Krab Smoke Tests..."
echo "--------------------------------"
.venv/bin/python tests/smoke_test.py
echo "--------------------------------"
read -p "Press any key to exit..."

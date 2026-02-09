#!/bin/bash
cd "$(dirname "$0")"
source ../../nexus/.venv/bin/activate 2>/dev/null || python3 -m venv venv && source venv/bin/activate
pip install -r requirements_voice.txt
python3 ear.py

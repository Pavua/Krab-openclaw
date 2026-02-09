
import os
import sys
import psutil
import requests
import time

def check_process(name):
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if name in " ".join(proc.info['cmdline'] or []):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False

def check_gateway():
    try:
        # Check if port 18789 is listening
        if os.system("lsof -i:18789 > /dev/null") == 0:
            return True
        return False
    except:
        return False

def check_file_lock(path):
    # Returns True if locked (used by lsof)
    return os.system(f"lsof {path} > /dev/null") == 0

print("🔍 AUTO-DIAGNOSIS STARTED...")

# 1. Gateway
if check_gateway():
    print("✅ Gateway: ONLINE (Port 18789)")
else:
    print("❌ Gateway: OFFLINE")

# 2. Bridge
if check_process("nexus_bridge/main.py"):
    print("✅ Bridge: RUNNING")
    if check_file_lock("openclaw_official/nexus_bridge/nexus_session.session"):
        print("   -> Database Locked (Normal if by Bridge)")
    else:
        print("   -> Database: FREE (Suspicious if Bridge is running)")
else:
    print("❌ Bridge: NOT RUNNING")

# 3. Ear UI
if check_process("nexus_bridge/ear_ui.py"):
    print("✅ Ear UI: RUNNING")
else:
    print("❌ Ear UI: NOT RUNNING")

# 4. Old Bot Check
# We assume 'old bot' might be a rogue python process
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    cmd = " ".join(proc.info['cmdline'] or [])
    if "python" in cmd and "nexus" in cmd and "ear_ui" not in cmd and "nexus_bridge/main.py" not in cmd:
        print(f"⚠️ SUSPICIOUS PROCESS: {cmd} (PID: {proc.info['pid']})")

print("🏁 DIAGNOSIS COMPLETE.")

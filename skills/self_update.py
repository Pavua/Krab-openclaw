
"""
Skill: Self-Updater
Description: Allows the bot to update itself via git pull.
Usage: Loaded by sys_developer role.
"""
import subprocess
import structlog

logger = structlog.get_logger(__name__)

def update_codebase():
    """Pulls the latest changes from git."""
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True)
        if result.returncode == 0:
            return f"✅ Update successful:\n{result.stdout}"
        else:
            return f"❌ Update failed:\n{result.stderr}"
    except Exception as e:
        return f"❌ Error executing update: {e}"

if __name__ == "__main__":
    print(update_codebase())

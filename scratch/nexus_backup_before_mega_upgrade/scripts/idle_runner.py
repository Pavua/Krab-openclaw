import psutil
import time
import subprocess
import logging
import os

# Configuration
IDLE_CPU_THRESHOLD = 15.0  # Percent
IDLE_DURATION_REQUIRED = 30  # Seconds
CHECK_INTERVAL = 10  # Seconds

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("IdleRunner")

def is_system_idle():
    cpu_usage = psutil.cpu_percent(interval=1)
    logger.debug(f"CPU: {cpu_usage}%")
    return cpu_usage < IDLE_CPU_THRESHOLD

def run_tasks():
    logger.info("💤 System is idle. Running background tasks...")
    
    # 1. Run Tests
    logger.info("🧪 Running Tests...")
    try:
        # Assume we are in nexus/scripts, go up to nexus
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(["pytest", "tests/"], cwd=cwd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("✅ All tests passed!")
        else:
            logger.warning("⚠️ Tests failed!")
            logger.warning(result.stderr)
            
    except Exception as e:
        logger.error(f"Failed to run tasks: {e}")

    logger.info("✅ Background tasks completed.")

def main():
    logger.info("👀 Monitoring system for idle state...")
    idle_streak = 0
    
    while True:
        if is_system_idle():
            idle_streak += CHECK_INTERVAL
            if idle_streak >= IDLE_DURATION_REQUIRED:
                run_tasks()
                # Sleep a long time after running tasks to avoid loop
                time.sleep(300) 
                idle_streak = 0
        else:
            idle_streak = 0
        
        time.sleep(CHECK_INTERVAL)

# if __name__ == "__main__":
#     main()


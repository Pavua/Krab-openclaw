"""
iMessage Integration - Отправка сообщений через AppleScript
"""
import subprocess
import structlog

logger = structlog.get_logger(__name__)

def send_imessage(target: str, message: str) -> bool:
    """
    Отправляет iMessage через AppleScript
    target: номер телефона или email
    """
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type is iMessage
        set targetBuddy to buddy "{target}" of targetService
        send "{message}" to targetBuddy
    end tell
    '''
    try:
        subprocess.run(['osascript', '-e', script], check=True)
        logger.info("imessage_sent", to=target)
        return True
    except Exception as e:
        logger.error("imessage_failed", error=str(e))
        return False

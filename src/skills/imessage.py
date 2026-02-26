"""
iMessage Integration - Отправка сообщений через AppleScript
"""
import asyncio
import structlog

logger = structlog.get_logger(__name__)

OSASCRIPT_TIMEOUT = 30.0


async def send_imessage(target: str, message: str) -> bool:
    """
    Отправляет iMessage через AppleScript (асинхронно).
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
        process = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=OSASCRIPT_TIMEOUT)
        if process.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"osascript exited {process.returncode}: {err}")
        logger.info("imessage_sent", to=target)
        return True
    except asyncio.TimeoutError as e:
        logger.error("imessage_timeout", error=str(e))
        return False
    except Exception as e:
        logger.error("imessage_failed", error=str(e))
        return False

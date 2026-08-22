import logging
import httpx
from app.database import get_setting

logger = logging.getLogger("site_monitor.alerts")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

def is_pushover_configured() -> bool:
    enabled = get_setting("pushover_enabled", "false").lower() in ("true", "1", "yes")
    token = get_setting("pushover_api_token", "").strip()
    user = get_setting("pushover_user_key", "").strip()
    return enabled and bool(token) and bool(user)

async def send_pushover_notification(title: str, message: str, priority: int = 0) -> tuple[bool, str]:
    """
    Sends a Pushover notification asynchronously.
    Returns (success: bool, response_message: str).
    """
    if not is_pushover_configured():
        return False, "Pushover is disabled or missing credentials"

    token = get_setting("pushover_api_token", "").strip()
    user = get_setting("pushover_user_key", "").strip()

    data = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": priority
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(PUSHOVER_URL, data=data)
            if resp.status_code == 200:
                logger.info(f"Pushover notification sent: {title}")
                return True, "Notification sent successfully"
            else:
                err = f"Pushover API returned {resp.status_code}: {resp.text}"
                logger.error(err)
                return False, err
    except Exception as e:
        err = f"Failed to send Pushover notification: {e}"
        logger.error(err)
        return False, err

async def send_test_alert() -> tuple[bool, str]:
    """Sends a test Pushover notification asynchronously."""
    return await send_pushover_notification(
        title="Site Monitor - Test Alert",
        message="This is a test notification from your Site Monitor instance.",
        priority=0
    )


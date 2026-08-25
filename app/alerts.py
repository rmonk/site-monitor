import logging
from typing import Optional, Tuple
import httpx
from app.database import get_setting, set_setting

logger = logging.getLogger("site_monitor.alerts")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_CANCEL_URL = "https://api.pushover.net/1/receipts/{receipt}/cancel.json"

def is_pushover_configured() -> bool:
    enabled = get_setting("pushover_enabled", "false").lower() in ("true", "1", "yes")
    token = get_setting("pushover_api_token", "").strip()
    user = get_setting("pushover_user_key", "").strip()
    return enabled and bool(token) and bool(user)

async def send_pushover_notification(
    title: str,
    message: str,
    priority: int = 0,
    retry: int = 60,
    expire: int = 3600
) -> Tuple[bool, str, Optional[str]]:
    """
    Sends a Pushover notification asynchronously.
    For priority=2 (Emergency), includes retry and expire parameters.
    Returns (success: bool, response_message: str, receipt_id: Optional[str]).
    """
    if not is_pushover_configured():
        return False, "Pushover is disabled or missing credentials", None

    token = get_setting("pushover_api_token", "").strip()
    user = get_setting("pushover_user_key", "").strip()

    data = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": priority
    }

    # Emergency priority requirements in Pushover API
    if priority == 2:
        data["retry"] = max(30, int(retry))
        data["expire"] = min(86400, max(30, int(expire)))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(PUSHOVER_URL, data=data)
            if resp.status_code == 200:
                receipt = None
                try:
                    res_json = resp.json()
                    receipt = res_json.get("receipt")
                except Exception:
                    pass

                logger.info(f"Pushover notification sent: {title} (priority={priority}, receipt={receipt})")
                return True, "Notification sent successfully", receipt
            else:
                err = f"Pushover API returned {resp.status_code}: {resp.text}"
                logger.error(err)
                return False, err, None
    except Exception as e:
        err = f"Failed to send Pushover notification: {e}"
        logger.error(err)
        return False, err, None

async def cancel_pushover_receipt(receipt_id: str) -> Tuple[bool, str]:
    """
    Cancels an active emergency-priority alert retry in Pushover using the receipt ID.
    Returns (success: bool, response_message: str).
    """
    if not is_pushover_configured():
        return False, "Pushover is disabled or missing credentials"

    if not receipt_id or not receipt_id.strip():
        return False, "No receipt ID provided"

    receipt_id = receipt_id.strip()
    token = get_setting("pushover_api_token", "").strip()
    cancel_url = PUSHOVER_CANCEL_URL.format(receipt=receipt_id)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(cancel_url, data={"token": token})
            if resp.status_code == 200:
                logger.info(f"Pushover emergency receipt {receipt_id} cancelled successfully.")
                return True, f"Receipt {receipt_id} cancelled"
            else:
                err = f"Pushover cancel API returned {resp.status_code}: {resp.text}"
                logger.warning(err)
                return False, err
    except Exception as e:
        err = f"Failed to cancel Pushover receipt {receipt_id}: {e}"
        logger.error(err)
        return False, err

async def send_normal_test_alert() -> Tuple[bool, str]:
    """Sends a standard priority=0 test notification."""
    ok, msg, _ = await send_pushover_notification(
        title="Site Monitor - Normal Test Alert",
        message="This is a standard (priority 0) test notification from your Site Monitor instance.",
        priority=0
    )
    return ok, msg

async def send_down_test_alert() -> Tuple[bool, str, Optional[str]]:
    """
    Sends a DOWN / Emergency alert test and saves the test receipt token for subsequent cancellation.
    """
    try:
        prio = int(get_setting("pushover_priority_down", "2"))
    except ValueError:
        prio = 2

    try:
        retry = int(get_setting("pushover_emergency_retry", "60"))
    except ValueError:
        retry = 60

    try:
        expire = int(get_setting("pushover_emergency_expire", "3600"))
    except ValueError:
        expire = 3600

    ok, msg, receipt = await send_pushover_notification(
        title="DOWN: Test Host (Alert Test)",
        message="TEST ALERT: Host 'Test Host' (https://example.com) is simulated DOWN!\nConsecutive failures: 1",
        priority=prio,
        retry=retry,
        expire=expire
    )

    if ok and receipt:
        set_setting("last_test_receipt", receipt)
        msg += f" (Emergency Receipt: {receipt})"

    return ok, msg, receipt

async def send_recovery_test_alert() -> Tuple[bool, str, Optional[str]]:
    """
    Sends a RECOVERED test notification and cancels any active test receipt.
    """
    ok, msg, _ = await send_pushover_notification(
        title="RECOVERED: Test Host (Recovery Test)",
        message="TEST RECOVERY: Host 'Test Host' (https://example.com) has recovered and is back UP.",
        priority=0
    )

    last_test_receipt = get_setting("last_test_receipt", "").strip()
    cancelled_receipt = None
    if last_test_receipt:
        cancel_ok, cancel_msg = await cancel_pushover_receipt(last_test_receipt)
        set_setting("last_test_receipt", "")
        if cancel_ok:
            cancelled_receipt = last_test_receipt
            msg += f" | Cancelled active alert receipt: {last_test_receipt}"
        else:
            msg += f" | Warning: Could not cancel receipt ({cancel_msg})"

    return ok, msg, cancelled_receipt

async def send_test_alert() -> Tuple[bool, str]:
    """Legacy backward-compatible alias for normal test."""
    return await send_normal_test_alert()



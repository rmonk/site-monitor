import logging
from typing import Optional, Tuple, Dict, Any
import httpx
from app.database import (
    get_setting,
    set_setting,
    get_setting_bool,
    get_setting_int,
    parse_receipt_list,
)

logger = logging.getLogger("site_monitor.alerts")

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_CANCEL_URL = "https://api.pushover.net/1/receipts/{receipt}/cancel.json"
PUSHOVER_RECEIPT_STATUS_URL = "https://api.pushover.net/1/receipts/{receipt}.json"

_alert_client: Optional[httpx.AsyncClient] = None


def get_alert_client() -> httpx.AsyncClient:
    """Returns a shared httpx.AsyncClient with connection pooling."""
    global _alert_client
    if _alert_client is None or _alert_client.is_closed:
        _alert_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _alert_client


async def close_alert_client():
    """Closes the shared HTTP client on application shutdown."""
    global _alert_client
    if _alert_client is not None and not _alert_client.is_closed:
        await _alert_client.aclose()
        _alert_client = None


def is_pushover_configured() -> bool:
    enabled = get_setting_bool("pushover_enabled", False)
    token = get_setting("pushover_api_token", "").strip()
    user = get_setting("pushover_user_key", "").strip()
    return enabled and bool(token) and bool(user)


async def send_pushover_notification(
    title: str, message: str, priority: int = 0, retry: int = 60, expire: int = 3600
) -> Tuple[bool, str, Optional[str]]:
    """
    Sends a Pushover notification asynchronously using the pooled HTTP client.
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
        "priority": priority,
    }

    if priority == 2:
        data["retry"] = max(30, int(retry))
        data["expire"] = min(86400, max(30, int(expire)))

    try:
        client = get_alert_client()
        resp = await client.post(PUSHOVER_URL, data=data)
        if resp.status_code == 200:
            receipt = None
            try:
                receipt = resp.json().get("receipt")
            except Exception:
                pass

            logger.info(
                f"Pushover notification sent: {title} (priority={priority}, receipt={receipt})"
            )
            return True, "Notification sent successfully", receipt
        else:
            err = f"Pushover API returned {resp.status_code}: {resp.text}"
            logger.error(err)
            return False, err, None
    except Exception as e:
        err = f"Failed to send Pushover notification: {e}"
        logger.error(err)
        return False, err, None


async def get_pushover_receipt_status(receipt_id: str) -> Optional[Dict[str, Any]]:
    """
    Queries Pushover Receipts API for acknowledgment status of an emergency alert.
    Returns parsed dictionary or None if query fails.
    """
    if not is_pushover_configured() or not receipt_id or not receipt_id.strip():
        return None

    receipt_id = receipt_id.strip()
    token = get_setting("pushover_api_token", "").strip()
    url = PUSHOVER_RECEIPT_STATUS_URL.format(receipt=receipt_id)

    try:
        client = get_alert_client()
        resp = await client.get(url, params={"token": token})
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == 1:
                return {
                    "receipt": receipt_id,
                    "acknowledged": bool(data.get("acknowledged") == 1),
                    "acknowledged_at": int(data.get("acknowledged_at", 0) or 0),
                    "acknowledged_by": str(data.get("acknowledged_by", "") or ""),
                    "acknowledged_by_device": str(
                        data.get("acknowledged_by_device", "") or ""
                    ),
                    "expired": bool(data.get("expired") == 1),
                    "expires_at": int(data.get("expires_at", 0) or 0),
                }
            else:
                logger.warning(f"Pushover receipt {receipt_id} status error: {data}")
        else:
            logger.warning(
                f"Pushover receipt query returned HTTP {resp.status_code}: {resp.text}"
            )
    except Exception as e:
        logger.error(f"Failed to query Pushover receipt status for {receipt_id}: {e}")

    return None


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
        client = get_alert_client()
        resp = await client.post(cancel_url, data={"token": token})
        if resp.status_code == 200:
            logger.info(
                f"Pushover emergency receipt {receipt_id} cancelled successfully."
            )
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
        priority=0,
    )
    return ok, msg


async def send_down_test_alert() -> Tuple[bool, str, Optional[str]]:
    """
    Sends an Emergency (priority=2) DOWN alert test and saves the test receipt token so the recovery test can cancel it.
    """
    retry = get_setting_int("pushover_emergency_retry", 60)
    expire = get_setting_int("pushover_emergency_expire", 3600)

    ok, msg, receipt = await send_pushover_notification(
        title="DOWN: Test Host (Alert Test)",
        message="TEST ALERT: Host 'Test Host' (https://example.com) is simulated DOWN!\n(Emergency Priority 2 - will repeat until cancelled by Recovery Test)",
        priority=2,
        retry=retry,
        expire=expire,
    )

    if ok and receipt:
        receipts_list = parse_receipt_list(get_setting("last_test_receipt", ""))
        if receipt not in receipts_list:
            receipts_list.append(receipt)
        set_setting("last_test_receipt", ",".join(receipts_list))
        logger.info(
            f"Saved active test receipt: {receipt} (Total active test receipts: {len(receipts_list)})"
        )
        msg += f" (Emergency Receipt: {receipt})"
    elif ok and not receipt:
        logger.warning(
            "Emergency test alert succeeded but Pushover returned no receipt ID."
        )

    return ok, msg, receipt


async def send_recovery_test_alert() -> Tuple[bool, str, Optional[str]]:
    """
    Sends a RECOVERED test notification and cancels all active test receipts.
    """
    ok, msg, _ = await send_pushover_notification(
        title="RECOVERED: Test Host (Recovery Test)",
        message="TEST RECOVERY: Host 'Test Host' (https://example.com) has recovered and is back UP.\nActive emergency alert(s) cancelled.",
        priority=0,
    )

    last_receipts = parse_receipt_list(get_setting("last_test_receipt", ""))
    cancelled_list = []
    failed_list = []

    if last_receipts:
        for r_id in last_receipts:
            cancel_ok, cancel_msg = await cancel_pushover_receipt(r_id)
            if cancel_ok:
                cancelled_list.append(r_id)
            else:
                failed_list.append(f"{r_id} ({cancel_msg})")

        set_setting("last_test_receipt", "")

        if cancelled_list:
            msg += f" | Cancelled test receipt(s): {', '.join(cancelled_list)}"
        if failed_list:
            msg += f" | Warning: Could not cancel {', '.join(failed_list)}"
    else:
        msg += " | No active test receipts were pending cancellation."

    return ok, msg, ",".join(cancelled_list) if cancelled_list else None


async def send_test_alert() -> Tuple[bool, str]:
    """Legacy backward-compatible alias for normal test."""
    return await send_normal_test_alert()

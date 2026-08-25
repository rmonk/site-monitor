import asyncio
import re
import time
import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from app.database import (
    get_db,
    get_setting,
    get_setting_bool,
    get_setting_int,
    parse_receipt_list,
)
from app.alerts import (
    send_pushover_notification,
    cancel_pushover_receipt,
    get_pushover_receipt_status,
)
from app.screenshots import capture_screenshot

logger = logging.getLogger("site_monitor.checker")

# Limit concurrent checks in the worker loop to prevent resource exhaustion
_check_semaphore = asyncio.Semaphore(5)


def _should_take_screenshot(
    monitor: Dict[str, Any], is_up: bool, is_manual: bool, is_currently_down: int
) -> bool:
    """
    Determines if a screenshot should be captured for this check to avoid redundant browser launches.
    Captures when:
    - Triggered manually ("Check Now")
    - Check failed (site is down)
    - State changed (recovered from down)
    - No success screenshot exists yet
    - Existing success screenshot is older than 6 hours
    """
    if is_manual:
        return True

    if not is_up:
        # Always capture on failure
        return True

    # If recovering from DOWN
    if is_currently_down == 1:
        return True

    # If no success screenshot exists yet
    last_success = monitor.get("last_success_screenshot_time")
    if not last_success:
        return True

    # If older than 6 hours, refresh screenshot
    try:
        dt = datetime.strptime(last_success, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        if datetime.now(timezone.utc) - dt > timedelta(hours=6):
            return True
    except Exception:
        return True

    return False


async def check_monitor(
    monitor: Dict[str, Any], is_manual: bool = False
) -> Dict[str, Any]:
    """
    Performs an HTTP GET check for a single monitor and records the result.
    """
    monitor_id = monitor["id"]
    name = monitor["name"]
    url = monitor["url"]
    timeout = monitor.get("timeout", 10)
    regex_pattern = monitor.get("regex_pattern")
    failure_threshold = monitor.get("failure_threshold", 1)

    start_time = time.time()
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    is_up = False
    regex_matched: Optional[bool] = None
    error_message: Optional[str] = None

    try:
        async with httpx.AsyncClient(
            timeout=float(timeout), follow_redirects=True
        ) as client:
            response = await client.get(url)
            response_time_ms = round((time.time() - start_time) * 1000, 2)
            status_code = response.status_code

            # Check HTTP Status Code (200 - 399 considered successful HTTP status)
            if 200 <= status_code < 400:
                is_up = True
            else:
                is_up = False
                error_message = f"HTTP status code {status_code}"

            # Check Regex if up so far and regex specified
            if is_up and regex_pattern:
                try:
                    match = re.search(regex_pattern, response.text)
                    if match:
                        regex_matched = True
                    else:
                        regex_matched = False
                        is_up = False
                        error_message = f"Regex pattern '{regex_pattern}' not matched in response body"
                except Exception as re_err:
                    regex_matched = False
                    is_up = False
                    error_message = (
                        f"Invalid regex pattern or evaluation error: {re_err}"
                    )

    except httpx.TimeoutException:
        response_time_ms = round((time.time() - start_time) * 1000, 2)
        is_up = False
        error_message = f"Request timed out after {timeout} seconds"
    except httpx.RequestError as req_err:
        response_time_ms = round((time.time() - start_time) * 1000, 2)
        is_up = False
        error_message = f"Connection error: {req_err}"
    except Exception as exc:
        response_time_ms = round((time.time() - start_time) * 1000, 2)
        is_up = False
        error_message = f"Unexpected error: {exc}"

    now_ts = int(time.time())

    # 1. Fetch current alert state
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        alert_state_row = cursor.fetchone()

    consecutive_failures = (
        alert_state_row["consecutive_failures"] if alert_state_row else 0
    )
    is_currently_down = alert_state_row["is_currently_down"] if alert_state_row else 0
    last_alert_time = alert_state_row["last_alert_time"] if alert_state_row else 0
    alert_count = alert_state_row["alert_count"] if alert_state_row else 0
    active_receipts = (
        alert_state_row["active_receipts"]
        if (alert_state_row and alert_state_row["active_receipts"])
        else ""
    )

    # 2. Determine whether to capture screenshot
    if monitor.get("capture_screenshots") is not None:
        should_capture_screenshots = bool(monitor["capture_screenshots"])
    else:
        should_capture_screenshots = get_setting_bool(
            "default_capture_screenshots", True
        )

    screenshot_ts = None
    if should_capture_screenshots and _should_take_screenshot(
        monitor, is_up, is_manual, is_currently_down
    ):
        try:
            screenshot_ts = await capture_screenshot(
                monitor_id=monitor_id,
                url=url,
                is_success=is_up,
                error_message=error_message or "",
            )
        except Exception as scr_err:
            logger.error(
                f"Failed to capture screenshot for monitor {monitor_id}: {scr_err}"
            )

    # 3. Determine repeat alert & Pushover configuration
    if monitor.get("repeat_alerts") is not None:
        should_repeat = bool(monitor["repeat_alerts"])
    else:
        should_repeat = get_setting_bool("default_repeat_alerts", True)

    if (
        monitor.get("repeat_interval_minutes") is not None
        and monitor["repeat_interval_minutes"] > 0
    ):
        repeat_interval_mins = int(monitor["repeat_interval_minutes"])
    else:
        repeat_interval_mins = get_setting_int("default_repeat_interval_minutes", 60)

    down_priority = get_setting_int("pushover_priority_down", 2)
    emergency_retry = get_setting_int("pushover_emergency_retry", 60)
    emergency_expire = get_setting_int("pushover_emergency_expire", 3600)

    # 4. Handle state transitions and alerting (Network calls OUTSIDE DB transaction)
    new_receipts = parse_receipt_list(active_receipts)
    new_is_down = is_currently_down
    new_last_alert_time = last_alert_time
    new_alert_count = alert_count
    new_consecutive_failures = consecutive_failures

    if is_up:
        if is_currently_down == 1:
            # Recovery notice!
            await send_pushover_notification(
                title=f"RECOVERED: {name}",
                message=f"Host '{name}' ({url}) has recovered and is back UP.",
                priority=0,
            )
            logger.info(f"Monitor '{name}' recovered.")

            # Cancel all active emergency alert receipts for this monitor
            for r_id in new_receipts:
                cancel_ok, cancel_msg = await cancel_pushover_receipt(r_id)
                logger.info(
                    f"Recovery receipt cancellation for {name} ({r_id}): {cancel_msg}"
                )

        new_consecutive_failures = 0
        new_is_down = 0
        new_alert_count = 0
        new_receipts = []
    else:
        new_consecutive_failures += 1
        if new_consecutive_failures >= failure_threshold:
            if is_currently_down == 0:
                # Transition to DOWN -> Send initial alert
                new_is_down = 1
                new_last_alert_time = now_ts
                new_alert_count = 1
                ok, msg, receipt = await send_pushover_notification(
                    title=f"DOWN: {name}",
                    message=f"Host '{name}' ({url}) is DOWN!\nError: {error_message or 'Unknown error'}\nConsecutive failures: {new_consecutive_failures}",
                    priority=down_priority,
                    retry=emergency_retry,
                    expire=emergency_expire,
                )
                if ok and receipt and receipt not in new_receipts:
                    new_receipts.append(receipt)
                logger.warning(f"Monitor '{name}' is DOWN. Initial alert sent.")
            else:
                # Already DOWN -> Check repeat alert
                if should_repeat:
                    elapsed_seconds = now_ts - last_alert_time
                    if elapsed_seconds >= repeat_interval_mins * 60:
                        new_last_alert_time = now_ts
                        new_alert_count += 1
                        ok, msg, receipt = await send_pushover_notification(
                            title=f"STILL DOWN: {name}",
                            message=f"Host '{name}' ({url}) is STILL DOWN!\nError: {error_message or 'Unknown error'}\nConsecutive failures: {new_consecutive_failures}\nAlert #{new_alert_count}",
                            priority=down_priority,
                            retry=emergency_retry,
                            expire=emergency_expire,
                        )
                        if ok and receipt and receipt not in new_receipts:
                            new_receipts.append(receipt)
                        logger.warning(
                            f"Monitor '{name}' still DOWN. Repeat alert #{new_alert_count} sent."
                        )

    updated_active_receipts = ",".join(new_receipts)

    # 5. Consolidated single atomic DB write transaction
    with get_db() as conn:
        cursor = conn.cursor()

        # Update screenshot timestamp if captured
        if screenshot_ts:
            col = (
                "last_success_screenshot_time"
                if is_up
                else "last_failed_screenshot_time"
            )
            cursor.execute(
                f"UPDATE monitors SET {col} = ? WHERE id = ?",
                (screenshot_ts, monitor_id),
            )

        # Record check history
        cursor.execute(
            """
            INSERT INTO check_history (monitor_id, status_code, response_time_ms, is_up, regex_matched, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                monitor_id,
                status_code,
                response_time_ms,
                1 if is_up else 0,
                1 if regex_matched is True else (0 if regex_matched is False else None),
                error_message,
            ),
        )

        # Prune history to keep max 1000 entries per monitor
        cursor.execute(
            """
            DELETE FROM check_history
            WHERE monitor_id = ? AND id NOT IN (
                SELECT id FROM check_history WHERE monitor_id = ? ORDER BY id DESC LIMIT 1000
            )
        """,
            (monitor_id, monitor_id),
        )

        # Update or Insert alert state
        cursor.execute(
            """
            INSERT INTO alert_state (
                monitor_id, consecutive_failures, is_currently_down, last_alert_time,
                alert_count, active_receipts, receipt_acknowledged, receipt_acknowledged_at,
                receipt_acknowledged_by, receipt_acknowledged_device, receipt_last_checked
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, '', '', 0)
            ON CONFLICT(monitor_id) DO UPDATE SET
                consecutive_failures = excluded.consecutive_failures,
                is_currently_down = excluded.is_currently_down,
                last_alert_time = excluded.last_alert_time,
                alert_count = excluded.alert_count,
                active_receipts = excluded.active_receipts,
                receipt_acknowledged = CASE WHEN excluded.is_currently_down = 0 THEN 0 ELSE alert_state.receipt_acknowledged END,
                receipt_acknowledged_at = CASE WHEN excluded.is_currently_down = 0 THEN 0 ELSE alert_state.receipt_acknowledged_at END,
                receipt_acknowledged_by = CASE WHEN excluded.is_currently_down = 0 THEN '' ELSE alert_state.receipt_acknowledged_by END,
                receipt_acknowledged_device = CASE WHEN excluded.is_currently_down = 0 THEN '' ELSE alert_state.receipt_acknowledged_device END,
                receipt_last_checked = CASE WHEN excluded.is_currently_down = 0 THEN 0 ELSE alert_state.receipt_last_checked END
        """,
            (
                monitor_id,
                new_consecutive_failures,
                new_is_down,
                new_last_alert_time,
                new_alert_count,
                updated_active_receipts,
            ),
        )

    return {
        "monitor_id": monitor_id,
        "name": name,
        "is_up": is_up,
        "status_code": status_code,
        "response_time_ms": response_time_ms,
        "error_message": error_message,
    }


async def _safe_check_monitor(monitor: Dict[str, Any]):
    """
    Executes check_monitor under semaphore and strict per-task timeout.
    """
    timeout = float(monitor.get("timeout", 10)) + 25.0
    async with _check_semaphore:
        try:
            await asyncio.wait_for(check_monitor(monitor), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                f"Check for monitor '{monitor.get('name')}' (id: {monitor.get('id')}) exceeded timeout of {timeout}s"
            )
        except Exception as e:
            logger.error(
                f"Unhandled error checking monitor '{monitor.get('name')}': {e}"
            )


_worker_heartbeat: float = time.time()


def get_worker_heartbeat() -> float:
    global _worker_heartbeat
    return _worker_heartbeat


def update_worker_heartbeat():
    global _worker_heartbeat
    _worker_heartbeat = time.time()


async def monitoring_worker_loop():
    """
    Background worker loop that checks active monitors based on their check intervals.
    Guaranteed never to block indefinitely.
    """
    last_checked: Dict[int, float] = {}

    logger.info("Starting site monitoring worker loop...")
    while True:
        update_worker_heartbeat()
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM monitors WHERE is_active = 1")
                monitors = [dict(row) for row in cursor.fetchall()]

            now = time.time()
            tasks = []
            for monitor in monitors:
                m_id = monitor["id"]
                interval = monitor.get("check_interval", 60)
                last = last_checked.get(m_id, 0)

                if now - last >= interval:
                    last_checked[m_id] = now
                    tasks.append(_safe_check_monitor(monitor))

            if tasks:
                try:
                    # Guard entire batch with 45s timeout so loop ALWAYS continues
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), timeout=45.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Batch monitor check reached 45s timeout; proceeding to next cycle."
                    )

        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")

        await asyncio.sleep(5)  # Check every 5 seconds for due monitors


async def sync_monitor_receipt_status(monitor_id: int) -> Optional[Dict[str, Any]]:
    """
    Queries Pushover Receipts API for active receipts associated with a DOWN monitor
    and updates alert_state in the database if acknowledged.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        a_state = cursor.fetchone()
        if not a_state:
            return None

        active_receipts_str = (
            a_state["active_receipts"] if a_state["active_receipts"] else ""
        )
        receipt_ids = parse_receipt_list(active_receipts_str)
        if not receipt_ids:
            return None

        # Query status of the latest active receipt
        latest_receipt = receipt_ids[-1]
        status = await get_pushover_receipt_status(latest_receipt)
        now_ts = int(time.time())

        if status:
            is_ack = 1 if status.get("acknowledged") else 0
            ack_at = int(status.get("acknowledged_at", 0) or 0)
            ack_by = str(status.get("acknowledged_by", "") or "")
            ack_device = str(status.get("acknowledged_by_device", "") or "")

            cursor.execute(
                """
                UPDATE alert_state
                SET receipt_acknowledged = ?,
                    receipt_acknowledged_at = ?,
                    receipt_acknowledged_by = ?,
                    receipt_acknowledged_device = ?,
                    receipt_last_checked = ?
                WHERE monitor_id = ?
            """,
                (is_ack, ack_at, ack_by, ack_device, now_ts, monitor_id),
            )
            return status

        # If query returned nothing, update last checked timestamp
        cursor.execute(
            """
            UPDATE alert_state
            SET receipt_last_checked = ?
            WHERE monitor_id = ?
        """,
            (now_ts, monitor_id),
        )
        return None


async def watchdog_worker_loop(task_holder: Optional[Dict[str, Any]] = None):
    """
    Independent watchdog task that monitors the health of the background worker loop,
    sends Pushover emergency alerts if the worker stalls, triggers auto-recovery,
    polls receipt acknowledgment status for down hosts every 60 seconds,
    and sends periodic Dead Man's Switch external heartbeat pings.
    """
    logger.info("Starting site monitoring watchdog loop...")
    watchdog_alert_sent = False
    last_ping_time = 0.0
    last_receipt_poll_time = 0.0

    while True:
        try:
            now = time.time()
            heartbeat = get_worker_heartbeat()
            stalled_duration = now - heartbeat

            # 1. Check if worker is stalled (>180 seconds)
            if stalled_duration > 180.0:
                has_active_monitors = False
                try:
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT COUNT(*) as cnt FROM monitors WHERE is_active = 1"
                        )
                        row = cursor.fetchone()
                        has_active_monitors = bool(row and row["cnt"] > 0)
                except Exception as db_err:
                    logger.error(f"Watchdog DB check error: {db_err}")
                    has_active_monitors = True

                if has_active_monitors:
                    logger.critical(
                        f"WATCHDOG: Monitoring worker stalled! No heartbeat for {int(stalled_duration)}s."
                    )
                    if not watchdog_alert_sent:
                        await send_pushover_notification(
                            title="CRITICAL: Site Monitor Worker Stalled",
                            message=f"Site Monitor background worker has stopped responding (no heartbeat for {int(stalled_duration)}s).\nAttempting automatic task restart.",
                            priority=1,
                        )
                        watchdog_alert_sent = True

                    # Trigger auto-recovery if task_holder provided
                    if task_holder and "worker_task" in task_holder:
                        old_task = task_holder["worker_task"]
                        if old_task and not old_task.done():
                            logger.info("Watchdog cancelling stalled worker task...")
                            old_task.cancel()
                        logger.info("Watchdog relaunching monitoring_worker_loop...")
                        task_holder["worker_task"] = asyncio.create_task(
                            monitoring_worker_loop()
                        )
                        update_worker_heartbeat()
            else:
                if watchdog_alert_sent and stalled_duration < 60.0:
                    # Worker recovered
                    await send_pushover_notification(
                        title="RECOVERED: Site Monitor Worker Resumed",
                        message="Site Monitor background monitoring worker has recovered and resumed normal operation.",
                        priority=0,
                    )
                    watchdog_alert_sent = False

            # 2. External Heartbeat Ping (Dead Man's Switch)
            try:
                ping_url = get_setting("heartbeat_ping_url", "").strip()
                if ping_url:
                    interval_mins = get_setting_int(
                        "heartbeat_ping_interval_minutes", 15
                    )
                    interval_secs = max(60, interval_mins * 60)

                    if now - last_ping_time >= interval_secs:
                        last_ping_time = now
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.get(ping_url)
                            logger.info(
                                f"Dead Man's Switch external ping sent to {ping_url} (HTTP {resp.status_code})"
                            )
            except Exception as ping_err:
                logger.warning(
                    f"Failed to send Dead Man's Switch external ping: {ping_err}"
                )

            # 3. Periodic Pushover Receipt Acknowledgment Polling (every 60s for down monitors with unacknowledged receipts)
            if now - last_receipt_poll_time >= 60.0:
                last_receipt_poll_time = now
                try:
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT monitor_id FROM alert_state
                            WHERE is_currently_down = 1 AND active_receipts != '' AND receipt_acknowledged = 0
                        """)
                        down_rows = cursor.fetchall()
                    for row in down_rows:
                        await sync_monitor_receipt_status(row["monitor_id"])
                except Exception as r_err:
                    logger.error(f"Error in periodic receipt status polling: {r_err}")

        except Exception as e:
            logger.error(f"Error in watchdog loop: {e}")

        await asyncio.sleep(30)

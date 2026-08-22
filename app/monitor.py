import asyncio
import re
import time
import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from app.database import get_db, get_setting
from app.alerts import send_pushover_notification
from app.screenshots import capture_screenshot

logger = logging.getLogger("site_monitor.checker")

# Limit concurrent checks in the worker loop to prevent resource exhaustion
_check_semaphore = asyncio.Semaphore(5)


def _should_take_screenshot(monitor: Dict[str, Any], is_up: bool, is_manual: bool, is_currently_down: int) -> bool:
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
        dt = datetime.strptime(last_success, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - dt > timedelta(hours=6):
            return True
    except Exception:
        return True

    return False


async def check_monitor(monitor: Dict[str, Any], is_manual: bool = False) -> Dict[str, Any]:
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
        async with httpx.AsyncClient(timeout=float(timeout), follow_redirects=True) as client:
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
                    error_message = f"Invalid regex pattern or evaluation error: {re_err}"

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

    # Get current alert state to know if state is transitioning
    is_currently_down = 0
    consecutive_failures = 0
    last_alert_time = 0
    alert_count = 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        alert_state = cursor.fetchone()
        if alert_state:
            is_currently_down = alert_state["is_currently_down"]
            consecutive_failures = alert_state["consecutive_failures"]
            last_alert_time = alert_state["last_alert_time"]
            alert_count = alert_state["alert_count"]

    # Determine whether to capture screenshot
    if monitor.get("capture_screenshots") is not None:
        should_capture_screenshots = bool(monitor["capture_screenshots"])
    else:
        global_screenshots = get_setting("default_capture_screenshots", "true")
        should_capture_screenshots = global_screenshots.lower() in ("true", "1", "yes")

    screenshot_ts = None
    if should_capture_screenshots and _should_take_screenshot(monitor, is_up, is_manual, is_currently_down):
        try:
            screenshot_ts = await capture_screenshot(
                monitor_id=monitor_id,
                url=url,
                is_success=is_up,
                error_message=error_message or ""
            )
        except Exception as scr_err:
            logger.error(f"Failed to capture screenshot for monitor {monitor_id}: {scr_err}")

    # Process check result in DB
    with get_db() as conn:
        cursor = conn.cursor()

        # Update screenshot timestamp in monitors table if captured
        if screenshot_ts:
            if is_up:
                cursor.execute(
                    "UPDATE monitors SET last_success_screenshot_time = ? WHERE id = ?",
                    (screenshot_ts, monitor_id)
                )
            else:
                cursor.execute(
                    "UPDATE monitors SET last_failed_screenshot_time = ? WHERE id = ?",
                    (screenshot_ts, monitor_id)
                )

        # 1. Record check history
        cursor.execute("""
            INSERT INTO check_history (monitor_id, status_code, response_time_ms, is_up, regex_matched, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            monitor_id,
            status_code,
            response_time_ms,
            1 if is_up else 0,
            1 if regex_matched is True else (0 if regex_matched is False else None),
            error_message
        ))

        # Prune history to keep max 1000 entries per monitor
        cursor.execute("""
            DELETE FROM check_history
            WHERE monitor_id = ? AND id NOT IN (
                SELECT id FROM check_history WHERE monitor_id = ? ORDER BY id DESC LIMIT 1000
            )
        """, (monitor_id, monitor_id))

        # 2. Get or create alert state
        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        alert_state = cursor.fetchone()

        if not alert_state:
            cursor.execute(
                "INSERT INTO alert_state (monitor_id, consecutive_failures, is_currently_down, last_alert_time, alert_count) VALUES (?, 0, 0, 0, 0)",
                (monitor_id,)
            )
            cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
            alert_state = cursor.fetchone()

        consecutive_failures = alert_state["consecutive_failures"]
        is_currently_down = alert_state["is_currently_down"]
        last_alert_time = alert_state["last_alert_time"]
        alert_count = alert_state["alert_count"]

        # Determine repeat alert configuration
        if monitor.get("repeat_alerts") is not None:
            should_repeat = bool(monitor["repeat_alerts"])
        else:
            global_repeat = get_setting("default_repeat_alerts", "true")
            should_repeat = global_repeat.lower() in ("true", "1", "yes")

        if monitor.get("repeat_interval_minutes") is not None and monitor["repeat_interval_minutes"] > 0:
            repeat_interval_mins = int(monitor["repeat_interval_minutes"])
        else:
            try:
                repeat_interval_mins = int(get_setting("default_repeat_interval_minutes", "60"))
            except ValueError:
                repeat_interval_mins = 60

        # Handle state transitions and alerting
        if is_up:
            if is_currently_down == 1:
                # Recovery!
                await send_pushover_notification(
                    title=f"RECOVERED: {name}",
                    message=f"Host '{name}' ({url}) has recovered and is back UP.",
                    priority=0
                )
                logger.info(f"Monitor '{name}' recovered.")
            
            # Reset state
            cursor.execute("""
                UPDATE alert_state
                SET consecutive_failures = 0, is_currently_down = 0, alert_count = 0
                WHERE monitor_id = ?
            """, (monitor_id,))

        else:
            consecutive_failures += 1
            new_is_down = is_currently_down
            new_last_alert_time = last_alert_time
            new_alert_count = alert_count

            if consecutive_failures >= failure_threshold:
                if is_currently_down == 0:
                    # Transition to DOWN -> Send initial alert
                    new_is_down = 1
                    new_last_alert_time = now_ts
                    new_alert_count = 1
                    await send_pushover_notification(
                        title=f"DOWN: {name}",
                        message=f"Host '{name}' ({url}) is DOWN!\nError: {error_message or 'Unknown error'}\nConsecutive failures: {consecutive_failures}",
                        priority=1
                    )
                    logger.warning(f"Monitor '{name}' is DOWN. Initial alert sent.")
                else:
                    # Already DOWN -> Check repeat alert
                    if should_repeat:
                        elapsed_seconds = now_ts - last_alert_time
                        if elapsed_seconds >= repeat_interval_mins * 60:
                            new_last_alert_time = now_ts
                            new_alert_count += 1
                            await send_pushover_notification(
                                title=f"STILL DOWN: {name}",
                                message=f"Host '{name}' ({url}) is STILL DOWN!\nError: {error_message or 'Unknown error'}\nConsecutive failures: {consecutive_failures}\nAlert #{new_alert_count}",
                                priority=1
                            )
                            logger.warning(f"Monitor '{name}' still DOWN. Repeat alert #{new_alert_count} sent.")

            cursor.execute("""
                UPDATE alert_state
                SET consecutive_failures = ?, is_currently_down = ?, last_alert_time = ?, alert_count = ?
                WHERE monitor_id = ?
            """, (consecutive_failures, new_is_down, new_last_alert_time, new_alert_count, monitor_id))

    return {
        "monitor_id": monitor_id,
        "name": name,
        "is_up": is_up,
        "status_code": status_code,
        "response_time_ms": response_time_ms,
        "error_message": error_message
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
            logger.error(f"Check for monitor '{monitor.get('name')}' (id: {monitor.get('id')}) exceeded timeout of {timeout}s")
        except Exception as e:
            logger.error(f"Unhandled error checking monitor '{monitor.get('name')}': {e}")


async def monitoring_worker_loop():
    """
    Background worker loop that checks active monitors based on their check intervals.
    Guaranteed never to block indefinitely.
    """
    last_checked: Dict[int, float] = {}

    logger.info("Starting site monitoring worker loop...")
    while True:
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
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=45.0)
                except asyncio.TimeoutError:
                    logger.warning("Batch monitor check reached 45s timeout; proceeding to next cycle.")

        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")

        await asyncio.sleep(5)  # Check every 5 seconds for due monitors


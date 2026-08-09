import asyncio
import re
import time
import logging
import httpx
from typing import Optional, Dict, Any
from app.database import get_db, get_setting
from app.alerts import send_pushover_notification

logger = logging.getLogger("site_monitor.checker")

async def check_monitor(monitor: Dict[str, Any]) -> Dict[str, Any]:
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

    # Process check result in DB
    with get_db() as conn:
        cursor = conn.cursor()

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
                send_pushover_notification(
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
                    send_pushover_notification(
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
                            send_pushover_notification(
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


async def monitoring_worker_loop():
    """
    Background worker loop that checks active monitors based on their check intervals.
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
                    tasks.append(check_monitor(monitor))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")

        await asyncio.sleep(5)  # Check every 5 seconds for due monitors

import asyncio
import os
import logging
import signal
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from app.config import DATA_DIR

logger = logging.getLogger("site_monitor.screenshots")

# Limit concurrent headless browser processes to prevent memory/CPU spikes
_screenshot_semaphore = asyncio.Semaphore(2)

# Background Playwright start-up and shutdown tasks; held here so they are not garbage
# collected before they finish.
_cleanup_tasks: set = set()

# How long a cancelled capture's Playwright start-up may keep running before its driver
# is killed, and how long a stop may take before the driver is killed.
_STARTUP_REAP_TIMEOUT = 30.0
_STOP_TIMEOUT = 15.0

# One lock per event loop, held only while a driver process is being spawned, so each
# capture can tell which new driver process is its own.
_spawn_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _keep_task(task: asyncio.Future) -> asyncio.Future:
    """Holds a reference to a background task until it finishes, and returns it."""
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)
    return task


def _driver_child_pids() -> set:
    """
    Returns the PIDs of live Playwright driver processes (`node ... run-driver`) that are
    children of this process. Read from /proc, because Playwright has no public handle on
    its driver process and its private attributes change between versions.
    """
    me = os.getpid()
    pids = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                # Fields after the parenthesised command name: state, ppid, ...
                state, ppid = f.read().rsplit(")", 1)[1].split()[:2]
            if int(ppid) != me or state == "Z":
                continue
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                if b"run-driver" in f.read():
                    pids.add(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return pids


class _Driver:
    """The Playwright driver process behind one capture, once its PID is known."""

    def __init__(self) -> None:
        self.pid: Optional[int] = None

    def kill(self) -> None:
        """Last resort for a driver that does not respond to a normal start or stop."""
        # Re-check it is still our driver, so a reused PID is never signalled.
        if self.pid is None or self.pid not in _driver_child_pids():
            return
        try:
            os.kill(self.pid, signal.SIGKILL)
            logger.warning(f"Killed unresponsive Playwright driver (pid {self.pid})")
        except ProcessLookupError:
            pass


async def _start_tracked(driver: _Driver):
    """Starts Playwright and records the PID of the driver process it spawns."""
    from playwright.async_api import async_playwright

    loop = asyncio.get_running_loop()
    lock = _spawn_locks.setdefault(loop, asyncio.Lock())
    async with lock:
        before = _driver_child_pids()
        start = asyncio.ensure_future(async_playwright().start())
        new: set = set()
        for _ in range(500):  # the spawn normally takes milliseconds
            new = _driver_child_pids() - before
            if new or start.done():
                break
            await asyncio.sleep(0.01)
        if len(new) == 1:
            driver.pid = next(iter(new))
    try:
        return await start
    except asyncio.CancelledError:
        start.cancel()
        raise


async def _stop_playwright(driver: _Driver, playwright) -> None:
    """
    Stops a Playwright instance, which ends its node driver process (and with it any
    Chromium it launched). The first step closes the driver's stdin synchronously; if the
    driver still has not exited after the timeout, it is killed.
    """
    try:
        await asyncio.wait_for(playwright.stop(), timeout=_STOP_TIMEOUT)
    except Exception as e:
        logger.warning(f"Playwright driver did not stop cleanly: {e}")
        driver.kill()


async def _reap_cancelled_start(driver: _Driver, start_task: asyncio.Future) -> None:
    """
    Cleans up after a caller that was cancelled while Playwright was starting: stops the
    instance if start-up completes, or kills the driver if start-up stalls.
    """
    done, _ = await asyncio.wait({start_task}, timeout=_STARTUP_REAP_TIMEOUT)
    if start_task in done:
        if not start_task.cancelled() and start_task.exception() is None:
            await _stop_playwright(driver, start_task.result())
        return
    logger.warning(
        f"Playwright driver start-up stalled for {_STARTUP_REAP_TIMEOUT:.0f}s after cancellation"
    )
    driver.kill()
    start_task.cancel()


async def _start_playwright():
    """
    Starts Playwright without leaking its driver if the caller is cancelled, and returns
    `(driver, playwright)`.

    Playwright spawns the node driver before its start-up handshake completes. If the
    awaiting coroutine is cancelled during that handshake (a capture timeout, a check
    timeout or the watchdog restarting the worker), `async with async_playwright()` never
    runs `__aexit__`, and the driver process stays alive for the life of the container.
    The start runs as its own task here, so a cancelled caller hands it to a background
    reaper instead of dropping it.
    """
    driver = _Driver()
    start_task = _keep_task(asyncio.ensure_future(_start_tracked(driver)))
    try:
        return driver, await asyncio.shield(start_task)
    except asyncio.CancelledError:
        _keep_task(asyncio.ensure_future(_reap_cancelled_start(driver, start_task)))
        raise


def get_screenshots_dir() -> Path:
    s_dir = DATA_DIR / "screenshots"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir


def generate_fallback_screenshot(target_path: Path, url: str, error_msg: str):
    """
    Generates a dark placeholder image showing error details when browser navigation fails.
    """
    try:
        width, height = 1280, 720
        img = Image.new("RGB", (width, height), color=(24, 28, 36))
        draw = ImageDraw.Draw(img)

        # Header banner
        draw.rectangle([(0, 0), (width, 80)], fill=(220, 53, 69))
        draw.text(
            (40, 25), "SITE MONITOR - CONNECTION / CHECK ERROR", fill=(255, 255, 255)
        )

        # Error details
        y = 120
        draw.text((40, y), f"Target URL: {url}", fill=(200, 200, 200))
        y += 40
        draw.text(
            (40, y),
            f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            fill=(200, 200, 200),
        )
        y += 50
        draw.text((40, y), "Error Summary:", fill=(255, 100, 100))
        y += 35

        # Wrap or truncate error message
        lines = [error_msg[i : i + 80] for i in range(0, min(len(error_msg), 400), 80)]
        for line in lines:
            draw.text((60, y), line, fill=(240, 240, 240))
            y += 30

        img.save(str(target_path), format="PNG")
    except Exception as fallback_err:
        logger.error(f"Failed to generate fallback screenshot image: {fallback_err}")


async def _capture_with_playwright(
    filepath: Path, url: str, monitor_id: int, is_success: bool, error_message: str
):
    """Captures one screenshot with its own Playwright driver, always stopping the driver."""
    driver, p = await _start_playwright()
    try:
        await _run_capture(p, filepath, url, monitor_id, is_success, error_message)
    finally:
        # Shielded so a cancellation here cannot skip the stop: the stop task keeps
        # running in the background and the cancellation still propagates.
        await asyncio.shield(
            _keep_task(asyncio.ensure_future(_stop_playwright(driver, p)))
        )


async def _run_capture(
    p,
    filepath: Path,
    url: str,
    monitor_id: int,
    is_success: bool,
    error_message: str,
):
    """Launches Chromium and saves the screenshot, or a fallback image if navigation fails."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    browser = await p.chromium.launch(
        headless=True,
        timeout=10000,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    try:
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            try:
                await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                await page.screenshot(path=str(filepath), timeout=5000)
                logger.info(
                    f"Captured Playwright screenshot for monitor {monitor_id} ({'success' if is_success else 'failed'})"
                )
            except PlaywrightTimeoutError as te:
                logger.warning(
                    f"Playwright navigation timed out (10s) for {url}. Generating fallback screenshot."
                )
                generate_fallback_screenshot(
                    filepath, url, error_message or "Navigation timed out (10s)"
                )
            except Exception as nav_err:
                logger.warning(
                    f"Playwright navigation error for {url}: {nav_err}. Generating fallback screenshot."
                )
                generate_fallback_screenshot(
                    filepath, url, error_message or str(nav_err)
                )
        finally:
            try:
                await page.close()
            except Exception:
                pass
    finally:
        try:
            await browser.close()
        except Exception:
            pass


async def capture_screenshot(
    monitor_id: int, url: str, is_success: bool, error_message: str = ""
) -> str:
    """
    Captures a screenshot of the target URL using Playwright (or Pillow fallback)
    and saves it to the screenshots directory.
    Guarded by concurrency semaphore and overall timeout.
    Returns ISO timestamp string of capture time.
    """
    s_dir = get_screenshots_dir()
    filename = f"monitor_{monitor_id}_{'success' if is_success else 'failed'}.png"
    filepath = s_dir / filename
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    async with _screenshot_semaphore:
        try:
            # 25-second overall timeout guard
            await asyncio.wait_for(
                _capture_with_playwright(
                    filepath, url, monitor_id, is_success, error_message
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Screenshot capture timed out after 25s for monitor {monitor_id} ({url}). Using fallback image."
            )
            generate_fallback_screenshot(
                filepath, url, error_message or "Screenshot capture timed out (25s)"
            )
        except Exception as e:
            logger.warning(
                f"Failed to capture Playwright screenshot for monitor {monitor_id}: {e}. Using fallback image."
            )
            generate_fallback_screenshot(filepath, url, error_message or str(e))

    return timestamp_str

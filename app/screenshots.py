import asyncio
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from app.config import DATA_DIR

logger = logging.getLogger("site_monitor.screenshots")

# Limit concurrent headless browser processes to prevent memory/CPU spikes
_screenshot_semaphore = asyncio.Semaphore(2)

# Background Playwright shutdown tasks; held here so they are not garbage collected
# before they finish.
_cleanup_tasks: set = set()


def _keep_task(task: asyncio.Future) -> asyncio.Future:
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)
    return task


async def _stop_playwright(playwright) -> None:
    """
    Stops a Playwright instance, which ends its node driver process (and with it any
    Chromium it launched). The first step closes the driver's stdin synchronously, so the
    driver exits even if this coroutine is later cancelled or times out.
    """
    try:
        await asyncio.wait_for(playwright.stop(), timeout=15.0)
    except Exception as e:
        logger.warning(f"Playwright driver did not stop cleanly: {e}")


def _stop_playwright_when_started(start_task: asyncio.Future) -> None:
    """Stops the Playwright instance a start task produces, once it has produced one."""

    def _on_started(task: asyncio.Future) -> None:
        if task.cancelled() or task.exception() is not None:
            return
        _keep_task(task.get_loop().create_task(_stop_playwright(task.result())))

    start_task.add_done_callback(_on_started)


async def _start_playwright():
    """
    Starts Playwright without leaking its driver if the caller is cancelled.

    Playwright spawns the node driver before its start-up handshake completes. If the
    awaiting coroutine is cancelled during that handshake (a capture timeout, a check
    timeout or the watchdog restarting the worker), `async with async_playwright()` never
    runs `__aexit__`, and the driver process stays alive for the life of the container.
    The start runs as its own task here, so a cancelled caller hands the instance to a
    background stop instead of dropping it.
    """
    from playwright.async_api import async_playwright

    start_task = _keep_task(asyncio.ensure_future(async_playwright().start()))
    try:
        return await asyncio.shield(start_task)
    except asyncio.CancelledError:
        _stop_playwright_when_started(start_task)
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
    p = await _start_playwright()
    try:
        await _run_capture(p, filepath, url, monitor_id, is_success, error_message)
    finally:
        # Shielded so a cancellation here cannot skip the stop: the stop task keeps
        # running in the background and the cancellation still propagates.
        await asyncio.shield(_keep_task(asyncio.ensure_future(_stop_playwright(p))))


async def _run_capture(
    p,
    filepath: Path,
    url: str,
    monitor_id: int,
    is_success: bool,
    error_message: str,
):
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

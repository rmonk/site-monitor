import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from app.config import DATA_DIR

logger = logging.getLogger("site_monitor.screenshots")

def get_screenshots_dir() -> Path:
    s_dir = DATA_DIR / "screenshots"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir

def generate_fallback_screenshot(target_path: Path, url: str, error_msg: str):
    """
    Generates a dark placeholder image showing error details when browser navigation fails.
    """
    width, height = 1280, 720
    img = Image.new('RGB', (width, height), color=(24, 28, 36))
    draw = ImageDraw.Draw(img)

    # Header banner
    draw.rectangle([(0, 0), (width, 80)], fill=(220, 53, 69))
    draw.text((40, 25), "SITE MONITOR - CONNECTION / CHECK ERROR", fill=(255, 255, 255))

    # Error details
    y = 120
    draw.text((40, y), f"Target URL: {url}", fill=(200, 200, 200))
    y += 40
    draw.text((40, y), f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", fill=(200, 200, 200))
    y += 50
    draw.text((40, y), "Error Summary:", fill=(255, 100, 100))
    y += 35

    # Wrap or truncate error message
    lines = [error_msg[i:i+80] for i in range(0, min(len(error_msg), 400), 80)]
    for line in lines:
        draw.text((60, y), line, fill=(240, 240, 240))
        y += 30

    img.save(str(target_path), format="PNG")

async def capture_screenshot(monitor_id: int, url: str, is_success: bool, error_message: str = "") -> str:
    """
    Captures a screenshot of the target URL using Playwright (or Pillow fallback)
    and saves it to the screenshots directory.
    Returns ISO timestamp string of capture time.
    """
    s_dir = get_screenshots_dir()
    filename = f"monitor_{monitor_id}_{'success' if is_success else 'failed'}.png"
    filepath = s_dir / filename
    timestamp_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                # 10s timeout for rendering
                await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                await page.screenshot(path=str(filepath))
                logger.info(f"Captured Playwright screenshot for monitor {monitor_id} ({'success' if is_success else 'failed'})")
            except Exception as nav_err:
                logger.warning(f"Playwright navigation error for {url}: {nav_err}. Using fallback screenshot.")
                generate_fallback_screenshot(filepath, url, error_message or str(nav_err))
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Failed to launch Playwright: {e}. Using fallback image generator.")
        generate_fallback_screenshot(filepath, url, error_message or str(e))

    return timestamp_str

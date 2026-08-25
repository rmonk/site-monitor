import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from app.config import DB_PATH, INITIAL_ADMIN_USER, INITIAL_ADMIN_PASSWORD

logger = logging.getLogger("site_monitor.database")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database tables and default values."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Settings table (key-value store)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Monitors table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                check_interval INTEGER NOT NULL DEFAULT 60,
                timeout INTEGER NOT NULL DEFAULT 10,
                regex_pattern TEXT,
                failure_threshold INTEGER NOT NULL DEFAULT 1,
                repeat_alerts INTEGER DEFAULT NULL,
                repeat_interval_minutes INTEGER DEFAULT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                capture_screenshots INTEGER DEFAULT NULL,
                last_success_screenshot_time TEXT,
                last_failed_screenshot_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Add columns if migrating from older schema version
        cursor.execute("PRAGMA table_info(monitors);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "capture_screenshots" not in columns:
            cursor.execute(
                "ALTER TABLE monitors ADD COLUMN capture_screenshots INTEGER DEFAULT NULL;"
            )
        if "last_success_screenshot_time" not in columns:
            cursor.execute(
                "ALTER TABLE monitors ADD COLUMN last_success_screenshot_time TEXT;"
            )
        if "last_failed_screenshot_time" not in columns:
            cursor.execute(
                "ALTER TABLE monitors ADD COLUMN last_failed_screenshot_time TEXT;"
            )

        # Check history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS check_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status_code INTEGER,
                response_time_ms REAL,
                is_up INTEGER NOT NULL,
                regex_matched INTEGER,
                error_message TEXT,
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
            );
        """)

        # Alert state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_state (
                monitor_id INTEGER PRIMARY KEY,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                is_currently_down INTEGER NOT NULL DEFAULT 0,
                last_alert_time INTEGER DEFAULT 0,
                alert_count INTEGER NOT NULL DEFAULT 0,
                active_receipts TEXT DEFAULT '',
                receipt_acknowledged INTEGER DEFAULT 0,
                receipt_acknowledged_at INTEGER DEFAULT 0,
                receipt_acknowledged_by TEXT DEFAULT '',
                receipt_acknowledged_device TEXT DEFAULT '',
                receipt_last_checked INTEGER DEFAULT 0,
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
            );
        """)

        # Migration check for alert_state columns
        cursor.execute("PRAGMA table_info(alert_state);")
        as_columns = [row["name"] for row in cursor.fetchall()]
        if "active_receipts" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN active_receipts TEXT DEFAULT '';"
            )
        if "receipt_acknowledged" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN receipt_acknowledged INTEGER DEFAULT 0;"
            )
        if "receipt_acknowledged_at" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN receipt_acknowledged_at INTEGER DEFAULT 0;"
            )
        if "receipt_acknowledged_by" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN receipt_acknowledged_by TEXT DEFAULT '';"
            )
        if "receipt_acknowledged_device" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN receipt_acknowledged_device TEXT DEFAULT '';"
            )
        if "receipt_last_checked" not in as_columns:
            cursor.execute(
                "ALTER TABLE alert_state ADD COLUMN receipt_last_checked INTEGER DEFAULT 0;"
            )

        # Default settings if not present
        default_settings = {
            "auth_mode": "readonly_public",  # "readonly_public" or "require_login"
            "pushover_enabled": "false",
            "pushover_api_token": "",
            "pushover_user_key": "",
            "pushover_priority_down": "2",
            "pushover_emergency_retry": "60",
            "pushover_emergency_expire": "3600",
            "last_test_receipt": "",
            "default_repeat_alerts": "true",
            "default_repeat_interval_minutes": "60",
            "default_capture_screenshots": "true",
            "theme_mode": "light",
            "theme_color_preset": "default",
            "theme_custom_primary": "#0d6efd",
            "theme_custom_bg": "#f8f9fa",
            "theme_custom_card": "#ffffff",
            "theme_custom_text": "#212529",
            "heartbeat_ping_url": "",
            "heartbeat_ping_interval_minutes": "15",
        }

        for key, val in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val)
            )

        # Create initial admin user if no users exist
        cursor.execute("SELECT COUNT(*) as count FROM users")
        user_count = cursor.fetchone()["count"]
        if user_count == 0:
            from app.auth import hash_password

            pwd_hash = hash_password(INITIAL_ADMIN_PASSWORD)
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (INITIAL_ADMIN_USER, pwd_hash),
            )
            logger.info(f"Initialized initial admin user: '{INITIAL_ADMIN_USER}'")


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def get_setting_int(key: str, default: int = 0) -> int:
    """Retrieves an integer setting with fallback."""
    val = get_setting(key, str(default)).strip()
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Retrieves a boolean setting evaluating truthy strings."""
    val = get_setting(key, "true" if default else "false").strip().lower()
    return val in ("true", "1", "yes", "on", "enabled")


def set_setting(key: str, value: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_all_settings() -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in cursor.fetchall()}


def parse_receipt_list(receipts_str: Optional[str]) -> list:
    """Splits a comma-separated receipts string into a cleaned list of receipt IDs."""
    if not receipts_str:
        return []
    return [r.strip() for r in str(receipts_str).split(",") if r.strip()]


def format_utc_timestamp(ts: Optional[int]) -> str:
    """Formats a Unix epoch timestamp into UTC ISO/readable string, or empty string if None/0."""
    if not ts or ts <= 0:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

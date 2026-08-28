import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
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

        # Passkeys (WebAuthn credentials) table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS passkeys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                credential_id TEXT UNIQUE NOT NULL,
                public_key TEXT NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL,
                aaguid TEXT,
                transports TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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
            "default_time_display": "utc",
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


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Retrieves a user row by username."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves a user row by user ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_passkeys(user_id: int) -> List[Dict[str, Any]]:
    """Returns all registered passkeys for a given user ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id, credential_id, name, aaguid, transports, created_at, last_used_at, sign_count "
            "FROM passkeys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_passkey_by_credential_id(credential_id: str) -> Optional[Dict[str, Any]]:
    """Looks up a passkey record by its unique credential ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM passkeys WHERE credential_id = ?",
            (credential_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_passkey_credentials() -> List[Dict[str, Any]]:
    """Returns all registered passkey credentials across all users."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id, credential_id, public_key, sign_count, transports FROM passkeys"
        )
        return [dict(row) for row in cursor.fetchall()]


def save_passkey(
    user_id: int,
    credential_id: str,
    public_key: str,
    sign_count: int,
    name: str,
    aaguid: Optional[str] = None,
    transports: Optional[str] = None,
) -> int:
    """Saves a new passkey credential to the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO passkeys (user_id, credential_id, public_key, sign_count, name, aaguid, transports)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, credential_id, public_key, sign_count, name, aaguid, transports),
        )
        return cursor.lastrowid


def update_passkey_usage(credential_id: str, sign_count: int):
    """Updates the sign count and last used timestamp for a passkey."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE passkeys
            SET sign_count = ?, last_used_at = CURRENT_TIMESTAMP
            WHERE credential_id = ?
            """,
            (sign_count, credential_id),
        )


def delete_passkey(passkey_id: int, user_id: int) -> bool:
    """Deletes a passkey belonging to a specific user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM passkeys WHERE id = ? AND user_id = ?",
            (passkey_id, user_id),
        )
        return cursor.rowcount > 0


# ==========================================
# Uptime Periods & Statistics
# ==========================================

VALID_UPTIME_PERIODS: Dict[str, Tuple[Optional[int], str]] = {
    "1h": (3600, "1 Hour"),
    "24h": (86400, "1 Day"),
    "7d": (604800, "1 Week"),
    "30d": (2592000, "1 Month"),
    "all": (None, "All Time"),
}


def canonicalize_period(period: Optional[str]) -> str:
    """Normalizes period query strings to standard canonical keys: 1h, 24h, 7d, 30d, all."""
    if not period:
        return "24h"
    p = str(period).strip().lower()
    mapping = {
        "1h": "1h",
        "1hour": "1h",
        "1hr": "1h",
        "1d": "24h",
        "24h": "24h",
        "1day": "24h",
        "7d": "7d",
        "1w": "7d",
        "1week": "7d",
        "30d": "30d",
        "1m": "30d",
        "1month": "30d",
        "all": "all",
        "alltime": "all",
    }
    return mapping.get(p, "24h")


def get_uptime_statistics(
    period: str = "24h",
) -> Tuple[float, Dict[int, Dict[str, Any]]]:
    """
    Computes overall and per-monitor uptime percentages from check_history over the given period.
    Returns (overall_uptime_pct, {monitor_id: {"total_checks": int, "up_checks": int, "uptime_pct": Optional[float]}}).
    """
    canon_p = canonicalize_period(period)
    seconds, _ = VALID_UPTIME_PERIODS.get(canon_p, (86400, "1 Day"))

    with get_db() as conn:
        cursor = conn.cursor()
        if seconds is not None:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
            cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """
                SELECT 
                    monitor_id,
                    COUNT(*) AS total_checks,
                    SUM(CASE WHEN is_up = 1 THEN 1 ELSE 0 END) AS up_checks
                FROM check_history
                WHERE timestamp >= ?
                GROUP BY monitor_id
                """,
                (cutoff_str,),
            )
        else:
            cursor.execute("""
                SELECT 
                    monitor_id,
                    COUNT(*) AS total_checks,
                    SUM(CASE WHEN is_up = 1 THEN 1 ELSE 0 END) AS up_checks
                FROM check_history
                GROUP BY monitor_id
                """)
        rows = cursor.fetchall()

    monitor_stats: Dict[int, Dict[str, Any]] = {}
    overall_total = 0
    overall_up = 0

    for r in rows:
        m_id = r["monitor_id"]
        total = r["total_checks"] or 0
        up = r["up_checks"] or 0
        overall_total += total
        overall_up += up
        pct = round((up / total * 100.0), 1) if total > 0 else None
        monitor_stats[m_id] = {
            "total_checks": total,
            "up_checks": up,
            "uptime_pct": pct,
        }

    overall_pct = (
        round((overall_up / overall_total * 100.0), 1) if overall_total > 0 else 100.0
    )
    return overall_pct, monitor_stats

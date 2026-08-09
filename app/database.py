import sqlite3
import logging
from contextlib import contextmanager
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

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
                FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
            );
        """)

        # Default settings if not present
        default_settings = {
            "auth_mode": "readonly_public",  # "readonly_public" or "require_login"
            "pushover_enabled": "false",
            "pushover_api_token": "",
            "pushover_user_key": "",
            "default_repeat_alerts": "true",
            "default_repeat_interval_minutes": "60"
        }

        for key, val in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val)
            )

        # Create initial admin user if no users exist
        cursor.execute("SELECT COUNT(*) as count FROM users")
        user_count = cursor.fetchone()["count"]
        if user_count == 0:
            from app.auth import hash_password
            pwd_hash = hash_password(INITIAL_ADMIN_PASSWORD)
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (INITIAL_ADMIN_USER, pwd_hash)
            )
            logger.info(f"Initialized initial admin user: '{INITIAL_ADMIN_USER}'")

def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )

def get_all_settings() -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in cursor.fetchall()}

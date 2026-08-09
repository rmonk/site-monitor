import os
import secrets
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", os.getenv("DATABASE_URL", DATA_DIR / "site-monitor.db")))

# Secret Key for Sessions
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # Generate a temporary key if none provided
    SECRET_KEY = secrets.token_hex(32)

# Initial Admin Credentials
INITIAL_ADMIN_USER = os.getenv("INITIAL_ADMIN_USER", os.getenv("ADMIN_USER", "admin"))
INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "admin123"))

# App Options
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

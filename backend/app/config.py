import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
CWA_API_KEY = os.getenv("CWA_API_KEY", "")
PORT = int(os.getenv("PORT", "8000"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smart_commute.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

TDX_CLIENT_ID = os.getenv("TDX_CLIENT_ID", "")
TDX_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET", "")
UNDELETE_API_KEY = os.getenv("UNDELETE_API_KEY", "default-secret-key-change-me")

# LIFF application ID. Not a secret — it appears in every URL the user taps — but
# it is deployment configuration, so it does not belong in source. See known-issues D-6.
LIFF_ID = os.getenv("LIFF_ID", "2009982765-aKb3T2ca")
LIFF_URL = f"https://liff.line.me/{LIFF_ID}"

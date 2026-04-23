import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
CWA_API_KEY = os.getenv("CWA_API_KEY", "").strip()

TDX_CLIENT_ID = os.getenv("TDX_CLIENT_ID", "").strip()
TDX_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET", "").strip()

PORT = int(os.getenv("PORT", "8000"))

print(f"[config] env_path={ENV_PATH}")
print(f"[config] env_exists={ENV_PATH.exists()}")
print(
    f"[config] loaded_keys="
    f"{[k for k in ['LINE_CHANNEL_SECRET', 'LINE_CHANNEL_ACCESS_TOKEN', 'PORT', 'GOOGLE_MAPS_API_KEY', 'CWA_API_KEY', 'TDX_CLIENT_ID', 'TDX_CLIENT_SECRET'] if os.getenv(k) is not None]}"
)
print(f"[config] LINE_CHANNEL_SECRET loaded={bool(LINE_CHANNEL_SECRET)} len={len(LINE_CHANNEL_SECRET)}")
print(f"[config] LINE_CHANNEL_ACCESS_TOKEN loaded={bool(LINE_CHANNEL_ACCESS_TOKEN)} len={len(LINE_CHANNEL_ACCESS_TOKEN)}")
print(f"[config] GOOGLE_MAPS_API_KEY loaded={bool(GOOGLE_MAPS_API_KEY)}")
print(f"[config] CWA_API_KEY loaded={bool(CWA_API_KEY)}")
print(f"[config] TDX_CLIENT_ID loaded={bool(TDX_CLIENT_ID)}")
print(f"[config] TDX_CLIENT_SECRET loaded={bool(TDX_CLIENT_SECRET)}")
import os
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH, override=True)
ENV_VALUES = dotenv_values(ENV_PATH)


def read_env(name: str, default: str = "") -> str:
    value = ENV_VALUES.get(name)
    if value is None or value == "":
        value = os.getenv(name, default)
    return value or default


LINE_CHANNEL_SECRET = read_env("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = read_env("LINE_CHANNEL_ACCESS_TOKEN")
GOOGLE_MAPS_API_KEY = read_env("GOOGLE_MAPS_API_KEY")
CWA_API_KEY = read_env("CWA_API_KEY")
TDX_CLIENT_ID = read_env("TDX_CLIENT_ID")
TDX_CLIENT_SECRET = read_env("TDX_CLIENT_SECRET")
PORT = int(read_env("PORT", "8000"))

print(f"[config] env_path={ENV_PATH}")
print(f"[config] env_exists={ENV_PATH.exists()}")
print(f"[config] loaded_keys={list(ENV_VALUES.keys())}")
print(f"[config] LINE_CHANNEL_SECRET loaded={bool(LINE_CHANNEL_SECRET)} len={len(LINE_CHANNEL_SECRET)}")
print(f"[config] LINE_CHANNEL_ACCESS_TOKEN loaded={bool(LINE_CHANNEL_ACCESS_TOKEN)} len={len(LINE_CHANNEL_ACCESS_TOKEN)}")
print(f"[config] GOOGLE_MAPS_API_KEY loaded={bool(GOOGLE_MAPS_API_KEY)}")
print(f"[config] CWA_API_KEY loaded={bool(CWA_API_KEY)}")
print(f"[config] TDX_CLIENT_ID loaded={bool(TDX_CLIENT_ID)}")
print(f"[config] TDX_CLIENT_SECRET loaded={bool(TDX_CLIENT_SECRET)}")
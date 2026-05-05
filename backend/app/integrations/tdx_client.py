import asyncio
import httpx
import time
from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET
from app.integrations.redis_cache import get_cache, set_cache

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"

async def _get_access_token() -> str | None:
    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        return None
    
    cache_key = "tdx:access_token"
    cached_token = await get_cache(cache_key)
    if cached_token:
        return cached_token

    payload = {
        "content-type": "application/x-www-form-urlencoded",
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            response = await client.post(AUTH_URL, data=payload)
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 86400)
            
            if token:
                # Cache token a bit shorter than expiration to be safe
                await set_cache(cache_key, token, expire_seconds=expires_in - 300)
                return token
    except Exception as e:
        print(f"[tdx] auth error: {e}")
    return None

async def fetch_tdx_data(endpoint: str, params: dict = None, cache_seconds: int = 300, retries: int = 2) -> list | dict | None:
    # 1. Try Cache
    cache_key = f"tdx:api:{endpoint}:{params}"
    cached = await get_cache(cache_key)
    if cached is not None:
        return cached

    # 2. Get Token
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # 3. Request with Retry
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                await set_cache(cache_key, data, expire_seconds=cache_seconds)
                return data
        except httpx.HTTPError as e:
            print(f"[tdx] fetch error on {endpoint} (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(0.5)
            continue
    return None

async def get_bus_estimated_time_of_arrival(city: str, route_name: str) -> list | None:
    # Cache for 30 seconds as requested
    endpoint = f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"
    params = {"$format": "JSON"}
    return await fetch_tdx_data(endpoint, params=params, cache_seconds=30)

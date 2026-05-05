import asyncio
import hashlib
import httpx
import time
from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET
from app.integrations.redis_cache import get_cache, set_cache

AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_URL = "https://tdx.transportdata.tw/api/basic"

# In-memory fallback cache for TDX (avoids Redis round-trip for hot paths)
_MEM_CACHE: dict[str, tuple[float, object]] = {}
_MEM_CACHE_TTL: dict[str, int] = {}

def _mem_get(key: str):
    entry = _MEM_CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    ttl = _MEM_CACHE_TTL.get(key, 300)
    if time.time() - ts > ttl:
        _MEM_CACHE.pop(key, None)
        return None
    return val

def _mem_set(key: str, val, ttl: int = 300):
    _MEM_CACHE[key] = (time.time(), val)
    _MEM_CACHE_TTL[key] = ttl


async def _get_access_token() -> str | None:
    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        return None

    cache_key = "tdx:access_token"
    # Check in-memory first (fastest)
    mem_token = _mem_get(cache_key)
    if mem_token:
        return mem_token
    # Then Redis
    cached_token = await get_cache(cache_key)
    if cached_token:
        _mem_set(cache_key, cached_token, ttl=3300)
        return cached_token

    payload = {
        "content-type": "application/x-www-form-urlencoded",
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            response = await client.post(AUTH_URL, data=payload)
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 86400)
            if token:
                ttl = max(60, expires_in - 300)
                await set_cache(cache_key, token, expire_seconds=ttl)
                _mem_set(cache_key, token, ttl=ttl)
                return token
    except Exception as e:
        print(f"[tdx] auth error: {e}")
    return None


async def fetch_tdx_data(
    endpoint: str,
    params: dict = None,
    cache_seconds: int = 300,
    retries: int = 2,
) -> list | dict | None:
    # Stable cache key using sorted params
    param_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    raw_key = f"tdx:api:{endpoint}:{param_str}"
    cache_key = "tdx:api:" + hashlib.md5(raw_key.encode()).hexdigest()

    # 1. In-memory cache (sub-millisecond)
    mem_val = _mem_get(cache_key)
    if mem_val is not None:
        return mem_val

    # 2. Redis cache
    cached = await get_cache(cache_key)
    if cached is not None:
        _mem_set(cache_key, cached, ttl=cache_seconds)
        return cached

    # 3. Fetch from TDX
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                await set_cache(cache_key, data, expire_seconds=cache_seconds)
                _mem_set(cache_key, data, ttl=cache_seconds)
                return data
        except httpx.HTTPError as e:
            print(f"[tdx] fetch error on {endpoint} (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(0.3)
    return None


async def get_bus_estimated_time_of_arrival(city: str, route_name: str) -> list | None:
    endpoint = f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"
    params = {"$format": "JSON"}
    # Bus ETA: 90 sec cache (realtime data)
    return await fetch_tdx_data(endpoint, params=params, cache_seconds=90)

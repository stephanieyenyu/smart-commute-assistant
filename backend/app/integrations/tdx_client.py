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
        print("[tdx-auth] TDX_CLIENT_ID or TDX_CLIENT_SECRET is not set")
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
    print(f"[tdx-auth] Requesting access token with client_id={TDX_CLIENT_ID}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            response = await client.post(AUTH_URL, data=payload)
            print(f"[tdx-auth] Response status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 86400)
            if token:
                ttl = max(60, expires_in - 300)
                await set_cache(cache_key, token, expire_seconds=ttl)
                _mem_set(cache_key, token, ttl=ttl)
                print(f"[tdx-auth] Successfully obtained access token, expires in {expires_in}s")
                return token
    except Exception as e:
        print(f"[tdx-auth] exception: {e}")
        print(f"[tdx-auth] Error details - AUTH_URL={AUTH_URL}, client_id={TDX_CLIENT_ID}")
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

    print(f"[tdx-fetch] endpoint={endpoint}, params={params}, cache_key={cache_key}")

    # 1. In-memory cache (sub-millisecond)
    mem_val = _mem_get(cache_key)
    if mem_val is not None:
        print(f"[tdx-fetch] Cache hit (in-memory)")
        return mem_val

    # 2. Redis cache
    cached = await get_cache(cache_key)
    if cached is not None:
        _mem_set(cache_key, cached, ttl=cache_seconds)
        print(f"[tdx-fetch] Cache hit (Redis)")
        return cached

    # 3. Fetch from TDX
    token = await _get_access_token()
    if not token:
        print(f"[tdx-fetch] Failed to get access token")
        return None
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{BASE_URL}{endpoint}"

    print(f"[tdx-fetch] Fetching from TDX API: url={url}, headers={headers}, params={params}")

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
                response = await client.get(url, headers=headers, params=params)
                print(f"[tdx-fetch] Response status: {response.status_code} (attempt {attempt+1}/{retries})")
                response.raise_for_status()
                data = response.json()
                await set_cache(cache_key, data, expire_seconds=cache_seconds)
                _mem_set(cache_key, data, ttl=cache_seconds)
                print(f"[tdx-fetch] Success: received data")
                return data
        except httpx.HTTPError as e:
            print(f"[tdx-fetch] HTTP error on {endpoint} (attempt {attempt+1}/{retries}): {e}")
            print(f"[tdx-fetch] Error details - url={url}, params={params}")
            if attempt < retries - 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[tdx-fetch] Exception on {endpoint} (attempt {attempt+1}/{retries}): {e}")
            print(f"[tdx-fetch] Error details - url={url}, params={params}")
            if attempt < retries - 1:
                await asyncio.sleep(0.3)
    return None


async def get_bus_estimated_time_of_arrival(city: str, route_name: str) -> list | None:
    endpoint = f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"
    params = {"$format": "JSON"}
    print(f"[tdx-bus-eta] city={city}, route_name={route_name}, endpoint={endpoint}")
    # Bus ETA: 90 sec cache (realtime data)
    return await fetch_tdx_data(endpoint, params=params, cache_seconds=90)

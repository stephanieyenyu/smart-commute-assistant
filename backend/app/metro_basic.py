import time
from typing import Any

import httpx

from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET

TDX_AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_BASE_URL = "https://tdx.transportdata.tw/api/basic/v2"

_TOKEN_CACHE: dict[str, Any] = {
    "access_token": None,
    "expires_at": 0,
}

_METRO_NEARBY_CACHE: dict[str, tuple[float, dict | None]] = {}
METRO_CACHE_SECONDS = 180

# 關鍵修正：TDX 這個 Nearby API 最大搜尋半徑為 1000 公尺
MAX_NEARBY_RADIUS_M = 1000


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dx = (lng2 - lng1) * 101320
    dy = (lat2 - lat1) * 110540
    return (dx * dx + dy * dy) ** 0.5


async def get_access_token() -> str:
    now = time.time()
    if _TOKEN_CACHE["access_token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return _TOKEN_CACHE["access_token"]

    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        raise RuntimeError("TDX_CLIENT_ID or TDX_CLIENT_SECRET is missing")

    data = {
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.post(
            TDX_AUTH_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()

    access_token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))

    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = now + expires_in

    return access_token


async def tdx_get(path: str, params: dict | None = None) -> list[dict]:
    token = await get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = f"{TDX_BASE_URL}{path}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(url, headers=headers, params=params)

        if response.status_code >= 400:
            print(f"[metro] nearby lookup failed status={response.status_code} body={response.text}")

        response.raise_for_status()
        data = response.json()

    if isinstance(data, list):
        return data
    return []


def _extract_station_name(station: dict) -> str:
    station_name_obj = station.get("StationName") or {}
    return station_name_obj.get("Zh_tw") or station_name_obj.get("En") or "未知站名"


async def get_nearest_metro_station_async(
    lat: float,
    lng: float,
    radius_m: int = 1000,
) -> dict | None:
    # 關鍵修正：不允許超過 TDX Nearby 上限
    radius_m = min(radius_m, MAX_NEARBY_RADIUS_M)

    cache_key = f"{round(lat, 4)}|{round(lng, 4)}|{radius_m}"
    now = time.time()
    cached = _METRO_NEARBY_CACHE.get(cache_key)
    if cached and now - cached[0] <= METRO_CACHE_SECONDS:
        return cached[1]

    params = {
        "$spatialFilter": f"nearby({lat},{lng},{radius_m})",
        "$top": 30,
        "$format": "JSON",
    }

    result: list[dict] = []
    try:
        result = await tdx_get("/Rail/Metro/Station/NearBy", params=params)
    except Exception as e:
        print(f"[metro-snapshot] failed: {e}")
        _METRO_NEARBY_CACHE[cache_key] = (now, None)
        return None

    if not result:
        _METRO_NEARBY_CACHE[cache_key] = (now, None)
        return None

    best_station = None
    best_distance = None

    for station in result:
        station_position = station.get("StationPosition") or {}
        station_lat = station_position.get("PositionLat")
        station_lng = station_position.get("PositionLon")

        if station_lat is None or station_lng is None:
            continue

        dist = _distance_m(lat, lng, station_lat, station_lng)
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_station = {
                "id": station.get("StationID"),
                "name": _extract_station_name(station),
                "lat": station_lat,
                "lng": station_lng,
                "distance_m": dist,
            }

    _METRO_NEARBY_CACHE[cache_key] = (now, best_station)
    return best_station
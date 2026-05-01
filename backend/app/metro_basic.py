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
_METRO_EXIT_CACHE: dict[str, tuple[float, list[dict]]] = {}
METRO_CACHE_SECONDS = 180
MAX_NEARBY_RADIUS_M = 1000


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dx = (lng2 - lng1) * 101320
    dy = (lat2 - lat1) * 110540
    return (dx * dx + dy * dy) ** 0.5


async def get_access_token() -> str:
    now = time.time()
    token = _TOKEN_CACHE.get("access_token")
    expires_at = _TOKEN_CACHE.get("expires_at", 0)

    if token and now < expires_at - 60:
        return token

    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        raise RuntimeError("TDX credentials missing")

    data = {
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
        response = await client.post(
            TDX_AUTH_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()

    _TOKEN_CACHE["access_token"] = payload["access_token"]
    _TOKEN_CACHE["expires_at"] = now + int(payload.get("expires_in", 3600))
    return _TOKEN_CACHE["access_token"]


async def tdx_get(path: str, params: dict | None = None) -> list[dict]:
    token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    url = f"{TDX_BASE_URL}{path}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
        response = await client.get(url, headers=headers, params=params)
        if response.status_code >= 400:
            print(f"[metro-api] status={response.status_code} path={path} body={response.text}")
        response.raise_for_status()
        data = response.json()

    return data if isinstance(data, list) else []


def _station_name(station: dict) -> str:
    station_name_obj = station.get("StationName") or {}
    return station_name_obj.get("Zh_tw") or station_name_obj.get("En") or "無法識別捷運站"


def _exit_name(exit_info: dict) -> str:
    exit_name_obj = exit_info.get("ExitName") or {}
    return exit_name_obj.get("Zh_tw") or exit_name_obj.get("En") or "出口"


def _simplify_station_exit(exit_info: dict) -> dict | None:
    exit_position = exit_info.get("ExitPosition") or {}
    lat = exit_position.get("PositionLat")
    lng = exit_position.get("PositionLon")

    return {
        "station_id": exit_info.get("StationID"),
        "exit_id": exit_info.get("ExitID"),
        "name": _exit_name(exit_info),
        "lat": lat,
        "lng": lng,
    }


async def get_station_exits_async(
    station_id: str | None,
    operator_id: str = "TRTC",
) -> list[dict]:
    if not station_id:
        return []

    cache_key = f"{operator_id}|{station_id}"
    now = time.time()
    cached = _METRO_EXIT_CACHE.get(cache_key)
    if cached and now - cached[0] <= METRO_CACHE_SECONDS:
        return cached[1]

    params = {
        "$filter": f"StationID eq '{station_id}'",
        "$format": "JSON",
    }

    try:
        rows = await tdx_get(f"/Rail/Metro/StationExit/{operator_id}", params=params)
    except Exception as e:
        print(f"[metro-exit] station_id={station_id} error={e}")
        rows = []

    exits = [
        simplified
        for row in rows
        if (simplified := _simplify_station_exit(row)) is not None
    ]
    _METRO_EXIT_CACHE[cache_key] = (now, exits)
    return exits


async def get_nearest_metro_station_async(
    lat: float,
    lng: float,
    radius_m: int = 1000,
) -> dict | None:
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

    try:
        rows = await tdx_get("/Rail/Metro/Station/NearBy", params=params)
    except Exception as e:
        print(f"[metro-nearby] error={e}")
        _METRO_NEARBY_CACHE[cache_key] = (now, None)
        return None

    if not rows:
        _METRO_NEARBY_CACHE[cache_key] = (now, None)
        return None

    best_station = None
    best_distance = None

    for station in rows:
        pos = station.get("StationPosition") or {}
        station_lat = pos.get("PositionLat")
        station_lng = pos.get("PositionLon")
        if station_lat is None or station_lng is None:
            continue

        dist = _distance_m(lat, lng, station_lat, station_lng)
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_station = {
                "id": station.get("StationID"),
                "name": _station_name(station),
                "lat": station_lat,
                "lng": station_lng,
                "distance_m": dist,
            }

    _METRO_NEARBY_CACHE[cache_key] = (now, best_station)
    return best_station

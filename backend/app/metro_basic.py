import time

import httpx

from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic/v2"

_token_cache = {
    "access_token": None,
    "expire_at": 0.0,
}


async def _get_access_token() -> str:
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expire_at"]:
        return _token_cache["access_token"]

    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        raise RuntimeError("TDX_CLIENT_ID or TDX_CLIENT_SECRET is missing")

    data = {
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), verify=False) as client:
        response = await client.post(TDX_TOKEN_URL, data=data)
        response.raise_for_status()
        payload = response.json()

    access_token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))

    _token_cache["access_token"] = access_token
    _token_cache["expire_at"] = now + max(300, expires_in - 60)
    return access_token


def _station_name(station: dict) -> str:
    station_name = station.get("StationName", {}) or {}
    if isinstance(station_name, dict):
        return station_name.get("Zh_tw") or station_name.get("En") or "未知站名"
    return station.get("name") or "未知站名"


def _station_lat(station: dict):
    return (
        station.get("lat")
        or station.get("latitude")
        or station.get("StationPosition", {}).get("PositionLat")
    )


def _station_lng(station: dict):
    return (
        station.get("lng")
        or station.get("longitude")
        or station.get("StationPosition", {}).get("PositionLon")
    )


def _distance_sq(lat1, lng1, lat2, lng2):
    if None in [lat1, lng1, lat2, lng2]:
        return 999999999
    return (lat1 - lat2) ** 2 + (lng1 - lng2) ** 2


async def get_nearby_metro_stations(lat: float, lng: float, distance_m: int = 1500, top: int = 8):
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    path = "/Rail/Metro/Station/NearBy"
    params = {
        "$spatialFilter": f"nearby({lat},{lng},{distance_m})",
        "$top": top,
        "$format": "JSON",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), verify=False) as client:
        response = await client.get(f"{TDX_API_BASE}{path}", params=params, headers=headers)
        if response.status_code >= 400:
            print(f"[metro] nearby lookup failed status={response.status_code} body={response.text}")
        response.raise_for_status()
        return response.json()


def normalize_station(station: dict) -> dict:
    return {
        "id": station.get("StationID"),
        "name": _station_name(station),
        "lat": _station_lat(station),
        "lng": _station_lng(station),
        "raw": station,
    }


def get_best_station_from_raw(raw_stations: list[dict], lat: float, lng: float) -> dict | None:
    if not raw_stations:
        return None

    normalized = [normalize_station(s) for s in raw_stations]
    normalized.sort(key=lambda s: _distance_sq(lat, lng, s.get("lat"), s.get("lng")))
    return normalized[0]


async def get_nearest_metro_station_async(lat: float, lng: float):
    raw_stations = await get_nearby_metro_stations(lat, lng)
    return get_best_station_from_raw(raw_stations, lat, lng)
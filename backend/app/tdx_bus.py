import time
from typing import Any

import httpx

from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET
from app.address_utils import normalize_city_name

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic/v2"

CITY_PATH_MAP = {
    "臺北市": "Taipei",
    "新北市": "NewTaipei",
    "桃園市": "Taoyuan",
    "臺中市": "Taichung",
    "臺南市": "Tainan",
    "高雄市": "Kaohsiung",
    "基隆市": "Keelung",
    "新竹市": "Hsinchu",
    "嘉義市": "Chiayi",
    "新竹縣": "HsinchuCounty",
    "苗栗縣": "MiaoliCounty",
    "彰化縣": "ChanghuaCounty",
    "南投縣": "NantouCounty",
    "雲林縣": "YunlinCounty",
    "嘉義縣": "ChiayiCounty",
    "屏東縣": "PingtungCounty",
    "宜蘭縣": "YilanCounty",
    "花蓮縣": "HualienCounty",
    "臺東縣": "TaitungCounty",
    "澎湖縣": "PenghuCounty",
    "金門縣": "KinmenCounty",
    "連江縣": "LienchiangCounty",
}

_token_cache = {
    "access_token": None,
    "expire_at": 0.0,
}


def _to_city_path(city_name: str | None) -> str:
    city_name = normalize_city_name(city_name)
    if not city_name:
        raise ValueError("city_name is missing")
    mapped = CITY_PATH_MAP.get(city_name)
    if not mapped:
        raise ValueError(f"unsupported city_name: {city_name}")
    return mapped


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


async def _get(path: str, params: dict[str, Any] | None = None):
    token = await _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), verify=False) as client:
        response = await client.get(f"{TDX_API_BASE}{path}", params=params, headers=headers)
        if response.status_code >= 400:
            print(f"[tdx] GET {path} failed status={response.status_code} body={response.text}")
        response.raise_for_status()
        return response.json()


async def get_nearby_stops(city_name: str, lat: float, lng: float, distance_m: int = 500, top: int = 8):
    city_path = _to_city_path(city_name)
    path = f"/Bus/Stop/NearBy/City/{city_path}"
    params = {
        "$spatialFilter": f"nearby({lat},{lng},{distance_m})",
        "$top": top,
        "$format": "JSON",
    }
    return await _get(path, params=params)


async def get_estimated_arrivals(city_name: str, stop_uid: str | None = None, stop_id: str | None = None):
    city_path = _to_city_path(city_name)
    path = f"/Bus/EstimatedTimeOfArrival/City/{city_path}"

    if not stop_uid and not stop_id:
        raise ValueError("stop_uid or stop_id is required")

    if stop_uid:
        filter_expr = f"StopUID eq '{stop_uid}'"
    else:
        filter_expr = f"StopID eq '{stop_id}'"

    params = {
        "$filter": filter_expr,
        "$format": "JSON",
    }
    return await _get(path, params=params)


def simplify_stop_list(raw_stops: list[dict]) -> list[dict]:
    simplified = []

    for stop in raw_stops or []:
        stop_name_obj = stop.get("StopName", {})
        stop_position = stop.get("StopPosition", {}) or {}

        simplified.append({
            "stop_uid": stop.get("StopUID"),
            "stop_id": stop.get("StopID"),
            "stop_name": stop_name_obj.get("Zh_tw") or stop_name_obj.get("En") or "未知站牌",
            "lat": stop_position.get("PositionLat"),
            "lng": stop_position.get("PositionLon"),
        })

    return simplified


def simplify_eta_list(raw_eta_list: list[dict]) -> list[dict]:
    simplified = []

    for item in raw_eta_list or []:
        route_name_obj = item.get("RouteName", {}) or {}
        subroute_name_obj = item.get("SubRouteName", {}) or {}

        estimate_seconds = item.get("EstimateTime")
        eta_min = None
        if estimate_seconds is not None:
            try:
                eta_min = max(1, round(int(estimate_seconds) / 60))
            except Exception:
                eta_min = None

        simplified.append({
            "route_name": route_name_obj.get("Zh_tw") or route_name_obj.get("En") or "未知路線",
            "subroute_name": subroute_name_obj.get("Zh_tw") or subroute_name_obj.get("En"),
            "eta_min": eta_min,
            "direction": item.get("Direction"),
            "stop_status": item.get("StopStatus"),
        })

    simplified.sort(
        key=lambda x: (99999 if x["eta_min"] is None else x["eta_min"], x["route_name"])
    )
    return simplified
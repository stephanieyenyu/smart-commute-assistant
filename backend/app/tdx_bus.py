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

_CITY_STOP_PAGE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_NEARBY_STOPS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_ETA_CACHE: dict[str, tuple[float, list[dict]]] = {}

CITY_STOP_PAGE_CACHE_SECONDS = 600
NEARBY_STOPS_CACHE_SECONDS = 60
ETA_CACHE_SECONDS = 20

CITY_STOPS_PAGE_SIZE = 300
CITY_STOPS_MAX_PAGES = 4

_BUS_API_COOLDOWN_UNTIL = 0.0
BUS_API_COOLDOWN_SECONDS = 120


CITY_EN_MAP = {
    "臺北市": "Taipei",
    "台北市": "Taipei",
    "Taipei City": "Taipei",
    "Taipei": "Taipei",
    "新北市": "NewTaipei",
    "New Taipei City": "NewTaipei",
    "NewTaipei": "NewTaipei",
    "桃園市": "Taoyuan",
    "Taoyuan City": "Taoyuan",
    "Taoyuan": "Taoyuan",
    "臺中市": "Taichung",
    "台中市": "Taichung",
    "Taichung City": "Taichung",
    "Taichung": "Taichung",
    "臺南市": "Tainan",
    "台南市": "Tainan",
    "Tainan City": "Tainan",
    "Tainan": "Tainan",
    "高雄市": "Kaohsiung",
    "Kaohsiung City": "Kaohsiung",
    "Kaohsiung": "Kaohsiung",
    "基隆市": "Keelung",
    "Keelung": "Keelung",
    "新竹市": "Hsinchu",
    "Hsinchu City": "Hsinchu",
    "Hsinchu": "Hsinchu",
}


def normalize_city_en(city_name: str | None) -> str | None:
    if not city_name:
        return None
    return CITY_EN_MAP.get(city_name.strip(), city_name.strip())


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
    global _BUS_API_COOLDOWN_UNTIL

    if time.time() < _BUS_API_COOLDOWN_UNTIL:
        raise RuntimeError("TDX bus API cooldown active")

    token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = f"{TDX_BASE_URL}{path}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.0)) as client:
        response = await client.get(url, headers=headers, params=params)

        if response.status_code == 429:
            _BUS_API_COOLDOWN_UNTIL = time.time() + BUS_API_COOLDOWN_SECONDS
            print(f"[tdx-bus] 429 cooldown={BUS_API_COOLDOWN_SECONDS}s path={path} body={response.text}")
        elif response.status_code >= 400:
            print(f"[tdx-bus] status={response.status_code} path={path} body={response.text}")

        response.raise_for_status()
        data = response.json()

    return data if isinstance(data, list) else []


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dx = (lng2 - lng1) * 101320
    dy = (lat2 - lat1) * 110540
    return (dx * dx + dy * dy) ** 0.5


def _dedupe_by_uid_or_id(stops: list[dict]) -> list[dict]:
    seen = set()
    deduped = []

    for stop in stops:
        key = stop.get("stop_uid") or stop.get("stop_id") or stop.get("stop_name")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(stop)

    return deduped


def simplify_stop_list(raw_stops: list[dict]) -> list[dict]:
    simplified: list[dict] = []

    for stop in raw_stops or []:
        # 已經是簡化格式
        if "stop_name" in stop and ("stop_uid" in stop or "stop_id" in stop):
            simplified.append({
                "stop_uid": stop.get("stop_uid"),
                "stop_id": stop.get("stop_id"),
                "stop_name": stop.get("stop_name") or "無法識別站牌",
                "lat": stop.get("lat"),
                "lng": stop.get("lng"),
                "distance_m": stop.get("distance_m"),
            })
            continue

        stop_name_obj = stop.get("StopName") or {}
        stop_position = stop.get("StopPosition") or {}
        if not stop_position:
            stop_position = stop.get("StationPosition") or {}

        name_zh = stop_name_obj.get("Zh_tw") or stop_name_obj.get("En") or "無法識別站牌"

        simplified.append({
            "stop_uid": stop.get("StopUID"),
            "stop_id": stop.get("StopID"),
            "stop_name": name_zh,
            "lat": stop_position.get("PositionLat"),
            "lng": stop_position.get("PositionLon"),
            "distance_m": stop.get("distance_m"),
        })

    return _dedupe_by_uid_or_id(simplified)


def simplify_eta_list(raw_eta_list: list[dict]) -> list[dict]:
    simplified: list[dict] = []

    for item in raw_eta_list or []:
        route_name_obj = item.get("RouteName") or {}
        subroute_name_obj = item.get("SubRouteName") or {}

        route_name = route_name_obj.get("Zh_tw") or route_name_obj.get("En") or "無路線資訊"
        subroute_name = subroute_name_obj.get("Zh_tw") or subroute_name_obj.get("En")

        estimate_time = item.get("EstimateTime")
        eta_min = None
        if estimate_time is not None:
            try:
                eta_min = max(0, round(int(estimate_time) / 60))
            except Exception:
                eta_min = None

        simplified.append({
            "route_name": route_name,
            "subroute_name": subroute_name,
            "stop_uid": item.get("StopUID"),
            "stop_id": item.get("StopID"),
            "direction": item.get("Direction"),
            "eta_min": eta_min,
            "stop_status": item.get("StopStatus"),
        })

    simplified.sort(
        key=lambda x: (
            x["eta_min"] is None,
            999999 if x["eta_min"] is None else x["eta_min"],
            x["route_name"],
        )
    )
    return simplified


async def _get_city_stop_page(city_en: str, page: int) -> list[dict]:
    cache_key = f"{city_en}|{page}"
    now = time.time()
    cached = _CITY_STOP_PAGE_CACHE.get(cache_key)
    if cached and now - cached[0] <= CITY_STOP_PAGE_CACHE_SECONDS:
        return cached[1]

    params = {
        "$top": CITY_STOPS_PAGE_SIZE,
        "$skip": page * CITY_STOPS_PAGE_SIZE,
        "$format": "JSON",
    }

    rows: list[dict] = []
    try:
        rows = await tdx_get(f"/Bus/Stop/City/{city_en}", params=params)
    except Exception as e:
        print(f"[bus-page] city={city_en} page={page} error={e}")
        rows = []

    simplified = simplify_stop_list(rows)
    _CITY_STOP_PAGE_CACHE[cache_key] = (now, simplified)
    return simplified


async def get_nearby_stops(
    city_name: str,
    lat: float,
    lng: float,
    distance_m: int = 500,
    top: int = 8,
) -> list[dict]:
    city_en = normalize_city_en(city_name)
    if not city_en:
        return []

    cache_key = f"{city_en}|{round(lat, 4)}|{round(lng, 4)}|{distance_m}|{top}"
    now = time.time()
    cached = _NEARBY_STOPS_CACHE.get(cache_key)
    if cached and now - cached[0] <= NEARBY_STOPS_CACHE_SECONDS:
        return cached[1]

    nearby_candidates: list[dict] = []

    for page in range(CITY_STOPS_MAX_PAGES):
        page_stops = await _get_city_stop_page(city_en, page)
        if not page_stops:
            break

        for stop in page_stops:
            stop_lat = stop.get("lat")
            stop_lng = stop.get("lng")
            if stop_lat is None or stop_lng is None:
                continue

            dist = _distance_m(lat, lng, stop_lat, stop_lng)
            if dist <= distance_m:
                stop_copy = dict(stop)
                stop_copy["distance_m"] = dist
                nearby_candidates.append(stop_copy)

        if len(page_stops) < CITY_STOPS_PAGE_SIZE:
            break

    nearby_candidates = _dedupe_by_uid_or_id(nearby_candidates)
    nearby_candidates.sort(key=lambda x: x.get("distance_m", 999999))
    result = nearby_candidates[:top]

    print(f"[bus-nearby] city={city_en} pages_checked={CITY_STOPS_MAX_PAGES} nearby_found={len(result)}")
    _NEARBY_STOPS_CACHE[cache_key] = (now, result)
    return result


async def get_estimated_arrivals(
    city_name: str,
    stop_uid: str | None = None,
    stop_id: str | None = None,
) -> list[dict]:
    city_en = normalize_city_en(city_name)
    if not city_en or (not stop_uid and not stop_id):
        return []

    cache_key = f"{city_en}|{stop_uid}|{stop_id}"
    now = time.time()
    cached = _ETA_CACHE.get(cache_key)
    if cached and now - cached[0] <= ETA_CACHE_SECONDS:
        return cached[1]

    params = {"$format": "JSON"}
    if stop_uid:
        params["$filter"] = f"StopUID eq '{stop_uid}'"
    else:
        params["$filter"] = f"StopID eq '{stop_id}'"

    rows: list[dict] = []
    try:
        rows = await tdx_get(f"/Bus/EstimatedTimeOfArrival/City/{city_en}", params=params)
    except Exception as e:
        print(f"[bus-eta] city={city_en} stop_uid={stop_uid} stop_id={stop_id} error={e}")
        rows = []

    _ETA_CACHE[cache_key] = (now, rows)
    return rows

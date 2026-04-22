import time
import httpx

from app.config import TDX_CLIENT_ID, TDX_CLIENT_SECRET

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic/v2"

_token_cache = {
    "access_token": None,
    "expires_at": 0,
}


def normalize_city_name_to_tdx(city_name: str | None) -> str | None:
    if not city_name:
        return None

    mapping = {
        "臺北市": "Taipei",
        "台北市": "Taipei",
        "新北市": "NewTaipei",
        "桃園市": "Taoyuan",
        "臺中市": "Taichung",
        "台中市": "Taichung",
        "臺南市": "Tainan",
        "台南市": "Tainan",
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
        "台東縣": "TaitungCounty",
        "澎湖縣": "PenghuCounty",
        "金門縣": "KinmenCounty",
        "連江縣": "LienchiangCounty",
    }
    return mapping.get(city_name, city_name)


async def get_access_token() -> str:
    now = time.time()

    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    if not TDX_CLIENT_ID or not TDX_CLIENT_SECRET:
        raise RuntimeError("TDX_CLIENT_ID 或 TDX_CLIENT_SECRET 尚未設定")

    data = {
        "grant_type": "client_credentials",
        "client_id": TDX_CLIENT_ID,
        "client_secret": TDX_CLIENT_SECRET,
    }

    headers = {
        "content-type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(TDX_TOKEN_URL, data=data, headers=headers)
        response.raise_for_status()
        result = response.json()

    access_token = result["access_token"]
    expires_in = result.get("expires_in", 86400)

    # 提前 60 秒視為過期，避免邊界問題
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in - 60

    return access_token


async def tdx_get(path: str, params: dict | None = None):
    token = await get_access_token()

    headers = {
        "authorization": f"Bearer {token}",
    }

    url = f"{TDX_API_BASE}{path}"

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


async def get_nearby_stops(city_name: str, lat: float, lng: float, distance_m: int = 500, top: int = 10):
    city_slug = normalize_city_name_to_tdx(city_name)
    if not city_slug:
        return []

    params = {
        "$spatialFilter": f"nearby(StopPosition, {lat}, {lng}, {distance_m})",
        "$top": top,
        "$format": "JSON",
    }

    path = f"/Bus/Stop/City/{city_slug}"
    data = await tdx_get(path, params=params)

    if not isinstance(data, list):
        return []

    return data


async def get_estimated_arrivals(
    city_name: str,
    stop_id: str | None = None,
    stop_name: str | None = None,
    top: int = 20,
):
    city_slug = normalize_city_name_to_tdx(city_name)
    if not city_slug:
        return []

    params = {
        "$top": top,
        "$format": "JSON",
    }

    filters = []
    if stop_id:
        filters.append(f"StopID eq '{stop_id}'")
    if stop_name:
        filters.append(f"StopName/Zh_tw eq '{stop_name}'")

    if filters:
        params["$filter"] = " and ".join(filters)

    path = f"/Bus/EstimatedTimeOfArrival/City/{city_slug}"
    data = await tdx_get(path, params=params)

    if not isinstance(data, list):
        return []

    return data


def simplify_stop_list(stops: list[dict]) -> list[dict]:
    result = []

    for item in stops:
        stop_name = (
            item.get("StopName", {}).get("Zh_tw")
            or item.get("StopName", {}).get("En")
            or "未知站牌"
        )

        stop_id = item.get("StopID")
        stop_uid = item.get("StopUID")

        stop_position = item.get("StopPosition", {})
        lat = stop_position.get("PositionLat")
        lng = stop_position.get("PositionLon")

        result.append({
            "stop_name": stop_name,
            "stop_id": stop_id,
            "stop_uid": stop_uid,
            "lat": lat,
            "lng": lng,
        })

    return result


def simplify_eta_list(etas: list[dict]) -> list[dict]:
    result = []

    for item in etas:
        route_name = (
            item.get("RouteName", {}).get("Zh_tw")
            or item.get("RouteName", {}).get("En")
            or "未知路線"
        )

        stop_name = (
            item.get("StopName", {}).get("Zh_tw")
            or item.get("StopName", {}).get("En")
            or "未知站牌"
        )

        stop_id = item.get("StopID")
        direction = item.get("Direction")
        subroute_name = (
            item.get("SubRouteName", {}).get("Zh_tw")
            or item.get("SubRouteName", {}).get("En")
        )

        estimate_time = item.get("EstimateTime")
        stop_status = item.get("StopStatus")
        is_last_bus = item.get("IsLastBus")

        eta_min = None
        if isinstance(estimate_time, (int, float)):
            eta_min = round(estimate_time / 60)

        result.append({
            "route_name": route_name,
            "subroute_name": subroute_name,
            "stop_name": stop_name,
            "stop_id": stop_id,
            "direction": direction,
            "estimate_time_sec": estimate_time,
            "eta_min": eta_min,
            "stop_status": stop_status,
            "is_last_bus": is_last_bus,
        })

    return result

def dedupe_stops_by_name(stops: list[dict]) -> list[dict]:
    seen = set()
    result = []

    for stop in stops:
        name = stop.get("stop_name")
        if name in seen:
            continue
        seen.add(name)
        result.append(stop)

    return result

def dedupe_stops_by_name(stops: list[dict]) -> list[dict]:
    seen = set()
    result = []

    for stop in stops:
        name = stop.get("stop_name")
        if not name:
            continue

        if name in seen:
            continue

        seen.add(name)
        result.append(stop)

    return result

def choose_catchable_bus(eta_list: list[dict], walk_minutes: int | None, safety_buffer_min: int = 1):
    if walk_minutes is None:
        return None, []

    arrival_at_stop_min = walk_minutes + safety_buffer_min

    valid_list = []
    for eta in eta_list:
        if eta.get("eta_min") is None:
            continue
        if eta.get("stop_status") not in [0, None]:
            continue
        valid_list.append(eta)

    valid_list.sort(key=lambda x: x["eta_min"])

    chosen = None
    for eta in valid_list:
        if eta["eta_min"] >= arrival_at_stop_min:
            chosen = eta
            break

    return chosen, valid_list
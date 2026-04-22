import httpx
from datetime import datetime, timezone
from app.config import GOOGLE_MAPS_API_KEY

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _normalize_city_name(name: str | None) -> str | None:
    if not name:
        return None

    mapping = {
        "台北市": "臺北市",
        "台中市": "臺中市",
        "台南市": "臺南市",
        "台東縣": "臺東縣",
        "Taipei City": "臺北市",
        "New Taipei City": "新北市",
        "Taoyuan City": "桃園市",
        "Taichung City": "臺中市",
        "Tainan City": "臺南市",
        "Kaohsiung City": "高雄市",
        "Keelung City": "基隆市",
        "Hsinchu City": "新竹市",
        "Chiayi City": "嘉義市",
        "Hsinchu County": "新竹縣",
        "Miaoli County": "苗栗縣",
        "Changhua County": "彰化縣",
        "Nantou County": "南投縣",
        "Yunlin County": "雲林縣",
        "Chiayi County": "嘉義縣",
        "Pingtung County": "屏東縣",
        "Yilan County": "宜蘭縣",
        "Hualien County": "花蓮縣",
        "Taitung County": "臺東縣",
        "Penghu County": "澎湖縣",
        "Kinmen County": "金門縣",
        "Lienchiang County": "連江縣",
    }
    return mapping.get(name, name)


def _normalize_township_name(name: str | None) -> str | None:
    if not name:
        return None

    mapping = {
        "Daan District": "大安區",
        "Da’an District": "大安區",
        "Shilin District": "士林區",
        "Zhongshan District": "中山區",
        "Songshan District": "松山區",
        "Xinyi District": "信義區",
        "Zhongzheng District": "中正區",
        "Datong District": "大同區",
        "Wanhua District": "萬華區",
        "Neihu District": "內湖區",
        "Nangang District": "南港區",
        "Wenshan District": "文山區",
        "Beitou District": "北投區",
    }
    return mapping.get(name, name)


def _extract_city_and_township(address_components: list[dict]) -> tuple[str | None, str | None]:
    city = None
    township = None

    for comp in address_components:
        long_name = comp.get("long_name")
        short_name = comp.get("short_name")
        types = comp.get("types", [])

        if city is None and "administrative_area_level_1" in types:
            city = long_name or short_name

        if township is None and (
            "administrative_area_level_3" in types
            or "sublocality_level_1" in types
            or "locality" in types
        ):
            township = long_name or short_name

    if township is None:
        for comp in address_components:
            long_name = comp.get("long_name")
            short_name = comp.get("short_name")
            types = comp.get("types", [])

            if "administrative_area_level_2" in types:
                candidate = long_name or short_name
                if candidate:
                    township = candidate
                    break

    city = _normalize_city_name(city)
    township = _normalize_township_name(township)

    return city, township


async def geocode_address(address: str):
    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
        "region": "tw",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(GEOCODE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    status = data.get("status")
    if status != "OK":
        print(f"[geocode] status={status}")
        return None

    results = data.get("results", [])
    if not results:
        print("[geocode] no results")
        return None

    first = results[0]
    location = first["geometry"]["location"]
    formatted_address = first.get("formatted_address")
    address_components = first.get("address_components", [])

    city, township = _extract_city_and_township(address_components)

    print(f"[geocode] address={address}")
    print(f"[geocode] formatted_address={formatted_address}")
    print(f"[geocode] city={city}, township={township}")

    return {
        "formatted_address": formatted_address,
        "lat": location["lat"],
        "lng": location["lng"],
        "city": city,
        "township": township,
        "place_name": formatted_address,
    }


async def estimate_transit_minutes(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    arrival_datetime: datetime,
):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration",
    }

    if arrival_datetime.tzinfo is None:
        arrival_datetime = arrival_datetime.replace(tzinfo=timezone.utc)

    body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin_lat,
                    "longitude": origin_lng,
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": dest_lat,
                    "longitude": dest_lng,
                }
            }
        },
        "travelMode": "TRANSIT",
        "arrivalTime": arrival_datetime.isoformat(),
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        print("[routes] no routes")
        return None

    duration_str = routes[0].get("duration")
    if not duration_str or not duration_str.endswith("s"):
        print(f"[routes] unexpected duration={duration_str}")
        return None

    seconds = float(duration_str[:-1])
    minutes = round(seconds / 60)

    print(f"[routes] estimated_minutes={minutes}")
    return minutes

async def estimate_walk_minutes(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration",
    }

    body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin_lat,
                    "longitude": origin_lng,
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": dest_lat,
                    "longitude": dest_lng,
                }
            }
        },
        "travelMode": "WALK",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        print("[walk] no routes")
        return None

    duration_str = routes[0].get("duration")
    if not duration_str or not duration_str.endswith("s"):
        print(f"[walk] unexpected duration={duration_str}")
        return None

    seconds = float(duration_str[:-1])
    minutes = round(seconds / 60)

    print(f"[walk] estimated_minutes={minutes}")
    return minutes
import httpx
from datetime import datetime, timezone
from app.config import GOOGLE_MAPS_API_KEY
from app.integrations.api_health import api_timer_start, log_api_health
from app.integrations.redis_cache import get_cache, set_cache

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _route_duration_seconds(route: dict) -> int | None:
    duration = route.get("duration")
    if not isinstance(duration, str) or not duration.endswith("s"):
        return None
    try:
        return round(float(duration[:-1]))
    except (TypeError, ValueError):
        return None


def _select_shortest_route(routes: list[dict]) -> dict | None:
    if not routes:
        return None
    return min(
        routes,
        key=lambda route: (
            _route_duration_seconds(route) is None,
            _route_duration_seconds(route) or float("inf"),
        ),
    )

def _normalize_city_name(name: str | None) -> str | None:
    if not name:
        return None
    mapping = {
        "台北市": "臺北市",
        "台中市": "臺中市",
        "台南市": "臺南市",
        "台東縣": "臺東縣",
    }
    return mapping.get(name, name)

def _extract_city_from_address_components(components: list[dict]) -> str | None:
    city = None
    for component in components:
        long_name = component.get("long_name")
        types = component.get("types", [])
        if "administrative_area_level_1" in types:
            if long_name and (long_name.endswith("市") or long_name.endswith("縣")):
                city = long_name
                break
    if city is None:
        for component in components:
            long_name = component.get("long_name")
            types = component.get("types", [])
            if "administrative_area_level_2" in types:
                if long_name and (long_name.endswith("市") or long_name.endswith("縣")):
                    city = long_name
                    break
    return _normalize_city_name(city)

async def geocode_address(address: str):
    print(f"[maps-geocode] Called with address={address}")
    
    cache_key = f"maps:geocode:{address}"
    cached = await get_cache(cache_key)
    if cached:
        print(f"[maps-geocode] Cache hit for address={address}")
        return cached

    params = {"address": address, "key": GOOGLE_MAPS_API_KEY}
    print(f"[maps-geocode] Request params: {params}")
    
    timer = api_timer_start()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=1.5)) as client:
            response = await client.get(GEOCODE_URL, params=params)
            print(f"[maps-geocode] Response status: {response.status_code}")
            log_api_health("google.geocode", timer, status_code=response.status_code)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        log_api_health("google.geocode", timer, error_message=str(e))
        print(f"[maps-geocode] exception: {e}")
        print(f"[maps-geocode] Error details - address={address}, params={params}")
        raise

    status = data.get("status")
    if status != "OK":
        print(f"[maps-geocode] API returned non-OK status: {status}")
        return None

    results = data.get("results", [])
    if not results:
        print(f"[maps-geocode] No results found for address={address}")
        return None

    first = results[0]
    location = first["geometry"]["location"]
    address_components = first.get("address_components", [])
    city = _extract_city_from_address_components(address_components)

    result = {
        "formatted_address": first.get("formatted_address"),
        "lat": location["lat"],
        "lng": location["lng"],
        "city": city,
    }
    print(f"[maps-geocode] Success for address={address}: lat={location['lat']}, lng={location['lng']}, city={city}")
    # Cache geocode for 24 hours since locations rarely change
    await set_cache(cache_key, result, expire_seconds=86400)
    return result

async def estimate_transit_minutes_detailed(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
    arrival_datetime: datetime,
    allowed_travel_modes: list[str] | None = None,
    routing_preference: str | None = None,
    compute_alternatives: bool = True,
):
    if not GOOGLE_MAPS_API_KEY:
        print("[maps] estimate_transit_minutes_detailed: GOOGLE_MAPS_API_KEY is not set, skipping")
        return None

    if origin_lat is None or origin_lng is None or destination_lat is None or destination_lng is None:
        print(
            "[maps-transit-detailed] Missing coordinates, skipping: "
            f"origin_lat={origin_lat}, origin_lng={origin_lng}, "
            f"destination_lat={destination_lat}, destination_lng={destination_lng} "
            "(住家或目的地尚未成功轉換成經緯度座標，請確認地址已正確設定)"
        )
        return None

    print(f"[maps-transit-detailed] Called with parameters: origin_lat={origin_lat}, origin_lng={origin_lng}, destination_lat={destination_lat}, destination_lng={destination_lng}, arrival_datetime={arrival_datetime}, allowed_travel_modes={allowed_travel_modes}")

    # Create cache key, round coordinates and minute precision
    time_str = arrival_datetime.strftime('%Y%m%d%H%M')
    mode_key = ",".join(allowed_travel_modes or ["ANY"])
    preference_key = routing_preference or "FASTEST"
    alternatives_key = "alts" if compute_alternatives else "single"
    cache_key = f"maps:transit:detailed:v2:{origin_lat:.4f},{origin_lng:.4f}:{destination_lat:.4f},{destination_lng:.4f}:{time_str}:{mode_key}:{preference_key}:{alternatives_key}"



    cached = await get_cache(cache_key)
    if cached:
        return cached

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.legs.steps.travelMode,routes.legs.steps.transitDetails,routes.legs.steps.navigationInstruction",
    }

    # 確保 datetime 帶有 timezone（若已有 tzinfo 則直接使用，不強制轉 UTC）
    if arrival_datetime.tzinfo is None:
        from zoneinfo import ZoneInfo as _ZI
        arrival_datetime = arrival_datetime.replace(tzinfo=_ZI("Asia/Taipei"))

    # Google Routes API 需要 RFC3339 格式（含 timezone offset）
    arrival_time_str = arrival_datetime.isoformat()

    transit_preferences = {}
    if allowed_travel_modes:
        transit_preferences["allowedTravelModes"] = allowed_travel_modes
    if routing_preference:
        transit_preferences["routingPreference"] = routing_preference

    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}},
        "destination": {"location": {"latLng": {"latitude": destination_lat, "longitude": destination_lng}}},
        "travelMode": "TRANSIT",
        "arrivalTime": arrival_time_str,
    }

    print(f"[maps-transit-detailed] Request payload: {body}")
    if transit_preferences:
        body["transitPreferences"] = transit_preferences
    if compute_alternatives:
        body["computeAlternativeRoutes"] = True

    print(
        f"[maps-transit] origin=({origin_lat:.5f},{origin_lng:.5f}) "
        f"dest=({destination_lat:.5f},{destination_lng:.5f}) "
        f"arrivalTime={arrival_time_str} modes={allowed_travel_modes}"
    )

    timer = api_timer_start()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=1.5)) as client:
            response = await client.post(ROUTES_URL, headers=headers, json=body)
            if response.status_code == 400 and compute_alternatives:
                print(f"[maps-transit] 400 with alternatives, retrying without")
                retry_body = dict(body)
                retry_body.pop("computeAlternativeRoutes", None)
                response = await client.post(ROUTES_URL, headers=headers, json=retry_body)
            log_api_health("google.routes.transit", timer, status_code=response.status_code)
            if response.status_code >= 400:
                print(f"[maps-transit] API error {response.status_code}: {response.text[:300]}")
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        log_api_health("google.routes.transit", timer, error_message=str(e))
        print(f"[maps-transit] exception: {e}")
        print(f"[maps-transit] Error details - origin=({origin_lat},{origin_lng}) destination=({destination_lat},{destination_lng}) arrival_time={arrival_time_str}")
        raise

    routes = data.get("routes", [])
    if not routes:
        print(f"[maps-transit] no routes returned for origin=({origin_lat},{origin_lng}) dest=({destination_lat},{destination_lng})")
        return None

    route = _select_shortest_route(routes)
    duration_seconds = _route_duration_seconds(route or {})
    if duration_seconds is None:
        print(f"[maps-transit] could not parse duration from route: {route}")
        return None

    minutes = round(duration_seconds / 60)
    print(f"[maps-transit] success: duration={minutes}min")

    steps = []
    legs = route.get("legs", [])
    if legs:
        for step in legs[0].get("steps", []):
            transit_details = step.get("transitDetails")
            step_info = {
                "type": "TRANSIT" if transit_details else step.get("travelMode") or "WALK",
                "instructions": step.get("navigationInstruction", {}).get("instructions"),
            }
            if transit_details:
                line = transit_details.get("transitLine", {})
                stop_details = transit_details.get("stopDetails", {})
                line_short_name = line.get("nameShort") or line.get("shortName")
                step_info.update({
                    "line_name": line.get("name") or line_short_name,
                    "line_short_name": line_short_name,
                    "line_full_name": line.get("name"),
                    "vehicle_type": line.get("vehicle", {}).get("type"),
                    "vehicle_name": line.get("vehicle", {}).get("name", {}).get("text"),
                    "departure_stop": stop_details.get("departureStop", {}).get("name"),
                    "arrival_stop": stop_details.get("arrivalStop", {}).get("name"),
                })
            steps.append(step_info)

    result = {
        "duration_minutes": minutes,
        "steps": steps,
    }
    # Cache detailed result for 5 minutes
    await set_cache(cache_key, result, expire_seconds=300)
    return result


async def estimate_transit_minutes(origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float, arrival_datetime: datetime):
    # Backward compatibility
    print(f"[maps-transit-simple] Called with parameters: origin_lat={origin_lat}, origin_lng={origin_lng}, destination_lat={destination_lat}, destination_lng={destination_lng}, arrival_datetime={arrival_datetime}")
    try:
        res = await estimate_transit_minutes_detailed(origin_lat, origin_lng, destination_lat, destination_lng, arrival_datetime)
        return res["duration_minutes"] if res else None
    except Exception as e:
        print(f"[maps-transit-simple] exception: {e}")
        print(f"[maps-transit-simple] Error details - origin=({origin_lat},{origin_lng}) destination=({destination_lat},{destination_lng}) arrival_datetime={arrival_datetime}")
        raise

async def estimate_walking_minutes(origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float):
    print(f"[maps-walk] Called with parameters: origin_lat={origin_lat}, origin_lng={origin_lng}, destination_lat={destination_lat}, destination_lng={destination_lng}")

    if origin_lat is None or origin_lng is None or destination_lat is None or destination_lng is None:
        print("[maps-walk] Missing coordinates, skipping (住家或目的地尚未成功轉換成經緯度座標)")
        return None

    time_str = datetime.now().strftime('%Y%m%d%H')
    cache_key = f"maps:walk:{origin_lat:.4f},{origin_lng:.4f}:{destination_lat:.4f},{destination_lng:.4f}:{time_str}"
    
    cached = await get_cache(cache_key)
    if cached:
        print(f"[maps-walk] Cache hit, returning cached value: {cached} minutes")
        return cached

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration",
    }

    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}},
        "destination": {"location": {"latLng": {"latitude": destination_lat, "longitude": destination_lng}}},
        "travelMode": "WALK",
    }

    print(f"[maps-walk] Request payload: {body}")

    timer = api_timer_start()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=1.5)) as client:
            response = await client.post(ROUTES_URL, headers=headers, json=body)
            print(f"[maps-walk] Response status: {response.status_code}")
            log_api_health("google.routes.walk", timer, status_code=response.status_code)
            if response.status_code >= 400:
                print(f"[maps-walk] failed: {response.text}")
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        log_api_health("google.routes.walk", timer, error_message=str(e))
        print(f"[maps-walk] exception: {e}")
        print(f"[maps-walk] Error details - origin=({origin_lat},{origin_lng}) destination=({destination_lat},{destination_lng})")
        raise

    routes = data.get("routes", [])
    if not routes:
        print(f"[maps-walk] No routes returned")
        return None

    duration_str = routes[0].get("duration")
    if not duration_str or not duration_str.endswith("s"):
        print(f"[maps-walk] Invalid duration format: {duration_str}")
        return None

    minutes = max(1, round(float(duration_str[:-1]) / 60))
    print(f"[maps-walk] Success: duration={minutes} minutes")
    await set_cache(cache_key, minutes, expire_seconds=3600)
    return minutes

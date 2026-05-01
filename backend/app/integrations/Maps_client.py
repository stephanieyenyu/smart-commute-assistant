import httpx
from datetime import datetime, timezone
from app.config import GOOGLE_MAPS_API_KEY
from app.integrations.redis_cache import get_cache, set_cache

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
    cache_key = f"maps:geocode:{address}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    params = {"address": address, "key": GOOGLE_MAPS_API_KEY}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(GEOCODE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    status = data.get("status")
    if status != "OK":
        return None

    results = data.get("results", [])
    if not results:
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
    # Cache geocode for 24 hours since locations rarely change
    await set_cache(cache_key, result, expire_seconds=86400)
    return result

async def estimate_transit_minutes_detailed(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, arrival_datetime: datetime):
    # Create cache key, round coordinates and minute precision
    time_str = arrival_datetime.strftime('%Y%m%d%H%M')
    cache_key = f"maps:transit:detailed:{origin_lat:.4f},{origin_lng:.4f}:{dest_lat:.4f},{dest_lng:.4f}:{time_str}"
    
    cached = await get_cache(cache_key)
    if cached:
        return cached

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.legs.steps.transitDetails,routes.legs.steps.navigationInstruction",
    }
    if arrival_datetime.tzinfo is None:
        arrival_datetime = arrival_datetime.replace(tzinfo=timezone.utc)

    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lng}}},
        "travelMode": "TRANSIT",
        "arrivalTime": arrival_datetime.isoformat(),
        "transitPreferences": {
            "routingPreference": "LESS_WALKING"
        }
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(7.0, connect=3.0)) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        return None

    route = routes[0]
    duration_str = route.get("duration")
    if not duration_str or not duration_str.endswith("s"):
        return None

    minutes = round(float(duration_str[:-1]) / 60)
    
    steps = []
    legs = route.get("legs", [])
    if legs:
        for step in legs[0].get("steps", []):
            transit_details = step.get("transitDetails")
            if transit_details:
                line = transit_details.get("transitLine", {})
                stop_details = transit_details.get("stopDetails", {})
                steps.append({
                    "type": "TRANSIT",
                    "line_name": line.get("shortName") or line.get("name"),
                    "vehicle_type": line.get("vehicle", {}).get("type"),
                    "departure_stop": stop_details.get("departureStop", {}).get("name"),
                    "arrival_stop": stop_details.get("arrivalStop", {}).get("name"),
                    "instructions": step.get("navigationInstruction", {}).get("instructions"),
                })
    
    result = {
        "duration_minutes": minutes,
        "steps": steps,
    }
    # Cache detailed result for 3 minutes
    await set_cache(cache_key, result, expire_seconds=180)
    return result

async def estimate_transit_minutes(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, arrival_datetime: datetime):
    # Backward compatibility
    res = await estimate_transit_minutes_detailed(origin_lat, origin_lng, dest_lat, dest_lng, arrival_datetime)
    return res["duration_minutes"] if res else None

async def estimate_walking_minutes(origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float):
    time_str = datetime.now().strftime('%Y%m%d%H')
    cache_key = f"maps:walk:{origin_lat:.4f},{origin_lng:.4f}:{destination_lat:.4f},{destination_lng:.4f}:{time_str}"
    
    cached = await get_cache(cache_key)
    if cached:
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(7.0, connect=3.0)) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        if response.status_code >= 400:
            print(f"[maps-walk] failed: {response.text}")
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        return None

    duration_str = routes[0].get("duration")
    if not duration_str or not duration_str.endswith("s"):
        return None

    minutes = max(1, round(float(duration_str[:-1]) / 60))
    await set_cache(cache_key, minutes, expire_seconds=3600)
    return minutes

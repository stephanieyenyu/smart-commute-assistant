from datetime import datetime, timedelta, timezone
import math

import httpx

from app.config import GOOGLE_MAPS_API_KEY
from app.address_utils import (
    normalize_city_name,
    normalize_township_name,
    extract_city_from_text,
)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _find_component(components: list[dict], target_types: list[str]) -> dict | None:
    for component in components:
        comp_types = component.get("types", [])
        if any(t in comp_types for t in target_types):
            return component
    return None


def _extract_city_from_components(components: list[dict]) -> str | None:
    city_component = _find_component(components, ["administrative_area_level_1"])
    if city_component:
        city_value = city_component.get("long_name") or city_component.get("short_name")
        return normalize_city_name(city_value)

    locality_component = _find_component(components, ["locality"])
    if locality_component:
        city_value = locality_component.get("long_name") or locality_component.get("short_name")
        return normalize_city_name(city_value)

    return None


def _extract_township_from_components(components: list[dict]) -> str | None:
    township_targets = [
        "administrative_area_level_2",
        "administrative_area_level_3",
        "sublocality_level_1",
        "sublocality",
        "locality",
    ]

    component = _find_component(components, township_targets)
    if not component:
        return None

    value = component.get("long_name") or component.get("short_name")
    return normalize_township_name(value)


def _parse_duration_to_minutes(duration_str: str | None) -> int | None:
    if not duration_str:
        return None

    try:
        seconds = int(float(duration_str.replace("s", "").strip()))
        return max(1, math.ceil(seconds / 60))
    except Exception:
        return None


def _ensure_taipei_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt


async def geocode_address(address: str) -> dict:
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing")

    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
        "region": "tw",
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0, connect=3.0),
        verify=False,
    ) as client:
        response = await client.get(GEOCODE_URL, params=params)
        if response.status_code >= 400:
            print(f"[geocode] failed status={response.status_code} body={response.text}")
        response.raise_for_status()
        data = response.json()

    results = data.get("results", [])
    if not results:
        raise ValueError("No geocoding result")

    first = results[0]
    formatted_address = first.get("formatted_address") or address
    geometry = first.get("geometry", {}).get("location", {})
    lat = geometry.get("lat")
    lng = geometry.get("lng")
    components = first.get("address_components", [])

    city = (
        _extract_city_from_components(components)
        or extract_city_from_text(formatted_address)
        or extract_city_from_text(address)
    )
    township = _extract_township_from_components(components)
    place_name = first.get("formatted_address") or address

    result = {
        "formatted_address": formatted_address,
        "lat": lat,
        "lng": lng,
        "city": city,
        "township": township,
        "place_name": place_name,
    }

    print(
        f"[geocode] address={address} | formatted_address={formatted_address} | "
        f"city={city} | township={township}"
    )
    return result


async def estimate_transit_minutes(
    origin_lat: float | None = None,
    origin_lng: float | None = None,
    destination_lat: float | None = None,
    destination_lng: float | None = None,
    arrival_datetime: datetime | None = None,
    **kwargs,
) -> int:
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing")

    if origin_lat is None:
        origin_lat = kwargs.get("home_lat") or kwargs.get("src_lat") or kwargs.get("orig_lat")
    if origin_lng is None:
        origin_lng = kwargs.get("home_lng") or kwargs.get("src_lng") or kwargs.get("orig_lng")
    if destination_lat is None:
        destination_lat = kwargs.get("office_lat") or kwargs.get("dest_lat")
    if destination_lng is None:
        destination_lng = kwargs.get("office_lng") or kwargs.get("dest_lng")
    if arrival_datetime is None:
        arrival_datetime = kwargs.get("arrival_time")

    if None in [origin_lat, origin_lng, destination_lat, destination_lng, arrival_datetime]:
        raise ValueError("estimate_transit_minutes missing required arguments")

    arrival_datetime = _ensure_taipei_tz(arrival_datetime)

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
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
                    "latitude": destination_lat,
                    "longitude": destination_lng,
                }
            }
        },
        "travelMode": "TRANSIT",
        "arrivalTime": arrival_datetime.isoformat(),
        "languageCode": "zh-TW",
        "units": "METRIC",
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0, connect=3.0),
        verify=False,
    ) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        if response.status_code >= 400:
            print(f"[routes] estimate failed: status={response.status_code} body={response.text}")
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        raise ValueError("No transit routes returned")

    minutes = _parse_duration_to_minutes(routes[0].get("duration"))
    if minutes is None:
        raise ValueError("Invalid transit duration")

    print(f"[routes] estimated_minutes={minutes}")
    return minutes


async def estimate_walking_minutes(
    origin_lat: float | None = None,
    origin_lng: float | None = None,
    destination_lat: float | None = None,
    destination_lng: float | None = None,
    **kwargs,
) -> int:
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing")

    if origin_lat is None:
        origin_lat = kwargs.get("home_lat") or kwargs.get("src_lat") or kwargs.get("orig_lat")
    if origin_lng is None:
        origin_lng = kwargs.get("home_lng") or kwargs.get("src_lng") or kwargs.get("orig_lng")
    if destination_lat is None:
        destination_lat = kwargs.get("office_lat") or kwargs.get("dest_lat")
    if destination_lng is None:
        destination_lng = kwargs.get("office_lng") or kwargs.get("dest_lng")

    if None in [origin_lat, origin_lng, destination_lat, destination_lng]:
        raise ValueError("estimate_walking_minutes missing required arguments")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
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
                    "latitude": destination_lat,
                    "longitude": destination_lng,
                }
            }
        },
        "travelMode": "WALK",
        "languageCode": "zh-TW",
        "units": "METRIC",
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0, connect=3.0),
        verify=False,
    ) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        if response.status_code >= 400:
            print(f"[walk] estimate failed: status={response.status_code} body={response.text}")
        response.raise_for_status()
        data = response.json()

    routes = data.get("routes", [])
    if not routes:
        raise ValueError("No walking routes returned")

    minutes = _parse_duration_to_minutes(routes[0].get("duration"))
    if minutes is None:
        raise ValueError("Invalid walking duration")

    print(f"[walk] estimated_minutes={minutes}")
    return minutes
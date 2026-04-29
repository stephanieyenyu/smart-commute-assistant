<<<<<<< HEAD
from app.integrations.Maps_client import geocode_address, estimate_transit_minutes

# This file proxies to the new implementation in app.integrations.Maps_client
# to prevent breaking existing code that imports from app.google_maps.
=======
﻿from datetime import datetime
from typing import Any

import httpx

from app.config import GOOGLE_MAPS_API_KEY
from app.address_utils import extract_city_from_text, normalize_city_name


GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _parse_duration_to_minutes(duration_text: str | None) -> int | None:
    if not duration_text:
        return None
    try:
        if duration_text.endswith("s"):
            seconds = int(duration_text[:-1])
            return max(1, round(seconds / 60))
    except Exception:
        return None
    return None


def _extract_township_from_components(components: list[dict]) -> str | None:
    for comp in components:
        types = comp.get("types", [])
        long_name = comp.get("long_name")
        if "administrative_area_level_3" in types:
            return long_name
        if "administrative_area_level_2" in types:
            return long_name
        if "sublocality_level_1" in types:
            return long_name
        if "locality" in types:
            return long_name
    return None


async def geocode_address(address: str) -> dict | None:
    if not GOOGLE_MAPS_API_KEY or not address:
        return None

    params = {
        "address": address,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "zh-TW",
        "region": "tw",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(GEOCODE_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    results = payload.get("results", [])
    if not results:
        return None

    result = results[0]
    location = result.get("geometry", {}).get("location", {}) or {}
    formatted_address = result.get("formatted_address") or address
    components = result.get("address_components", []) or []

    city = None
    for comp in components:
        types = comp.get("types", [])
        long_name = comp.get("long_name")
        if "administrative_area_level_1" in types:
            city = normalize_city_name(long_name) or city

    city = city or normalize_city_name(extract_city_from_text(formatted_address))
    township = _extract_township_from_components(components)
    place_name = formatted_address

    return {
        "formatted_address": formatted_address,
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "city": city,
        "township": township,
        "place_name": place_name,
    }


async def _compute_route(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
    travel_mode: str,
    arrival_datetime: datetime | None = None,
) -> int:
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing")

    body: dict[str, Any] = {
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
        "travelMode": travel_mode,
        "languageCode": "zh-TW",
        "units": "METRIC",
    }

    if arrival_datetime and travel_mode == "TRANSIT":
        body["arrivalTime"] = arrival_datetime.isoformat()

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
        response = await client.post(ROUTES_URL, headers=headers, json=body)
        response.raise_for_status()
        payload = response.json()

    routes = payload.get("routes", [])
    if not routes:
        raise RuntimeError("No routes returned")

    minutes = _parse_duration_to_minutes(routes[0].get("duration"))
    if minutes is None:
        raise RuntimeError("Invalid route duration")

    return minutes


async def estimate_transit_minutes(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
    arrival_datetime: datetime,
) -> int:
    return await _compute_route(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        destination_lat=destination_lat,
        destination_lng=destination_lng,
        travel_mode="TRANSIT",
        arrival_datetime=arrival_datetime,
    )


async def estimate_walking_minutes(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
) -> int:
    return await _compute_route(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        destination_lat=destination_lat,
        destination_lng=destination_lng,
        travel_mode="WALK",
        arrival_datetime=None,
    )
>>>>>>> cb646c664c1b63374efeeb9cc188560a21e05b4a

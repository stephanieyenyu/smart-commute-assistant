import os
from datetime import datetime
from typing import Any

import httpx

from app.address_utils import normalize_city_name, extract_city_from_text
from app.integrations.api_health import api_timer_start, log_api_health


_weather_cache: dict[str, tuple[float, dict]] = {}
WEATHER_CACHE_SECONDS = 300


def _get_weather_api_key() -> str | None:
    return (
        os.getenv("CWA_API_KEY")
        or os.getenv("WEATHER_API_KEY")
        or os.getenv("CWB_API_KEY")
        or os.getenv("CWA_TOKEN")
    )


def _build_failed_weather_result(
    *,
    city: str | None = None,
    township: str | None = None,
    scope: str = "weather_unavailable",
) -> dict[str, Any]:
    return {
        "weather_text": "未知",
        "weather_description": None,
        "pop": None,
        "temperature": None,
        "temperature_min": None,
        "temperature_max": None,
        "apparent_temperature": None,
        "extra_buffer_minutes": 0,
        "scope": scope,
        "city": city,
        "township": township,
    }


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _calculate_weather_buffer(pop: int | None, weather_text: str | None) -> int:
    wx = weather_text or ""

    if pop is not None and pop >= 80:
        return 10
    if pop is not None and pop >= 60:
        return 8
    if pop is not None and pop >= 40:
        return 5

    rainy_keywords = ["雨", "雷", "陣雨", "雷雨", "豪雨", "大雨"]
    if any(keyword in wx for keyword in rainy_keywords):
        return 6

    return 0


def _estimate_apparent_temperature(
    temperature: int | None,
    temperature_min: int | None,
    temperature_max: int | None,
) -> int | None:
    if temperature is not None:
        return temperature
    if temperature_min is not None and temperature_max is not None:
        return round((temperature_min + temperature_max) / 2)
    return temperature_min if temperature_min is not None else temperature_max


def _pick_parameter_value(parameter_block: dict[str, Any]) -> str | None:
    if not parameter_block:
        return None

    value = parameter_block.get("parameterName")
    if value is not None:
        return str(value).strip()

    value = parameter_block.get("parameterValue")
    if value is not None:
        return str(value).strip()

    return None


def _select_best_time_block(time_blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not time_blocks:
        return None

    now = datetime.now()

    for block in time_blocks:
        try:
            start_str = block.get("startTime")
            end_str = block.get("endTime")
            if not start_str or not end_str:
                continue

            start_dt = datetime.fromisoformat(start_str)
            end_dt = datetime.fromisoformat(end_str)

            if start_dt <= now <= end_dt:
                return block
        except Exception:
            continue

    return time_blocks[0]


def _parse_city_weather_payload(city_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    records = payload.get("records") or {}
    locations = records.get("location") or []
    location = next((loc for loc in locations if loc.get("locationName") == city_name), None)

    if not location:
        return _build_failed_weather_result(city=city_name, scope="city_not_found")

    weather_elements = location.get("weatherElement") or []

    wx_value = None
    pop_value = None
    min_t = None
    max_t = None

    for element in weather_elements:
        element_name = element.get("elementName")
        time_blocks = element.get("time") or []
        chosen_block = _select_best_time_block(time_blocks)
        if not chosen_block:
            continue

        parameter = chosen_block.get("parameter") or {}
        raw_value = _pick_parameter_value(parameter)

        if element_name == "Wx":
            wx_value = raw_value
        elif element_name == "PoP":
            pop_value = _safe_int(raw_value)
        elif element_name == "MinT":
            min_t = _safe_int(raw_value)
        elif element_name == "MaxT":
            max_t = _safe_int(raw_value)

    extra_buffer = _calculate_weather_buffer(pop_value, wx_value)
    apparent_temperature = _estimate_apparent_temperature(None, min_t, max_t)

    return {
        "weather_text": wx_value or "未知",
        "weather_description": None,
        "pop": pop_value,
        "temperature": None,
        "temperature_min": min_t,
        "temperature_max": max_t,
        "apparent_temperature": apparent_temperature,
        "extra_buffer_minutes": extra_buffer,
        "scope": "city",
        "city": city_name,
        "township": None,
    }


async def get_today_weather_by_city(city_name: str | None) -> dict[str, Any]:
    city_name = normalize_city_name(city_name)

    if not city_name:
        return _build_failed_weather_result(scope="missing_city")

    now_ts = datetime.now().timestamp()
    cached = _weather_cache.get(city_name)
    if cached and now_ts - cached[0] <= WEATHER_CACHE_SECONDS:
        return cached[1]
    stale_cached = cached

    api_key = _get_weather_api_key()
    if not api_key:
        print("[weather] missing API key")
        return _build_failed_weather_result(city=city_name, scope="missing_api_key")

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    params = {
        "Authorization": api_key,
        "format": "JSON",
        "locationName": city_name,
    }

    timer = api_timer_start()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(2.0, connect=1.0),
            verify=False,
        ) as client:
            response = await client.get(url, params=params)
            log_api_health("cwa.weather.city", timer, status_code=response.status_code)
            if response.status_code >= 400:
                print(f"[weather] city lookup failed: city={city_name}, status={response.status_code}, body={response.text}")
            response.raise_for_status()
            payload = response.json()
            result = _parse_city_weather_payload(city_name, payload)
            _weather_cache[city_name] = (now_ts, result)
            print(f"[weather] success city={city_name} result={result}")
            return result

    except Exception as e:
        log_api_health("cwa.weather.city", timer, error_message=str(e))
        print(f"[weather] city lookup failed: city={city_name}, error={e}")
        if stale_cached:
            result = dict(stale_cached[1])
            result["scope"] = f"{result.get('scope') or 'city'}_stale_cache"
            print(f"[weather] using stale cache city={city_name}")
            return result
        return _build_failed_weather_result(city=city_name, scope="weather_api_failed")


async def get_commute_weather(profile) -> dict[str, Any]:
    home_city = normalize_city_name(getattr(profile, "home_city", None))
    office_city = normalize_city_name(getattr(profile, "office_city", None))

    home_township = getattr(profile, "home_township", None)
    home_address = getattr(profile, "home_address", None)
    office_address = getattr(profile, "office_address", None)

    print(
        f"[weather-debug] home_city={home_city}, home_township={home_township}, "
        f"home_address={home_address}, office_city={office_city}, office_address={office_address}"
    )

    city_name = (
        home_city
        or extract_city_from_text(home_address)
        or office_city
        or extract_city_from_text(office_address)
    )

    if not city_name:
        print("[weather] no city info found from profile/address")
        return _build_failed_weather_result(
            city=None,
            township=home_township,
            scope="missing_city",
        )

    result = await get_today_weather_by_city(city_name)
    result["city"] = city_name
    result["township"] = home_township
    return result

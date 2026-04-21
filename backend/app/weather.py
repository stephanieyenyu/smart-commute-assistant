import httpx
from app.config import CWA_API_KEY

CWA_FORECAST_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"


def normalize_city_name(city_name: str | None) -> str | None:
    if not city_name:
        return None

    mapping = {
        "台北市": "臺北市",
        "台中市": "臺中市",
        "台南市": "臺南市",
        "台東縣": "臺東縣",
    }
    return mapping.get(city_name, city_name)


async def get_today_weather_by_city(city_name: str | None):
    default_result = {
        "weather_text": "未知",
        "temperature_min": None,
        "temperature_max": None,
        "pop": None,
        "extra_buffer_minutes": 0,
    }

    city_name = normalize_city_name(city_name)

    if not city_name:
        print("[weather] city_name is None")
        return default_result

    if not CWA_API_KEY:
        print("[weather] CWA_API_KEY is empty")
        return default_result

    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON",
        "locationName": city_name,
    }

    try:
        async with httpx.AsyncClient(
            timeout=15,
            verify=False,
            trust_env=False,
        ) as client:
            response = await client.get(CWA_FORECAST_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"[weather] request failed: {e}")
        return default_result

    print(f"[weather] city_name={city_name}")
    print(f"[weather] success={data.get('success')}")

    records = data.get("records", {})
    locations = records.get("location", [])

    if not locations:
        print("[weather] no locations found")
        return default_result

    location = locations[0]
    weather_elements = location.get("weatherElement", [])

    weather_text = None
    temperature_min = None
    temperature_max = None
    pop = None

    for element in weather_elements:
        element_name = element.get("elementName")
        times = element.get("time", [])
        if not times:
            continue

        first_time = times[0]
        parameter = first_time.get("parameter", {})
        parameter_name = parameter.get("parameterName")

        if element_name == "Wx":
            weather_text = parameter_name
        elif element_name == "MinT":
            temperature_min = parameter_name
        elif element_name == "MaxT":
            temperature_max = parameter_name
        elif element_name == "PoP":
            try:
                pop = int(parameter_name)
            except (TypeError, ValueError):
                pop = None

    extra_buffer_minutes = 0
    if pop is not None:
        if pop >= 80:
            extra_buffer_minutes = 15
        elif pop >= 60:
            extra_buffer_minutes = 10
        elif pop >= 30:
            extra_buffer_minutes = 5

    return {
        "weather_text": weather_text or "未知",
        "temperature_min": temperature_min,
        "temperature_max": temperature_max,
        "pop": pop,
        "extra_buffer_minutes": extra_buffer_minutes,
    }
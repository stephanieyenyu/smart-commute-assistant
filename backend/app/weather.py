import httpx
from app.config import CWA_API_KEY

CWA_CITY_FORECAST_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
CWA_TOWNSHIP_FORECAST_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093"


def normalize_city_name(city_name: str | None) -> str | None:
    if not city_name:
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
    }
    return mapping.get(city_name, city_name)


def normalize_township_name(township_name: str | None) -> str | None:
    if not township_name:
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
    return mapping.get(township_name, township_name)


def build_default_result(scope: str = "unknown", city: str | None = None, township: str | None = None):
    return {
        "scope": scope,
        "city": city,
        "township": township,
        "weather_text": "未知",
        "weather_description": None,
        "temperature": None,
        "temperature_min": None,
        "temperature_max": None,
        "pop": None,
        "wind": None,
        "risk_level": "low",
        "extra_buffer_minutes": 0,
    }


def extract_first_parameter_value(time_item: dict):
    parameter = time_item.get("parameter", {})
    if isinstance(parameter, dict):
        value = parameter.get("parameterName")
        if value is not None:
            return value
    return None


def extract_first_element_value(time_item: dict):
    element_values = time_item.get("elementValue", [])
    if not element_values:
        return None

    first = element_values[0]
    if not isinstance(first, dict):
        return first

    # 常見欄位優先順序
    preferred_keys = [
        "value",
        "Value",
        "Temperature",
        "Weather",
        "WeatherDescription",
        "ProbabilityOfPrecipitation",
        "Wind",
    ]

    for key in preferred_keys:
        if key in first and first[key] is not None:
            return first[key]

    # 找第一個非空值
    for v in first.values():
        if v is not None:
            return v

    return None


def calculate_buffer_and_risk(pop, weather_text, weather_description):
    extra_buffer_minutes = 0
    risk_level = "low"

    if pop is not None:
        if pop >= 80:
            extra_buffer_minutes += 15
            risk_level = "high"
        elif pop >= 60:
            extra_buffer_minutes += 10
            risk_level = "medium"
        elif pop >= 30:
            extra_buffer_minutes += 5
            risk_level = "medium"

    combined_text = f"{weather_text or ''} {weather_description or ''}"

    severe_keywords = ["大雨", "豪雨", "雷雨", "暴雨", "強風"]
    if any(keyword in combined_text for keyword in severe_keywords):
        extra_buffer_minutes += 5
        if risk_level == "low":
            risk_level = "medium"
        elif risk_level == "medium":
            risk_level = "high"

    return extra_buffer_minutes, risk_level


async def get_today_weather_by_city(city_name: str | None):
    result = build_default_result(scope="city", city=city_name, township=None)

    city_name = normalize_city_name(city_name)

    if not city_name:
        print("[weather-city] city_name is None")
        return result

    if not CWA_API_KEY:
        print("[weather-city] CWA_API_KEY is empty")
        return result

    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON",
        "locationName": city_name,
    }

    try:
        async with httpx.AsyncClient(timeout=15, verify=False, trust_env=False) as client:
            response = await client.get(CWA_CITY_FORECAST_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"[weather-city] request failed: {e}")
        return result

    records = data.get("records", {})
    locations = records.get("location", [])

    if not locations:
        print("[weather-city] no locations found")
        return result

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
        value = extract_first_parameter_value(first_time)

        if element_name == "Wx":
            weather_text = value
        elif element_name == "MinT":
            temperature_min = value
        elif element_name == "MaxT":
            temperature_max = value
        elif element_name == "PoP":
            try:
                pop = int(value)
            except (TypeError, ValueError):
                pop = None

    extra_buffer_minutes, risk_level = calculate_buffer_and_risk(
        pop=pop,
        weather_text=weather_text,
        weather_description=None,
    )

    result.update({
        "weather_text": weather_text or "未知",
        "temperature_min": temperature_min,
        "temperature_max": temperature_max,
        "pop": pop,
        "risk_level": risk_level,
        "extra_buffer_minutes": extra_buffer_minutes,
    })

    print(f"[weather-city] city={city_name} pop={pop} wx={weather_text}")
    return result


async def get_today_weather_by_township(city_name: str | None, township_name: str | None):
    city_name = normalize_city_name(city_name)
    township_name = normalize_township_name(township_name)

    result = build_default_result(scope="township", city=city_name, township=township_name)

    if not city_name or not township_name:
        print(f"[weather-township] missing city/township | city={city_name} township={township_name}")
        return result

    if not CWA_API_KEY:
        print("[weather-township] CWA_API_KEY is empty")
        return result

    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON",
        "LocationName": township_name,
    }

    try:
        async with httpx.AsyncClient(timeout=20, verify=False, trust_env=False) as client:
            response = await client.get(CWA_TOWNSHIP_FORECAST_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"[weather-township] request failed: {e}")
        return result

    records = data.get("records", {})
    locations_groups = records.get("locations", [])

    if not locations_groups:
        print("[weather-township] no locations groups found")
        return result

    target_location = None

    for group in locations_groups:
        group_name = normalize_city_name(group.get("locationsName"))
        if group_name and group_name != city_name:
            continue

        for loc in group.get("location", []):
            loc_name = normalize_township_name(loc.get("locationName"))
            if loc_name == township_name:
                target_location = loc
                break

        if target_location:
            break

    if not target_location:
        print(f"[weather-township] location not found | city={city_name} township={township_name}")
        return result

    weather_elements = target_location.get("weatherElement", [])

    weather_text = None
    weather_description = None
    temperature = None
    pop = None
    wind = None

    for element in weather_elements:
        element_name = element.get("elementName")
        times = element.get("time", [])
        if not times:
            continue

        first_time = times[0]
        value = extract_first_element_value(first_time)

        if element_name == "Wx":
            weather_text = value
        elif element_name in ["PoP6h", "PoP12h", "PoP"]:
            if pop is None:
                try:
                    pop = int(value)
                except (TypeError, ValueError):
                    pop = None
        elif element_name == "T":
            temperature = value
        elif element_name == "WeatherDescription":
            weather_description = value
        elif element_name == "Wind":
            wind = value

    extra_buffer_minutes, risk_level = calculate_buffer_and_risk(
        pop=pop,
        weather_text=weather_text,
        weather_description=weather_description,
    )

    result.update({
        "weather_text": weather_text or "未知",
        "weather_description": weather_description,
        "temperature": temperature,
        "pop": pop,
        "wind": wind,
        "risk_level": risk_level,
        "extra_buffer_minutes": extra_buffer_minutes,
    })

    print(
        f"[weather-township] city={city_name} township={township_name} "
        f"pop={pop} wx={weather_text} desc={weather_description}"
    )
    return result


async def get_commute_weather(profile, target_datetime=None):
    if getattr(profile, "home_city", None):
        city_result = await get_today_weather_by_city(profile.home_city)
        city_result["township"] = getattr(profile, "home_township", None)
        return city_result

    if getattr(profile, "office_city", None):
        city_result = await get_today_weather_by_city(profile.office_city)
        city_result["township"] = getattr(profile, "office_township", None)
        return city_result

    return build_default_result()
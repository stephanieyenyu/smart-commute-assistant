from math import radians, sin, cos, sqrt, atan2


def get_candidate_metro_stations():
    # 先做一版台北北線常用站，之後再慢慢擴充
    return [
        {"name": "芝山", "lat": 25.10306, "lng": 121.52251},
        {"name": "士林", "lat": 25.08895, "lng": 121.52598},
        {"name": "明德", "lat": 25.10982, "lng": 121.51896},
        {"name": "石牌", "lat": 25.11452, "lng": 121.51599},
        {"name": "劍潭", "lat": 25.08487, "lng": 121.52508},
        {"name": "圓山", "lat": 25.07135, "lng": 121.52012},
        {"name": "民權西路", "lat": 25.06291, "lng": 121.51927},
        {"name": "雙連", "lat": 25.05782, "lng": 121.52066},
        {"name": "中山", "lat": 25.05269, "lng": 121.52219},
        {"name": "台北車站", "lat": 25.04776, "lng": 121.51706},
    ]


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c


def get_nearest_metro_station(lat: float, lng: float):
    stations = get_candidate_metro_stations()

    best_station = None
    best_distance = None

    for station in stations:
        distance = haversine_km(lat, lng, station["lat"], station["lng"])

        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_station = station

    return {
        "station": best_station,
        "distance_km": best_distance,
    }
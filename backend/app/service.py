from datetime import date, datetime, time, timedelta, timezone

from app.google_maps import estimate_transit_minutes

DEFAULT_COMMUTE_MINUTES = 35
DEFAULT_BUFFER_MINUTES = 10
TAIPEI_TZ = timezone(timedelta(hours=8))


async def estimate_commute_minutes(profile, target_date: date, arrival_time_str: str) -> int:
    if (
        profile.home_lat is None
        or profile.home_lng is None
        or profile.office_lat is None
        or profile.office_lng is None
    ):
        return DEFAULT_COMMUTE_MINUTES

    hour, minute = map(int, arrival_time_str.split(":"))
    arrival_dt = datetime.combine(
        target_date,
        time(hour=hour, minute=minute, tzinfo=TAIPEI_TZ),
    )

    google_minutes = await estimate_transit_minutes(
        origin_lat=profile.home_lat,
        origin_lng=profile.home_lng,
        dest_lat=profile.office_lat,
        dest_lng=profile.office_lng,
        arrival_datetime=arrival_dt,
    )

    if google_minutes is None:
        return DEFAULT_COMMUTE_MINUTES

    return google_minutes


async def calculate_departure_time(
    profile,
    target_date: date,
    arrival_time_str: str,
    safety_buffer_minutes: int = DEFAULT_BUFFER_MINUTES,
    weather_buffer_minutes: int = 0,
) -> str:
    estimated_commute_minutes = await estimate_commute_minutes(
        profile,
        target_date,
        arrival_time_str,
    )

    departure_time = datetime.strptime(arrival_time_str, "%H:%M") - timedelta(
        minutes=(
            estimated_commute_minutes
            + safety_buffer_minutes
            + weather_buffer_minutes
            + profile.walk_to_bus_stop_min
        )
    )

    return departure_time.strftime("%H:%M")
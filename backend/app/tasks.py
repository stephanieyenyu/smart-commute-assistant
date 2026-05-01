import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.celery_app import celery_app
from app.db import SessionLocal
from app.crud import get_all_profiles, get_user_by_id
from app.service import calculate_departure_time, estimate_commute_minutes
from app.crud import get_override_for_date
from app.address_utils import extract_city_from_text

def get_effective_arrival_time(db, user_id, target_date, default_time):
    override = get_override_for_date(db, user_id, target_date)
    if override and override.target_arrival_time:
        return override.target_arrival_time, True
    return default_time, False

def select_city_name(profile):
    return (
        getattr(profile, "home_city", None)
        or extract_city_from_text(getattr(profile, "home_address", None))
        or getattr(profile, "office_city", None)
        or extract_city_from_text(getattr(profile, "office_address", None))
    )

def ensure_profile_defaults_for_calc(db, user_id, profile):
    return profile
from app.weather import get_today_weather_by_city
from app.line_client import push_text
from app.integrations.redis_cache import get_cache, set_cache

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


async def async_check_all_commutes():
    db = SessionLocal()
    try:
        profiles = get_all_profiles(db)
        now = datetime.now(TAIPEI_TZ).replace(tzinfo=None)
        today = now.date()
        
        for profile in profiles:
            if not profile.preferred_arrival_time or not profile.home_lat or not profile.office_lat:
                continue
                
            user = get_user_by_id(db, profile.user_id)
            if not user or not user.line_user_id:
                continue
                
            # Check if already notified today
            cache_key = f"notified_today:{user.id}_{today.isoformat()}"
            if await get_cache(cache_key):
                continue
                
            profile = ensure_profile_defaults_for_calc(db, user.id, profile)
            effective_arrival_time, used_override = get_effective_arrival_time(
                db, user.id, today, profile.preferred_arrival_time
            )
            
            city_name = select_city_name(profile)
            weather_info = await get_today_weather_by_city(city_name)
            weather_buffer = weather_info["extra_buffer_minutes"]
            
            departure_time_str = await calculate_departure_time(
                profile, today, effective_arrival_time, weather_buffer_minutes=weather_buffer
            )
            
            # Parse departure_time to datetime
            hour, minute = map(int, departure_time_str.split(":"))
            departure_dt = datetime.combine(today, datetime.min.time()).replace(hour=hour, minute=minute)
            
            diff_minutes = (departure_dt - now).total_seconds() / 60
            
            # If departure time is approaching within 15 minutes
            if 0 <= diff_minutes <= 15:
                estimated_minutes = await estimate_commute_minutes(profile, today, effective_arrival_time)
                weather_text = weather_info["weather_text"]
                
                msg = (
                    f"⏰ 該準備出門囉！\n"
                    f"建議出門時間：{departure_time_str}\n"
                    f"預估通勤時間：{estimated_minutes} 分鐘\n"
                    f"天氣狀況：{weather_text}"
                )
                if weather_buffer > 0:
                    msg += f"\n(已包含天氣緩衝 {weather_buffer} 分鐘)"
                    
                await push_text(user.line_user_id, msg)
                # Mark as notified for 24 hours
                await set_cache(cache_key, "1", expire=86400)
    finally:
        db.close()

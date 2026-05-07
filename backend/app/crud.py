from datetime import date, datetime
from sqlalchemy.orm import Session

from app.models import User, CommuteProfile, CommuteOverride, CommuteSchedule


def _clear_override_reminder_fields(override: CommuteOverride):
    override.frozen_plan_key = None
    override.frozen_departure_time = None
    override.frozen_reminder_text = None
    override.reminder_prepared_at = None


# ─────────────────────────────────────────────────────────
# User helpers
# ─────────────────────────────────────────────────────────

def get_or_create_user(db: Session, line_user_id: str) -> User:
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if user:
        return user

    user = User(line_user_id=line_user_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_line_id(db: Session, line_user_id: str) -> User | None:
    return db.query(User).filter(User.line_user_id == line_user_id).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_all_profiles(db: Session):
    return db.query(CommuteProfile).all()


# ─────────────────────────────────────────────────────────
# CommuteProfile helpers
# ─────────────────────────────────────────────────────────

def get_or_create_profile(db: Session, user_id: int) -> CommuteProfile:
    profile = db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()
    if profile:
        return profile

    profile = CommuteProfile(
        user_id=user_id,
        pending_field=None,
        reminder_enabled=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_profile(db: Session, user_id: int) -> CommuteProfile:
    profile = db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()
    if profile:
        return profile
    return get_or_create_profile(db, user_id)


def set_pending_field(db: Session, user_id: int, field_name: str | None):
    profile = get_profile(db, user_id)
    profile.pending_field = field_name
    db.commit()
    db.refresh(profile)
    return profile


def update_profile_field(db: Session, user_id: int, field_name: str, value):
    profile = get_profile(db, user_id)
    setattr(profile, field_name, value)
    db.commit()
    db.refresh(profile)
    return profile


def update_address_and_coords(
    db: Session,
    user_id: int,
    field_prefix: str,
    address: str | None,
    lat: float | None,
    lng: float | None,
    city: str | None = None,
    township: str | None = None,
    place_name: str | None = None,
):
    profile = get_profile(db, user_id)

    if field_prefix not in ["home", "office"]:
        raise ValueError("field_prefix must be home or office")

    setattr(profile, f"{field_prefix}_address", address)
    setattr(profile, f"{field_prefix}_lat", lat)
    setattr(profile, f"{field_prefix}_lng", lng)
    setattr(profile, f"{field_prefix}_city", city)
    setattr(profile, f"{field_prefix}_township", township)
    setattr(profile, f"{field_prefix}_place_name", place_name)

    if field_prefix == "home":
        profile.selected_bus_stop_id = None
        profile.selected_bus_stop_name = None
        profile.selected_bus_stop_lat = None
        profile.selected_bus_stop_lng = None

        profile.selected_metro_station_id = None
        profile.selected_metro_station_name = None
        profile.selected_metro_station_lat = None
        profile.selected_metro_station_lng = None

        profile.last_computed_walk_to_bus_stop_min = None
        profile.last_computed_walk_to_metro_min = None

    db.commit()
    db.refresh(profile)
    return profile


def get_next_setup_step(profile: CommuteProfile) -> str | None:
    """已改為 LIFF 設定流程，所以不再強制要求 profile 欄位完整。
    若 CommuteSchedule 已設定，視為設定完成，回傳 None。
    此函式保留為向下相容，但邏輯已鬆綁。"""
    return None


def reset_profile_for_reconfigure(db: Session, user_id: int):
    profile = get_profile(db, user_id)

    profile.home_address = None
    profile.home_lat = None
    profile.home_lng = None
    profile.home_city = None
    profile.home_township = None
    profile.home_place_name = None

    profile.office_address = None
    profile.office_lat = None
    profile.office_lng = None
    profile.office_city = None
    profile.office_township = None
    profile.office_place_name = None

    profile.selected_bus_stop_id = None
    profile.selected_bus_stop_name = None
    profile.selected_bus_stop_lat = None
    profile.selected_bus_stop_lng = None

    profile.selected_metro_station_id = None
    profile.selected_metro_station_name = None
    profile.selected_metro_station_lat = None
    profile.selected_metro_station_lng = None

    profile.last_computed_walk_to_bus_stop_min = None
    profile.last_computed_walk_to_metro_min = None
    profile.walk_to_bus_stop_min = None

    profile.preferred_mode = None
    profile.preferred_arrival_time = None
    profile.pending_field = None
    profile.reminder_enabled = True

    db.commit()
    db.refresh(profile)
    return profile


def set_reminder_enabled(db: Session, user_id: int, enabled: bool):
    profile = get_profile(db, user_id)
    profile.reminder_enabled = enabled
    db.commit()
    db.refresh(profile)

    # 同步更新 CommuteSchedule 的 reminder_enabled
    schedule = db.query(CommuteSchedule).filter(CommuteSchedule.user_id == user_id).first()
    if schedule:
        schedule.reminder_enabled = enabled
        db.commit()
        db.refresh(schedule)

    return profile


# ─────────────────────────────────────────────────────────
# CommuteSchedule helpers (新統一排程系統)
# ─────────────────────────────────────────────────────────

def upsert_commute_schedule(db: Session, line_user_id: str, data: dict) -> CommuteSchedule:
    """由 LIFF POST /api/schedule/submit 呼叫，upsert 使用者的通勤排程。
    data 欄位（內部格式）：
      originName, originAddress, originLat, originLng,
      destName, destAddress, destLat, destLng,
      time, days, reminderEnabled
    """
    user = get_or_create_user(db, line_user_id)
    get_or_create_profile(db, user.id)

    schedule = db.query(CommuteSchedule).filter(CommuteSchedule.user_id == user.id).first()
    if not schedule:
        schedule = CommuteSchedule(user_id=user.id)
        db.add(schedule)

    schedule.origin_name    = data.get("originName")
    schedule.origin_address = data.get("originAddress")
    schedule.origin_lat     = data.get("originLat")
    schedule.origin_lng     = data.get("originLng")
    schedule.dest_name      = data.get("destName")
    schedule.dest_address   = data.get("destAddress")
    schedule.dest_lat       = data.get("destLat")
    schedule.dest_lng       = data.get("destLng")
    schedule.time           = data.get("time")
    schedule.days           = data.get("days")
    schedule.reminder_enabled = data.get("reminderEnabled", True)

    db.commit()
    db.refresh(schedule)

    # 同步更新 CommuteProfile（讓 service.py 的通勤計算能直接用座標）
    profile = get_profile(db, user.id)
    if data.get("originAddress"):
        profile.home_address    = data.get("originAddress")
        profile.home_place_name = data.get("originName")
    if data.get("originLat") is not None:
        profile.home_lat = data.get("originLat")
    if data.get("originLng") is not None:
        profile.home_lng = data.get("originLng")
    if data.get("destAddress"):
        profile.office_address    = data.get("destAddress")
        profile.office_place_name = data.get("destName")
    if data.get("destLat") is not None:
        profile.office_lat = data.get("destLat")
    if data.get("destLng") is not None:
        profile.office_lng = data.get("destLng")
    if data.get("time"):
        profile.preferred_arrival_time = data.get("time")
    db.commit()

    return schedule



def get_commute_schedule(db: Session, line_user_id: str) -> CommuteSchedule | None:
    """讀取使用者的通勤排程（供 GET /api/schedule 使用）。"""
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        return None
    return db.query(CommuteSchedule).filter(CommuteSchedule.user_id == user.id).first()


def get_all_schedules_for_day(db: Session, day_of_week: int) -> list[CommuteSchedule]:
    """取得今天需要提醒的所有排程（day_of_week: 0=週一, 1=週二, ..., 6=週日）。"""
    all_schedules = db.query(CommuteSchedule).filter(
        CommuteSchedule.reminder_enabled == True,
        CommuteSchedule.time.isnot(None),
        CommuteSchedule.days.isnot(None),
    ).all()
    return [s for s in all_schedules if day_of_week in (s.days or [])]


# ─────────────────────────────────────────────────────────
# CommuteOverride helpers (每日排程狀態記錄)
# ─────────────────────────────────────────────────────────

def get_override_for_date(db: Session, user_id: int, target_date):
    return db.query(CommuteOverride).filter(
        CommuteOverride.user_id == user_id,
        CommuteOverride.target_date == target_date,
    ).first()


def get_or_create_override(db: Session, user_id: int, target_date):
    override = get_override_for_date(db, user_id, target_date)
    if override:
        return override

    override = CommuteOverride(
        user_id=user_id,
        target_date=target_date,
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    return override


def upsert_override(db: Session, user_id: int, target_date, target_arrival_time: str):
    override = get_or_create_override(db, user_id, target_date)
    override.target_arrival_time = target_arrival_time
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def upsert_transport_mode_override(db: Session, user_id: int, target_date, mode: str):
    override = get_or_create_override(db, user_id, target_date)
    override.transport_mode_override = mode
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def get_transport_mode_override(db: Session, user_id: int, target_date):
    override = get_override_for_date(db, user_id, target_date)
    if not override:
        return None
    return override.transport_mode_override


def save_frozen_reminder(
    db: Session,
    user_id: int,
    target_date,
    plan_key: str,
    frozen_departure_time: str,
    frozen_reminder_text: str,
    prepared_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.frozen_plan_key = plan_key
    override.frozen_departure_time = frozen_departure_time
    override.frozen_reminder_text = frozen_reminder_text
    override.reminder_prepared_at = prepared_at
    db.commit()
    db.refresh(override)
    return override


def mark_reminder_sent(
    db: Session,
    user_id: int,
    target_date,
    plan_key: str,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.last_sent_plan_key = plan_key
    override.last_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def clear_today_reminder_state_db(db: Session, user_id: int, target_date):
    override = get_or_create_override(db, user_id, target_date)
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override

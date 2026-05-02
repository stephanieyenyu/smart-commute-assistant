from datetime import date, datetime
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ApiHealthLog, CommuteLog, User, CommuteProfile, CommuteOverride
from app.commute_schedule import schedule_label


def _clear_override_reminder_fields(override: CommuteOverride):
    override.frozen_plan_key = None
    override.frozen_departure_time = None
    override.frozen_reminder_text = None
    override.reminder_prepared_at = None
    override.departure_confirmed_at = None
    override.departure_check_sent_at = None
    override.departure_snoozed_until = None
    override.snooze_one_min_sent_at = None
    override.snooze_departure_sent_at = None


def get_or_create_user(db: Session, line_user_id: str) -> User:
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if user:
        return user

    user = User(line_user_id=line_user_id, household_id="default")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

def get_all_profiles(db: Session):
    return db.query(CommuteProfile).all()


def get_or_create_profile(db: Session, user_id: int) -> CommuteProfile:
    profile = db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()
    if profile:
        return profile

    profile = CommuteProfile(
        user_id=user_id,
        pending_field="home_location",
        reminder_enabled=True,
        active_weekdays=None,
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


def get_household_id_for_user(user: User | None) -> str:
    if not user:
        return "default"
    return user.household_id or "default"


def normalize_household_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "default"
    normalized = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
    return (normalized[:32] or "default").lower()


def set_user_household_id(db: Session, user_id: int, household_id: str) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise ValueError("user not found")
    user.household_id = normalize_household_id(household_id)
    db.commit()
    db.refresh(user)
    return user


def ensure_personal_household(db: Session, user_id: int) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise ValueError("user not found")
    if get_household_id_for_user(user) == "default":
        user.household_id = normalize_household_id(f"family-{user.id}")
        db.commit()
        db.refresh(user)
    return user


def set_user_display_name(db: Session, user_id: int, display_name: str) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise ValueError("user not found")
    name = (display_name or "").strip()
    user.display_name = name[:30] if name else None
    db.commit()
    db.refresh(user)
    return user


def get_users_for_household(db: Session, household_id: str = "default") -> list[User]:
    household_id = (household_id or "default").strip() or "default"
    query = db.query(User)
    if household_id == "default":
        query = query.filter(or_(User.household_id == "default", User.household_id.is_(None)))
    else:
        query = query.filter(User.household_id == household_id)
    return query.order_by(User.id.asc()).all()


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
    home_ready = bool(profile.home_address) and profile.home_lat is not None and profile.home_lng is not None
    office_ready = bool(profile.office_address) and profile.office_lat is not None and profile.office_lng is not None
    arrival_ready = bool(profile.preferred_arrival_time)

    if not home_ready:
        return "home_location"
    if not office_ready:
        return "office_location"
    if not arrival_ready:
        return "preferred_arrival_time"
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
    profile.pending_field = "home_location"
    profile.reminder_enabled = True
    profile.active_weekdays = None

    db.commit()
    db.refresh(profile)
    return profile


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
    override.commute_disabled = False
    override.commute_enabled = True
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def upsert_transport_mode_override(db: Session, user_id: int, target_date, mode: str):
    override = get_or_create_override(db, user_id, target_date)
    override.transport_mode_override = mode
    override.commute_disabled = False
    override.commute_enabled = True
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def get_transport_mode_override(db: Session, user_id: int, target_date):
    override = get_override_for_date(db, user_id, target_date)
    if not override:
        return None
    return override.transport_mode_override


def set_reminder_enabled(db: Session, user_id: int, enabled: bool):
    profile = get_profile(db, user_id)
    profile.reminder_enabled = enabled
    db.commit()
    db.refresh(profile)
    return profile


def set_active_weekdays(db: Session, user_id: int, weekdays: list[int] | None):
    profile = get_profile(db, user_id)
    profile.active_weekdays = weekdays
    db.commit()
    db.refresh(profile)
    return profile


def set_commute_disabled_for_date(db: Session, user_id: int, target_date, disabled: bool = True):
    override = get_or_create_override(db, user_id, target_date)
    override.commute_disabled = disabled
    override.commute_enabled = False if disabled else True
    if disabled:
        _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def schedule_text_for_profile(profile: CommuteProfile) -> str:
    return schedule_label(getattr(profile, "active_weekdays", None))


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


def mark_departure_confirmed(
    db: Session,
    user_id: int,
    target_date,
    confirmed_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.departure_confirmed_at = confirmed_at
    override.departure_snoozed_until = None
    override.snooze_one_min_sent_at = None
    override.snooze_departure_sent_at = None
    db.commit()
    db.refresh(override)
    return override


def mark_departure_check_sent(
    db: Session,
    user_id: int,
    target_date,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    if override.departure_check_sent_at:
        return override
    override.departure_check_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def snooze_departure_confirmation(
    db: Session,
    user_id: int,
    target_date,
    snoozed_until: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.departure_confirmed_at = None
    override.departure_check_sent_at = None
    override.departure_snoozed_until = snoozed_until
    override.snooze_one_min_sent_at = None
    override.snooze_departure_sent_at = None
    db.commit()
    db.refresh(override)
    return override


def mark_snooze_one_min_sent(
    db: Session,
    user_id: int,
    target_date,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.snooze_one_min_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def mark_snooze_departure_sent(
    db: Session,
    user_id: int,
    target_date,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.snooze_departure_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def clear_today_reminder_state_db(db: Session, user_id: int, target_date):
    override = get_or_create_override(db, user_id, target_date)
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def mark_nightly_brief_sent(
    db: Session,
    user_id: int,
    target_date,
    plan_key: str,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.nightly_brief_plan_key = plan_key
    override.nightly_brief_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def mark_watchdog_alert_sent(
    db: Session,
    user_id: int,
    target_date,
    alert_key: str,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date)
    override.watchdog_alert_key = alert_key
    override.watchdog_alert_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def record_api_health_log(
    db: Session,
    endpoint: str,
    timestamp: datetime,
    latency_ms: int | None = None,
    status_code: int | None = None,
    error_message: str | None = None,
):
    log = ApiHealthLog(
        endpoint=endpoint,
        timestamp=timestamp,
        latency_ms=latency_ms,
        status_code=status_code,
        error_message=error_message,
    )
    db.add(log)
    db.commit()
    return log


def record_commute_plan_log(
    db: Session,
    user_id: int,
    plan: dict,
):
    target_date = plan.get("target_date")
    if target_date is None:
        target_date = date.today()

    weather_info = plan.get("weather_info") or {}
    commute_log = CommuteLog(
        user_id=user_id,
        date=target_date,
        day_of_week=target_date.weekday() if hasattr(target_date, "weekday") else None,
        is_holiday=False,
        target_arrival_time=plan.get("effective_arrival_time"),
        suggested_departure_time=plan.get("final_departure_time"),
        suggested_transport=plan.get("transport_line"),
        selection_source=plan.get("selection_source"),
        recommended_mode=plan.get("recommended_mode"),
        weather_condition=weather_info.get("weather_text"),
        rain_prob=weather_info.get("pop"),
        temp=weather_info.get("temperature"),
        gmaps_traffic_duration=plan.get("baseline_minutes"),
        weather_buffer_minutes=plan.get("weather_buffer"),
    )
    db.add(commute_log)
    db.commit()
    return commute_log

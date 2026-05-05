from datetime import date, datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ApiHealthLog, CommuteDestination, CommuteLog, User, CommuteProfile, CommuteOverride, CommuteScheduleTemplate
from app.commute_schedule import (
    commute_date_is_active,
    destination_label_for_profile,
    schedule_label,
    template_is_active_on_date,
    template_weekdays,
)


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
    override.departure_timeout_at = None
    override.departure_timeout_silent = False


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


def hard_reset_database_via_crud(db: Session):
    """
    Convenience wrapper to hard reset the physical database file or drop
    and recreate all tables. This function expects the caller to have
    an active SQLAlchemy session (which will be closed after this call).
    """
    try:
        # Import here to avoid circular import at module load time
        from app.db import hard_reset_database
        hard_reset_database()
        return True
    except Exception as e:
        print(f"[crud.hard_reset_database_via_crud] error: {e}")
        return False


def get_profile(db: Session, user_id: int) -> CommuteProfile:
    profile = db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()
    if profile:
        return profile
    return get_or_create_profile(db, user_id)


def get_household_id_for_user(user: User | None) -> str:
    if not user:
        return "default"
    return user.household_id or "default"


def household_owner_user_id(household_id: str | None) -> int | None:
    household_id = normalize_household_id(household_id)
    if not household_id.startswith("family-"):
        return None
    raw_id = household_id.removeprefix("family-")
    return int(raw_id) if raw_id.isdigit() else None


def user_is_household_owner(user: User | None) -> bool:
    if not user:
        return False
    owner_id = household_owner_user_id(get_household_id_for_user(user))
    return owner_id == user.id


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


def remove_user_from_household(db: Session, requester_user_id: int, member_user_id: int) -> User:
    requester = get_user_by_id(db, requester_user_id)
    member = get_user_by_id(db, member_user_id)
    if requester is None or member is None:
        raise ValueError("user not found")

    household_id = get_household_id_for_user(requester)
    if not user_is_household_owner(requester):
        raise PermissionError("only household owner can remove members")
    if member.id == requester.id:
        raise ValueError("owner cannot remove self")
    if get_household_id_for_user(member) != household_id:
        raise ValueError("member is not in requester household")

    member.household_id = normalize_household_id(f"family-{member.id}")
    db.commit()
    db.refresh(member)
    return member


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

    # Manage temporary pending timeout: auto-clear after 15 minutes
    try:
        from app.temp_pending import schedule_pending_timeout, cancel_pending_timeout
        if field_name is None:
            # Cancel any pending timeout when clearing
            try:
                cancel_pending_timeout(user_id)
            except Exception:
                pass
        else:
            try:
                schedule_pending_timeout(db, user_id, minutes=15)
            except Exception:
                pass
    except Exception:
        # If temp_pending module unavailable, ignore
        pass

    return profile


def update_profile_field(db: Session, user_id: int, field_name: str, value):
    profile = get_profile(db, user_id)
    setattr(profile, field_name, value)
    db.commit()
    db.refresh(profile)
    return profile


def set_identity_and_destination_label(db: Session, user_id: int, identity_type: str | None, destination_label: str | None):
    profile = get_profile(db, user_id)
    profile.identity_type = identity_type
    profile.destination_label = (destination_label or "").strip()[:20] or None
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
    if not home_ready:
        return "home_location"
    return None


def set_schedule_origin_from_home(db: Session, user_id: int, schedule_id: int):
    """Copy user's home coordinates to a schedule's origin fields."""
    profile = get_profile(db, user_id)
    schedule = db.query(CommuteScheduleTemplate).filter(
        CommuteScheduleTemplate.id == schedule_id,
        CommuteScheduleTemplate.user_id == user_id,
    ).first()
    if not schedule:
        return None
    schedule.origin_address = profile.home_address
    schedule.origin_lat = profile.home_lat
    schedule.origin_lng = profile.home_lng
    schedule.origin_city = profile.home_city
    schedule.origin_township = profile.home_township
    schedule.origin_place_name = profile.home_place_name
    db.commit()
    db.refresh(schedule)
    return schedule


def update_schedule_origin_coords(
    db: Session,
    user_id: int,
    schedule_id: int,
    address: str | None,
    lat: float | None,
    lng: float | None,
    city: str | None = None,
    township: str | None = None,
    place_name: str | None = None,
):
    """Update a schedule's custom origin coordinates."""
    schedule = db.query(CommuteScheduleTemplate).filter(
        CommuteScheduleTemplate.id == schedule_id,
        CommuteScheduleTemplate.user_id == user_id,
    ).first()
    if not schedule:
        return None
    schedule.origin_address = address
    schedule.origin_lat = lat
    schedule.origin_lng = lng
    schedule.origin_city = city
    schedule.origin_township = township
    schedule.origin_place_name = place_name
    db.commit()
    db.refresh(schedule)
    return schedule


def reset_profile_for_reconfigure(db: Session, user_id: int):
    profile = get_profile(db, user_id)
    print(f"[reset-profile] ========== HARD RESET START =========")
    print(f"[reset-profile] Starting reset for user_id={user_id}")

    # 物理刪除所有用戶相關資料
    deleted_templates = db.query(CommuteScheduleTemplate).filter(CommuteScheduleTemplate.user_id == user_id).delete(synchronize_session=False)
    deleted_destinations = db.query(CommuteDestination).filter(CommuteDestination.user_id == user_id).delete(synchronize_session=False)
    deleted_overrides = db.query(CommuteOverride).filter(CommuteOverride.user_id == user_id).delete(synchronize_session=False)
    deleted_logs = db.query(CommuteLog).filter(CommuteLog.user_id == user_id).delete(synchronize_session=False)
    print(f"[reset-profile] Deleted records: templates={deleted_templates}, destinations={deleted_destinations}, overrides={deleted_overrides}, logs={deleted_logs}")
    
    # 同時清除 session 狀態，確保不會有殘留狀態
    db.query(User).filter(User.id == user_id).update({"household_id": None})
    print(f"[reset-profile] Cleared household_id")

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
    profile.transport_preference = None
    profile.max_walk_mins = None
    profile.identity_type = None
    profile.destination_label = None
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


def get_schedule_templates(db: Session, user_id: int, active_only: bool = False) -> list[CommuteScheduleTemplate]:
    query = db.query(CommuteScheduleTemplate).filter(CommuteScheduleTemplate.user_id == user_id).filter(CommuteScheduleTemplate.is_deleted.is_(False))
    if active_only:
        query = query.filter(CommuteScheduleTemplate.is_active.is_(True))
    return query.order_by(CommuteScheduleTemplate.id.asc()).all()


def get_user_destinations(db: Session, user_id: int) -> list[CommuteDestination]:
    return db.query(CommuteDestination).filter(CommuteDestination.user_id == user_id).filter(CommuteDestination.is_deleted.is_(False)).order_by(CommuteDestination.id.asc()).all()


def get_recent_destinations(db: Session, user_id: int, limit: int = 5) -> list[CommuteDestination]:
    """
    取得用戶最近使用的目的地（有座標的優先，按 updated_at 降序）。
    用於 pick_location 的歷史快選 Quick Reply。
    """
    return (
        db.query(CommuteDestination)
        .filter(
            CommuteDestination.user_id == user_id,
            CommuteDestination.is_deleted.is_(False),
            CommuteDestination.label.isnot(None),
        )
        .order_by(CommuteDestination.updated_at.desc())
        .limit(limit)
        .all()
    )


def get_destination_by_label(db: Session, user_id: int, label: str | None) -> CommuteDestination | None:
    normalized = (label or "").strip()
    if not normalized:
        return None
    return db.query(CommuteDestination).filter(
        CommuteDestination.user_id == user_id,
        CommuteDestination.label == normalized,
        CommuteDestination.is_deleted.is_(False),
    ).first()


def get_destination_by_id(db: Session, user_id: int, destination_id: int | None) -> CommuteDestination | None:
    if destination_id is None:
        return None
    return db.query(CommuteDestination).filter(
        CommuteDestination.user_id == user_id,
        CommuteDestination.id == destination_id,
    ).first()


def upsert_destination(
    db: Session,
    user_id: int,
    label: str,
    address: str | None,
    lat: float | None,
    lng: float | None,
    city: str | None = None,
    township: str | None = None,
    place_name: str | None = None,
) -> CommuteDestination:
    normalized_label = (label or "").strip()[:40] or "目的地"
    destination = get_destination_by_label(db, user_id, normalized_label)
    if destination is None:
        destination = CommuteDestination(user_id=user_id, label=normalized_label)
        destination.is_deleted = False
        db.add(destination)
    destination.address = address
    destination.lat = lat
    destination.lng = lng
    destination.city = city
    destination.township = township
    destination.place_name = place_name
    db.commit()
    db.refresh(destination)
    return destination


def undelete_destination(db: Session, user_id: int, destination_id: int) -> bool:
    import logging
    logger = logging.getLogger(__name__)
    dest = db.query(CommuteDestination).filter(
        CommuteDestination.user_id == user_id,
        CommuteDestination.id == destination_id,
    ).first()
    if dest is None:
        logger.warning(f"[undelete_destination] user_id={user_id} destination_id={destination_id} not found")
        return False
    dest.is_deleted = False
    dest.deleted_at = None
    db.commit()
    db.refresh(dest)
    logger.info(f"[undelete_destination] user_id={user_id} destination_id={destination_id} label={dest.label} restored successfully")
    return True


def get_schedule_template(db: Session, user_id: int, template_id: int) -> CommuteScheduleTemplate | None:
    return db.query(CommuteScheduleTemplate).filter(
        CommuteScheduleTemplate.user_id == user_id,
        CommuteScheduleTemplate.id == template_id,
    ).first()


def get_active_schedule_template_for_date(db: Session, user_id: int, target_date) -> CommuteScheduleTemplate | None:
    templates = [
        template
        for template in get_schedule_templates(db, user_id, active_only=True)
        if template_is_active_on_date(template, target_date)
    ]
    if not templates:
        return None
    return sorted(templates, key=lambda item: (item.target_arrival_time, item.id or 0))[0]


def get_schedule_conflicts(
    db: Session,
    user_id: int,
    weekdays: list[int],
    exclude_template_id: int | None = None,
) -> list[CommuteScheduleTemplate]:
    target_days = set(int(day) for day in weekdays)
    conflicts = []
    for template in get_schedule_templates(db, user_id, active_only=True):
        if exclude_template_id is not None and template.id == exclude_template_id:
            continue
        if target_days.intersection(template_weekdays(template)):
            conflicts.append(template)
    return conflicts


def remove_weekdays_from_conflicting_templates(
    db: Session,
    user_id: int,
    weekdays: list[int],
    exclude_template_id: int | None = None,
):
    target_days = set(int(day) for day in weekdays)
    for template in get_schedule_conflicts(db, user_id, weekdays, exclude_template_id=exclude_template_id):
        remaining = [day for day in template_weekdays(template) if day not in target_days]
        template.active_weekdays = remaining
        if not remaining:
            template.is_active = False
    db.commit()


def create_schedule_template(
    db: Session,
    user_id: int,
    target_arrival_time: str,
    destination_label: str,
    active_weekdays: list[int],
    name: str | None = None,
    destination_id: int | None = None,
    is_fixed: bool = True,
    *,
    replace_conflicts: bool = False,
    origin_address: str | None = None,
    origin_lat: float | None = None,
    origin_lng: float | None = None,
    origin_city: str | None = None,
    origin_township: str | None = None,
    origin_place_name: str | None = None,
) -> CommuteScheduleTemplate:
    weekdays = sorted({int(day) for day in active_weekdays if 0 <= int(day) <= 6})
    if replace_conflicts:
        remove_weekdays_from_conflicting_templates(db, user_id, weekdays)

    template = CommuteScheduleTemplate(
        user_id=user_id,
        destination_id=destination_id,
        name=(name or "").strip()[:40] or None,
        target_arrival_time=target_arrival_time,
        destination_label=(destination_label or "目的地").strip()[:20],
        active_weekdays=weekdays,
        is_fixed=bool(is_fixed),
        is_active=True,
        origin_address=origin_address,
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        origin_city=origin_city,
        origin_township=origin_township,
        origin_place_name=origin_place_name,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def effective_commute_setting_for_date(db: Session, profile: CommuteProfile, target_date, override=None) -> dict | None:
    user_id = profile.user_id
    templates = get_schedule_templates(db, user_id, active_only=True)
    schedule_template = get_active_schedule_template_for_date(db, user_id, target_date) if templates else None

    if override and getattr(override, "commute_disabled", False):
        return None

    destination_label = destination_label_for_profile(profile)
    arrival_time = profile.preferred_arrival_time
    source = "default"
    template_id = None

    if schedule_template is not None:
        destination_label = schedule_template.destination_label
        arrival_time = schedule_template.target_arrival_time
        source = "schedule_template"
        template_id = schedule_template.id
    elif not templates and not commute_date_is_active(profile, target_date, override):
        return None

    if override and override.target_arrival_time:
        arrival_time = override.target_arrival_time
        source = "override"

    if not arrival_time:
        return None

    return {
        "arrival_time": arrival_time,
        "destination_label": destination_label,
        "source": source,
        "schedule_template_id": template_id,
        "schedule_template": schedule_template,
        "destination": getattr(schedule_template, "destination", None) if schedule_template is not None else None,
        "origin_lat": getattr(schedule_template, "origin_lat", None) if schedule_template is not None else None,
        "origin_lng": getattr(schedule_template, "origin_lng", None) if schedule_template is not None else None,
        "origin_address": getattr(schedule_template, "origin_address", None) if schedule_template is not None else None,
    }


def update_schedule_template(
    db: Session,
    user_id: int,
    template_id: int,
    *,
    target_arrival_time: str | None = None,
    destination_label: str | None = None,
    destination_id: int | None = None,
    active_weekdays: list[int] | None = None,
    is_fixed: bool | None = None,
    is_active: bool | None = None,
) -> CommuteScheduleTemplate | None:
    template = get_schedule_template(db, user_id, template_id)
    if template is None:
        return None
    if target_arrival_time is not None:
        template.target_arrival_time = target_arrival_time
    if destination_label is not None:
        template.destination_label = (destination_label or "目的地").strip()[:20]
    if destination_id is not None:
        template.destination_id = destination_id
    if active_weekdays is not None:
        template.active_weekdays = sorted({int(day) for day in active_weekdays if 0 <= int(day) <= 6})
    if is_fixed is not None:
        template.is_fixed = bool(is_fixed)
    if is_active is not None:
        template.is_active = bool(is_active)
    db.commit()
    db.refresh(template)
    return template


def delete_schedule_template(db: Session, user_id: int, template_id: int) -> bool:
    template = get_schedule_template(db, user_id, template_id)
    if template is None:
        return False
    # Soft delete: mark as deleted and clear active flag
    template.is_deleted = True
    template.is_active = False
    template.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(template)
    return True


def undelete_schedule_template(db: Session, user_id: int, template_id: int) -> bool:
    template = get_schedule_template(db, user_id, template_id)
    if template is None:
        return False
    template.is_deleted = False
    template.is_active = True
    template.deleted_at = None
    db.commit()
    db.refresh(template)
    return True


def effective_commute_date_is_active(db: Session, profile: CommuteProfile, target_date, override=None) -> bool:
    return effective_commute_setting_for_date(db, profile, target_date, override) is not None


def next_effective_commute_date(db: Session, profile: CommuteProfile, start_date, max_days: int = 14):
    for day_offset in range(max_days + 1):
        candidate = start_date + timedelta(days=day_offset)
        override = get_override_for_date(db, profile.user_id, candidate)
        if effective_commute_date_is_active(db, profile, candidate, override):
            return candidate
    return None


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
    override.target_arrival_time = None
    override.departure_snoozed_until = None
    override.snooze_one_min_sent_at = None
    override.snooze_departure_sent_at = None
    override.departure_timeout_at = None
    override.departure_timeout_silent = False
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
    override.departure_timeout_at = None
    override.departure_timeout_silent = False
    db.commit()
    db.refresh(override)
    return override


def mark_departure_timeout(
    db: Session,
    user_id: int,
    target_date,
    timed_out_at: datetime,
    *,
    silent: bool = False,
):
    override = get_or_create_override(db, user_id, target_date)
    if override.departure_timeout_at:
        return override
    override.departure_timeout_at = timed_out_at
    override.departure_timeout_silent = bool(silent)
    override.departure_confirmed_at = timed_out_at
    override.target_arrival_time = None
    override.departure_snoozed_until = None
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

import secrets
import string
from datetime import date, datetime
from sqlalchemy.orm import Session

from app.models import User, Household, CommuteProfile, CommuteOverride, CommuteSchedule


def _clear_override_reminder_fields(override: CommuteOverride):
    override.frozen_plan_key = None
    override.frozen_departure_time = None
    override.frozen_reminder_text = None
    override.reminder_prepared_at = None
    override.monitor_one_hour_sent_at = None
    override.monitor_five_min_sent_at = None
    override.departure_question_sent_at = None
    override.departed_at = None


# ─────────────────────────────────────────────────────────
# User helpers
# ─────────────────────────────────────────────────────────

def get_or_create_user(db: Session, line_user_id: str) -> User:
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if user:
        return user

    user = User(line_user_id=line_user_id, household_id="default")
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
    """已改為 LIFF 設定流程，所以不再強制要求 profile 欄位完整。
    若 CommuteSchedule 已設定，視為設定完成，回傳 None。
    此函式保留為向下相容，但邏輯已鬆綁。"""
    return None


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
    profile.pending_field = None
    profile.reminder_enabled = True
    profile.active_weekdays = None

    db.commit()
    db.refresh(profile)
    return profile


def set_reminder_enabled(db: Session, user_id: int, enabled: bool):
    profile = get_profile(db, user_id)
    profile.reminder_enabled = enabled
    db.commit()
    db.refresh(profile)

    # 同步更新使用者所有 CommuteSchedule 的 reminder_enabled
    schedules = db.query(CommuteSchedule).filter(CommuteSchedule.user_id == user_id).all()
    for schedule in schedules:
        schedule.reminder_enabled = enabled
    if schedules:
        db.commit()

    return profile


# ─────────────────────────────────────────────────────────
# CommuteSchedule helpers (新統一排程系統)
# ─────────────────────────────────────────────────────────

def _destination_key(data: dict) -> str:
    raw_value = (
        data.get("destName")
        or data.get("destinationName")
        or data.get("destAddress")
        or data.get("destinationAddress")
        or "未命名目的地"
    )
    return str(raw_value).strip() or "未命名目的地"


def _schedule_by_id(db: Session, user_id: int, schedule_id) -> CommuteSchedule | None:
    if schedule_id is None:
        return None
    try:
        schedule_id_int = int(schedule_id)
    except (TypeError, ValueError):
        return None
    return db.query(CommuteSchedule).filter(
        CommuteSchedule.id == schedule_id_int,
        CommuteSchedule.user_id == user_id,
    ).first()


def upsert_commute_schedule(db: Session, line_user_id: str, data: dict) -> CommuteSchedule:
    """由 LIFF POST /api/schedule/submit 呼叫，儲存使用者的通勤排程。
    data 欄位（內部格式）：
      originName, originAddress, originLat, originLng,
      destName, destAddress, destLat, destLng,
      time, days, reminderEnabled

    新增模式一律 append 成新排程；編輯模式只依 scheduleId 更新指定排程，
    不再用 userId 或目的地名稱合併，避免覆蓋同一使用者的其他排程。
    """
    user = get_or_create_user(db, line_user_id)
    get_or_create_profile(db, user.id)

    dest_name = _destination_key(data)
    mode = data.get("mode")
    schedule = _schedule_by_id(db, user.id, data.get("scheduleId"))
    if schedule is None and mode == "edit":
        raise ValueError("找不到要編輯的排程")
    if not schedule:
        schedule = CommuteSchedule(user_id=user.id, dest_name=dest_name, is_active=True)
        db.add(schedule)

    partial = bool(data.get("partial"))

    def apply_if_present(attr: str, key: str, default=None):
        if partial and data.get(key) is None:
            return
        setattr(schedule, attr, data.get(key, default))

    apply_if_present("origin_name", "originName")
    apply_if_present("origin_address", "originAddress")
    apply_if_present("origin_lat", "originLat")
    apply_if_present("origin_lng", "originLng")
    apply_if_present("dest_name", "destName")
    apply_if_present("dest_address", "destAddress")
    apply_if_present("dest_lat", "destLat")
    apply_if_present("dest_lng", "destLng")
    apply_if_present("time", "time")
    apply_if_present("days", "days")
    if not partial or data.get("reminderEnabled") is not None:
        schedule.reminder_enabled = data.get("reminderEnabled", True)
    schedule.is_active = True
    if not schedule.dest_name:
        schedule.dest_name = dest_name

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
    return db.query(CommuteSchedule).filter(
        CommuteSchedule.user_id == user.id,
        CommuteSchedule.is_active == True,
    ).order_by(CommuteSchedule.id.asc()).first()


def get_commute_schedules(db: Session, line_user_id: str) -> list[CommuteSchedule]:
    """讀取使用者所有有效通勤排程。"""
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        return []
    return db.query(CommuteSchedule).filter(
        CommuteSchedule.user_id == user.id,
        CommuteSchedule.is_active == True,
    ).order_by(CommuteSchedule.id.asc()).all()


def get_commute_schedules_by_user_id(db: Session, user_id: int) -> list[CommuteSchedule]:
    return db.query(CommuteSchedule).filter(
        CommuteSchedule.user_id == user_id,
        CommuteSchedule.is_active == True,
    ).order_by(CommuteSchedule.id.asc()).all()


def delete_commute_schedule(db: Session, line_user_id: str, schedule_id: int) -> CommuteSchedule | None:
    """Soft delete a user's schedule so old records stay auditable but no longer trigger reminders."""
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        return None
    schedule = _schedule_by_id(db, user.id, schedule_id)
    if not schedule or not getattr(schedule, "is_active", True):
        return None
    schedule.is_active = False
    schedule.reminder_enabled = False
    db.commit()
    db.refresh(schedule)
    return schedule


def get_all_schedules_for_day(db: Session, day_of_week: int) -> list[CommuteSchedule]:
    """取得今天需要提醒的所有排程（day_of_week: 0=週一, 1=週二, ..., 6=週日）。"""
    all_schedules = db.query(CommuteSchedule).filter(
        CommuteSchedule.reminder_enabled == True,
        CommuteSchedule.is_active == True,
        CommuteSchedule.time.isnot(None),
        CommuteSchedule.days.isnot(None),
    ).all()
    return [s for s in all_schedules if day_of_week in (s.days or [])]


# ─────────────────────────────────────────────────────────
# Household helpers
# ─────────────────────────────────────────────────────────

def _new_invite_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def ensure_household_for_user(db: Session, user: User) -> Household:
    if user.household_id and user.household:
        return user.household

    invite_code = _new_invite_code()
    while db.query(Household).filter(Household.invite_code == invite_code).first():
        invite_code = _new_invite_code()

    household = Household(invite_code=invite_code, name="家庭通勤群組")
    db.add(household)
    db.flush()
    user.household_id = household.id
    db.commit()
    db.refresh(household)
    db.refresh(user)
    return household


def join_household_by_code(db: Session, user: User, invite_code: str) -> Household | None:
    normalized_code = (invite_code or "").strip().upper()
    household = db.query(Household).filter(Household.invite_code == normalized_code).first()
    if not household:
        return None
    user.household_id = household.id
    db.commit()
    db.refresh(user)
    return household


def get_household_members(db: Session, household_id: int | None) -> list[User]:
    if not household_id:
        return []
    return db.query(User).filter(User.household_id == household_id).order_by(User.id.asc()).all()


# ─────────────────────────────────────────────────────────
# CommuteOverride helpers (每日排程狀態記錄)
# ─────────────────────────────────────────────────────────

def get_override_for_date(db: Session, user_id: int, target_date, schedule_id: int | None = None):
    query = db.query(CommuteOverride).filter(
        CommuteOverride.user_id == user_id,
        CommuteOverride.target_date == target_date,
    )
    if schedule_id is None:
        query = query.filter(CommuteOverride.schedule_id.is_(None))
    else:
        query = query.filter(CommuteOverride.schedule_id == schedule_id)
    return query.first()


def get_or_create_override(db: Session, user_id: int, target_date, schedule_id: int | None = None):
    override = get_override_for_date(db, user_id, target_date, schedule_id=schedule_id)
    if override:
        return override

    override = CommuteOverride(
        user_id=user_id,
        target_date=target_date,
        schedule_id=schedule_id,
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    return override


def upsert_override(db: Session, user_id: int, target_date, target_arrival_time: str, schedule_id: int | None = None):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
    override.target_arrival_time = target_arrival_time
    override.commute_disabled = False
    override.commute_enabled = True
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def upsert_transport_mode_override(db: Session, user_id: int, target_date, mode: str, schedule_id: int | None = None):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
    override.transport_mode_override = mode
    override.commute_disabled = False
    override.commute_enabled = True
    _clear_override_reminder_fields(override)
    db.commit()
    db.refresh(override)
    return override


def get_transport_mode_override(db: Session, user_id: int, target_date, schedule_id: int | None = None):
    override = get_override_for_date(db, user_id, target_date, schedule_id=schedule_id)
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
    schedule_id: int | None = None,
):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
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
    schedule_id: int | None = None,
):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
    override.last_sent_plan_key = plan_key
    override.last_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def mark_monitor_sent(
    db: Session,
    user_id: int,
    target_date,
    schedule_id: int,
    monitor_key: str,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
    if monitor_key == "one_hour":
        override.monitor_one_hour_sent_at = sent_at
    elif monitor_key == "five_min":
        override.monitor_five_min_sent_at = sent_at
    else:
        raise ValueError("monitor_key must be one_hour or five_min")
    db.commit()
    db.refresh(override)
    return override


def mark_departure_question_sent(
    db: Session,
    user_id: int,
    target_date,
    schedule_id: int,
    sent_at: datetime,
):
    override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
    override.departure_question_sent_at = sent_at
    db.commit()
    db.refresh(override)
    return override


def mark_departed_for_today(
    db: Session,
    user_id: int,
    target_date,
    departed_at: datetime,
    schedule_id: int | None = None,
) -> list[CommuteOverride]:
    query = db.query(CommuteOverride).filter(
        CommuteOverride.user_id == user_id,
        CommuteOverride.target_date == target_date,
    )
    if schedule_id is not None:
        query = query.filter(CommuteOverride.schedule_id == schedule_id)
    else:
        query = query.filter(
            CommuteOverride.departure_question_sent_at.isnot(None),
            CommuteOverride.departed_at.is_(None),
        )
    overrides = query.all()
    for override in overrides:
        override.departed_at = departed_at
    if overrides:
        db.commit()
    return overrides


def clear_today_reminder_state_db(db: Session, user_id: int, target_date, schedule_id: int | None = None):
    if schedule_id is None:
        overrides = db.query(CommuteOverride).filter(
            CommuteOverride.user_id == user_id,
            CommuteOverride.target_date == target_date,
        ).all()
        if not overrides:
            overrides = [get_or_create_override(db, user_id, target_date)]
        for override in overrides:
            _clear_override_reminder_fields(override)
    else:
        override = get_or_create_override(db, user_id, target_date, schedule_id=schedule_id)
        _clear_override_reminder_fields(override)
    db.commit()
    return None

def undelete_commute_schedule(db: Session, line_user_id: str, schedule_id: int):
    """復原被軟刪除的排程"""
    from app.models import User
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        return None
    
    schedule = _schedule_by_id(db, user.id, schedule_id)
    if not schedule:
        return None
        
    # 將狀態改回啟用
    schedule.is_active = True
    schedule.reminder_enabled = True
    db.commit()
    db.refresh(schedule)
    return schedule

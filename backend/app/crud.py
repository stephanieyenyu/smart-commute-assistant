from datetime import date
from sqlalchemy.orm import Session
from app.models import User, CommuteProfile, CommuteScheduleOverride

FIELD_ORDER = [
    "home_address",
    "office_address",
    "preferred_arrival_time",
    "walk_to_bus_stop_min",
]


def get_or_create_user(db: Session, line_user_id: str) -> User:
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if user:
        return user

    user = User(line_user_id=line_user_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_profile(db: Session, user_id: int) -> CommuteProfile:
    profile = db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()
    if profile:
        return profile

    profile = CommuteProfile(user_id=user_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_profile(db: Session, user_id: int):
    return db.query(CommuteProfile).filter(CommuteProfile.user_id == user_id).first()


def set_pending_field(db: Session, user_id: int, field_name: str | None):
    profile = get_or_create_profile(db, user_id)
    profile.pending_field = field_name
    db.commit()
    db.refresh(profile)
    return profile


def update_profile_field(db: Session, user_id: int, field_name: str, value):
    profile = get_or_create_profile(db, user_id)
    setattr(profile, field_name, value)
    db.commit()
    db.refresh(profile)
    return profile


def get_next_missing_field(profile: CommuteProfile | None):
    if profile is None:
        return "home_address"

    for field in FIELD_ORDER:
        value = getattr(profile, field)

        if value is None:
            return field

        if isinstance(value, str) and not value.strip():
            return field

    return None


def get_override_for_date(db: Session, user_id: int, commute_date: date):
    return (
        db.query(CommuteScheduleOverride)
        .filter(
            CommuteScheduleOverride.user_id == user_id,
            CommuteScheduleOverride.commute_date == commute_date,
        )
        .first()
    )


def upsert_override(
    db: Session,
    user_id: int,
    commute_date: date,
    target_arrival_time: str,
):
    override = get_override_for_date(db, user_id, commute_date)

    if override:
        override.target_arrival_time = target_arrival_time
    else:
        override = CommuteScheduleOverride(
            user_id=user_id,
            commute_date=commute_date,
            target_arrival_time=target_arrival_time,
        )
        db.add(override)

    db.commit()
    db.refresh(override)
    return override


def update_address_and_coords(
    db: Session,
    user_id: int,
    field_prefix: str,
    address: str,
    lat: float | None,
    lng: float | None,
    city: str | None,
):
    profile = get_or_create_profile(db, user_id)

    if field_prefix == "home":
        profile.home_address = address
        profile.home_lat = lat
        profile.home_lng = lng
        profile.home_city = city
    elif field_prefix == "office":
        profile.office_address = address
        profile.office_lat = lat
        profile.office_lng = lng
        profile.office_city = city
    else:
        raise ValueError("field_prefix must be 'home' or 'office'")

    db.commit()
    db.refresh(profile)
    return profile
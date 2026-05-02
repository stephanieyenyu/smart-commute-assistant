from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.crud import (
    get_override_for_date,
    get_user_by_id,
    mark_departure_check_sent,
    mark_departure_confirmed,
    snooze_departure_confirmation,
)
from app.line_client import push_departure_check_message


TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SNOOZE_MINUTES = 5


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def parse_target_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return now_taipei().date()


def snooze_until_from(now_dt: datetime | None = None) -> datetime:
    return (now_dt or now_taipei()) + timedelta(minutes=SNOOZE_MINUTES)


async def send_departure_check_for_user(
    db: Session,
    user_id: int,
    target_date,
    sent_at: datetime | None = None,
) -> dict:
    sent_at = sent_at or now_taipei()
    target = parse_target_date(target_date)
    user = get_user_by_id(db, user_id)
    if not user or not user.line_user_id:
        return {"ok": False, "reason": "line_user_missing"}

    override = get_override_for_date(db, user_id, target)
    if override and override.departure_confirmed_at:
        return {"ok": True, "sent": False, "reason": "already_confirmed"}
    if override and override.departure_check_sent_at:
        return {"ok": True, "sent": False, "reason": "already_sent"}

    await push_departure_check_message(user.line_user_id)
    mark_departure_check_sent(db, user_id, target, sent_at)
    return {"ok": True, "sent": True}


def confirm_departure_for_user(
    db: Session,
    user_id: int,
    target_date,
    confirmed_at: datetime | None = None,
):
    return mark_departure_confirmed(
        db,
        user_id,
        parse_target_date(target_date),
        confirmed_at or now_taipei(),
    )


def snooze_departure_for_user(
    db: Session,
    user_id: int,
    target_date,
    now_dt: datetime | None = None,
):
    return snooze_departure_confirmation(
        db,
        user_id,
        parse_target_date(target_date),
        snooze_until_from(now_dt),
    )

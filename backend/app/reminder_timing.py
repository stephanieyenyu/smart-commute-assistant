from enum import StrEnum


STALE_REMINDER_GRACE_SECONDS = 120


class ReminderTimingDecision(StrEnum):
    WAIT = "wait"
    SEND = "send"
    SKIP_STALE = "skip_stale"
    ALREADY_SENT = "already_sent"


def hhmm_to_seconds(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 3600 + int(minute) * 60


def evaluate_departure_reminder(
    now_seconds: int,
    departure_time: str,
    *,
    already_sent: bool = False,
    stale_grace_seconds: int = STALE_REMINDER_GRACE_SECONDS,
) -> ReminderTimingDecision:
    if already_sent:
        return ReminderTimingDecision.ALREADY_SENT

    departure_seconds = hhmm_to_seconds(departure_time)
    if now_seconds > departure_seconds + stale_grace_seconds:
        return ReminderTimingDecision.SKIP_STALE
    if now_seconds >= departure_seconds:
        return ReminderTimingDecision.SEND
    return ReminderTimingDecision.WAIT

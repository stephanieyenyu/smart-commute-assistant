import threading
import time
from typing import Dict, Tuple

# Manage temporary pending-field timeouts per user
# user_id -> (timer, expire_ts)
_PENDING_TIMEOUTS: Dict[int, Tuple[threading.Timer, float]] = {}


def _clear_pending(db, user_id: int):
    try:
        from app.crud import set_pending_field
    except Exception:
        return
    try:
        set_pending_field(db, user_id, None)
        print(f"[temp_pending] cleared pending for user {user_id}")
    except Exception as e:
        print(f"[temp_pending] error clearing pending for user {user_id}: {e}")


def schedule_pending_timeout(db, user_id: int, minutes: int = 15) -> float:
    """Schedule clearing user's pending_field after minutes. Returns expiry timestamp."""
    def _fn():
        try:
            _clear_pending(db, user_id)
        finally:
            _PENDING_TIMEOUTS.pop(user_id, None)

    cancel_pending_timeout(user_id)
    t = threading.Timer(minutes * 60, _fn)
    t.daemon = True
    expiry = time.time() + minutes * 60
    _PENDING_TIMEOUTS[user_id] = (t, expiry)
    t.start()
    print(f"[temp_pending] scheduled pending timeout for user {user_id} at {expiry}")
    return expiry


def cancel_pending_timeout(user_id: int) -> bool:
    entry = _PENDING_TIMEOUTS.pop(user_id, None)
    if not entry:
        return False
    timer, _ = entry
    try:
        timer.cancel()
        print(f"[temp_pending] cancelled pending timeout for user {user_id}")
        return True
    except Exception as e:
        print(f"[temp_pending] cancel failed for user {user_id}: {e}")
        return False


def get_pending_timeout_expiry(user_id: int):
    entry = _PENDING_TIMEOUTS.get(user_id)
    if not entry:
        return None
    return entry[1]

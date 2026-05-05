import threading
import time
from typing import Dict, Tuple

from app.crud import reset_profile_for_reconfigure

# In-memory registry of pending soft resets: user_id -> (timer, expire_ts)
_PENDING_RESETS: Dict[int, Tuple[threading.Timer, float]] = {}


def schedule_soft_reset(db, user_id: int, delay_minutes: int = 15) -> float:
    """
    Schedule a soft reset to run after delay_minutes. Returns expiry timestamp.
    Allows cancellation before timer fires.
    """
    def _do_reset():
        try:
            reset_profile_for_reconfigure(db, user_id)
            print(f"[soft_reset] executed reset for user {user_id}")
        except Exception as e:
            print(f"[soft_reset] failed to reset user {user_id}: {e}")
        finally:
            _PENDING_RESETS.pop(user_id, None)

    # Cancel existing if any
    cancel_soft_reset(user_id)
    timer = threading.Timer(delay_minutes * 60, _do_reset)
    expire_ts = time.time() + delay_minutes * 60
    _PENDING_RESETS[user_id] = (timer, expire_ts)
    timer.daemon = True
    timer.start()
    print(f"[soft_reset] scheduled reset for user {user_id} at {expire_ts}")
    return expire_ts


def cancel_soft_reset(user_id: int) -> bool:
    entry = _PENDING_RESETS.pop(user_id, None)
    if not entry:
        return False
    timer, _ = entry
    try:
        timer.cancel()
        print(f"[soft_reset] cancelled pending reset for user {user_id}")
        return True
    except Exception as e:
        print(f"[soft_reset] cancel failed for user {user_id}: {e}")
        return False


def get_pending_reset_expiry(user_id: int):
    entry = _PENDING_RESETS.get(user_id)
    if not entry:
        return None
    return entry[1]

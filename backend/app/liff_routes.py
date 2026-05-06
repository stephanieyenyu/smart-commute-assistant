import asyncio
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.db import SessionLocal
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    get_profile,
    upsert_destination,
    create_schedule_template,
    get_schedule_templates,
)
from app.line_client import push_text
from app.models import User
from app.reminder_scheduler import clear_today_reminder_state_for_user

router = APIRouter(prefix="/liff", tags=["liff"])
api_router = APIRouter(prefix="/api", tags=["api"])

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


class ScheduleSubmitRequest(BaseModel):
    """LIFF ?????????"""
    userId: str
    destination: str
    arrivalTime: str  # HH:MM ??
    originAddress: str  # ?????
    weekdays: list[int]  # [0, 1, 2, 3, 4] ???????


@router.get("/schedule")
async def get_schedule_form(request: Request):
    """
    ?? LIFF ??????? HTML ??
    Route: GET /liff/schedule
    """
    try:
        with open("backend/static/schedule_form.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return {
            "html": html_content
        }
    except Exception as e:
        print(f"[LIFF Schedule Form] Error: {e}")
        raise HTTPException(status_code=500, detail="??????")


@api_router.post("/schedule/submit")
async def submit_schedule(data: ScheduleSubmitRequest):
    """
    ?? LIFF ???????
    Route: POST /api/schedule/submit
    
    ?? Request Body:
    {
        "userId": "U1234567890...",
        "destination": "??",
        "arrivalTime": "09:00",
        "originAddress": "??",
        "weekdays": [0, 1, 2, 3, 4]
    }
    """
    db = SessionLocal()
    try:
        # 1. ????? User??? LINE user ID?
        user = get_or_create_user(db, line_user_id=data.userId)
        get_or_create_profile(db, user.id)

        print(f"[LIFF Submit] user_id={user.id} destination={data.destination} arrival={data.arrivalTime}")

        # 2. ????????
        destination = upsert_destination(
            db=db,
            user_id=user.id,
            label=data.destination,
            address=data.originAddress,  # ???????????
            lat=None,
            lng=None,
        )

        # 3. ??????
        today = date.today()
        template = create_schedule_template(
            db=db,
            user_id=user.id,
            target_arrival_time=data.arrivalTime,
            destination_label=data.destination,
            active_weekdays=data.weekdays,
            name=f"{data.destination} {data.arrivalTime}",
            destination_id=destination.id if destination else None,
        )

        print(f"[LIFF Submit] template_id={template.id} created successfully")

        # 4. ????????????
        try:
            clear_today_reminder_state_for_user(user.id)
        except Exception as e:
            print(f"[LIFF Submit] Clear cache error: {e}")

        # 5. ?? LINE Messaging API ????????
        try:
            line_user_id = user.line_user_id
            confirmation_text = (
                f"? ???????\n\n"
                f"?? ????{data.destination}\n"
                f"? ?????{data.arrivalTime}\n"
                f"?? ?????{_format_weekdays(data.weekdays)}\n\n"
                f"?????????????????"
            )
            
            # ??????????
            asyncio.create_task(push_text(line_user_id, confirmation_text))
            
        except Exception as e:
            print(f"[LIFF Submit] Push notification error: {e}")
            # ????????????????

        return {
            "ok": True,
            "message": "??????",
            "template_id": template.id,
        }

    except ValueError as ve:
        print(f"[LIFF Submit] ValueError: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"[LIFF Submit] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="???????????")
    finally:
        db.close()


def _format_weekdays(weekdays: list[int]) -> str:
    """
    ??????????????
    0=?, 1=?, ..., 6=?
    """
    weekday_names = ["?", "?", "?", "?", "?", "?", "?"]
    if set(weekdays) == set(range(7)):
        return "??"
    if set(weekdays) == {0, 1, 2, 3, 4}:
        return "???????"
    if set(weekdays) == {5, 6}:
        return "???????"
    
    names = [weekday_names[d] for d in sorted(weekdays)]
    return "?".join(names)

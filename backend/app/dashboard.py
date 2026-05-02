import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from app import route_formatter
from app.dashboard_page import render_dashboard_html
from app.dashboard_status import build_dashboard_payload
from app.db import SessionLocal
from app.service import build_today_commute_payload


router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
WEBSOCKET_REFRESH_SECONDS = 30


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


async def get_dashboard_status_payload(user_id: int) -> dict:
    db = SessionLocal()
    try:
        today = now_taipei().date()
        plan = await build_today_commute_payload(
            db=db,
            user_id=user_id,
            target_date=today,
            force_mode_override=None,
            header="今日通勤建議：",
            log_plan=False,
        )
        if plan.get("ok"):
            route_formatter.get_transport_line(plan)
        return build_dashboard_payload(user_id=user_id, plan=plan, now=now_taipei())
    finally:
        db.close()


@router.get("/status/{user_id}")
async def dashboard_status(user_id: int):
    return await get_dashboard_status_payload(user_id)


@router.get("/view/{user_id}", response_class=HTMLResponse)
async def dashboard_view(user_id: int):
    return HTMLResponse(render_dashboard_html(user_id))


@router.websocket("/ws/{user_id}")
async def dashboard_ws(websocket: WebSocket, user_id: int):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await get_dashboard_status_payload(user_id))
            await asyncio.sleep(WEBSOCKET_REFRESH_SECONDS)
    except WebSocketDisconnect:
        print(f"[dashboard-ws] disconnected user_id={user_id}")

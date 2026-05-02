import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from app import route_formatter
from app.commute_schedule import dashboard_should_sleep, next_active_commute_date
from app.crud import get_household_id_for_user, get_override_for_date, get_profile, get_users_for_household
from app.dashboard_page import render_dashboard_html, render_household_dashboard_html
from app.dashboard_status import (
    build_dashboard_payload,
    build_no_active_day_payload,
    build_sleeping_payload,
    dashboard_plan_is_expired,
)
from app.db import SessionLocal
from app.departure_confirmation import parse_target_date, send_departure_check_for_user
from app.service import build_today_commute_payload


router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
WEBSOCKET_REFRESH_SECONDS = 30


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


async def build_dashboard_plan(db, user_id: int, target_date, header: str) -> dict:
    plan = await build_today_commute_payload(
        db=db,
        user_id=user_id,
        target_date=target_date,
        force_mode_override=None,
        header=header,
        log_plan=False,
    )
    if plan.get("ok"):
        route_formatter.get_transport_line(plan)
    return plan


def _coerce_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=TAIPEI_TZ)
    return value.astimezone(TAIPEI_TZ)


def _apply_snoozed_departure(plan: dict, snoozed_until: datetime | None, now: datetime) -> None:
    snoozed_until = _coerce_datetime(snoozed_until)
    if not plan.get("ok") or snoozed_until is None:
        return
    if snoozed_until < now - timedelta(hours=1):
        return

    plan["final_departure_time"] = snoozed_until.strftime("%H:%M")
    plan["departure_snoozed_until"] = snoozed_until.isoformat()


async def get_dashboard_status_payload(user_id: int) -> dict:
    db = SessionLocal()
    try:
        now = now_taipei()
        today = now.date()
        profile = get_profile(db, user_id)
        today_override = get_override_for_date(db, user_id, today)
        target_date = next_active_commute_date(db, profile, today, get_override_for_date)

        if today_override and today_override.departure_confirmed_at:
            target_date = next_active_commute_date(db, profile, today + timedelta(days=1), get_override_for_date)

        if target_date is None:
            payload = build_no_active_day_payload(user_id=user_id, now=now)
            payload["reminder_enabled"] = bool(getattr(profile, "reminder_enabled", True))
            return payload

        plan = await build_dashboard_plan(
            db,
            user_id,
            target_date,
            "明日通勤建議：" if target_date != today else "今日通勤建議：",
        )
        if target_date == today:
            _apply_snoozed_departure(
                plan,
                getattr(today_override, "departure_snoozed_until", None),
                now,
            )
        if dashboard_plan_is_expired(now, plan):
            target_date = next_active_commute_date(db, profile, today + timedelta(days=1), get_override_for_date)
            if target_date is None:
                payload = build_no_active_day_payload(user_id=user_id, now=now)
                payload["reminder_enabled"] = bool(getattr(profile, "reminder_enabled", True))
                return payload
            plan = await build_dashboard_plan(
                db,
                user_id,
                target_date,
                "明日通勤建議：",
            )
        should_sleep, sleep_until = dashboard_should_sleep(
            now,
            target_date,
            plan.get("final_departure_time"),
            TAIPEI_TZ,
        )
        payload = (
            build_sleeping_payload(user_id=user_id, plan=plan, now=now, sleep_until=sleep_until)
            if should_sleep
            else build_dashboard_payload(user_id=user_id, plan=plan, now=now)
        )
        payload["reminder_enabled"] = bool(getattr(profile, "reminder_enabled", True))
        return payload
    finally:
        db.close()


@router.get("/status/{user_id}")
async def dashboard_status(user_id: int):
    return await get_dashboard_status_payload(user_id)


@router.get("/view/{user_id}", response_class=HTMLResponse)
async def dashboard_view(user_id: int):
    return HTMLResponse(render_dashboard_html(user_id))


async def get_household_dashboard_status_payload(household_id: str = "default") -> dict:
    db = SessionLocal()
    try:
        now = now_taipei()
        users = get_users_for_household(db, household_id)
        members = []
        for user in users:
            try:
                payload = await get_dashboard_status_payload(user.id)
                payload["display_name"] = user.display_name or f"成員 {user.id}"
                payload["household_id"] = get_household_id_for_user(user)
                members.append(payload)
            except Exception as e:
                print(f"[household-dashboard] user_id={user.id} error={e}")

        active_members = [member for member in members if member.get("ok") and not member.get("sleeping")]
        candidates = active_members or [member for member in members if member.get("ok")] or members
        primary = min(
            candidates,
            key=lambda item: (
                item.get("seconds_until_departure") is None,
                item.get("seconds_until_departure") if item.get("seconds_until_departure") is not None else 10**9,
            ),
            default=None,
        )
        all_sleeping = bool(members) and all(member.get("sleeping") for member in members)
        if primary:
            payload = dict(primary)
        else:
            payload = build_no_active_day_payload(user_id=0, now=now, reason="household_empty")
        payload["household_id"] = household_id or "default"
        payload["members"] = members
        payload["primary"] = primary
        payload["all_sleeping"] = all_sleeping
        payload["mode"] = "household"
        payload["refresh_seconds"] = 300 if all_sleeping else payload.get("refresh_seconds", 30)
        return payload
    finally:
        db.close()


@router.get("/household/{household_id}/status")
async def household_dashboard_status(household_id: str):
    return await get_household_dashboard_status_payload(household_id)


@router.get("/household/{household_id}/view", response_class=HTMLResponse)
async def household_dashboard_view(household_id: str):
    return HTMLResponse(render_household_dashboard_html(household_id))


@router.post("/departure-check/{user_id}")
async def dashboard_departure_check(user_id: int, payload: dict | None = Body(default=None)):
    db = SessionLocal()
    try:
        target_date = parse_target_date((payload or {}).get("target_date"))
        return await send_departure_check_for_user(db, user_id, target_date)
    finally:
        db.close()


@router.websocket("/ws/{user_id}")
async def dashboard_ws(websocket: WebSocket, user_id: int):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await get_dashboard_status_payload(user_id))
            await asyncio.sleep(WEBSOCKET_REFRESH_SECONDS)
    except WebSocketDisconnect:
        print(f"[dashboard-ws] disconnected user_id={user_id}")


@router.websocket("/household/{household_id}/ws")
async def household_dashboard_ws(websocket: WebSocket, household_id: str):
    await websocket.accept()
    try:
        while True:
            payload = await get_household_dashboard_status_payload(household_id)
            await websocket.send_json(payload)
            await asyncio.sleep(payload.get("refresh_seconds") or WEBSOCKET_REFRESH_SECONDS)
    except WebSocketDisconnect:
        print(f"[dashboard-ws] household disconnected household_id={household_id}")

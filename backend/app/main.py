from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import Base, engine, SessionLocal
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler
from app.crud import (
    upsert_commute_schedule,
    get_commute_schedules,
    get_commute_schedules_by_user_id,
    get_household_members,
    get_transport_mode_override,
)
from app.google_maps import geocode_address
from app.dashboard_view import render_dashboard_html
from app.models import User
from app.schedule_summary import build_member_status_payload, build_schedule_status_payload


Base.metadata.create_all(bind=engine)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def ensure_runtime_schema() -> None:
    """Best-effort schema repair for Render instances that only run app startup."""
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        dialect = engine.dialect.name
        with engine.begin() as conn:
            if "users" in tables:
                user_columns = {column["name"] for column in inspector.get_columns("users")}
                if "display_name" not in user_columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR"))
                if "household_id" not in user_columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN household_id INTEGER"))

            if "commute_schedules" in tables:
                schedule_columns = {column["name"] for column in inspector.get_columns("commute_schedules")}
                if "is_active" not in schedule_columns:
                    if dialect == "postgresql":
                        conn.execute(text(
                            "ALTER TABLE commute_schedules "
                            "ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
                        ))
                    else:
                        conn.execute(text(
                            "ALTER TABLE commute_schedules "
                            "ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"
                        ))

                if dialect == "postgresql":
                    conn.execute(text(
                        "ALTER TABLE commute_schedules "
                        "DROP CONSTRAINT IF EXISTS uq_commute_schedules_user_id"
                    ))
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commute_schedules_user_destination "
                        "ON commute_schedules (user_id, dest_name)"
                    ))

            if "commute_overrides" in tables:
                override_columns = {column["name"] for column in inspector.get_columns("commute_overrides")}
                if "schedule_id" not in override_columns:
                    conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN schedule_id INTEGER"))
                if dialect == "postgresql":
                    conn.execute(text(
                        "ALTER TABLE commute_overrides "
                        "DROP CONSTRAINT IF EXISTS uq_commute_overrides_user_date"
                    ))
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commute_overrides_user_date_schedule "
                        "ON commute_overrides (user_id, target_date, schedule_id)"
                    ))
    except Exception as schema_error:
        print(f"[schema] runtime schema repair skipped: {schema_error}")


ensure_runtime_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_reminder_scheduler()
    try:
        yield
    finally:
        if reminder_scheduler.running:
            reminder_scheduler.shutdown()


app = FastAPI(title="Smart Commute Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ScheduleSubmitPayload(BaseModel):
    """前端 LIFF POST /api/schedule/submit 的 Payload 格式"""
    userId: str
    originName: Optional[str] = None
    originAddress: Optional[str] = None
    originLat: Optional[float] = None
    originLng: Optional[float] = None
    destinationName: Optional[str] = None
    destinationAddress: Optional[str] = None
    destLat: Optional[float] = None
    destLng: Optional[float] = None
    arrivalTime: Optional[str] = None       # HH:MM 格式
    weekdays: Optional[List[int]] = None    # 0=週一, 1=週二, ..., 6=週日
    reminderEnabled: Optional[bool] = True
    mode: Optional[str] = None              # create/edit，由 LIFF query 或表單帶入
    scheduleId: Optional[int] = None        # edit 時指定要修改的排程


# ── POST /api/schedule/submit ─────────────────────────────────────────────────

@app.post("/api/schedule/submit")
async def submit_schedule(payload: ScheduleSubmitPayload, db: Session = Depends(get_db)):
    """LIFF 前端送出通勤排程設定。
    若前端未提供座標（地址直接輸入情況），後端自動 geocode 補充。
    """
    try:
        origin_lat = payload.originLat
        origin_lng = payload.originLng
        dest_lat   = payload.destLat
        dest_lng   = payload.destLng

        # 出發地：若無座標，嘗試 geocode
        if (origin_lat is None or origin_lng is None) and payload.originAddress:
            try:
                geo = await geocode_address(payload.originAddress)
                if geo:
                    origin_lat = geo.get("lat") or origin_lat
                    origin_lng = geo.get("lng") or origin_lng
                    # 若前端給的是地址字串，補充格式化地址
                    if not payload.originName and geo.get("place_name"):
                        payload.originName = geo.get("place_name")
            except Exception as geo_err:
                print(f"[geocode origin] error={geo_err}")

        # 目的地：若無座標，嘗試 geocode
        if (dest_lat is None or dest_lng is None) and payload.destinationAddress:
            try:
                geo = await geocode_address(payload.destinationAddress)
                if geo:
                    dest_lat = geo.get("lat") or dest_lat
                    dest_lng = geo.get("lng") or dest_lng
                    if not payload.destinationName and geo.get("place_name"):
                        payload.destinationName = geo.get("place_name")
            except Exception as geo_err:
                print(f"[geocode dest] error={geo_err}")

        # 將前端欄位名稱轉換為內部格式
        data = {
            "originName":      payload.originName,
            "originAddress":   payload.originAddress,
            "originLat":       origin_lat,
            "originLng":       origin_lng,
            "destName":        payload.destinationName,
            "destAddress":     payload.destinationAddress,
            "destLat":         dest_lat,
            "destLng":         dest_lng,
            "time":            payload.arrivalTime,
            "days":            payload.weekdays,
            "reminderEnabled": payload.reminderEnabled,
            "mode":            payload.mode,
            "scheduleId":      payload.scheduleId,
            "partial":         payload.mode == "edit",
        }
        schedule = upsert_commute_schedule(db, payload.userId, data)
        return {
            "ok": True,
            "message": "排程設定已儲存",
            "data": {
                "userId":            payload.userId,
                "scheduleId":        schedule.id,
                "originName":        schedule.origin_name,
                "originAddress":     schedule.origin_address,
                "originLat":         schedule.origin_lat,
                "originLng":         schedule.origin_lng,
                "destinationName":   schedule.dest_name,
                "destinationAddress":schedule.dest_address,
                "destLat":           schedule.dest_lat,
                "destLng":           schedule.dest_lng,
                "arrivalTime":       schedule.time,
                "weekdays":          schedule.days,
                "reminderEnabled":   schedule.reminder_enabled,
            }
        }
    except Exception as e:
        print(f"[POST /api/schedule/submit] error={e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/schedule ─────────────────────────────────────────────────────────

@app.get("/api/schedule")
async def get_schedule(
    userId: str = Query(...),
    scheduleId: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """LIFF 前端讀取既有排程設定（用於預填編輯表單）。
    若無資料回傳 { hasData: false }。
    """
    schedules = get_commute_schedules(db, userId)
    if not schedules:
        return {"hasData": False, "schedules": []}
    schedule = next((s for s in schedules if s.id == scheduleId), None) if scheduleId else schedules[0]
    if not schedule:
        raise HTTPException(status_code=404, detail="找不到指定排程")
    return {
        "hasData":           True,
        "userId":            userId,
        "scheduleId":        schedule.id,
        "originName":        schedule.origin_name,
        "originAddress":     schedule.origin_address,
        "originLat":         schedule.origin_lat,
        "originLng":         schedule.origin_lng,
        "destinationName":   schedule.dest_name,
        "destinationAddress":schedule.dest_address,
        "destLat":           schedule.dest_lat,
        "destLng":           schedule.dest_lng,
        "arrivalTime":       schedule.time,
        "weekdays":          schedule.days,
        "reminderEnabled":   schedule.reminder_enabled,
        "schedules": [
            {
                "scheduleId": s.id,
                "originName": s.origin_name,
                "originAddress": s.origin_address,
                "originLat": s.origin_lat,
                "originLng": s.origin_lng,
                "destinationName": s.dest_name,
                "destinationAddress": s.dest_address,
                "destLat": s.dest_lat,
                "destLng": s.dest_lng,
                "arrivalTime": s.time,
                "weekdays": s.days,
                "reminderEnabled": s.reminder_enabled,
            }
            for s in schedules
        ],
    }


# ── Dashboard ─────────────────────────────────────────────────────────────────

TRANSPORT_MODE_NAME_MAP = {
    None: "自動判斷",
    "auto": "自動判斷",
    "shortest": "最短時間優先 (Google)",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
}


def get_user_for_dashboard(db: Session, line_user_id: str):
    return db.query(User).filter(User.line_user_id == line_user_id).first()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(render_dashboard_html(), headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/dashboard/status")
async def dashboard_status(
    userId: str = Query(...),
    view: str = Query("personal"),
    db: Session = Depends(get_db),
):
    user = get_user_for_dashboard(db, userId)
    schedules = get_commute_schedules(db, userId)
    profile = user.profile if user else None
    today_mode = get_transport_mode_override(db, user.id, datetime.now(TAIPEI_TZ).date()) if user else None
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")
    schedule_payload = build_schedule_status_payload(schedules, profile, mode_label)
    family_view = view == "family"
    member_payloads = []
    invite_code = None
    if family_view and user and user.household_id:
        invite_code = user.household.invite_code if user.household else None
        members = get_household_members(db, user.household_id)
        for member in members:
            member_schedules = get_commute_schedules_by_user_id(db, member.id)
            member_profile = member.profile
            member_mode = get_transport_mode_override(db, member.id, datetime.now(TAIPEI_TZ).date())
            member_mode_label = TRANSPORT_MODE_NAME_MAP.get(member_mode or "auto", "自動判斷")
            member_payloads.append(build_member_status_payload(
                member,
                member_schedules,
                member_profile,
                member_mode_label,
                now_dt=datetime.now(TAIPEI_TZ),
            ))
    elif user:
        member_payloads.append(build_member_status_payload(
            user,
            schedules,
            profile,
            mode_label,
            now_dt=datetime.now(TAIPEI_TZ),
        ))
    return {
        "ok": True,
        "title": "家庭通勤看板" if family_view else "個人通勤看板",
        "view": "family" if family_view else "personal",
        "viewLabel": "家庭看板" if family_view else "個人看板",
        "generatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "refreshSeconds": 30,
        "schedule": schedule_payload,
        "members": member_payloads,
        "inviteCode": invite_code,
        "lineText": schedule_payload["lineText"],
        "weeklyText": schedule_payload["weeklyText"],
    }


# ── GET /api/history/{userId} ─────────────────────────────────────────────────

@app.get("/api/history/{userId}")
async def get_history(userId: str, db: Session = Depends(get_db)):
    """讀取用戶歷史使用過的地點紀錄（出發地 + 目的地）。
    從 CommuteSchedule 提取，未來可擴充為完整歷史記錄表。
    """
    from app.crud import get_commute_schedules
    schedules = get_commute_schedules(db, userId)

    history = []
    seen = set()
    for schedule in schedules:
        if schedule.origin_address:
            key = ("origin", schedule.origin_address)
            if key not in seen:
                seen.add(key)
                history.append({
                    "type":    "origin",
                    "name":    schedule.origin_name,
                    "address": schedule.origin_address,
                    "lat":     schedule.origin_lat,
                    "lng":     schedule.origin_lng,
                })
        if schedule.dest_address:
            key = ("destination", schedule.dest_address)
            if key not in seen:
                seen.add(key)
                history.append({
                    "type":    "destination",
                    "name":    schedule.dest_name,
                    "address": schedule.dest_address,
                    "lat":     schedule.dest_lat,
                    "lng":     schedule.dest_lng,
                })

    return {
        "userId":  userId,
        "history": history,
    }


# ── 基礎路由 ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "Smart Commute Assistant is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

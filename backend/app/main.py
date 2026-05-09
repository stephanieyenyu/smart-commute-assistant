from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import re
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import Base, engine, SessionLocal
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler
from app.crud import (
    upsert_commute_schedule,
    delete_commute_schedule,
    get_commute_schedules,
    get_commute_schedules_by_user_id,
    get_household_members,
    get_transport_mode_override,
)
from app.google_maps import geocode_address
from app.dashboard_view import render_dashboard_html
from app.dashboard_ws import router as ws_router
from app.family import router as family_router
from app.dashboard import router as dashboard_router
from app.models import User
from app.schedule_summary import (
    active_schedules_for_date,
    build_member_status_payload,
    build_schedule_status_payload,
    dashboard_target_schedule,
)
from app.service import build_today_commute_payload


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
                if "role" not in user_columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'user' NOT NULL"))
                if "household_id" not in user_columns:
                    conn.execute(text("ALTER TABLE users ADD COLUMN household_id INTEGER"))
                if "created_at" not in user_columns:
                    if dialect == "postgresql":
                        conn.execute(text("ALTER TABLE users ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
                    else:
                        conn.execute(text("ALTER TABLE users ADD COLUMN created_at DATETIME"))

            if "commute_profiles" in tables:
                profile_columns = {column["name"] for column in inspector.get_columns("commute_profiles")}
                profile_string_columns = (
                    "home_township",
                    "home_place_name",
                    "office_township",
                    "office_place_name",
                    "selected_bus_stop_id",
                    "selected_bus_stop_name",
                    "selected_metro_station_id",
                    "selected_metro_station_name",
                    "preferred_mode",
                )
                profile_float_columns = (
                    "selected_bus_stop_lat",
                    "selected_bus_stop_lng",
                    "selected_metro_station_lat",
                    "selected_metro_station_lng",
                )
                profile_integer_columns = (
                    "last_computed_walk_to_bus_stop_min",
                    "last_computed_walk_to_metro_min",
                )
                for column_name in profile_string_columns:
                    if column_name not in profile_columns:
                        conn.execute(text(f"ALTER TABLE commute_profiles ADD COLUMN {column_name} VARCHAR"))
                for column_name in profile_float_columns:
                    if column_name not in profile_columns:
                        conn.execute(text(f"ALTER TABLE commute_profiles ADD COLUMN {column_name} FLOAT"))
                for column_name in profile_integer_columns:
                    if column_name not in profile_columns:
                        conn.execute(text(f"ALTER TABLE commute_profiles ADD COLUMN {column_name} INTEGER"))
                if "reminder_enabled" not in profile_columns:
                    if dialect == "postgresql":
                        conn.execute(text("ALTER TABLE commute_profiles ADD COLUMN reminder_enabled BOOLEAN DEFAULT TRUE NOT NULL"))
                    else:
                        conn.execute(text("ALTER TABLE commute_profiles ADD COLUMN reminder_enabled BOOLEAN DEFAULT 1 NOT NULL"))
                for column_name in ("created_at", "updated_at"):
                    if column_name not in profile_columns:
                        if dialect == "postgresql":
                            conn.execute(text(f"ALTER TABLE commute_profiles ADD COLUMN {column_name} TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
                        else:
                            conn.execute(text(f"ALTER TABLE commute_profiles ADD COLUMN {column_name} DATETIME"))

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
                        "ALTER TABLE commute_schedules "
                        "DROP CONSTRAINT IF EXISTS ix_commute_schedules_user_id"
                    ))
                    conn.execute(text(
                        "DROP INDEX IF EXISTS ix_commute_schedules_user_id"
                    ))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_commute_schedules_user_id "
                        "ON commute_schedules (user_id)"
                    ))
                    conn.execute(text(
                        "ALTER TABLE commute_schedules "
                        "DROP CONSTRAINT IF EXISTS uq_commute_schedules_user_destination"
                    ))
                    conn.execute(text(
                        "DROP INDEX IF EXISTS uq_commute_schedules_user_destination"
                    ))

            if "commute_overrides" in tables:
                override_columns = {column["name"] for column in inspector.get_columns("commute_overrides")}
                if "schedule_id" not in override_columns:
                    conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN schedule_id INTEGER"))
                for column_name in (
                    "monitor_one_hour_sent_at",
                    "monitor_five_min_sent_at",
                    "departure_question_sent_at",
                    "departed_at",
                    "departure_check_sent_at",
                    "departure_confirmed_at",
                    "departure_timeout_at",
                    "departure_snoozed_until",
                ):
                    if column_name not in override_columns:
                        if dialect == "postgresql":
                            conn.execute(text(f"ALTER TABLE commute_overrides ADD COLUMN {column_name} TIMESTAMP WITH TIME ZONE"))
                        else:
                            conn.execute(text(f"ALTER TABLE commute_overrides ADD COLUMN {column_name} DATETIME"))
                if "alert_status" not in override_columns:
                    conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN alert_status VARCHAR"))
                if "nightly_brief_plan_key" not in override_columns:
                    conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN nightly_brief_plan_key VARCHAR"))
                if "watchdog_alert_key" not in override_columns:
                    conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN watchdog_alert_key VARCHAR"))
                if "departure_timeout_silent" not in override_columns:
                    if dialect == "postgresql":
                        conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN departure_timeout_silent BOOLEAN DEFAULT FALSE NOT NULL"))
                    else:
                        conn.execute(text("ALTER TABLE commute_overrides ADD COLUMN departure_timeout_silent BOOLEAN DEFAULT 0 NOT NULL"))
                if dialect == "postgresql":
                    conn.execute(text(
                        "ALTER TABLE commute_overrides "
                        "DROP CONSTRAINT IF EXISTS uq_commute_overrides_user_date"
                    ))
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commute_overrides_user_date_schedule "
                        "ON commute_overrides (user_id, target_date, schedule_id)"
                    ))

            if "commute_destinations" in tables:
                destination_columns = {column["name"] for column in inspector.get_columns("commute_destinations")}
                if "address" not in destination_columns:
                    conn.execute(text("ALTER TABLE commute_destinations ADD COLUMN address VARCHAR"))
                if "lat" not in destination_columns:
                    conn.execute(text("ALTER TABLE commute_destinations ADD COLUMN lat FLOAT"))
                if "lng" not in destination_columns:
                    conn.execute(text("ALTER TABLE commute_destinations ADD COLUMN lng FLOAT"))
                if "is_active" not in destination_columns:
                    if dialect == "postgresql":
                        conn.execute(text("ALTER TABLE commute_destinations ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL"))
                    else:
                        conn.execute(text("ALTER TABLE commute_destinations ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"))
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
app.include_router(ws_router)
app.include_router(family_router)
app.include_router(dashboard_router)

# ── Static Dashboard 前端 ────────────────────────────────────────────────────
import os
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "dashboard")
if os.path.isdir(_STATIC_DIR):
    app.mount("/dashboard", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ScheduleSubmitPayload(BaseModel):
    """前端 LIFF POST /api/schedule/submit 的 Payload 格式"""
    model_config = ConfigDict(extra="ignore")

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

    @model_validator(mode="before")
    @classmethod
    def accept_liff_payload_variants(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        data = dict(values)

        def pick(*keys: str) -> Any:
            for key in keys:
                value = data.get(key)
                if value is not None and value != "":
                    return value
            return None

        normalized_keys = {
            "userId": ("userId", "user_id", "lineUserId", "line_user_id"),
            "originName": ("originName", "origin_name", "homeName", "home_name", "startName", "start_name"),
            "originAddress": ("originAddress", "origin_address", "homeAddress", "home_address", "startAddress", "start_address"),
            "originLat": ("originLat", "origin_lat", "homeLat", "home_lat", "startLat", "start_lat"),
            "originLng": ("originLng", "origin_lng", "homeLng", "home_lng", "startLng", "start_lng"),
            "destinationName": ("destinationName", "destination_name", "destName", "dest_name", "destinationLabel", "destination_label"),
            "destinationAddress": ("destinationAddress", "destination_address", "destAddress", "dest_address", "officeAddress", "office_address"),
            "destLat": ("destLat", "dest_lat", "destinationLat", "destination_lat", "officeLat", "office_lat"),
            "destLng": ("destLng", "dest_lng", "destinationLng", "destination_lng", "officeLng", "office_lng"),
            "arrivalTime": ("arrivalTime", "arrival_time", "targetArrivalTime", "target_arrival_time", "preferredArrivalTime", "preferred_arrival_time"),
            "weekdays": ("weekdays", "activeWeekdays", "active_weekdays", "days"),
            "reminderEnabled": ("reminderEnabled", "reminder_enabled"),
            "scheduleId": ("scheduleId", "schedule_id"),
        }

        for target, aliases in normalized_keys.items():
            value = pick(*aliases)
            if value is not None:
                data[target] = value

        for key, value in list(data.items()):
            if isinstance(value, str):
                stripped = value.strip()
                data[key] = stripped if stripped else None

        return data

    @field_validator("userId")
    @classmethod
    def require_user_id(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("userId 不可為空")
        return value

    @field_validator("arrivalTime")
    @classmethod
    def normalize_arrival_time(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
        if not match:
            raise ValueError("arrivalTime 必須為 HH:MM 格式")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("arrivalTime 時間超出範圍")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("weekdays", mode="before")
    @classmethod
    def normalize_weekdays(cls, value: Any) -> Optional[List[int]]:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            raw_days = [part for part in re.split(r"[\s,]+", value) if part]
        elif isinstance(value, (list, tuple, set)):
            raw_days = list(value)
        else:
            raise ValueError("weekdays 必須是陣列或逗號分隔字串")

        try:
            days = sorted({int(day) for day in raw_days})
        except (TypeError, ValueError) as exc:
            raise ValueError("weekdays 只能包含 0 到 6 的數字") from exc

        if any(day < 0 or day > 6 for day in days):
            raise ValueError("weekdays 只能包含 0 到 6 的數字")
        return days


class ScheduleDeletePayload(BaseModel):
    userId: str
    scheduleId: int


# ── 排程總覽 Flex Builder ─────────────────────────────────────────────────────

def _build_schedule_summary_flex(schedule) -> list[dict]:
    """
    建立排程設定總覽的 Flex Message Bubble。
    包含：起點、目的地、抵達時間、適用星期。
    """
    days_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    days_str = "、".join(f"週{days_map[d]}" for d in sorted(schedule.days or [])) or "尚未設定"

    origin = schedule.origin_name or schedule.origin_address or "未設定"
    dest = schedule.dest_name or schedule.dest_address or "未設定"
    arrival = schedule.time or "未設定"

    def row(label: str, value: str, color: str = "#555555") -> dict:
        return {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
                {
                    "type": "text", "text": label,
                    "size": "sm", "color": "#888888", "flex": 2,
                },
                {
                    "type": "text", "text": value, "wrap": True,
                    "size": "sm", "color": color, "flex": 5, "weight": "bold",
                },
            ],
        }

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#4a90d9",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "text",
                    "text": "✅ 通勤排程已儲存",
                    "color": "#ffffff",
                    "weight": "bold",
                    "size": "lg",
                },
                {
                    "type": "text",
                    "text": "以下是您的設定總覽",
                    "color": "#cce4ff",
                    "size": "sm",
                    "margin": "xs",
                },
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "sm",
            "contents": [
                row("🏠 出發地", origin, "#1a1a2e"),
                row("🏢 目的地", dest, "#1a1a2e"),
                row("⏰ 抵達時間", arrival, "#e74c3c"),
                row("📅 適用星期", days_str, "#27ae60"),
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "請問接下來要做什麼？",
                    "size": "xs",
                    "color": "#888888",
                    "margin": "sm",
                }
            ],
        },
    }
    return [bubble]


SCHEDULE_SAVED_QUICK_REPLIES = [
    {"type": "message", "label": "📊 查看看板",      "text": "查看設定"},
    {"type": "message", "label": "➕ 新增另一筆",    "text": "重新設定"},
    {"type": "message", "label": "🚆 今日通勤建議",  "text": "今天通勤建議"},
]


# ── POST /api/schedule/submit ─────────────────────────────────────────────────

@app.options("/api/schedule/submit")
async def submit_schedule_options():
    return {"ok": True}


@app.post("/api/schedule/submit")
async def submit_schedule(payload: ScheduleSubmitPayload, db: Session = Depends(get_db)):
    """LIFF 前端送出通勤排程設定。
    若前端未提供座標（地址直接輸入情況），後端自動 geocode 補充。
    儲存成功後，自動推播排程總覽 Flex Message 給用戶。
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

        # ── 自動推播排程總覽 Flex Message 給用戶 ──────────────────────────
        try:
            from app.line_client import push_flex_message
            flex_bubbles = _build_schedule_summary_flex(schedule)
            await push_flex_message(
                user_id=payload.userId,
                alt_text="✅ 通勤排程設定已儲存！",
                flex_contents=flex_bubbles,
                quick_reply_items=SCHEDULE_SAVED_QUICK_REPLIES,
            )
            print(f"[submit] schedule summary flex sent to userId={payload.userId}")
        except Exception as push_err:
            # 推播失敗不影響儲存結果
            print(f"[submit] flex push error: {push_err}")

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
    except ValueError as e:
        print(f"[POST /api/schedule/submit] validation_error={e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[POST /api/schedule/submit] error={e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/liff/schedule/submit", include_in_schema=False)
async def submit_schedule_liff_alias(payload: ScheduleSubmitPayload, db: Session = Depends(get_db)):
    return await submit_schedule(payload, db)


# ── GET /api/schedule ─────────────────────────────────────────────────────────

def _schedule_response_list(schedules):
    return [
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
    ]


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
        "schedules": _schedule_response_list(schedules),
    }


# ── DELETE /api/schedule ──────────────────────────────────────────────────────

@app.delete("/api/schedule")
async def delete_schedule(
    userId: str = Query(...),
    scheduleId: int = Query(...),
    db: Session = Depends(get_db),
):
    schedule = delete_commute_schedule(db, userId, scheduleId)
    if not schedule:
        raise HTTPException(status_code=404, detail="找不到指定排程，或該排程已刪除")
    schedules = get_commute_schedules(db, userId)
    return {
        "ok": True,
        "message": "排程已刪除",
        "deletedScheduleId": schedule.id,
        "schedules": _schedule_response_list(schedules),
    }


@app.delete("/api/schedule/{schedule_id}")
async def delete_schedule_by_path(
    schedule_id: int,
    userId: str = Query(...),
    db: Session = Depends(get_db),
):
    return await delete_schedule(userId=userId, scheduleId=schedule_id, db=db)


@app.post("/api/schedule/delete")
async def post_delete_schedule(payload: ScheduleDeletePayload, db: Session = Depends(get_db)):
    schedule = delete_commute_schedule(db, payload.userId, payload.scheduleId)
    if not schedule:
        raise HTTPException(status_code=404, detail="找不到指定排程，或該排程已刪除")
    schedules = get_commute_schedules(db, payload.userId)
    return {
        "ok": True,
        "message": "排程已刪除",
        "deletedScheduleId": schedule.id,
        "schedules": _schedule_response_list(schedules),
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


def dashboard_commute_target_schedule(schedules, now_dt: datetime):
    """Use the same priority as LINE today advice: today's first schedule, then tomorrow."""
    today_schedules = active_schedules_for_date(schedules, now_dt.date())
    if today_schedules:
        return now_dt.date(), today_schedules[0]
    tomorrow = now_dt.date() + timedelta(days=1)
    tomorrow_schedules = active_schedules_for_date(schedules, tomorrow)
    if tomorrow_schedules:
        return tomorrow, tomorrow_schedules[0]
    return None, None


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return HTMLResponse(render_dashboard_html(), headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/dashboard/family", response_class=HTMLResponse)
async def dashboard_family_page():
    return HTMLResponse(render_dashboard_html(), headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/family-dashboard", response_class=HTMLResponse)
async def family_dashboard_page():
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
    now_dt = datetime.now(TAIPEI_TZ)
    today_mode = get_transport_mode_override(db, user.id, now_dt.date()) if user else None
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")
    schedule_payload = build_schedule_status_payload(schedules, profile, mode_label, now_dt=now_dt)
    commute_display = None
    commute_target_date, commute_schedule = dashboard_commute_target_schedule(schedules, now_dt=now_dt)
    if user and commute_target_date and commute_schedule:
        commute_mode = get_transport_mode_override(db, user.id, commute_target_date)
        commute_payload = await build_today_commute_payload(
            db=db,
            user_id=user.id,
            target_date=commute_target_date,
            force_mode_override=commute_mode,
            schedule_id=commute_schedule.id,
        )
        if commute_payload.get("ok"):
            commute_display = commute_payload.get("display")
        else:
            commute_display = {
                "targetDate": commute_target_date.isoformat(),
                "text": commute_payload.get("text") or "目前沒有可顯示的通勤建議",
            }
    family_view = view == "family"
    member_payloads = []
    invite_code = None
    if family_view and user and user.household_id:
        invite_code = user.household.invite_code if user.household else None
        members = get_household_members(db, user.household_id)
        for member in members:
            member_schedules = get_commute_schedules_by_user_id(db, member.id)
            member_profile = member.profile
            member_mode = get_transport_mode_override(db, member.id, now_dt.date())
            member_mode_label = TRANSPORT_MODE_NAME_MAP.get(member_mode or "auto", "自動判斷")
            member_payloads.append(build_member_status_payload(
                member,
                member_schedules,
                member_profile,
                member_mode_label,
                now_dt=now_dt,
            ))
    elif user:
        member_payloads.append(build_member_status_payload(
            user,
            schedules,
            profile,
            mode_label,
            now_dt=now_dt,
        ))
    return {
        "ok": True,
        "title": "家庭通勤看板" if family_view else "個人通勤看板",
        "view": "family" if family_view else "personal",
        "viewLabel": "家庭看板" if family_view else "個人看板",
        "generatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "refreshSeconds": 30,
        "schedule": schedule_payload,
        "commute": commute_display,
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


# ── GET /api/commute-status ───────────────────────────────────────────────────

@app.get("/api/commute-status")
async def get_commute_status(userId: str = Query(...), db: Session = Depends(get_db)):
    """
    Dashboard 輪詢端點：取得用戶今日通勤狀態。
    回傳排程設定、預計出門時間、alert_status。
    """
    from datetime import date
    from zoneinfo import ZoneInfo
    from app.models import User, CommuteOverride, CommuteSchedule

    TAIPEI_TZ = ZoneInfo("Asia/Taipei")
    today = datetime.now(TAIPEI_TZ).date()

    user = db.query(User).filter(User.line_user_id == userId).first()
    if not user:
        return {"hasData": False}

    schedule = db.query(CommuteSchedule).filter(CommuteSchedule.user_id == user.id).first()
    override = db.query(CommuteOverride).filter(
        CommuteOverride.user_id == user.id,
        CommuteOverride.target_date == today,
    ).first()

    days_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}

    return {
        "hasData": True,
        "today": today.isoformat(),
        "schedule": {
            "origin_name": schedule.origin_name if schedule else None,
            "origin_address": schedule.origin_address if schedule else None,
            "dest_name": schedule.dest_name if schedule else None,
            "dest_address": schedule.dest_address if schedule else None,
            "arrival_time": schedule.time if schedule else None,
            "weekdays": schedule.days if schedule else [],
            "weekdays_str": "、".join(
                f"週{days_map[d]}" for d in sorted(schedule.days or [])
            ) if schedule else "",
            "reminder_enabled": schedule.reminder_enabled if schedule else False,
        } if schedule else None,
        "override": {
            "departure_time": override.frozen_departure_time if override else None,
            "alert_status": override.alert_status if override else None,
            "arrival_time": override.target_arrival_time if override else None,
        } if override else None,
    }


# ── 基礎路由 ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "Smart Commute Assistant is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

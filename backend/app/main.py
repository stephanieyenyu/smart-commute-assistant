from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Base, engine, SessionLocal
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler
from app.crud import upsert_commute_schedule, get_commute_schedule
from app.google_maps import geocode_address
from app.dashboard_ws import router as ws_router
from app.family import router as family_router


Base.metadata.create_all(bind=engine)


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

# ── Static Dashboard 前端 ────────────────────────────────────────────────────
import os
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "dashboard")
if os.path.isdir(_STATIC_DIR):
    app.mount("/dashboard", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")


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
    weekdays: Optional[List[int]] = None    # 0=週日, 1=週一, ..., 6=週六
    reminderEnabled: Optional[bool] = True


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
async def get_schedule(userId: str = Query(...), db: Session = Depends(get_db)):
    """LIFF 前端讀取既有排程設定（用於預填編輯表單）。
    若無資料回傳 { hasData: false }。
    """
    schedule = get_commute_schedule(db, userId)
    if not schedule:
        return {"hasData": False}
    return {
        "hasData":           True,
        "userId":            userId,
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


# ── GET /api/history/{userId} ─────────────────────────────────────────────────

@app.get("/api/history/{userId}")
async def get_history(userId: str, db: Session = Depends(get_db)):
    """讀取用戶歷史使用過的地點紀錄（出發地 + 目的地）。
    從 CommuteSchedule 提取，未來可擴充為完整歷史記錄表。
    """
    from app.crud import get_commute_schedule
    schedule = get_commute_schedule(db, userId)

    history = []
    if schedule:
        if schedule.origin_address:
            history.append({
                "type":    "origin",
                "name":    schedule.origin_name,
                "address": schedule.origin_address,
                "lat":     schedule.origin_lat,
                "lng":     schedule.origin_lng,
            })
        if schedule.dest_address:
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

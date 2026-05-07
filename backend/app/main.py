from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Base, engine, SessionLocal
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler
from app.crud import upsert_commute_schedule, get_commute_schedule

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


# ── POST /api/schedule/submit ─────────────────────────────────────────────────

@app.post("/api/schedule/submit")
async def submit_schedule(payload: ScheduleSubmitPayload, db: Session = Depends(get_db)):
    """LIFF 前端送出通勤排程設定。"""
    try:
        # 將前端欄位名稱轉換為內部格式
        data = {
            "originName":    payload.originName,
            "originAddress": payload.originAddress,
            "originLat":     payload.originLat,
            "originLng":     payload.originLng,
            "destName":      payload.destinationName,
            "destAddress":   payload.destinationAddress,
            "destLat":       payload.destLat,
            "destLng":       payload.destLng,
            "time":          payload.arrivalTime,
            "days":          payload.weekdays,
            "reminderEnabled": payload.reminderEnabled,
        }
        schedule = upsert_commute_schedule(db, payload.userId, data)
        return {
            "ok": True,
            "message": "排程設定已儲存",
            "data": {
                "userId":            payload.userId,
                "originName":        schedule.origin_name,
                "originAddress":     schedule.origin_address,
                "destinationName":   schedule.dest_name,
                "destinationAddress":schedule.dest_address,
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


# ── 基礎路由 ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "Smart Commute Assistant is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

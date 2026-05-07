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

class SchedulePayload(BaseModel):
    userId: str
    originName: Optional[str] = None
    originAddress: Optional[str] = None
    destName: Optional[str] = None
    destAddress: Optional[str] = None
    time: Optional[str] = None
    days: Optional[List[int]] = None
    reminderEnabled: Optional[bool] = True


# ── /api/schedule routes ──────────────────────────────────────────────────────

@app.post("/api/schedule")
async def post_schedule(payload: SchedulePayload, db: Session = Depends(get_db)):
    """LIFF 前端提交通勤排程設定（POST）。"""
    try:
        schedule = upsert_commute_schedule(db, payload.userId, payload.dict())
        return {
            "ok": True,
            "message": "排程設定已儲存",
            "data": {
                "userId": payload.userId,
                "originName": schedule.origin_name,
                "originAddress": schedule.origin_address,
                "destName": schedule.dest_name,
                "destAddress": schedule.dest_address,
                "time": schedule.time,
                "days": schedule.days,
                "reminderEnabled": schedule.reminder_enabled,
            }
        }
    except Exception as e:
        print(f"[POST /api/schedule] error={e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schedule")
async def get_schedule(userId: str = Query(...), db: Session = Depends(get_db)):
    """LIFF 前端讀取已儲存的通勤排程設定（GET），用於預填編輯表單。"""
    schedule = get_commute_schedule(db, userId)
    if not schedule:
        return {"hasData": False}
    return {
        "hasData": True,
        "userId": userId,
        "originName": schedule.origin_name,
        "originAddress": schedule.origin_address,
        "destName": schedule.dest_name,
        "destAddress": schedule.dest_address,
        "time": schedule.time,
        "days": schedule.days,
        "reminderEnabled": schedule.reminder_enabled,
    }


@app.get("/")
async def root():
    return {"message": "Smart Commute Assistant is running"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

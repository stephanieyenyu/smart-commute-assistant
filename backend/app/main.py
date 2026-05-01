from fastapi import FastAPI

from app.db import Base, engine
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler

from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.tasks import async_check_all_commutes

Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    commute_scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
    commute_scheduler.add_job(
        async_check_all_commutes,
        "cron",
        hour="6-10",
        minute="*/5"
    )
    commute_scheduler.start()
    start_reminder_scheduler()
    try:
        yield
    finally:
        commute_scheduler.shutdown()
        if reminder_scheduler.running:
            reminder_scheduler.shutdown()

app = FastAPI(title="Smart Commute Assistant", lifespan=lifespan)
app.include_router(webhook_router)



@app.get("/")
async def root():
    return {"message": "Smart commute assistant is running"}

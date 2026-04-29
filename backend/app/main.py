from fastapi import FastAPI
from app.db import Base, engine
import app.models
from app.webhook import router as webhook_router

from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.tasks import async_check_all_commutes

Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        async_check_all_commutes,
        "cron",
        hour="6-10",
        minute="*/5"
    )
    scheduler.start()
    yield
    # Shutdown the scheduler
    scheduler.shutdown()

app = FastAPI(title="Smart Commute Assistant", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

app.include_router(webhook_router)
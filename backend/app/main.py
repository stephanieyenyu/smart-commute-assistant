from fastapi import FastAPI

from app.db import Base, engine
from app.webhook import router as webhook_router
from app.reminder_scheduler import start_reminder_scheduler

from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.tasks import async_check_all_commutes

Base.metadata.create_all(bind=engine)

<<<<<<< HEAD
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
=======
app = FastAPI()
app.include_router(webhook_router)
>>>>>>> cb646c664c1b63374efeeb9cc188560a21e05b4a


@app.on_event("startup")
async def startup_event():
    start_reminder_scheduler()


@app.get("/")
async def root():
    return {"message": "Smart commute assistant is running"}
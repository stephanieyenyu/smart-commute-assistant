from fastapi import FastAPI

from app.db import engine
from app.dashboard import router as dashboard_router
from app.webhook import router as webhook_router
from app.reminder_scheduler import scheduler as reminder_scheduler, start_reminder_scheduler
from app.schema_guard import ensure_runtime_schema

from contextlib import asynccontextmanager

ensure_runtime_schema(engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_reminder_scheduler()
    try:
        yield
    finally:
        if reminder_scheduler.running:
            reminder_scheduler.shutdown()

app = FastAPI(title="Smart Commute Assistant", lifespan=lifespan)
app.include_router(dashboard_router)
app.include_router(webhook_router)



@app.get("/")
async def root():
    return {"message": "Smart commute assistant is running"}

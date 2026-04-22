from fastapi import FastAPI

from app.db import Base, engine
from app.webhook import router as webhook_router
from app.reminder_scheduler import start_reminder_scheduler

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.include_router(webhook_router)


@app.on_event("startup")
async def startup_event():
    start_reminder_scheduler()


@app.get("/")
async def root():
    return {"message": "Smart commute assistant is running"}
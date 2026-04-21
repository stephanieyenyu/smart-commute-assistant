from fastapi import FastAPI
from app.db import Base, engine
import app.models
from app.webhook import router as webhook_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Smart Commute Assistant")

@app.get("/health")
async def health():
    return {"status": "ok"}

app.include_router(webhook_router)
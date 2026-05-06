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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import engine
from app.dashboard import router as dashboard_router
from app.webhook import router as webhook_router
from app.liff_routes import router as liff_router, api_router as liff_api_router
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

# ⚠️ CORS 跨域設定：允許所有來源（開發階段）
# 部署後建議改為特定網域：["https://your-static-site.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生產環境請改為特定網域
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(webhook_router)
app.include_router(liff_router)
app.include_router(liff_api_router)

# 原有的 API 路由已經在 liff_routes.py 中定義：
# POST /api/schedule/submit
# 不需要額外修改，CORS 中間件會自動處理跨域請求
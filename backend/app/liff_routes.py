import asyncio
import os
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from app.db import SessionLocal
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    upsert_commute_schedule,
)
from app.line_client import push_text
from app.reminder_scheduler import clear_today_reminder_state_for_user

router = APIRouter(prefix="/liff", tags=["liff"])
api_router = APIRouter(prefix="/api", tags=["api"])

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 取得目前檔案的絕對路徑，用於定位 static 資料夾
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "..", "static")
SCHEDULE_FORM_PATH = os.path.join(STATIC_DIR, "schedule_form.html")


class ScheduleSubmitRequest(BaseModel):
    """LIFF 排程表單提交資料"""
    userId: str
    destination: str
    arrivalTime: str  # HH:MM 格式
    originAddress: str | None = None  # 出發地地址，可選
    weekdays: list[int]  # [0, 1, 2, 3, 4] 星期列表


@router.get("/schedule", response_class=HTMLResponse)
async def get_schedule_form(request: Request):
    """
    回傳 LIFF 排程表單 HTML 頁面
    Route: GET /liff/schedule
    """
    try:
        # 使用絕對路徑確保在 Render 環境下能找到檔案
        print(f"[LIFF] Loading HTML from: {SCHEDULE_FORM_PATH}")
        if not os.path.exists(SCHEDULE_FORM_PATH):
            print(f"[LIFF] ERROR: File not found at {SCHEDULE_FORM_PATH}")
            raise HTTPException(status_code=404, detail="表單頁面不存在")
        
        # 使用 FileResponse 直接回傳 HTML 檔案
        return FileResponse(
            path=SCHEDULE_FORM_PATH,
            media_type="text/html; charset=utf-8",
            filename="schedule_form.html"
        )
    except FileNotFoundError:
        print(f"[LIFF Schedule Form] File not found: {SCHEDULE_FORM_PATH}")
        raise HTTPException(status_code=404, detail="表單頁面不存在")
    except Exception as e:
        print(f"[LIFF Schedule Form] Error: {e}")
        raise HTTPException(status_code=500, detail="無法載入表單頁面")


@api_router.post("/schedule/add")
async def submit_schedule(data: ScheduleSubmitRequest):
    """
    接收 LIFF 表單提交資料
    Route: POST /api/schedule/submit
    
    Request Body:
    {
        "userId": "U1234567890...",
        "destination": "公司",
        "arrivalTime": "09:00",
        "originAddress": "台北市信義區...",
        "weekdays": [0, 1, 2, 3, 4]
    }
    """
    db = SessionLocal()
    try:
        # 1. 取得或建立 User（使用 LINE user ID）
        user = get_or_create_user(db, line_user_id=data.userId)
        get_or_create_profile(db, user.id)

        print(f"[LIFF Submit] user_id={user.id} destination={data.destination} arrival={data.arrivalTime}")

        # 2. 建立統一排程資料。LIFF 舊表單只提供地址文字時，由主 API 流程保留原始文字。
        schedule = upsert_commute_schedule(
            db=db,
            line_user_id=data.userId,
            data={
                "originName": data.originAddress,
                "originAddress": data.originAddress,
                "destName": data.destination,
                "destAddress": data.destination,
                "time": data.arrivalTime,
                "days": data.weekdays,
                "reminderEnabled": True,
            },
        )

        print(f"[LIFF Submit] schedule_id={schedule.id} created successfully")

        # 4. 清除今天的提醒狀態（強制重新計算）
        try:
            clear_today_reminder_state_for_user(user.id)
        except Exception as e:
            print(f"[LIFF Submit] Clear cache error: {e}")

        # 5. 透過 LINE Messaging API 推播確認訊息
        try:
            line_user_id = user.line_user_id
            confirmation_text = (
                f"✅ 排程設定成功\n\n"
                f"🏢 目的地：{data.destination}\n"
                f"⏰ 抵達時間：{data.arrivalTime}\n"
                f"📆 啟用日：{_format_weekdays(data.weekdays)}\n\n"
                f"已為您設定完成，屆時會主動提醒您出門！"
            )
            
            # 使用非同步推送，不阻塞回應
            asyncio.create_task(push_text(line_user_id, confirmation_text))
            
        except Exception as e:
            print(f"[LIFF Submit] Push notification error: {e}")
            # 即使推播失敗，排程已成功儲存，不影響主流程

        return {
            "ok": True,
            "message": "排程設定成功",
            "schedule_id": schedule.id,
        }

    except ValueError as ve:
        print(f"[LIFF Submit] ValueError: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"[LIFF Submit] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="伺服器內部錯誤，請稍後再試")
    finally:
        db.close()


def _format_weekdays(weekdays: list[int]) -> str:
    """
    將星期列表格式化為可讀字串
    0=週一, 1=週二, ..., 6=週日
    """
    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    
    # 特殊情況：每天都選
    if set(weekdays) == set(range(7)):
        return "每天"
    
    # 平日（週一至週五）
    if set(weekdays) == {0, 1, 2, 3, 4}:
        return "平日（週一至週五）"
    
    # 週末（週六、週日）
    if set(weekdays) == {5, 6}:
        return "週末（週六、週日）"
    
    # 自訂
    names = [weekday_names[d] for d in sorted(weekdays)]
    return "、".join(names)

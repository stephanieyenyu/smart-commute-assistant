"""
dashboard_ws.py — WebSocket 連線管理器
提供：
  - GET  /ws/dashboard/{user_id}       Dashboard 建立 WS 長連線
  - POST /api/alert/acknowledge/{user_id}  Dashboard 播完語音後確認
  - GET  /api/alert/status/{user_id}   Dashboard 輪詢用（WS fallback）
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CommuteOverride

router = APIRouter()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _override_query(db, user_id: int, today):
    return db.query(CommuteOverride).filter(
        CommuteOverride.user_id == user_id,
        CommuteOverride.target_date == today,
    )


def _latest_override_query(db, user_id: int, today):
    return _override_query(db, user_id, today).order_by(CommuteOverride.departure_question_sent_at.desc())


# ── Connection Manager ────────────────────────────────────────────────────────

class ConnectionManager:
    """管理每個 user_id 對應的 WebSocket 連線集合（允許多視窗/裝置）。"""

    def __init__(self):
        # user_id (str) → set[WebSocket]
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(user_id, set()).add(ws)
        print(f"[ws] connected user_id={user_id} total={len(self._connections[user_id])}")

    def disconnect(self, user_id: str, ws: WebSocket):
        sockets = self._connections.get(user_id, set())
        sockets.discard(ws)
        if not sockets:
            self._connections.pop(user_id, None)
        print(f"[ws] disconnected user_id={user_id}")

    async def broadcast(self, user_id: str, payload: dict):
        """廣播 JSON 訊息給指定 user_id 的所有連線。"""
        sockets = list(self._connections.get(user_id, set()))
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception as e:
                print(f"[ws] send failed user_id={user_id}: {e}")
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    async def broadcast_all(self, payload: dict):
        """廣播給所有已連線的客戶端（例如全域通知）。"""
        for user_id in list(self._connections.keys()):
            await self.broadcast(user_id, payload)

    def is_connected(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))


# 全域單例
manager = ConnectionManager()


# ── WebSocket Route ───────────────────────────────────────────────────────────

@router.websocket("/ws/dashboard/{user_id}")
async def dashboard_ws(websocket: WebSocket, user_id: str):
    """
    Dashboard WebSocket 端點。
    - 連線後每 30 秒發送 heartbeat {"type": "ping"}
    - 後端出門提醒觸發時推送 {"type": "voice_alert", "text": "...", "departure_time": "HH:MM"}
    - 前端確認後推送 {"type": "alert_ack"}
    """
    await manager.connect(user_id, websocket)
    try:
        # 連線建立後，立即傳送當前 alert 狀態
        await _push_current_alert_status(user_id, websocket)

        while True:
            try:
                # 等待前端訊息（心跳 / ack），timeout=30s 送 ping
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                event_type = data.get("type", "")

                if event_type == "ack":
                    # 前端已播完語音，更新 alert_status = "acknowledged"
                    await _acknowledge_alert(user_id)
                    await websocket.send_json({"type": "ack_confirmed"})

                elif event_type == "ping":
                    await websocket.send_json({"type": "pong"})

            except asyncio.TimeoutError:
                # 心跳
                await websocket.send_json({
                    "type": "ping",
                    "ts": datetime.now(TAIPEI_TZ).isoformat(),
                })

    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
    except Exception as e:
        print(f"[ws] error user_id={user_id}: {e}")
        manager.disconnect(user_id, websocket)


# ── REST fallback Routes ──────────────────────────────────────────────────────

@router.post("/api/alert/acknowledge/{user_id}")
async def acknowledge_alert(user_id: str):
    """
    Dashboard 播完語音後，呼叫此 API 確認（WebSocket 的 REST fallback）。
    """
    await _acknowledge_alert(user_id)
    return {"ok": True, "message": "alert acknowledged"}


@router.get("/api/alert/status/{user_id}")
async def get_alert_status(user_id: str):
    """
    Dashboard 輪詢用：取得目前 alert_status。
    回傳 {"alert_status": None | "pending" | "triggered" | "acknowledged", "departure_time": "HH:MM" | None}
    """
    db = SessionLocal()
    try:
        from datetime import date
        today = datetime.now(TAIPEI_TZ).date()
        # 找到 user
        from app.models import User
        user = db.query(User).filter(User.line_user_id == user_id).first()
        if not user:
            return {"alert_status": None, "departure_time": None}

        override = _latest_override_query(db, user.id, today).first()

        if not override:
            return {"alert_status": None, "departure_time": None}

        return {
            "alert_status": override.alert_status,
            "departure_time": override.frozen_departure_time,
        }
    finally:
        db.close()


# ── Internal Helpers ──────────────────────────────────────────────────────────

async def _push_current_alert_status(user_id: str, websocket: WebSocket):
    """連線後立即推送當前 alert 狀態（如果有 pending 提醒）。"""
    db = SessionLocal()
    try:
        from datetime import date
        today = datetime.now(TAIPEI_TZ).date()
        from app.models import User
        user = db.query(User).filter(User.line_user_id == user_id).first()
        if not user:
            return

        override = _override_query(db, user.id, today).filter(
            CommuteOverride.alert_status == "pending",
        ).order_by(CommuteOverride.departure_question_sent_at.desc()).first()

        if override and override.alert_status == "pending":
            await websocket.send_json({
                "type": "voice_alert",
                "text": override.frozen_reminder_text or "出發時間到了，請準備出門！",
                "departure_time": override.frozen_departure_time,
            })
    except Exception as e:
        print(f"[ws] _push_current_alert_status error: {e}")
    finally:
        db.close()


async def _acknowledge_alert(user_id: str):
    """將今天的 alert_status 更新為 acknowledged。"""
    db = SessionLocal()
    try:
        from datetime import date
        today = datetime.now(TAIPEI_TZ).date()
        from app.models import User
        user = db.query(User).filter(User.line_user_id == user_id).first()
        if not user:
            return

        override = _override_query(db, user.id, today).filter(
            CommuteOverride.alert_status == "pending",
        ).order_by(CommuteOverride.departure_question_sent_at.desc()).first()

        if override:
            override.alert_status = "acknowledged"
            db.commit()
            print(f"[ws] alert acknowledged user_id={user_id}")
    except Exception as e:
        print(f"[ws] _acknowledge_alert error: {e}")
    finally:
        db.close()


async def trigger_voice_alert(
    line_user_id: str,
    reminder_text: str,
    departure_time: str,
    schedule_id: int | None = None,
):
    """
    由 reminder_scheduler 呼叫：
    更新 alert_status = "pending"，並透過 WebSocket 廣播給已連線的 Dashboard。
    """
    db = SessionLocal()
    try:
        today = datetime.now(TAIPEI_TZ).date()
        from app.models import User
        user = db.query(User).filter(User.line_user_id == line_user_id).first()
        if not user:
            return

        override_query = db.query(CommuteOverride).filter(
            CommuteOverride.user_id == user.id,
            CommuteOverride.target_date == today,
        )
        if schedule_id is not None:
            override_query = override_query.filter(CommuteOverride.schedule_id == schedule_id)
        override = override_query.order_by(CommuteOverride.departure_question_sent_at.desc()).first()

        if override:
            override.alert_status = "pending"
            db.commit()
            print(f"[ws] alert_status=pending set for user_id={user.id}")

    except Exception as e:
        print(f"[ws] trigger_voice_alert db error: {e}")
    finally:
        db.close()

    # 廣播給所有已連線的 Dashboard（以 LINE user ID 為 key）
    if manager.is_connected(line_user_id):
        await manager.broadcast(line_user_id, {
            "type": "voice_alert",
            "text": reminder_text,
            "departure_time": departure_time,
        })
        print(f"[ws] voice_alert broadcast to user={line_user_id}")

"""
family.py — 家庭群組 API 路由
提供：
  POST /api/family/create                   建立家庭群組
  GET  /api/family/invite/{group_id}        取得邀請連結
  POST /api/family/join                     以 token 加入家庭群組
  GET  /api/family/dashboard/{group_id}     取得家庭看板資料（所有成員今日排程）
  PATCH /api/family/member/{member_id}/nickname  設定成員暱稱
  GET  /api/family/my-group                 查詢用戶所在的群組資訊
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import FamilyGroup, FamilyMember, User, CommuteSchedule, CommuteOverride

router = APIRouter(prefix="/api/family", tags=["family"])
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class CreateGroupPayload(BaseModel):
    line_user_id: str
    name: str


class JoinGroupPayload(BaseModel):
    line_user_id: str
    invite_token: str
    nickname: Optional[str] = None


class UpdateNicknamePayload(BaseModel):
    nickname: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_user(db: Session, line_user_id: str) -> User:
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        user = User(line_user_id=line_user_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _get_base_url() -> str:
    """回傳後端基礎 URL（由環境變數設定，fallback 為 localhost）。"""
    import os
    return os.getenv("BASE_URL", "http://localhost:8000")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/create")
async def create_family_group(payload: CreateGroupPayload, db: Session = Depends(get_db)):
    """建立新的家庭群組，建立者自動成為第一位成員。"""
    user = _get_or_create_user(db, payload.line_user_id)

    # 檢查是否已經是某群組成員（目前限制：一人僅能屬於一個群組）
    existing = db.query(FamilyMember).filter(FamilyMember.user_id == user.id).first()
    if existing:
        group = db.query(FamilyGroup).filter(FamilyGroup.id == existing.group_id).first()
        return {
            "ok": False,
            "message": "您已經是家庭群組成員",
            "group_id": existing.group_id,
            "group_name": group.name if group else None,
        }

    invite_token = uuid.uuid4().hex
    group = FamilyGroup(name=payload.name, invite_token=invite_token)
    db.add(group)
    db.commit()
    db.refresh(group)

    member = FamilyMember(group_id=group.id, user_id=user.id, nickname=None)
    db.add(member)
    db.commit()
    db.refresh(member)

    base_url = _get_base_url()
    invite_link = f"{base_url}/dashboard?family={group.id}&token={invite_token}"

    return {
        "ok": True,
        "group_id": group.id,
        "group_name": group.name,
        "invite_token": invite_token,
        "invite_link": invite_link,
        "message": f"家庭群組「{group.name}」已建立！",
    }


@router.get("/invite/{group_id}")
async def get_invite_link(
    group_id: int,
    line_user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """取得家庭群組的邀請連結（需為群組成員才能查詢）。"""
    group = db.query(FamilyGroup).filter(FamilyGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="找不到家庭群組")

    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        raise HTTPException(status_code=403, detail="用戶不存在")

    member = db.query(FamilyMember).filter(
        FamilyMember.group_id == group_id,
        FamilyMember.user_id == user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="您不是此家庭群組的成員")

    base_url = _get_base_url()
    invite_link = f"{base_url}/dashboard?family={group.id}&token={group.invite_token}"

    return {
        "ok": True,
        "group_id": group.id,
        "group_name": group.name,
        "invite_token": group.invite_token,
        "invite_link": invite_link,
    }


@router.post("/join")
async def join_family_group(payload: JoinGroupPayload, db: Session = Depends(get_db)):
    """以邀請 token 加入家庭群組。"""
    group = db.query(FamilyGroup).filter(
        FamilyGroup.invite_token == payload.invite_token
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="邀請連結無效或已過期")

    user = _get_or_create_user(db, payload.line_user_id)

    # 檢查是否已是成員
    existing = db.query(FamilyMember).filter(
        FamilyMember.group_id == group.id,
        FamilyMember.user_id == user.id,
    ).first()
    if existing:
        return {
            "ok": True,
            "message": "您已經是此家庭群組的成員",
            "group_id": group.id,
            "group_name": group.name,
            "member_id": existing.id,
        }

    member = FamilyMember(
        group_id=group.id,
        user_id=user.id,
        nickname=payload.nickname,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    return {
        "ok": True,
        "message": f"已成功加入家庭群組「{group.name}」！",
        "group_id": group.id,
        "group_name": group.name,
        "member_id": member.id,
    }


@router.get("/dashboard/{group_id}")
async def get_family_dashboard(
    group_id: int,
    db: Session = Depends(get_db),
):
    """
    取得家庭看板資料：群組所有成員今日排程與預計出門時間。
    此端點公開（Dashboard 直接讀取，不需登入）。
    """
    group = db.query(FamilyGroup).filter(FamilyGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="找不到家庭群組")

    members = db.query(FamilyMember).filter(FamilyMember.group_id == group_id).all()
    today = datetime.now(TAIPEI_TZ).date()

    members_data = []
    for member in members:
        user = db.query(User).filter(User.id == member.user_id).first()
        if not user:
            continue

        schedule = db.query(CommuteSchedule).filter(
            CommuteSchedule.user_id == user.id
        ).first()

        override = db.query(CommuteOverride).filter(
            CommuteOverride.user_id == user.id,
            CommuteOverride.target_date == today,
        ).first()

        # 今日是否在排程的適用星期內
        day_of_week = today.weekday()  # 0=Mon … 6=Sun
        is_active_today = False
        if schedule and schedule.days:
            is_active_today = day_of_week in schedule.days

        days_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
        days_str = ""
        if schedule and schedule.days:
            days_str = "、".join(f"週{days_map[d]}" for d in sorted(schedule.days))

        members_data.append({
            "member_id": member.id,
            "user_id": user.id,
            "line_user_id": user.line_user_id,
            "nickname": member.nickname,
            "is_active_today": is_active_today,
            "schedule": {
                "origin_name": schedule.origin_name if schedule else None,
                "origin_address": schedule.origin_address if schedule else None,
                "dest_name": schedule.dest_name if schedule else None,
                "dest_address": schedule.dest_address if schedule else None,
                "arrival_time": schedule.time if schedule else None,
                "weekdays": schedule.days if schedule else [],
                "weekdays_str": days_str,
                "reminder_enabled": schedule.reminder_enabled if schedule else False,
            } if schedule else None,
            "today_override": {
                "departure_time": override.frozen_departure_time if override else None,
                "alert_status": override.alert_status if override else None,
                "arrival_time": override.target_arrival_time if override else None,
            } if override else None,
        })

    return {
        "ok": True,
        "group_id": group.id,
        "group_name": group.name,
        "date": today.isoformat(),
        "members": members_data,
    }


@router.patch("/member/{member_id}/nickname")
async def update_member_nickname(
    member_id: int,
    payload: UpdateNicknamePayload,
    db: Session = Depends(get_db),
):
    """更新成員暱稱。"""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="找不到成員")

    member.nickname = payload.nickname
    db.commit()
    db.refresh(member)

    return {
        "ok": True,
        "member_id": member.id,
        "nickname": member.nickname,
        "message": "暱稱已更新",
    }


@router.get("/my-group")
async def get_my_group(
    line_user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """查詢用戶所屬的家庭群組資訊。"""
    user = db.query(User).filter(User.line_user_id == line_user_id).first()
    if not user:
        return {"ok": False, "group": None, "message": "用戶不存在"}

    member = db.query(FamilyMember).filter(FamilyMember.user_id == user.id).first()
    if not member:
        return {"ok": False, "group": None, "message": "您尚未加入任何家庭群組"}

    group = db.query(FamilyGroup).filter(FamilyGroup.id == member.group_id).first()
    if not group:
        return {"ok": False, "group": None, "message": "群組不存在"}

    base_url = _get_base_url()
    dashboard_link = f"{base_url}/dashboard?family={group.id}"

    return {
        "ok": True,
        "group": {
            "id": group.id,
            "name": group.name,
            "invite_token": group.invite_token,
            "dashboard_link": dashboard_link,
        },
        "member": {
            "id": member.id,
            "nickname": member.nickname,
        },
    }

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class Household(Base):
    """家庭群組，用 invite_code 讓多位 LINE 使用者綁到同一個家庭看板。"""
    __tablename__ = "households"

    id = Column(Integer, primary_key=True, index=True)
    invite_code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    members = relationship("User", back_populates="household")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    household_id = Column(Integer, ForeignKey("households.id"), index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    profile = relationship("CommuteProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    overrides = relationship("CommuteOverride", back_populates="user", cascade="all, delete-orphan")
    schedules = relationship(
        "CommuteSchedule",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="CommuteSchedule.id",
    )
    household = relationship("Household", back_populates="members")

    @property
    def schedule(self):
        """Backward-compatible primary schedule for legacy command paths."""
        active_schedules = [s for s in self.schedules if getattr(s, "is_active", True)]
        enabled = [s for s in active_schedules if getattr(s, "reminder_enabled", True)]
        return (enabled or active_schedules or self.schedules or [None])[0]


class CommuteProfile(Base):
    __tablename__ = "commute_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True, nullable=False)

    home_address = Column(String, nullable=True)
    home_lat = Column(Float, nullable=True)
    home_lng = Column(Float, nullable=True)
    home_city = Column(String, nullable=True)
    home_township = Column(String, nullable=True)
    home_place_name = Column(String, nullable=True)

    office_address = Column(String, nullable=True)
    office_lat = Column(Float, nullable=True)
    office_lng = Column(Float, nullable=True)
    office_city = Column(String, nullable=True)
    office_township = Column(String, nullable=True)
    office_place_name = Column(String, nullable=True)

    selected_bus_stop_id = Column(String, nullable=True)
    selected_bus_stop_name = Column(String, nullable=True)
    selected_bus_stop_lat = Column(Float, nullable=True)
    selected_bus_stop_lng = Column(Float, nullable=True)

    selected_metro_station_id = Column(String, nullable=True)
    selected_metro_station_name = Column(String, nullable=True)
    selected_metro_station_lat = Column(Float, nullable=True)
    selected_metro_station_lng = Column(Float, nullable=True)

    last_computed_walk_to_bus_stop_min = Column(Integer, nullable=True)
    last_computed_walk_to_metro_min = Column(Integer, nullable=True)
    walk_to_bus_stop_min = Column(Integer, nullable=True)

    preferred_arrival_time = Column(String, nullable=True)
    preferred_mode = Column(String, nullable=True)
    pending_field = Column(String, nullable=True)
    reminder_enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="profile")


class CommuteSchedule(Base):
    """
    統一排程設定：記錄使用者的固定通勤排程。
    由 LIFF 前端寫入，後端排程器讀取並觸發提醒。
    """
    __tablename__ = "commute_schedules"
    __table_args__ = (
        UniqueConstraint("user_id", "dest_name", name="uq_commute_schedules_user_destination"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    # 出發地資訊
    origin_name = Column(String, nullable=True)
    origin_address = Column(String, nullable=True)
    origin_lat = Column(Float, nullable=True)
    origin_lng = Column(Float, nullable=True)

    # 目的地資訊
    dest_name = Column(String, nullable=True)
    dest_address = Column(String, nullable=True)
    dest_lat = Column(Float, nullable=True)
    dest_lng = Column(Float, nullable=True)

    # 排程時間（HH:MM 格式）
    time = Column(String, nullable=True)

    # 提醒星期（JSON 陣列，0=週一，1=週二，...，6=週日）
    days = Column(JSON, nullable=True)

    # 提醒開關
    reminder_enabled = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="schedules")


class CommuteOverride(Base):
    """每日排程狀態記錄：記錄今天的提醒是否已發送、凍結的提醒內容等。"""
    __tablename__ = "commute_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "target_date", "schedule_id", name="uq_commute_overrides_user_date_schedule"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    schedule_id = Column(Integer, ForeignKey("commute_schedules.id"), index=True, nullable=True)
    target_date = Column(Date, index=True, nullable=False)

    target_arrival_time = Column(String, nullable=True)
    transport_mode_override = Column(String, nullable=True)

    frozen_plan_key = Column(String, nullable=True)
    frozen_departure_time = Column(String, nullable=True)
    frozen_reminder_text = Column(Text, nullable=True)
    reminder_prepared_at = Column(DateTime(timezone=True), nullable=True)

    last_sent_plan_key = Column(String, nullable=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    monitor_one_hour_sent_at = Column(DateTime(timezone=True), nullable=True)
    monitor_five_min_sent_at = Column(DateTime(timezone=True), nullable=True)
    departure_question_sent_at = Column(DateTime(timezone=True), nullable=True)
    departed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="overrides")
    schedule = relationship("CommuteSchedule")


class CommuteLog(Base):
    __tablename__ = "commute_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    day_of_week = Column(Integer, nullable=True)
    is_holiday = Column(Boolean, nullable=True)

    target_arrival_time = Column(String, nullable=True)
    suggested_departure_time = Column(String, nullable=True)
    actual_departure_time = Column(String, nullable=True)

    suggested_transport = Column(String, nullable=True)
    actual_transport = Column(String, nullable=True)

    weather_condition = Column(String, nullable=True)
    rain_prob = Column(Integer, nullable=True)
    temp = Column(Float, nullable=True)

    gmaps_traffic_duration = Column(Integer, nullable=True)
    tdx_bus_eta = Column(Integer, nullable=True)

    actual_arrival_time = Column(String, nullable=True)
    is_late = Column(Boolean, nullable=True)


class ApiHealthLog(Base):
    __tablename__ = "api_health_logs"

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)

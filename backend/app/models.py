from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
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
    __table_args__ = (
        Index("ix_users_household_id_id", "household_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    household_id = Column(Integer, ForeignKey("households.id"), index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    profile = relationship("CommuteProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    destinations = relationship("CommuteDestination", back_populates="user", cascade="all, delete-orphan")
    overrides = relationship("CommuteOverride", back_populates="user", cascade="all, delete-orphan")
    schedules = relationship("CommuteSchedule", back_populates="user", cascade="all, delete-orphan")
    household = relationship("Household", back_populates="members")
    logs = relationship("CommuteLog", back_populates="user")
    family_memberships = relationship("FamilyMember", back_populates="user", cascade="all, delete-orphan")
    
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
    identity_type = Column(String, nullable=True)
    destination_label = Column(String, nullable=True)
    preferred_mode = Column(String, nullable=True)
    transport_preference = Column(JSON, nullable=True)
    max_walk_mins = Column(Integer, nullable=True)
    pending_field = Column(String, nullable=True)
    reminder_enabled = Column(Boolean, nullable=False, default=True)
    active_weekdays = Column(JSON, nullable=True)  # 0=週一，1=週二，...，6=週日

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="profile")

class CommuteSchedule(Base):
    __tablename__ = "commute_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)

    origin_name = Column(String, nullable=True)
    origin_address = Column(String, nullable=True)
    origin_lat = Column(Float, nullable=True)
    origin_lng = Column(Float, nullable=True)

    dest_name = Column(String, nullable=True)
    dest_address = Column(String, nullable=True)
    dest_lat = Column(Float, nullable=True)
    dest_lng = Column(Float, nullable=True)

    time = Column(String, nullable=True)  # 預計抵達時間 (arrivalTime)
    days = Column(JSON, nullable=True)    # 適用星期 (weekdays): 0=週一，1=週二，...，6=週日
    reminder_enabled = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="schedules")
    overrides = relationship("CommuteOverride", back_populates="schedule")

    @property
    def target_arrival_time(self):
        return self.time

    @target_arrival_time.setter
    def target_arrival_time(self, value):
        self.time = value

    @property
    def destination_label(self):
        return self.dest_name or self.dest_address

    @destination_label.setter
    def destination_label(self, value):
        self.dest_name = value

    @property
    def active_weekdays(self):
        return self.days

    @active_weekdays.setter
    def active_weekdays(self, value):
        self.days = value


CommuteScheduleTemplate = CommuteSchedule

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
    commute_disabled = Column(Boolean, nullable=True)
    commute_enabled = Column(Boolean, nullable=True)

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
    alert_status = Column(String, nullable=True)
    departure_check_sent_at = Column(DateTime(timezone=True), nullable=True)
    departure_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    departure_snoozed_until = Column(DateTime(timezone=True), nullable=True)
    departure_timeout_at = Column(DateTime(timezone=True), nullable=True)
    departure_timeout_silent = Column(Boolean, nullable=False, default=False)
    nightly_brief_plan_key = Column(String, nullable=True)
    nightly_brief_sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="overrides")
    schedule = relationship("CommuteSchedule", back_populates="overrides")


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
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="logs")


class ApiHealthLog(Base):
    __tablename__ = "api_health_logs"

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)


# ─────────────────────────────────────────────────────────
# 家庭群組模組
# ─────────────────────────────────────────────────────────

class FamilyGroup(Base):
    """家庭群組：可包含多位成員，共享家庭看板。"""
    __tablename__ = "family_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    # 邀請用的唯一 token（UUID hex）
    invite_token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    members = relationship("FamilyMember", back_populates="group", cascade="all, delete-orphan")


class FamilyMember(Base):
    """家庭群組成員：將 User 與 FamilyGroup 關聯，並記錄成員暱稱。"""
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("family_groups.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    nickname = Column(String, nullable=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    group = relationship("FamilyGroup", back_populates="members")
    user = relationship("User", back_populates="family_memberships")

class CommuteDestination(Base):
    __tablename__ = "commute_destinations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    destination_name = Column(String)
    user = relationship("User", back_populates="destinations")

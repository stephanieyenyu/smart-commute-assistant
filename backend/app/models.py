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


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    household_id = Column(String, index=True, nullable=True)
    role = Column(String, nullable=False, default="user")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    profile = relationship("CommuteProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    overrides = relationship("CommuteOverride", back_populates="user", cascade="all, delete-orphan")
    logs = relationship("CommuteLog", back_populates="user", cascade="all, delete-orphan")


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
    transport_preference = Column(JSON, nullable=True)
    max_walk_mins = Column(Integer, nullable=True)
    pending_field = Column(String, nullable=True)
    reminder_enabled = Column(Boolean, nullable=False, default=True)
    active_weekdays = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="profile")


class CommuteOverride(Base):
    __tablename__ = "commute_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "target_date", name="uq_commute_overrides_user_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
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

    departure_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    departure_check_sent_at = Column(DateTime(timezone=True), nullable=True)
    departure_snoozed_until = Column(DateTime(timezone=True), nullable=True)
    snooze_one_min_sent_at = Column(DateTime(timezone=True), nullable=True)
    snooze_departure_sent_at = Column(DateTime(timezone=True), nullable=True)

    nightly_brief_plan_key = Column(String, nullable=True)
    nightly_brief_sent_at = Column(DateTime(timezone=True), nullable=True)

    watchdog_alert_key = Column(String, nullable=True)
    watchdog_alert_sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="overrides")


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
    selection_source = Column(String, nullable=True)
    recommended_mode = Column(String, nullable=True)
    risk_score = Column(Float, nullable=True)
    weather_buffer_minutes = Column(Integer, nullable=True)
    
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

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)


class CommuteProfile(Base):
    __tablename__ = "commute_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    home_address = Column(String, nullable=True)
    home_lat = Column(Float, nullable=True)
    home_lng = Column(Float, nullable=True)
    home_city = Column(String, nullable=True)

    office_address = Column(String, nullable=True)
    office_lat = Column(Float, nullable=True)
    office_lng = Column(Float, nullable=True)
    office_city = Column(String, nullable=True)

    preferred_arrival_time = Column(String, nullable=True)
    walk_to_bus_stop_min = Column(Integer, nullable=True)
    pending_field = Column(String, nullable=True)


class CommuteScheduleOverride(Base):
    __tablename__ = "commute_schedule_overrides"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    commute_date = Column(Date, nullable=False, index=True)
    target_arrival_time = Column(String, nullable=False)


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
from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String
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
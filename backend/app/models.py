from sqlalchemy import Column, Integer, String, Float, ForeignKey, Date, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    profile = relationship("CommuteProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    overrides = relationship("CommuteOverride", back_populates="user", cascade="all, delete-orphan")


class CommuteProfile(Base):
    __tablename__ = "commute_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True, nullable=False)

    # 住家
    home_address = Column(String, nullable=True)
    home_lat = Column(Float, nullable=True)
    home_lng = Column(Float, nullable=True)
    home_city = Column(String, nullable=True)
    home_township = Column(String, nullable=True)
    home_place_name = Column(String, nullable=True)

    # 公司
    office_address = Column(String, nullable=True)
    office_lat = Column(Float, nullable=True)
    office_lng = Column(Float, nullable=True)
    office_city = Column(String, nullable=True)
    office_township = Column(String, nullable=True)
    office_place_name = Column(String, nullable=True)

    # 公車站
    selected_bus_stop_id = Column(String, nullable=True)
    selected_bus_stop_name = Column(String, nullable=True)
    selected_bus_stop_lat = Column(Float, nullable=True)
    selected_bus_stop_lng = Column(Float, nullable=True)

    # 捷運站
    selected_metro_station_id = Column(String, nullable=True)
    selected_metro_station_name = Column(String, nullable=True)
    selected_metro_station_lat = Column(Float, nullable=True)
    selected_metro_station_lng = Column(Float, nullable=True)

    # 步行 / 計算
    last_computed_walk_to_bus_stop_min = Column(Integer, nullable=True)
    last_computed_walk_to_metro_min = Column(Integer, nullable=True)
    walk_to_bus_stop_min = Column(Integer, nullable=True)

    # 偏好 / 流程
    preferred_arrival_time = Column(String, nullable=True)
    preferred_mode = Column(String, nullable=True)
    pending_field = Column(String, nullable=True)

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

    # 當日 / 次日臨時覆蓋到公司時間
    target_arrival_time = Column(String, nullable=True)

    # 當日交通方式覆蓋
    # 可存: auto / bus / metro / bus_to_metro
    transport_mode_override = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="overrides")
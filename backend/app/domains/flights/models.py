"""SQLAlchemy ORM models for rag-flight-lab.

Tables are created by `db/init.sql` on first PostgreSQL start. These ORM
models mirror that schema so the backend can read/write through SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_number: Mapped[str] = mapped_column(Text, nullable=False)
    airline: Mapped[str] = mapped_column(Text, nullable=False)
    aircraft_type: Mapped[str] = mapped_column(Text, nullable=False)
    tail_number: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_departure: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_arrival: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    logs: Mapped[list["FlightLog"]] = relationship(
        back_populates="flight", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="flight", cascade="all, delete-orphan"
    )


class FlightLog(Base):
    __tablename__ = "flight_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("flights.id", ondelete="CASCADE"), nullable=False
    )
    log_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    log_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=True
    )
    embedding_model: Mapped[Optional[str]] = mapped_column(Text)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer)
    embedded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    flight: Mapped[Flight] = relationship(back_populates="logs")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("flights.id", ondelete="CASCADE"), nullable=False
    )
    incident_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    flight: Mapped[Flight] = relationship(back_populates="incidents")

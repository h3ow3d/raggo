"""Minimal Pydantic schemas exposed by the Phase 1 API."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str


class StatsResponse(BaseModel):
    flights: int
    flight_logs: int
    incidents: int
    embedded_logs: int
    unembedded_logs: int

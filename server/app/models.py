"""Pydantic-Modelle für die REST-API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capacity: Optional[int] = Field(default=None, ge=0)


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    # `capacity` mit explizitem Unterscheiden zwischen "nicht gesetzt" und "auf null".
    capacity: Optional[int] = Field(default=None, ge=0)


class ZoneOut(BaseModel):
    id: int
    name: str
    capacity: Optional[int] = None
    occupancy: int


class SensorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    side_a_zone_id: Optional[int] = None
    side_b_zone_id: Optional[int] = None


class SensorOut(BaseModel):
    id: str
    name: Optional[str] = None
    side_a_zone_id: Optional[int] = None
    side_b_zone_id: Optional[int] = None
    baseline_mm: Optional[int] = None
    last_seen: Optional[float] = None
    online: bool
    rssi: Optional[int] = None
    fw: Optional[str] = None


class ResetRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=240)

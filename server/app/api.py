"""REST-API + SSE-Stream für PeoplePassage."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from .auth import require_auth
from .db import Store
from .export import build_csv
from .models import ResetRequest, SensorUpdate, ZoneCreate, ZoneUpdate

# Öffentliche Endpunkte (nur lesend): kein Auth erforderlich.
public_router = APIRouter(prefix="/api")
# Schreibende Endpunkte + CSV-Export: Passwortschutz.
private_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def get_store(request: Request) -> Store:
    return request.app.state.store


# --------------------------------------------------------------------- Zonen
@public_router.get("/zones")
def list_zones(store: Store = Depends(get_store)) -> list[dict]:
    return store.list_zones()


@private_router.post("/zones", status_code=201)
def create_zone(payload: ZoneCreate, store: Store = Depends(get_store)) -> dict:
    return store.create_zone(payload.name, payload.capacity)


@private_router.patch("/zones/{zone_id}")
def update_zone(
    zone_id: int, payload: ZoneUpdate, store: Store = Depends(get_store)
) -> dict:
    fields = payload.model_fields_set
    zone = store.update_zone(
        zone_id,
        name=payload.name,
        capacity=payload.capacity,
        set_capacity="capacity" in fields,
    )
    if zone is None:
        raise HTTPException(404, "Zone nicht gefunden")
    return zone


@private_router.delete("/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: int, store: Store = Depends(get_store)) -> Response:
    if not store.delete_zone(zone_id):
        raise HTTPException(404, "Zone nicht gefunden")
    return Response(status_code=204)


@private_router.post("/zones/{zone_id}/reset")
def reset_zone(
    zone_id: int,
    payload: Optional[ResetRequest] = None,
    store: Store = Depends(get_store),
    actor: str = Depends(require_auth),
) -> dict:
    current = store.get_zone(zone_id)
    if current is None:
        raise HTTPException(404, "Zone nicht gefunden")
    if current["is_external"]:
        raise HTTPException(400, "Externe Zone wird automatisch berechnet und kann nicht genullt werden")
    reason = payload.reason if payload else None
    updated = store.reset_zone(zone_id, reason=reason, actor=actor)
    if updated is None:
        raise HTTPException(404, "Zone nicht gefunden")
    return updated


# ------------------------------------------------------------------- Sensoren
@public_router.get("/sensors")
def list_sensors(store: Store = Depends(get_store)) -> list[dict]:
    return store.list_sensors()


@private_router.patch("/sensors/{sensor_id}")
def update_sensor(
    sensor_id: str, payload: SensorUpdate, store: Store = Depends(get_store)
) -> dict:
    fields = payload.model_fields_set
    current = store.get_sensor(sensor_id)
    if current is None:
        raise HTTPException(404, "Sensor nicht gefunden")

    for side in ("side_a_zone_id", "side_b_zone_id"):
        zid = getattr(payload, side)
        if side in fields and zid is not None:
            zone = store.get_zone(zid)
            if zone is None:
                raise HTTPException(400, f"Zone {zid} existiert nicht")
            if zone["is_external"]:
                raise HTTPException(400, "Externe Zone kann keinem Sensor zugeordnet werden")

    # Gleichheit gegen den effektiven Endzustand prüfen (auch wenn nur eine Seite
    # gepatcht wird), damit A und B nie dieselbe Zone werden.
    next_a = payload.side_a_zone_id if "side_a_zone_id" in fields else current["side_a_zone_id"]
    next_b = payload.side_b_zone_id if "side_b_zone_id" in fields else current["side_b_zone_id"]
    if next_a is not None and next_a == next_b:
        raise HTTPException(400, "Seite A und B dürfen nicht dieselbe Zone sein")

    sensor = store.update_sensor(
        sensor_id,
        name=payload.name,
        set_name="name" in fields,
        side_a_zone_id=payload.side_a_zone_id,
        set_side_a="side_a_zone_id" in fields,
        side_b_zone_id=payload.side_b_zone_id,
        set_side_b="side_b_zone_id" in fields,
    )
    if sensor is None:
        raise HTTPException(404, "Sensor nicht gefunden")
    return sensor


@private_router.delete("/sensors/{sensor_id}", status_code=204)
def delete_sensor(sensor_id: str, store: Store = Depends(get_store)) -> Response:
    if not store.delete_sensor(sensor_id):
        raise HTTPException(404, "Sensor nicht gefunden")
    return Response(status_code=204)


@private_router.post("/sensors/{sensor_id}/calibrate")
def calibrate_sensor(sensor_id: str, store: Store = Depends(get_store)) -> dict:
    if store.get_sensor(sensor_id) is None:
        raise HTTPException(404, "Sensor nicht gefunden")
    try:
        store.send_command(sensor_id, "calibrate")
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"status": "sent", "sensor_id": sensor_id, "cmd": "calibrate"}


# --------------------------------------------------------------------- Export
def _parse_ts(value: Optional[str], default: float) -> float:
    if not value:
        return default
    try:
        # Akzeptiert ISO 8601 (auch mit 'Z') oder Unix-Epoch-Sekunden.
        if value.replace(".", "", 1).isdigit():
            return float(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise HTTPException(400, f"Ungültiger Zeitstempel: {value}")


@private_router.get("/export.csv")
def export_csv(
    request: Request,
    store: Store = Depends(get_store),
    date_from: Optional[str] = Query(default=None, alias="from"),
    date_to: Optional[str] = Query(default=None, alias="to"),
) -> Response:
    now = time.time()
    ts_to = _parse_ts(date_to, now)
    ts_from = _parse_ts(date_from, ts_to - 24 * 3600)
    if ts_from >= ts_to:
        raise HTTPException(400, "'from' muss vor 'to' liegen")
    csv_text = build_csv(store, ts_from, ts_to)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="peoplepassage-{stamp}.csv"'
        },
    )


# ------------------------------------------------------------------------ SSE
@public_router.get("/stream")
async def stream(request: Request, store: Store = Depends(get_store)) -> EventSourceResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    store.add_subscriber(queue)

    async def event_gen():
        try:
            # Initialer Snapshot, damit der Client sofort den Stand kennt.
            yield {"event": "snapshot", "data": _json(store.snapshot())}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": "snapshot", "data": _json(snap)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            store.remove_subscriber(queue)

    return EventSourceResponse(event_gen())


def _json(obj) -> str:
    import json

    return json.dumps(obj)

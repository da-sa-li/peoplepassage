"""PeoplePassage – Server-Einstiegspunkt.

Verdrahtet Datenschicht (Store), MQTT-Brücke, REST-API und SSE.
Dashboard (öffentlich, /): server/app/web/dashboard.html
Konfiguration (passwortgeschützt, /config): server/app/web/config.html
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from .api import private_router, public_router
from .auth import require_auth
from .db import Store
from .mqtt import MqttBridge

WEB_DIR = Path(__file__).parent / "web"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("peoplepassage")

DB_PATH = os.environ.get("DB_PATH", "/data/peoplepassage.db")
MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
# Sensor gilt als offline, wenn länger kein Lebenszeichen kam.
OFFLINE_TIMEOUT_S = float(os.environ.get("OFFLINE_TIMEOUT_S", "60"))
OFFLINE_SWEEP_S = float(os.environ.get("OFFLINE_SWEEP_S", "15"))


async def _offline_sweeper(store: Store) -> None:
    while True:
        await asyncio.sleep(OFFLINE_SWEEP_S)
        try:
            store.mark_stale_offline(OFFLINE_TIMEOUT_S)
        except Exception:  # pragma: no cover
            log.exception("Offline-Sweep fehlgeschlagen")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = Store(DB_PATH)
    store.set_loop(asyncio.get_running_loop())
    app.state.store = store

    bridge = MqttBridge(store, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD)
    bridge.start()
    app.state.mqtt = bridge

    sweeper = asyncio.create_task(_offline_sweeper(store))
    log.info("PeoplePassage gestartet (db=%s, mqtt=%s:%s)", DB_PATH, MQTT_HOST, MQTT_PORT)
    try:
        yield
    finally:
        sweeper.cancel()
        bridge.stop()
        store.close()


app = FastAPI(title="PeoplePassage", lifespan=lifespan)
app.include_router(public_router)
app.include_router(private_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/")
def dashboard() -> FileResponse:
    """Öffentliches Live-Dashboard (Beamer/Handy-optimiert, kein Login)."""
    return FileResponse(WEB_DIR / "dashboard.html", media_type="text/html")


@app.get("/config", dependencies=[Depends(require_auth)])
def config_panel() -> FileResponse:
    """Passwortgeschützte Konfigurationsseite (Zonen, Sensoren, CSV-Export)."""
    return FileResponse(WEB_DIR / "config.html", media_type="text/html")

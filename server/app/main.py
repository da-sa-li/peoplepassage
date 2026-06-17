"""PeoplePassage – Server-Einstiegspunkt.

Phase 1: minimaler Platzhalter, damit der Docker-Stack startfähig und verifizierbar
ist. Die eigentliche Server-Logik (SQLite-Schema, MQTT-Ingest, Belegungslogik,
REST-API, Dashboard, CSV-Export) folgt in Phase 2/3 – siehe ROADMAP.md.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="PeoplePassage")


@app.get("/healthz")
def healthz() -> dict:
    """Liveness-Check für Container/Orchestrierung."""
    return {"status": "ok"}


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(
        {
            "app": "PeoplePassage",
            "phase": 1,
            "message": (
                "Infrastruktur steht. Server-Funktionen (Dashboard, MQTT-Ingest, "
                "CSV-Export) folgen in Phase 2/3."
            ),
        }
    )

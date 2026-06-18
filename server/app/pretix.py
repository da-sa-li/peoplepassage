"""pretix-Checkin-API-Client (optional): liefert die aktuell laut Ticketkontrolle
anwesende Gesamtzahl, siehe CLAUDE.md Abschnitt "pretix-Integration".

https://docs.pretix.eu/dev/api/resources/checkinlists.html
"""

from __future__ import annotations

import httpx


async def fetch_inside_count(
    base_url: str,
    organizer: str,
    event: str,
    checkinlist_id: str,
    api_token: str,
    timeout: float = 10.0,
) -> int:
    """`inside_count` der Checkin-Liste abfragen (Personen mit Entry-Scan ohne
    folgenden Exit-Scan; erfordert eine Checkin-Liste mit Ein-/Auslass-Tracking)."""
    url = (
        f"{base_url.rstrip('/')}/api/v1/organizers/{organizer}/events/{event}"
        f"/checkinlists/{checkinlist_id}/status/"
    )
    headers = {"Authorization": f"Token {api_token}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return int(data["inside_count"])

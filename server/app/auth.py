"""Einfacher Passwortschutz via HTTP Basic.

Es zählt nur das Passwort (Benutzername beliebig) – verglichen gegen die
Umgebungsvariable `DASHBOARD_PASSWORD`. Browser cachen Basic-Auth, sodass auch
`EventSource`/SSE-Anfragen automatisch authentifiziert werden.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_basic = HTTPBasic(auto_error=True)


def require_auth(credentials: HTTPBasicCredentials = Depends(_basic)) -> str:
    expected = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected:
        # Ohne gesetztes Passwort wird der Zugriff bewusst verweigert.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHBOARD_PASSWORD ist nicht gesetzt.",
        )
    if not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falsches Passwort.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username or "admin"

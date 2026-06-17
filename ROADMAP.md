# ROADMAP — PeoplePassage

Lebender Fortschritts-Tracker. **Am Ende jeder Session aktualisieren, committen und pushen**,
damit Folge-Sessions nahtlos anknüpfen können. Projektkontext/Architektur: siehe
[`CLAUDE.md`](./CLAUDE.md).

Legende: `[ ]` offen · `[~]` in Arbeit · `[x]` fertig

## Phasen

### Phase 0 — Persistenz-Anker

- [x] `CLAUDE.md` (dauerhafter Projektkontext)
- [x] `ROADMAP.md` (dieser Tracker)
- [x] Initial committen & pushen auf `claude/determined-wozniak-jm252w`

### Phase 1 — Repo-Gerüst & Infrastruktur

- [x] `docker-compose.yml` (`mosquitto` + `app`, Volumes, Ports)
- [x] `mosquitto/config/mosquitto.conf` (Listener + Username/Passwort-Auth) +
      `mosquitto/entrypoint.sh` (Passwortdatei aus Env generieren)
- [x] `.env.example` (`DASHBOARD_PASSWORD`, `MQTT_USERNAME`, `MQTT_PASSWORD`)
- [x] `.gitignore`
- [x] `server/Dockerfile`, `server/requirements.txt`
- [x] `server/app/main.py` (minimaler Platzhalter `/healthz` + `/`, in Phase 2 ersetzt)

### Phase 2 — Server-Kern

- [x] `app/db.py` — SQLite-Schema (`zones`, `sensors`, `passages`, `adjustments`;
      Unique `(sensor_id, seq)`) + thread-sicherer `Store`
- [x] `app/models.py` — Pydantic-Modelle
- [x] `app/mqtt.py` — MQTT-Ingest (event/status, in/out→a2b/b2a) + cmd-Publish (paho v2)
- [x] Belegungslogik (Türen-als-Kanten, Live-Cache, Rekonstruktion beim Start,
      Recompute bei Re-Mapping)
- [x] `app/auth.py` — einfacher Passwortschutz (HTTP Basic gegen `DASHBOARD_PASSWORD`)
- [x] `app/api.py` — REST-Endpunkte (zones, sensors, reset, calibrate, export, stream/SSE)
- [x] `app/export.py` — minutengenaue CSV-Aggregation
- [x] `app/main.py` — App-Zusammenbau (lifespan), MQTT-Bridge, Offline-Sweeper, SSE

### Phase 3 — Dashboard

- [x] Live-Belegungs-Kacheln pro Zone (Farbe bei Kapazitätsannäherung)
- [x] Sensor-Health-Liste (online/offline, last_seen, RSSI, Baseline)
- [x] Buttons: Zone nullen, Sensor kalibrieren (mit Bestätigung)
- [x] Config-UI: Zonen anlegen/löschen, Sensor-Seiten (A/B) zuordnen
- [x] CSV-Export-Button mit Zeitraumwahl
- [x] SSE-Live-Updates
- Umsetzung: `server/app/web/index.html` (self-contained, Vanilla JS/CSS) +
  passwortgeschützte Route `GET /` in `app/main.py` (FileResponse).

### Phase 4 — Test ohne Hardware & Verifikation

- [x] `tools/sim_sensor.py` (MQTT-Simulator mehrerer Sensoren; status/event, LWT-Offline,
      konfigurierbar via Args/Env)
- [x] End-to-End-Verifikation gemäß Checkliste in `CLAUDE.md` (Simulator-Format →
      MQTT-Bridge → Belegung inkl. geteilter Tür → API → SSE-Snapshot → Reset →
      Calibrate → minutengenaues CSV → Offline-Erkennung). Alle grün.
- Hinweis: Ein *Live*-Lauf des Simulators braucht einen MQTT-Broker
      (`docker compose up`); die Verifikation hier nutzt den realen Server-Pfad
      (`MqttBridge._on_message`) ohne Broker.

### Phase 5 — Firmware (ESP32 + VL53L1X)

- [x] `firmware/platformio.ini` (ESP32, Libs: SparkFun VL53L1X, PubSubClient)
- [x] `firmware/src/config.h.example` (WLAN/MQTT/SENSOR_ID/ROI/Schwellen)
- [x] `firmware/src/main.cpp` — WiFi + MQTT (LWT) + Dual-ROI-Zähllogik + calibrate;
      `seq` in NVS persistiert (Reboot-sichere Idempotenz)
- [~] `pio run` Kompilier-Check — **in dieser Umgebung kein PlatformIO/Toolchain**;
      muss auf einem Host mit `pio` ausgeführt werden (Code reviewt, Libs gepinnt)
- [x] `firmware/README.md` — Verkabelung, Montage, Flashen, Kalibrierung, MQTT-Vertrag

### Phase 6 — Doku-Abschluss

- [x] Top-Level `README.md` (Gesamtüberblick, Architektur, Setup, Betrieb, API/MQTT,
      Verweise auf `CLAUDE.md`/`ROADMAP.md`/`firmware/README.md`)

## Offene Entscheidungen / Annahmen

- Zeitstempel intern in **UTC**; Anzeige/CSV ggf. lokale Zone (noch festzulegen).
- SSE gewählt (einfacher als WebSocket) für reine Live-Anzeige; Aktionen via REST.
- SQLite ausreichend für Veranstaltungs-Größenordnung (kein Postgres geplant).
- Sensor sendet rohes „in/out"; finale Richtung/Seitenzuordnung passiert serverseitig.

## Was als Nächstes

Alle geplanten Phasen (0–6) sind umgesetzt. Offene Punkte, die einen Host mit Toolchain
bzw. Hardware brauchen (in dieser Umgebung nicht möglich):
- `pio run` Kompilier-Check der Firmware auf einem Host mit PlatformIO (siehe Phase 5).
- Echter `docker compose up`-Smoke-Test mit Broker + Simulator bzw. echter Hardware.
- Optional: Feldkalibrierung/Tuning der ROI-Zentren je nach Türgeometrie.

Hinweise:
- In dieser Umgebung läuft kein Docker-Daemon — `docker compose build`/`up` muss auf
  einem Host mit Daemon laufen. `docker compose config` + Resolver-Checks sind grün.
- Belegung wird aus der **aktuellen** Sensor-Seitenzuordnung berechnet; ein Re-Mapping
  während der Veranstaltung rechnet die Historie unter der neuen Topologie neu
  (`recompute_occupancy`). Orientierung (in/out) korrigiert man durch Tausch von Seite A/B.

## Session-Log

- 2026-06-17: Projekt geplant, Architektur festgelegt (MQTT/Mosquitto, FastAPI+SQLite,
  Passwortschutz). Phase 0 (CLAUDE.md + ROADMAP.md) erstellt. PR #1 gemerged.
- 2026-06-17: Phase 1 umgesetzt — Docker-Compose (mosquitto + app), Mosquitto mit
  Passwort-Auth via Entrypoint, `.env.example`, `.gitignore`, Server-Container
  (Dockerfile + requirements) und Platzhalter-App. `docker compose config` valide,
  Dependencies aufgelöst (FastAPI 0.115, uvicorn 0.32, paho-mqtt 2.1).
- 2026-06-17: Phase 2 umgesetzt — Server-Kern (db/models/mqtt/auth/api/export/main).
  Verifiziert via stdlib-Tests (Belegung, geteilte Tür, Dedupe, Reset, Recompute, CSV)
  und End-to-End-API-Tests mit FastAPI-TestClient (Auth, Zonen-/Sensor-CRUD, Validierung,
  Reset, Calibrate, CSV-Header, SSE-Broadcast inkl. Cross-Thread, FK-Cascade). Alle grün.
- 2026-06-17: PR #2 (Phase 1+2) erstellt; CodeRabbit-Review umgesetzt: PATCH-Validierung
  (Seite A/B gegen Endzustand), nur UNIQUE als idempotentes Duplikat (sonst raise),
  `_notify` snapshotet Subscriber unter Lock, CSV-Formula-Injection-Schutz, Typannotationen
  (lifespan/MQTT-Callbacks), Dockerfile non-root + HEALTHCHECK, requirements exakt gepinnt
  + pydantic, QueueFull bei langsamen SSE-Clients abgefangen. SQLAlchemy bewusst NICHT
  ergänzt (stdlib sqlite3, s. CLAUDE.md). PR #2 gemerged.
- 2026-06-17: Phase 3 umgesetzt — Dashboard (`server/app/web/index.html`) + Route `GET /`.
  Live-Kacheln, Sensor-Health, Nullen/Kalibrieren/Zonen-Config, CSV-Export, SSE-Live.
  Verifiziert via TestClient (Auth 401/200, HTML ausgeliefert, API-Verdrahtung).
- 2026-06-17: Phase 4 umgesetzt — `tools/sim_sensor.py` (MQTT-Simulator) + E2E-Verifikation
  über den realen Server-Pfad (ohne Broker): Auto-Registrierung, geteilte Tür (Halle/
  Backstage), Dedupe, SSE-Snapshot, Reset, Calibrate, minutengenaues CSV, Offline nach
  Timeout — alle grün.
- 2026-06-17: Phase 5 umgesetzt — ESP32-Firmware (`firmware/`): VL53L1X Dual-ROI-
  Richtungserkennung, WiFi+MQTT (event/status, LWT), calibrate, NVS-persistierte seq;
  platformio.ini, config.h.example, README. `pio run` steht aus (kein PlatformIO hier).
- 2026-06-17: Phase 6 umgesetzt — Top-Level-`README.md` (Gesamtüberblick, Architektur,
  Repo-Struktur, Schnellstart, Bedienung, REST-/MQTT-Vertrag). Alle Phasen 0–6 fertig.
- 2026-06-17: PR #3 (Phase 3+4+5) erstellt; CodeRabbit-Review (11 Findings) umgesetzt:
  Firmware ignoriert ungültige (0-)Messungen + Ring-Puffer gegen Eventverlust bei
  MQTT-Ausfall; Dashboard XSS-sicher per DOM-APIs (keine Inline-Handler), Label-`for`,
  Kapazitäts-Validierung; Simulator leere-Sensorliste-Guard, geseedete RNG-Reihenfolge,
  Callback-Typannotationen, publish-wait beim Shutdown; ROADMAP-Doku (push-Schritt,
  pio-run-Dublette). E2E nach Fixes erneut grün.

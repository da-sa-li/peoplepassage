# ROADMAP — PeoplePassage

Lebender Fortschritts-Tracker. **Am Ende jeder Session aktualisieren** und committen, damit
Folge-Sessions nahtlos anknüpfen können. Projektkontext/Architektur: siehe
[`CLAUDE.md`](./CLAUDE.md).

Legende: `[ ]` offen · `[~]` in Arbeit · `[x]` fertig

## Phasen

### Phase 0 — Persistenz-Anker
- [x] `CLAUDE.md` (dauerhafter Projektkontext)
- [x] `ROADMAP.md` (dieser Tracker)
- [x] Initial committen & pushen auf `claude/determined-wozniak-jm252w`

### Phase 1 — Repo-Gerüst & Infrastruktur
- [ ] `docker-compose.yml` (`mosquitto` + `app`, Volumes, Ports)
- [ ] `mosquitto/config/mosquitto.conf` (Listener + Username/Passwort-Auth)
- [ ] `.env.example` (`DASHBOARD_PASSWORD`, `MQTT_USERNAME`, `MQTT_PASSWORD`)
- [ ] `.gitignore`
- [ ] `server/Dockerfile`, `server/requirements.txt`

### Phase 2 — Server-Kern
- [ ] `app/db.py` — SQLite-Schema + Migrations (`zones`, `sensors`, `passages`,
      `adjustments`; Unique `(sensor_id, seq)`)
- [ ] `app/models.py` — Pydantic-Modelle
- [ ] `app/mqtt.py` — MQTT-Ingest (event/status) + cmd-Publish
- [ ] Belegungslogik (Türen-als-Kanten, Live-Cache, Rekonstruktion beim Start)
- [ ] `app/auth.py` — einfacher Passwortschutz
- [ ] `app/api.py` — REST-Endpunkte (zones, sensors, reset, calibrate, export, stream)
- [ ] `app/export.py` — minutengenaue CSV-Aggregation
- [ ] `app/main.py` — App-Zusammenbau, SSE, Startup/Shutdown

### Phase 3 — Dashboard
- [ ] Live-Belegungs-Kacheln pro Zone (Farbe bei Kapazitätsannäherung)
- [ ] Sensor-Health-Liste (online/offline, last_seen, RSSI)
- [ ] Buttons: Zone nullen, Sensor kalibrieren (mit Bestätigung)
- [ ] Config-UI: Zonen anlegen, Sensor-Seiten (A/B) zuordnen
- [ ] CSV-Export-Button mit Zeitraumwahl
- [ ] SSE-Live-Updates

### Phase 4 — Test ohne Hardware & Verifikation
- [ ] `tools/sim_sensor.py` (MQTT-Simulator mehrerer Sensoren)
- [ ] End-to-End-Verifikation gemäß Checkliste in `CLAUDE.md`

### Phase 5 — Firmware (ESP32 + VL53L1X)
- [ ] `firmware/platformio.ini` (ESP32, Libs: VL53L1X, PubSubClient)
- [ ] `firmware/src/config.h.example`
- [ ] `firmware/src/main.cpp` — WiFi + MQTT (LWT) + Dual-ROI-Zähllogik + calibrate
- [ ] `pio run` Kompilier-Check
- [ ] `firmware/README.md` — Flashen, Montage, Kalibrierung

### Phase 6 — Doku-Abschluss
- [ ] Top-Level `README.md` (Gesamtüberblick, Setup, Betrieb)

## Offene Entscheidungen / Annahmen
- Zeitstempel intern in **UTC**; Anzeige/CSV ggf. lokale Zone (noch festzulegen).
- SSE gewählt (einfacher als WebSocket) für reine Live-Anzeige; Aktionen via REST.
- SQLite ausreichend für Veranstaltungs-Größenordnung (kein Postgres geplant).
- Sensor sendet rohes „in/out"; finale Richtung/Seitenzuordnung passiert serverseitig.

## Was als Nächstes
→ **Phase 1**: Repo-Gerüst + Docker-Compose + Mosquitto-Config anlegen.

## Session-Log
- 2026-06-17: Projekt geplant, Architektur festgelegt (MQTT/Mosquitto, FastAPI+SQLite,
  Passwortschutz). Phase 0 (CLAUDE.md + ROADMAP.md) erstellt.

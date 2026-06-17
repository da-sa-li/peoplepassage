# CLAUDE.md — PeoplePassage

Dauerhafter Projektkontext für jede Claude-/Entwickler-Session. **Zuerst lesen.**
Den **aktuellen Fortschritt** und „Was als Nächstes" findest du in [`ROADMAP.md`](./ROADMAP.md).

## Projektziel

Personenzähler für Teilbereiche einer Veranstaltung. An **jeder Tür** sitzt **ein ESP32 mit
einem VL53L1X-ToF-Sensor**, der **gerichtete** Durchgänge (rein/raus) erkennt und per WLAN
meldet. Ein **zentraler Server (Docker)** aggregiert die Durchgänge, fasst Sensoren zu
**Zonen** zusammen, zeigt die Live-Belegung auf einem **Dashboard** und erlaubt einen
**CSV-Export mit minutengenauer Auflösung**.

### Anforderungen (verbindlich)
1. Genau **ein ESP32 + ein VL53L1X pro Tür**, gerichtete Zählung.
2. Verbindung der Sensoren per WLAN zum zentralen Server.
3. Server aggregiert Durchgänge und stellt sie auf einem Dashboard dar.
4. Chronik nachvollziehbar — mindestens **CSV-Export per Klick, minutengenau**.
5. Mehrere Sensoren lassen sich zu einer **Zone** zusammenfassen (z. B. mehrere Türen eines
   Raums).
6. Ein Sensor kann **gleichzeitig Eingang einer Zone und Ausgang einer anderen** sein.
7. Server kann die **Personenzahl einer Zone nullen**.
8. Sensoren lassen sich **nullen/kalibrieren**, wenn niemand darunter steht.

## Kernkonzept: Türen als Kanten zwischen Zonen

Die Veranstaltung ist ein Graph:
- **Zone** = Knoten (Raum/Teilbereich). Spezielle „Außen"-Zone = `null` → wird nicht gezählt.
- **Tür/Sensor** = Kante mit zwei Seiten `side_a` und `side_b`; jede Seite zeigt auf eine Zone
  (oder Außen).

Ein gerichteter Durchgang aktualisiert **beide** angrenzenden Zonen:
- Richtung `a2b`: `zone_b += 1`, `zone_a -= 1`
- Richtung `b2a`: `zone_a += 1`, `zone_b -= 1`

Damit:
- **Mehrere Türen pro Raum** → mehrere Sensoren referenzieren dieselbe Zone.
- **Eingang Bereich X = Ausgang Bereich Y** → ein Sensor mit `side_a=Y`, `side_b=X`.

**Belegung einer Zone** = Σ(vorzeichenbehaftete Durchgänge) + Σ(manuelle Korrekturen).
Eine **Nullung** ist eine Korrektur-Buchung `delta = -aktuelle_Belegung` → auditierbar,
**keine Datenlöschung**.

## Richtungserkennung mit EINEM VL53L1X

Ein einzelner ToF-Sensor liefert nur eine Distanz. Richtung kommt aus der ST-„People-
Counting"-Technik per **Dual-ROI**: Der VL53L1X schaltet die SPAD-Region (Region of
Interest) um und misst abwechselnd **zwei nebeneinander liegende ROIs** quer über die
Türschwelle (zwei virtuelle Zonen):
- erst ROI 1, dann ROI 2 ausgelöst → eine Richtung
- erst ROI 2, dann ROI 1 → die andere Richtung

Schwelle = Distanz deutlich unter Ruhe-Baseline (Decke→Boden minus Personenhöhe).
Referenz: ST „VL53L1X people counting", SparkFun/Pololu-Beispiele.
**Grenzen:** sehr langsame Personen und zwei nebeneinander Gehende sind schwierig → Sensor
mittig über der Tür montieren, Türbreite ≤ Field-of-View.

## Architektur & Tech-Stack (entschieden)

- **Sensor:** ESP32 + VL53L1X, Firmware in **PlatformIO/Arduino (C++)**.
- **Transport:** **MQTT** über **Mosquitto**-Broker (Username/Passwort-Auth).
- **Server:** **Python + FastAPI + SQLite**, Dashboard via Jinja2 + statisches JS, Live per
  **SSE**.
- **Deployment:** **Docker Compose** (`mosquitto` + `app`).
- **Zugriffsschutz:** **einfacher Passwortschutz** (ein Passwort aus `.env`) für Dashboard
  und schreibende Aktionen.

## Repo-Struktur (Zielbild)

```text
firmware/                    PlatformIO-Projekt (ESP32)
  platformio.ini
  src/main.cpp               WiFi + MQTT + Dual-ROI-Zähllogik
  src/config.h.example       SSID/PW, MQTT-Host, sensor_id, Schwellen
  README.md                  Flashen, Montage, Kalibrierung
server/
  app/main.py                FastAPI-App, Startup, SSE
  app/mqtt.py                MQTT-Client, Event-Ingest + cmd-Publish
  app/db.py                  SQLite-Engine, Schema, Migrations
  app/models.py              Pydantic-/DB-Modelle
  app/api.py                 REST-Endpunkte
  app/auth.py                Einfacher Passwortschutz
  app/export.py              CSV-Aggregation (minutengenau)
  app/web/                   Dashboard (Templates + static)
  requirements.txt
  Dockerfile
mosquitto/config/mosquitto.conf
tools/sim_sensor.py          MQTT-Sensor-Simulator (Test ohne Hardware)
docker-compose.yml
.env.example                 DASHBOARD_PASSWORD, MQTT_USERNAME, MQTT_PASSWORD
README.md
```

## Datenmodell (SQLite)

- `zones(id, name, capacity NULLABLE, created_at)`
- `sensors(id TEXT pk, name, side_a_zone_id NULLABLE, side_b_zone_id NULLABLE, baseline_mm,
  last_seen, online, rssi)`
- `passages(id, sensor_id, ts_utc, direction CHECK('a2b'|'b2a'), seq)` — Roh-Eventlog,
  **Unique `(sensor_id, seq)`** gegen doppelte MQTT-Zustellung (Idempotenz)
- `adjustments(id, zone_id, ts_utc, delta INT, reason, actor)` — Nullungen/Korrekturen

Belegung wird aus `passages` + `adjustments` berechnet (Live-Cache im Speicher, beim Start
aus DB rekonstruiert).

## MQTT-Topics

- Event (Sensor→Server): `peoplepassage/<sensor_id>/event` — `{seq, direction:"in"|"out", ts}`
- Status/Heartbeat (Sensor→Server): `peoplepassage/<sensor_id>/status` —
  `{rssi, uptime, baseline_mm, fw}` (retained; LWT markiert offline)
- Kommando (Server→Sensor): `peoplepassage/<sensor_id>/cmd` — `{cmd:"calibrate"|"reboot"}`

Hinweis: „in/out" am Sensor wird **serverseitig** über die Seitenzuordnung
(`side_a`/`side_b`) auf `a2b`/`b2a` gemappt → Einbaurichtung in der Server-Config
korrigierbar, ohne Sensor neu zu flashen.

## REST-API (FastAPI)

- `GET  /api/zones` — Zonen inkl. Live-Belegung & Kapazität
- `POST /api/zones`, `PATCH/DELETE /api/zones/{id}`
- `POST /api/zones/{id}/reset` — **Belegung nullen** (legt `adjustment` an)
- `GET  /api/sensors`, `PATCH /api/sensors/{id}` — Sensor↔Zonen-Seiten zuordnen
- `POST /api/sensors/{id}/calibrate` — publiziert MQTT-`calibrate`
- `GET  /api/export.csv?from=&to=` — **minutengenauer CSV-Export**,
  Spalten `minute_utc, zone_id, zone_name, ins, outs, net, occupancy_end`
- `GET  /api/stream` (SSE) — Live-Push Belegung & Sensor-Status

Schreibende Routen + Dashboard hinter Passwortschutz.

## Konventionen

- **Branch:** `claude/determined-wozniak-jm252w`. Nicht ohne ausdrückliche Freigabe auf
  andere Branches pushen.
- **Commits:** klar & beschreibend. Kein Modell-Identifier in Commits/Code/Doku.
- **Push:** `git push -u origin claude/determined-wozniak-jm252w` (bei Netzwerkfehlern bis zu
  4× mit Backoff 2/4/8/16 s).
- **Kein PR** ohne ausdrückliche Aufforderung.
- **Nach jeder Phase** `ROADMAP.md` aktualisieren und committen/pushen.

## Setup / Run / Verifikation

```bash
# Server + Broker starten
cp .env.example .env            # DASHBOARD_PASSWORD etc. setzen
docker compose up --build       # Dashboard: http://localhost:8000

# Ohne Hardware testen
python tools/sim_sensor.py      # simulierte in/out-Events

# Firmware kompilieren
cd firmware && pio run
```

Verifikations-Checkliste: Dashboard erreichbar (Passwort) · Simulator lässt Zonen live
hoch/runter zählen · geteilte Tür ändert zwei Zonen gegenläufig · „Zone nullen" → 0 +
`adjustments`-Eintrag · „Sensor kalibrieren" → `cmd`-Topic erhält `calibrate` · CSV-Export
mit Minuten-Buckets · Sensor-Offline nach LWT/Timeout.

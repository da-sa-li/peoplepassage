# PeoplePassage

Personenzähler für Teilbereiche einer Veranstaltung. An **jeder Tür** sitzt **ein ESP32 mit
einem VL53L1X-ToF-Sensor**, der **gerichtete** Durchgänge (rein/raus) erkennt und per WLAN
an einen zentralen Docker-Server meldet. Der Server fasst Sensoren zu **Zonen** zusammen,
zeigt die Live-Belegung auf einem **Dashboard** und erlaubt einen **CSV-Export mit
minutengenauer Auflösung**.

> Weiterführend: [`CLAUDE.md`](./CLAUDE.md) (dauerhafter Architektur-Kontext),
> [`ROADMAP.md`](./ROADMAP.md) (Fortschritt), [`firmware/README.md`](./firmware/README.md)
> (Sensor-Hardware/Flashen/Kalibrierung).

## Funktionen

- Gerichtete Zählung pro Tür mit **einem** VL53L1X (Dual-ROI-Richtungserkennung).
- Mehrere Sensoren lassen sich zu einer **Zone** zusammenfassen (z. B. mehrere Türen
  eines Raums).
- Ein Sensor kann **gleichzeitig Eingang einer Zone und Ausgang einer anderen** sein
  (geteilte Tür).
- **Live-Dashboard** (SSE) mit Belegungs-Kacheln, Sensor-Health und Konfiguration.
- **Zone nullen** (auditierbar) und **Sensor kalibrieren** direkt aus dem Dashboard.
- **CSV-Export** der Chronik, minutengenau.
- **Passwortschutz** für Dashboard und Aktionen.

## Architektur

```text
  ESP32 + VL53L1X (pro Tür)                Docker-Server
  ┌─────────────────────┐   MQTT     ┌──────────────────────────────┐
  │ Dual-ROI-Zähllogik  │  (WLAN)    │  mosquitto  ◄──►  FastAPI-App │
  │ event / status (LWT)│ ─────────► │                   │  SQLite  │
  │ ◄── cmd (calibrate) │            │   Dashboard (SSE) ─┘  CSV     │
  └─────────────────────┘            └──────────────────────────────┘
```

**Kernkonzept „Türen als Kanten zwischen Zonen":** Eine Tür/ein Sensor verbindet zwei
Seiten (`A`/`B`), die je auf eine Zone (oder „Außen") zeigen. Ein Durchgang aktualisiert
beide angrenzenden Zonen gegenläufig (`a2b` → Zone B +1 / Zone A −1). Damit fallen geteilte
Türen und Mehrtür-Räume natürlich heraus. Die Belegung wird aus Durchgängen + manuellen
Korrekturen (Nullungen) berechnet; nichts wird gelöscht.

**Richtungserkennung mit EINEM Sensor:** Der VL53L1X misst abwechselnd zwei nebeneinander
liegende ROIs quer über die Türschwelle; die Auslöse-Reihenfolge ergibt die Richtung
(ST-„People-Counting"-Technik). Details/Grenzen: [`firmware/README.md`](./firmware/README.md).

## Repository

```text
server/        FastAPI-App (Datenschicht, MQTT-Bridge, REST, SSE, Dashboard), Dockerfile
mosquitto/     MQTT-Broker-Konfiguration + Entrypoint (Passwortdatei aus Env)
firmware/      ESP32/PlatformIO-Projekt (VL53L1X, WLAN, MQTT, calibrate)
tools/         sim_sensor.py – MQTT-Sensor-Simulator zum Testen ohne Hardware
docker-compose.yml   mosquitto + app
.env.example   DASHBOARD_PASSWORD, MQTT_USERNAME, MQTT_PASSWORD
```

## Schnellstart (Server)

Voraussetzung: Docker + Docker Compose.

```bash
cp .env.example .env          # DASHBOARD_PASSWORD / MQTT-Zugangsdaten setzen
docker compose up --build     # startet mosquitto + app
```

- Dashboard: <http://localhost:8000> (Login per `DASHBOARD_PASSWORD`)
- MQTT-Broker: Port `1883` (Username/Passwort aus `.env`)

### Ohne Hardware testen

```bash
pip install paho-mqtt
python tools/sim_sensor.py --username "$MQTT_USERNAME" --password "$MQTT_PASSWORD"
```

Der Simulator registriert Sensoren und sendet zufällige Durchgänge; im Dashboard die
Sensor-Seiten A/B den Zonen zuordnen und die Belegung live mitzählen sehen.

## Bedienung

1. **Zonen anlegen** (Name, optional Kapazität).
2. Sensoren erscheinen automatisch, sobald sie sich melden → **Seiten A/B** je einer Zone
   (oder „Außen") zuordnen. Falsche Richtung? Einfach A/B tauschen – kein Neu-Flashen nötig.
3. **Live-Belegung** verfolgen; bei Bedarf **Zone nullen** oder **Sensor kalibrieren**
   (nur wenn niemand darunter steht).
4. **CSV-Export** mit Zeitraumwahl für die Chronik.

## REST-API (Auszug)

Alle `/api/*`-Routen sind passwortgeschützt (HTTP Basic).

| Methode & Pfad | Zweck |
|---|---|
| `GET /api/zones` · `POST/PATCH/DELETE /api/zones[/{id}]` | Zonen inkl. Live-Belegung verwalten |
| `POST /api/zones/{id}/reset` | Belegung nullen (auditierbar) |
| `GET /api/sensors` · `PATCH /api/sensors/{id}` | Sensoren auflisten / Seiten zuordnen |
| `POST /api/sensors/{id}/calibrate` | Kalibrier-Kommando an den Sensor |
| `GET /api/export.csv?from=&to=` | minutengenauer CSV-Export |
| `GET /api/stream` | SSE-Livestream (Belegung + Sensor-Status) |

## MQTT-Vertrag

- Event (Sensor→Server): `peoplepassage/<id>/event` → `{seq, direction:"in"|"out"}`
- Status (Sensor→Server): `peoplepassage/<id>/status` →
  `{online, rssi, baseline_mm, fw, uptime}` (retained; LWT meldet `online:false`)
- Kommando (Server→Sensor): `peoplepassage/<id>/cmd` → `{cmd:"calibrate"|"reboot"}`

Idempotenz über `(sensor_id, seq)`; die Sequenznummer wird in der Firmware (NVS) persistiert.

## Firmware

Siehe [`firmware/README.md`](./firmware/README.md) für Verkabelung, Montage, Build/Flash
(`pio run -t upload`) und Kalibrierung.

## Lizenz

Siehe [`LICENSE.md`](./LICENSE.md).

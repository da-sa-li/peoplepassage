# PeoplePassage – ESP32-Türsensor (Firmware)

Ein ESP32 mit einem VL53L1X-ToF-Sensor pro Tür. Erkennt **gerichtete** Durchgänge
(rein/raus) per **Dual-ROI** und meldet sie per WLAN/MQTT an den zentralen Server.

## Hardware & Verkabelung

| VL53L1X | ESP32        |
|---------|--------------|
| VIN     | 3V3          |
| GND     | GND          |
| SDA     | GPIO 21      |
| SCL     | GPIO 22      |

(SDA/SCL über `PIN_SDA`/`PIN_SCL` in `config.h` änderbar.)

## Montage

- Sensor **mittig über der Tür** an der Decke, nach unten blickend.
- Die zwei ROIs teilen das Sichtfeld in **links/rechts quer zur Durchgangsrichtung** —
  eine Person löst beide Zonen nacheinander aus; die Reihenfolge ergibt die Richtung.
- Türbreite ≤ Sichtfeld; eine Person nach der anderen funktioniert am besten.
- Bei breiten/ungünstigen Türen die ROI-Zentren (`ROI_CENTER_LEFT/RIGHT`) tunen.

## Build & Flash (PlatformIO)

```bash
cd firmware
cp src/config.h.example src/config.h     # WLAN/MQTT/SENSOR_ID eintragen
pio run                                   # kompilieren
pio run -t upload                         # flashen
pio device monitor                        # serielle Ausgabe (115200)
```

`src/config.h` enthält Secrets und ist per `.gitignore` ausgeschlossen.
**`SENSOR_ID` muss pro Tür eindeutig sein** (z. B. `door1`, `door2`, …) und erscheint
so im Dashboard, wo die Seiten A/B den Zonen zugeordnet werden.

## Kalibrierung (Sensor „nullen")

Die Firmware vergleicht jede Messung mit einer **Baseline** (leere Decke→Boden-Distanz).
Eine Person, die näher als `Baseline − PRESENCE_MARGIN_MM` kommt, gilt als „anwesend".

- Auslösen über das Dashboard (Button **„Kalibrieren"**) oder MQTT:
  `peoplepassage/<id>/cmd` → `{"cmd":"calibrate"}`.
- **Nur kalibrieren, wenn niemand unter dem Sensor steht.** Es werden
  `CALIB_SAMPLES` Messungen gemittelt; die neue Baseline wird in NVS gespeichert und
  im `status` veröffentlicht.

## MQTT-Vertrag

- Event: `peoplepassage/<id>/event` → `{"seq":N,"direction":"in"|"out"}`
- Status/Heartbeat: `peoplepassage/<id>/status` →
  `{"online":true,"rssi":-55,"baseline_mm":2400,"fw":"esp32-1.0","uptime":123}` (retained)
- Last-Will: `{"online":false}` (retained) → Server markiert den Sensor bei
  Verbindungsabriss als offline.
- Kommando: `peoplepassage/<id>/cmd` → `{"cmd":"calibrate"|"reboot"}`

Die `seq` wird in NVS persistiert, damit der Server auch nach einem Reboot korrekt
dedupliziert (Idempotenz über `(sensor_id, seq)`).

## Hinweise / Grenzen

- „rein/raus" wird **serverseitig** über die Seiten-Zuordnung A/B auf die Zonen
  abgebildet — die Einbaurichtung lässt sich im Dashboard korrigieren, ohne neu zu flashen.
- Zwei nebeneinander Gehende oder sehr langsame Personen sind prinzipbedingt schwierig.
- `TIMING_BUDGET_MS` steuert den Kompromiss aus Geschwindigkeit und Rauschen.

#!/bin/bash
# SessionStart hook: zeigt den Status der PeoplePassage-Dev-Umgebung als Kontext an.

cd "$CLAUDE_PROJECT_DIR" || exit 0

if [ "$CLAUDE_CODE_REMOTE" = "true" ]; then
  echo "[peoplepassage] Cloud-Session: isolierte VM, kein Zugriff auf Pi-Hardware (ESP32/USB) oder den echten Mosquitto-Broker."
  echo "[peoplepassage] Docker ist hier verfügbar (z.B. \`docker compose up\`), startet aber eine eigene, isolierte Instanz."
  exit 0
fi

# Docker-Status
running="$(docker compose ps --status running --services 2>/dev/null)"
mosquitto_status="❌"
app_status="❌"
if echo "$running" | grep -qx mosquitto; then mosquitto_status="✅"; fi
if echo "$running" | grep -qx app; then app_status="✅"; fi
echo "[peoplepassage] Docker: mosquitto ${mosquitto_status} app ${app_status}"
if [ "$mosquitto_status" = "❌" ] || [ "$app_status" = "❌" ]; then
  echo "[peoplepassage]   -> nicht alle Container laufen, ggf. \`docker compose up -d\`"
fi

# MQTT erreichbar
if nc -z -w2 localhost 1883 2>/dev/null; then
  echo "[peoplepassage] MQTT (1883): ✅ erreichbar"
else
  echo "[peoplepassage] MQTT (1883): ❌ nicht erreichbar"
fi

# Server-Health
if curl -fs --max-time 2 http://localhost:8000/healthz >/dev/null 2>&1; then
  echo "[peoplepassage] Server (/healthz): ✅ erreichbar"
else
  echo "[peoplepassage] Server (/healthz): ❌ nicht erreichbar"
fi

# ESP32 per USB
esp32_dev="$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -n1)"
if [ -n "$esp32_dev" ]; then
  echo "[peoplepassage] ESP32: ✅ ${esp32_dev}"
else
  echo "[peoplepassage] ESP32: ❌ kein USB-Serial-Gerät gefunden"
fi

exit 0

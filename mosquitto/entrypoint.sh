#!/bin/sh
# Erzeugt die mosquitto-Passwortdatei aus den Umgebungsvariablen und startet den
# Broker. So bleiben Zugangsdaten aus dem Repo heraus (nur in .env).
set -e

PASSWD_FILE=/mosquitto/config/passwd

if [ -z "$MQTT_USERNAME" ] || [ -z "$MQTT_PASSWORD" ]; then
  echo "FEHLER: MQTT_USERNAME und MQTT_PASSWORD müssen gesetzt sein (siehe .env)." >&2
  exit 1
fi

# Passwortdatei (neu) generieren; -b nimmt das Passwort als Argument, -c legt neu an.
mosquitto_passwd -b -c "$PASSWD_FILE" "$MQTT_USERNAME" "$MQTT_PASSWORD"
chmod 0700 "$PASSWD_FILE"

exec mosquitto -c /mosquitto/config/mosquitto.conf

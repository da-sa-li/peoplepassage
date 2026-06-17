"""MQTT-Brücke: Ingest von Sensor-Events/-Status und Versand von Kommandos.

Topics (siehe CLAUDE.md):
- `peoplepassage/<sensor_id>/event`  ← {seq, direction:"in"|"out", ts?}
- `peoplepassage/<sensor_id>/status` ← {rssi, uptime, baseline_mm, fw, online?}
- `peoplepassage/<sensor_id>/cmd`     → {cmd:"calibrate"|"reboot"}

Läuft in einem Hintergrund-Thread (paho `loop_start`). Schreibvorgänge gehen über
den thread-sicheren `Store`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import paho.mqtt.client as mqtt

from .db import Store

log = logging.getLogger("peoplepassage.mqtt")

TOPIC_PREFIX = "peoplepassage"
# Sensor meldet "in"/"out"; serverseitiges Mapping auf gerichtete Kanten.
# Orientierung ist über das Tauschen der Sensor-Seiten (A/B) korrigierbar.
DIRECTION_MAP = {"in": "a2b", "out": "b2a"}


class MqttBridge:
    def __init__(
        self, store: Store, host: str, port: int, username: str, password: str
    ) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="peoplepassage-server"
        )
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------- Lifecycle
    def start(self) -> None:
        self._client.connect_async(self.host, self.port, keepalive=60)
        self._client.loop_start()
        # Command-Publisher im Store registrieren.
        self.store.command_publisher = self.publish_command

    def stop(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # pragma: no cover - best effort beim Shutdown
            pass

    # ------------------------------------------------------------- Callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            log.warning("MQTT-Verbindung fehlgeschlagen: %s", reason_code)
            return
        log.info("MQTT verbunden mit %s:%s", self.host, self.port)
        client.subscribe([(f"{TOPIC_PREFIX}/+/event", 0), (f"{TOPIC_PREFIX}/+/status", 0)])

    def _on_message(self, client, userdata, msg) -> None:
        try:
            parts = msg.topic.split("/")
            if len(parts) != 3 or parts[0] != TOPIC_PREFIX:
                return
            sensor_id, kind = parts[1], parts[2]
            payload = self._parse(msg.payload)
            if kind == "event":
                self._handle_event(sensor_id, payload)
            elif kind == "status":
                self._handle_status(sensor_id, payload)
        except Exception:  # pragma: no cover - robust gegen Müll-Payloads
            log.exception("Fehler beim Verarbeiten von %s", msg.topic)

    @staticmethod
    def _parse(raw: bytes) -> dict:
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _handle_event(self, sensor_id: str, payload: dict) -> None:
        seq = payload.get("seq")
        raw_dir = payload.get("direction")
        if seq is None or raw_dir is None:
            log.warning("Event ohne seq/direction von %s: %s", sensor_id, payload)
            return
        direction = DIRECTION_MAP.get(raw_dir, raw_dir)
        if direction not in ("a2b", "b2a"):
            log.warning("Unbekannte Richtung %r von %s", raw_dir, sensor_id)
            return
        ts = float(payload.get("ts") or time.time())
        new = self.store.record_passage(sensor_id, int(seq), direction, ts)
        if not new:
            log.debug("Duplikat seq=%s von %s ignoriert", seq, sensor_id)

    def _handle_status(self, sensor_id: str, payload: dict) -> None:
        online = bool(payload.get("online", True))
        self.store.record_status(
            sensor_id,
            online=online,
            rssi=_as_int(payload.get("rssi")),
            baseline_mm=_as_int(payload.get("baseline_mm")),
            fw=payload.get("fw"),
        )

    # ------------------------------------------------------------- Publish
    def publish_command(self, sensor_id: str, cmd: str) -> None:
        topic = f"{TOPIC_PREFIX}/{sensor_id}/cmd"
        self._client.publish(topic, json.dumps({"cmd": cmd}), qos=1)
        log.info("Kommando %r an %s gesendet", cmd, sensor_id)


def _as_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

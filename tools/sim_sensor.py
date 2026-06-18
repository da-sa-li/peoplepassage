#!/usr/bin/env python3
"""MQTT-Sensor-Simulator für PeoplePassage.

Simuliert einen oder mehrere ESP32/VL53L1X-Türsensoren, ohne echte Hardware:
veröffentlicht Status-/Heartbeat-Nachrichten und zufällige Durchgänge (in/out)
auf den MQTT-Topics, die der Server erwartet.

Topics (siehe CLAUDE.md):
- Status: peoplepassage/<sensor_id>/status  {rssi, uptime, baseline_mm, fw, online}
- Event:  peoplepassage/<sensor_id>/event   {seq, direction:"in"|"out", ts}

Beispiele:
    # Standard: door1/door2/door3 gegen lokalen Broker, 1 Event/Sekunde
    python tools/sim_sensor.py --username peoplepassage --password changeme-mqtt

    # Gegen den Docker-Compose-Broker, 5 Sensoren, schneller
    python tools/sim_sensor.py --host localhost --sensors d1,d2,d3,d4,d5 --interval 0.3

    # Endliche Anzahl Events (z. B. für einen Smoke-Test)
    python tools/sim_sensor.py --count 20

Die Zugangsdaten entsprechen MQTT_USERNAME/MQTT_PASSWORD aus der `.env`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from typing import Any, Optional

import paho.mqtt.client as mqtt

TOPIC_PREFIX = "peoplepassage"


def event_payload(seq: int, direction: str, ts: Optional[float] = None) -> dict:
    """Baut ein Durchgangs-Event (direction = 'in' oder 'out')."""
    return {"seq": seq, "direction": direction, "ts": ts if ts is not None else time.time()}


def status_payload(
    rssi: int, baseline_mm: int, fw: str = "sim-1.0", online: bool = True, uptime: float = 0.0
) -> dict:
    """Baut eine Status-/Heartbeat-Nachricht."""
    return {
        "online": online,
        "rssi": rssi,
        "baseline_mm": baseline_mm,
        "fw": fw,
        "uptime": uptime,
    }


class Simulator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.sensors = [s.strip() for s in args.sensors.split(",") if s.strip()]
        if not self.sensors:
            raise SystemExit("Mindestens eine Sensor-ID erforderlich (--sensors).")
        # Eine einzige, optional geseedete RNG für reproduzierbare Läufe.
        self.rng = random.Random(args.seed)
        self._seq_start = int(time.time())
        self.seq = dict.fromkeys(self.sensors, self._seq_start)
        self.baseline = {s: self.rng.randint(2200, 2600) for s in self.sensors}
        self.start = time.time()
        self.running = True

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"pp-sim-{os.getpid()}"
        )
        if args.username:
            self.client.username_pw_set(args.username, args.password)
        self.client.on_connect = self._on_connect

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if reason_code != 0:
            print(f"[sim] Verbindung fehlgeschlagen: {reason_code}", file=sys.stderr)
            return
        print(f"[sim] verbunden mit {self.args.host}:{self.args.port}")
        for s in self.sensors:
            self._publish_status(s, online=True)

    def _publish_status(self, sensor_id: str, online: bool) -> Any:
        payload = status_payload(
            rssi=self.rng.randint(-80, -45),
            baseline_mm=self.baseline[sensor_id],
            online=online,
            uptime=time.time() - self.start,
        )
        # retained + qos=1, damit der Server den letzten Status zuverlässig kennt
        return self.client.publish(
            f"{TOPIC_PREFIX}/{sensor_id}/status", json.dumps(payload), qos=1, retain=True
        )

    def _publish_event(self, sensor_id: str, direction: str) -> None:
        self.seq[sensor_id] += 1
        payload = event_payload(self.seq[sensor_id], direction)
        self.client.publish(f"{TOPIC_PREFIX}/{sensor_id}/event", json.dumps(payload), qos=1)
        print(f"[sim] {sensor_id} #{self.seq[sensor_id]} {direction}")

    def run(self) -> None:
        self.client.connect(self.args.host, self.args.port, keepalive=60)
        self.client.loop_start()

        emitted = 0
        last_status = time.time()
        try:
            while self.running:
                sensor_id = self.rng.choice(self.sensors)
                direction = self.rng.choice(["in", "out"])
                self._publish_event(sensor_id, direction)
                emitted += 1
                if self.args.count and emitted >= self.args.count:
                    break
                # gelegentlich Heartbeat erneuern
                if time.time() - last_status > 20:
                    for s in self.sensors:
                        self._publish_status(s, online=True)
                    last_status = time.time()
                time.sleep(self.args.interval)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        # Sensoren als offline markieren (entspricht dem LWT der echten Firmware)
        # und auf die Zustellung warten, bevor die Verbindung geschlossen wird.
        infos = [self._publish_status(s, online=False) for s in self.sensors]
        for info in infos:
            try:
                info.wait_for_publish(timeout=2)
            except Exception:
                pass
        self.client.loop_stop()
        self.client.disconnect()
        sent = sum(self.seq.values()) - self._seq_start * len(self.sensors)
        print(f"[sim] beendet ({sent} Events gesendet)")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PeoplePassage MQTT-Sensor-Simulator")
    p.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--username", default=os.environ.get("MQTT_USERNAME", ""))
    p.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
    p.add_argument("--sensors", default="door1,door2,door3", help="Komma-getrennte Sensor-IDs")
    p.add_argument("--interval", type=float, default=1.0, help="Sekunden zwischen Events")
    p.add_argument("--count", type=int, default=0, help="Anzahl Events (0 = unendlich)")
    p.add_argument("--seed", type=int, default=None, help="Zufalls-Seed für Reproduzierbarkeit")
    return p.parse_args(argv)


def main() -> None:
    sim = Simulator(parse_args())
    signal.signal(signal.SIGINT, lambda *_: setattr(sim, "running", False))
    sim.run()


if __name__ == "__main__":
    main()

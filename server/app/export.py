"""Minutengenauer CSV-Export der Belegungs-Chronik.

Spalten (Long-Format, eine Zeile pro Minute × Zone):
    minute_utc, zone_id, zone_name, ins, outs, net, occupancy_end

- `ins`/`outs` zählen Durchgänge, die die Zone in dieser Minute erhöhen bzw. senken.
- `net` = ins - outs.
- `occupancy_end` = Belegung am Ende der Minute (inkl. manueller Korrekturen/Nullungen).

Nur Standardbibliothek (sqlite3 über den Store, csv, datetime).
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone

from .db import Store, _delta_for


def _minute_index(ts: float) -> int:
    return int(ts // 60)


def _minute_iso(minute_index: int) -> str:
    dt = datetime.fromtimestamp(minute_index * 60, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:00Z")


def build_csv(store: Store, ts_from: float, ts_to: float) -> str:
    """CSV-String für den Zeitraum [ts_from, ts_to) erzeugen."""
    with store.lock:
        conn = store.conn
        zones = [
            (r["id"], r["name"])
            for r in conn.execute("SELECT id, name FROM zones ORDER BY name")
        ]
        zone_ids = [z[0] for z in zones]
        zone_names = {z[0]: z[1] for z in zones}

        # Belegung pro Zone strikt VOR ts_from (Ausgangsbasis).
        baseline = {zid: 0 for zid in zone_ids}
        for row in conn.execute(
            "SELECT p.direction, s.side_a_zone_id AS za, s.side_b_zone_id AS zb "
            "FROM passages p JOIN sensors s ON s.id = p.sensor_id WHERE p.ts_utc < ?",
            (ts_from,),
        ):
            da, db = _delta_for(row["direction"])
            if row["za"] in baseline:
                baseline[row["za"]] += da
            if row["zb"] in baseline:
                baseline[row["zb"]] += db
        for row in conn.execute(
            "SELECT zone_id, COALESCE(SUM(delta),0) AS d FROM adjustments WHERE ts_utc < ? "
            "GROUP BY zone_id",
            (ts_from,),
        ):
            if row["zone_id"] in baseline:
                baseline[row["zone_id"]] += row["d"]

        # Durchgänge im Zeitraum → (minute, zone) -> {ins, outs}
        ins: dict[tuple[int, int], int] = {}
        outs: dict[tuple[int, int], int] = {}
        for row in conn.execute(
            "SELECT p.ts_utc AS ts, p.direction, s.side_a_zone_id AS za, "
            "s.side_b_zone_id AS zb FROM passages p JOIN sensors s ON s.id = p.sensor_id "
            "WHERE p.ts_utc >= ? AND p.ts_utc < ?",
            (ts_from, ts_to),
        ):
            m = _minute_index(row["ts"])
            da, db = _delta_for(row["direction"])
            for zid, delta in ((row["za"], da), (row["zb"], db)):
                if zid not in baseline:
                    continue
                key = (m, zid)
                if delta > 0:
                    ins[key] = ins.get(key, 0) + 1
                else:
                    outs[key] = outs.get(key, 0) + 1

        # Manuelle Korrekturen im Zeitraum → (minute, zone) -> delta
        adj: dict[tuple[int, int], int] = {}
        for row in conn.execute(
            "SELECT ts_utc AS ts, zone_id, delta FROM adjustments "
            "WHERE ts_utc >= ? AND ts_utc < ?",
            (ts_from, ts_to),
        ):
            if row["zone_id"] in baseline:
                key = (_minute_index(row["ts"]), row["zone_id"])
                adj[key] = adj.get(key, 0) + row["delta"]

    # Zeilen erzeugen: jede Minute × jede Zone, laufende Belegung mitführen.
    m_start = _minute_index(ts_from)
    m_end = int(math.ceil(ts_to / 60))  # exklusiv
    running = dict(baseline)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["minute_utc", "zone_id", "zone_name", "ins", "outs", "net", "occupancy_end"]
    )
    for m in range(m_start, m_end):
        iso = _minute_iso(m)
        for zid in zone_ids:
            i = ins.get((m, zid), 0)
            o = outs.get((m, zid), 0)
            net = i - o
            running[zid] += net + adj.get((m, zid), 0)
            writer.writerow([iso, zid, zone_names[zid], i, o, net, running[zid]])

    return out.getvalue()

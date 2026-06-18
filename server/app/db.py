"""Datenschicht + Belegungslogik für PeoplePassage.

Bewusst nur mit der Standardbibliothek (sqlite3) umgesetzt, damit die Kernlogik
(Belegung, Nullung, Rekonstruktion) ohne FastAPI/MQTT testbar ist.

Kernkonzept „Türen als Kanten zwischen Zonen":
- Ein Sensor verbindet zwei Seiten `side_a`/`side_b`, die je auf eine Zone (oder
  Außen = NULL) zeigen.
- Durchgang `a2b`  → Zone B +1, Zone A -1
- Durchgang `b2a`  → Zone A +1, Zone B -1

Belegung einer Zone = Σ(Durchgangs-Deltas) + Σ(manuelle Korrekturen/Nullungen).
Die Belegung wird zusätzlich als In-Memory-Cache (`self._occ`) gehalten und beim
Start aus der DB rekonstruiert.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    capacity    INTEGER,
    created_at  REAL NOT NULL,
    is_external INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sensors (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    side_a_zone_id INTEGER REFERENCES zones(id) ON DELETE SET NULL,
    side_b_zone_id INTEGER REFERENCES zones(id) ON DELETE SET NULL,
    baseline_mm    INTEGER,
    last_seen      REAL,
    online         INTEGER NOT NULL DEFAULT 0,
    rssi           INTEGER,
    fw             TEXT
);

CREATE TABLE IF NOT EXISTS passages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id TEXT NOT NULL REFERENCES sensors(id) ON DELETE CASCADE,
    ts_utc    REAL NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('a2b', 'b2a')),
    seq       INTEGER NOT NULL,
    UNIQUE (sensor_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_passages_ts ON passages(ts_utc);

CREATE TABLE IF NOT EXISTS adjustments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    ts_utc  REAL NOT NULL,
    delta   INTEGER NOT NULL,
    reason  TEXT,
    actor   TEXT
);
CREATE INDEX IF NOT EXISTS idx_adjustments_ts ON adjustments(ts_utc);
"""


def _safe_put(queue: Any, payload: dict) -> None:
    """Snapshot in eine SSE-Queue legen; bei vollem Puffer (langsamer Client)
    den Snapshot verwerfen, statt im Event-Loop eine Exception zu werfen."""
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        pass


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Spalte zu einer bestehenden Tabelle hinzufügen, falls sie fehlt (einfache
    Inline-Migration; `CREATE TABLE IF NOT EXISTS` ergänzt keine Spalten bei
    bereits existierenden Tabellen aus älteren Server-Versionen)."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _delta_for(direction: str) -> tuple[int, int]:
    """Liefert (delta_side_a, delta_side_b) für eine Durchgangsrichtung."""
    if direction == "a2b":
        return (-1, +1)
    return (+1, -1)  # b2a


class Store:
    """Thread-sichere SQLite-Datenschicht inkl. Belegungs-Cache und SSE-Broadcast."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        _ensure_column(self._conn, "zones", "is_external", "INTEGER NOT NULL DEFAULT 0")
        # Erst nach der Spalten-Migration anlegen, da der Index `is_external` referenziert.
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_zones_single_external "
            "ON zones(is_external) WHERE is_external = 1"
        )
        self._conn.commit()

        self._lock = threading.RLock()
        self._occ: dict[int, int] = {}

        # Von außen gesetzt (main.py): Event-Loop für SSE und MQTT-Command-Publisher.
        self._loop: Any = None
        self._subscribers: set[Any] = set()
        self.command_publisher: Optional[Callable[[str, str], None]] = None

        # pretix-Checkin-Integration (optional, s. CLAUDE.md): zuletzt bekannter
        # Gesamtwert "anwesende Personen laut Ticketkontrolle" für die externe Zone.
        self._pretix_total: Optional[int] = None
        self._pretix_updated_at: Optional[float] = None
        self._pretix_error: Optional[str] = None

        self.recompute_occupancy()

    # ----------------------------------------------------------------- Helpers
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # --------------------------------------------------------- Belegungs-Cache
    def recompute_occupancy(self) -> None:
        """Belegung vollständig aus der DB neu berechnen (z. B. nach Re-Mapping)."""
        with self._lock:
            occ: dict[int, int] = {}
            for row in self._conn.execute("SELECT id FROM zones"):
                occ[row["id"]] = 0

            for row in self._conn.execute(
                "SELECT p.direction, s.side_a_zone_id AS za, s.side_b_zone_id AS zb "
                "FROM passages p JOIN sensors s ON s.id = p.sensor_id"
            ):
                da, db = _delta_for(row["direction"])
                if row["za"] is not None and row["za"] in occ:
                    occ[row["za"]] += da
                if row["zb"] is not None and row["zb"] in occ:
                    occ[row["zb"]] += db

            for row in self._conn.execute(
                "SELECT zone_id, COALESCE(SUM(delta), 0) AS d FROM adjustments GROUP BY zone_id"
            ):
                if row["zone_id"] in occ:
                    occ[row["zone_id"]] += row["d"]

            self._occ = occ

    def occupancy(self, zone_id: int) -> int:
        return self._occ.get(zone_id, 0)

    def _external_occupancy(self, zone_id: int) -> Optional[int]:
        """Belegung der externen (pretix-)Zone: Gesamtzahl laut Ticketkontrolle minus
        Summe aller anderen Zonen. `None`, solange noch kein pretix-Wert vorliegt."""
        if self._pretix_total is None:
            return None
        tracked = sum(v for k, v in self._occ.items() if k != zone_id)
        return self._pretix_total - tracked

    def _zone_row(self, r: sqlite3.Row) -> dict:
        is_external = bool(r["is_external"])
        occupancy = self._external_occupancy(r["id"]) if is_external else self._occ.get(r["id"], 0)
        return {
            "id": r["id"],
            "name": r["name"],
            "capacity": r["capacity"],
            "occupancy": occupancy,
            "is_external": is_external,
        }

    # ------------------------------------------------------------------- Zonen
    def list_zones(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, capacity, is_external FROM zones ORDER BY name"
            ).fetchall()
            return [self._zone_row(r) for r in rows]

    def get_zone(self, zone_id: int) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute(
                "SELECT id, name, capacity, is_external FROM zones WHERE id = ?", (zone_id,)
            ).fetchone()
            if r is None:
                return None
            return self._zone_row(r)

    def create_zone(self, name: str, capacity: Optional[int]) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO zones (name, capacity, created_at) VALUES (?, ?, ?)",
                (name, capacity, time.time()),
            )
            self._conn.commit()
            zid = int(cur.lastrowid)
            self._occ[zid] = 0
        self._notify()
        return self.get_zone(zid)  # type: ignore[return-value]

    def ensure_external_zone(self, default_name: str) -> dict:
        """Externe (pretix-)Zone idempotent sicherstellen: vorhandene zurückgeben,
        sonst neu anlegen. Höchstens eine externe Zone wird zusätzlich per partiellem
        UNIQUE-Index (`ux_zones_single_external`) auf DB-Ebene erzwungen; `INSERT OR
        IGNORE` + Re-Select macht das robust gegen eine Race auf die Invariante."""
        with self._lock:
            r = self._conn.execute(
                "SELECT id, name, capacity, is_external FROM zones WHERE is_external = 1 LIMIT 1"
            ).fetchone()
            if r is None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO zones (name, capacity, created_at, is_external) "
                    "VALUES (?, NULL, ?, 1)",
                    (default_name, time.time()),
                )
                self._conn.commit()
                r = self._conn.execute(
                    "SELECT id, name, capacity, is_external FROM zones WHERE is_external = 1 LIMIT 1"
                ).fetchone()
            self._occ.setdefault(r["id"], 0)
        self._notify()
        return self._zone_row(r)

    def update_zone(
        self, zone_id: int, name: Optional[str], capacity: Optional[int], set_capacity: bool
    ) -> Optional[dict]:
        with self._lock:
            if self.get_zone(zone_id) is None:
                return None
            if name is not None:
                self._conn.execute("UPDATE zones SET name = ? WHERE id = ?", (name, zone_id))
            if set_capacity:
                self._conn.execute(
                    "UPDATE zones SET capacity = ? WHERE id = ?", (capacity, zone_id)
                )
            self._conn.commit()
        self._notify()
        return self.get_zone(zone_id)

    def delete_zone(self, zone_id: int) -> bool:
        with self._lock:
            if self.get_zone(zone_id) is None:
                return False
            # ON DELETE SET NULL löst Sensor-Seiten, CASCADE entfernt adjustments.
            self._conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
            self._conn.commit()
            self.recompute_occupancy()
        self._notify()
        return True

    def reset_zone(
        self, zone_id: int, reason: Optional[str] = None, actor: Optional[str] = None
    ) -> Optional[dict]:
        """Belegung auf 0 setzen — als auditierbare Korrektur-Buchung."""
        with self._lock:
            if self.get_zone(zone_id) is None:
                return None
            current = self._occ.get(zone_id, 0)
            if current != 0:
                self._conn.execute(
                    "INSERT INTO adjustments (zone_id, ts_utc, delta, reason, actor) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (zone_id, time.time(), -current, reason or "reset", actor),
                )
                self._conn.commit()
                self._occ[zone_id] = 0
        self._notify()
        return self.get_zone(zone_id)

    # ----------------------------------------------------------------- Sensoren
    def list_sensors(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, side_a_zone_id, side_b_zone_id, baseline_mm, "
                "last_seen, online, rssi, fw FROM sensors ORDER BY id"
            ).fetchall()
            return [self._sensor_row(r) for r in rows]

    def get_sensor(self, sensor_id: str) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute(
                "SELECT id, name, side_a_zone_id, side_b_zone_id, baseline_mm, "
                "last_seen, online, rssi, fw FROM sensors WHERE id = ?",
                (sensor_id,),
            ).fetchone()
            return self._sensor_row(r) if r else None

    @staticmethod
    def _sensor_row(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "name": r["name"],
            "side_a_zone_id": r["side_a_zone_id"],
            "side_b_zone_id": r["side_b_zone_id"],
            "baseline_mm": r["baseline_mm"],
            "last_seen": r["last_seen"],
            "online": bool(r["online"]),
            "rssi": r["rssi"],
            "fw": r["fw"],
        }

    def _ensure_sensor(self, sensor_id: str) -> None:
        """Unbekannten Sensor automatisch registrieren (Sides = NULL)."""
        self._conn.execute(
            "INSERT INTO sensors (id, online) VALUES (?, 0) ON CONFLICT(id) DO NOTHING",
            (sensor_id,),
        )

    def update_sensor(
        self,
        sensor_id: str,
        *,
        name: Optional[str] = None,
        set_name: bool = False,
        side_a_zone_id: Optional[int] = None,
        set_side_a: bool = False,
        side_b_zone_id: Optional[int] = None,
        set_side_b: bool = False,
    ) -> Optional[dict]:
        with self._lock:
            if self.get_sensor(sensor_id) is None:
                return None
            if set_name:
                self._conn.execute(
                    "UPDATE sensors SET name = ? WHERE id = ?", (name, sensor_id)
                )
            if set_side_a:
                self._conn.execute(
                    "UPDATE sensors SET side_a_zone_id = ? WHERE id = ?",
                    (side_a_zone_id, sensor_id),
                )
            if set_side_b:
                self._conn.execute(
                    "UPDATE sensors SET side_b_zone_id = ? WHERE id = ?",
                    (side_b_zone_id, sensor_id),
                )
            self._conn.commit()
            if set_side_a or set_side_b:
                # Seitenzuordnung verändert die Topologie → Belegung neu berechnen.
                self.recompute_occupancy()
        self._notify()
        return self.get_sensor(sensor_id)

    def delete_sensor(self, sensor_id: str) -> bool:
        with self._lock:
            if self.get_sensor(sensor_id) is None:
                return False
            self._conn.execute("DELETE FROM sensors WHERE id = ?", (sensor_id,))
            self._conn.commit()
            self.recompute_occupancy()
        self._notify()
        return True

    # ------------------------------------------------------- Ingest (von MQTT)
    def record_passage(self, sensor_id: str, seq: int, direction: str, ts: float) -> bool:
        """Durchgang verbuchen. Liefert False bei Duplikat (gleiche (sensor_id, seq))."""
        with self._lock:
            self._ensure_sensor(sensor_id)
            try:
                self._conn.execute(
                    "INSERT INTO passages (sensor_id, ts_utc, direction, seq) "
                    "VALUES (?, ?, ?, ?)",
                    (sensor_id, ts, direction, seq),
                )
            except sqlite3.IntegrityError as exc:
                # Nur die UNIQUE(sensor_id, seq)-Verletzung ist ein idempotentes
                # Duplikat; andere Constraint-Fehler (z. B. CHECK direction) melden.
                if "UNIQUE constraint failed" in str(exc):
                    return False
                raise
            self._conn.execute(
                "UPDATE sensors SET online = 1, last_seen = ? WHERE id = ?",
                (ts, sensor_id),
            )
            self._conn.commit()

            r = self._conn.execute(
                "SELECT side_a_zone_id AS za, side_b_zone_id AS zb FROM sensors WHERE id = ?",
                (sensor_id,),
            ).fetchone()
            da, db = _delta_for(direction)
            if r["za"] is not None:
                self._occ[r["za"]] = self._occ.get(r["za"], 0) + da
            if r["zb"] is not None:
                self._occ[r["zb"]] = self._occ.get(r["zb"], 0) + db
        self._notify()
        return True

    def record_status(
        self,
        sensor_id: str,
        *,
        online: bool = True,
        rssi: Optional[int] = None,
        baseline_mm: Optional[int] = None,
        fw: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> None:
        ts = ts if ts is not None else time.time()
        with self._lock:
            self._ensure_sensor(sensor_id)
            self._conn.execute(
                "UPDATE sensors SET online = ?, last_seen = ?, "
                "rssi = COALESCE(?, rssi), baseline_mm = COALESCE(?, baseline_mm), "
                "fw = COALESCE(?, fw) WHERE id = ?",
                (1 if online else 0, ts, rssi, baseline_mm, fw, sensor_id),
            )
            self._conn.commit()
        self._notify()

    def set_baseline(self, sensor_id: str, baseline_mm: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sensors SET baseline_mm = ? WHERE id = ?", (baseline_mm, sensor_id)
            )
            self._conn.commit()
        self._notify()

    def mark_stale_offline(self, timeout_s: float) -> bool:
        """Sensoren ohne Lebenszeichen seit `timeout_s` als offline markieren."""
        cutoff = time.time() - timeout_s
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sensors SET online = 0 "
                "WHERE online = 1 AND (last_seen IS NULL OR last_seen < ?)",
                (cutoff,),
            )
            self._conn.commit()
            changed = cur.rowcount > 0
        if changed:
            self._notify()
        return changed

    # --------------------------------------------------- pretix-Checkin-Integration
    def set_pretix_total(self, count: int) -> None:
        with self._lock:
            self._pretix_total = count
            self._pretix_updated_at = time.time()
            self._pretix_error = None
        self._notify()

    def set_pretix_error(self, message: str) -> None:
        """Letzten Fehler vermerken, ohne den letzten guten Wert zu verwerfen."""
        with self._lock:
            self._pretix_error = message
        self._notify()

    def pretix_sync_status(self) -> Optional[dict]:
        with self._lock:
            if self._pretix_total is None and self._pretix_error is None:
                return None
            tracked_sum = sum(self._occ.values())
            remainder = (
                self._pretix_total - tracked_sum if self._pretix_total is not None else None
            )
            return {
                "total": self._pretix_total,
                "tracked_sum": tracked_sum,
                "remainder": remainder,
                "updated_at": self._pretix_updated_at,
                "error": self._pretix_error,
            }

    # ----------------------------------------------------------- Kommandos
    def send_command(self, sensor_id: str, cmd: str) -> None:
        if self.command_publisher is None:
            raise RuntimeError("MQTT nicht verbunden – Kommando kann nicht gesendet werden.")
        self.command_publisher(sensor_id, cmd)

    # ----------------------------------------------------------- SSE / Snapshot
    def set_loop(self, loop: Any) -> None:
        self._loop = loop

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "ts": time.time(),
            "zones": self.list_zones(),
            "sensors": self.list_sensors(),
            "pretix_sync": self.pretix_sync_status(),
        }

    def add_subscriber(self, queue: Any) -> None:
        with self._lock:
            self._subscribers.add(queue)

    def remove_subscriber(self, queue: Any) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def _notify(self) -> None:
        """Aktuellen Snapshot an alle SSE-Abonnenten pushen (thread-sicher)."""
        with self._lock:
            loop = self._loop
            subscribers = list(self._subscribers)
        if loop is None or not subscribers:
            return
        snap = self.snapshot()
        for q in subscribers:
            try:
                loop.call_soon_threadsafe(_safe_put, q, snap)
            except RuntimeError:
                pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

"""
Persistencia SQLite para REBA.

Tablas:
  - captures: cada foto recibida del ESP32 (JPEG anotado como BLOB)
  - detections: cada objeto detectado (clase, confianza, bbox, timestamp)
  - pump_events: ciclos de bomba inferidos (cada N fotos subidas)
  - counters: acumulado por clase (actualizado atómicamente)

Uso:
    from server.database import Database
    db = Database(settings.database_path_abs)
    db.connect()
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Capa delgada sobre sqlite3 (thread-safe vía check_same_thread=False + locks implícitos del GIL en escrituras cortas)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                class_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                x1 REAL, y1 REAL, x2 REAL, y2 REAL,
                source TEXT NOT NULL DEFAULT 'onnx',
                capture_id INTEGER REFERENCES captures(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                image_jpeg BLOB NOT NULL,
                source TEXT NOT NULL DEFAULT 'onnx',
                inference_ms REAL NOT NULL,
                detection_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                photos_trigger INTEGER NOT NULL,
                detail TEXT
            );

            CREATE TABLE IF NOT EXISTS counters (
                class_name TEXT PRIMARY KEY,
                total INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS device_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_detections_ts ON detections(ts);
            CREATE INDEX IF NOT EXISTS idx_detections_class ON detections(class_name);
            """
        )
        detection_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(detections)")
        }
        if "capture_id" not in detection_columns:
            self.conn.execute(
                "ALTER TABLE detections ADD COLUMN capture_id INTEGER REFERENCES captures(id) ON DELETE SET NULL"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_detections_capture ON detections(capture_id)"
        )
        # JPEG original del ESP32 (sin cajas dibujadas) — lo usa la exportación
        # YOLO para reentrenar; el dashboard sigue mostrando el anotado.
        capture_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(captures)")
        }
        if "image_orig_jpeg" not in capture_columns:
            self.conn.execute(
                "ALTER TABLE captures ADD COLUMN image_orig_jpeg BLOB"
            )
        self.conn.commit()

    def ensure_classes(self, class_names: List[str]) -> None:
        """Inicializa filas de contador para cada clase si no existen."""
        for name in class_names:
            self.conn.execute(
                "INSERT OR IGNORE INTO counters(class_name, total) VALUES (?, 0)",
                (name,),
            )
        self.conn.commit()

    def insert_detections(
        self,
        detections: List[Dict[str, Any]],
        source: str = "onnx",
    ) -> None:
        """
        Inserta detecciones y actualiza contadores acumulados.

        Cada dict espera: class_name, confidence, x1, y1, x2, y2
        """
        if not detections:
            return
        ts = _utc_now()
        cur = self.conn.cursor()
        for det in detections:
            cur.execute(
                """
                INSERT INTO detections(ts, class_name, confidence, x1, y1, x2, y2, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    det["class_name"],
                    float(det["confidence"]),
                    float(det.get("x1", 0)),
                    float(det.get("y1", 0)),
                    float(det.get("x2", 0)),
                    float(det.get("y2", 0)),
                    source,
                ),
            )
            cur.execute(
                """
                INSERT INTO counters(class_name, total) VALUES (?, 1)
                ON CONFLICT(class_name) DO UPDATE SET total = total + 1
                """,
                (det["class_name"],),
            )
        self.conn.commit()

    def insert_capture(
        self,
        image_jpeg: bytes,
        detections: List[Dict[str, Any]],
        inference_ms: float,
        source: str = "onnx",
        image_orig_jpeg: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Guarda una foto y sus detecciones en una sola transacción SQLite.

        `detections` puede estar vacía: la ESP32 dispara la foto por el
        ultrasónico, no por YOLO, así que una foto sin clase sigue siendo
        evidencia útil (y material para reentrenar el dataset).
        """
        if not image_jpeg:
            raise ValueError("La captura JPEG está vacía")

        ts = _utc_now()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO captures(ts, image_jpeg, image_orig_jpeg, source, inference_ms, detection_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, sqlite3.Binary(image_jpeg),
                 sqlite3.Binary(image_orig_jpeg) if image_orig_jpeg else None,
                 source, float(inference_ms), len(detections)),
            )
            capture_id = int(cur.lastrowid)
            for det in detections:
                self.conn.execute(
                    """
                    INSERT INTO detections(
                        ts, class_name, confidence, x1, y1, x2, y2, source, capture_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        det["class_name"],
                        float(det["confidence"]),
                        float(det.get("x1", 0)),
                        float(det.get("y1", 0)),
                        float(det.get("x2", 0)),
                        float(det.get("y2", 0)),
                        source,
                        capture_id,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO counters(class_name, total) VALUES (?, 1)
                    ON CONFLICT(class_name) DO UPDATE SET total = total + 1
                    """,
                    (det["class_name"],),
                )

        return {
            "id": capture_id,
            "ts": ts,
            "source": source,
            "inference_ms": round(float(inference_ms), 2),
            "detection_count": len(detections),
            "class_names": list(dict.fromkeys(d["class_name"] for d in detections)),
        }

    def get_captures(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT c.id, c.ts, c.source, c.inference_ms, c.detection_count,
                   GROUP_CONCAT(DISTINCT d.class_name) AS class_names
            FROM captures AS c
            LEFT JOIN detections AS d ON d.capture_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [
            {
                **dict(row),
                "class_names": row["class_names"].split(",") if row["class_names"] else [],
            }
            for row in rows
        ]

    def get_latest_capture(self) -> Optional[Dict[str, Any]]:
        captures = self.get_captures(limit=1)
        return captures[0] if captures else None

    def get_capture_image(self, capture_id: int) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT image_jpeg FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        return bytes(row["image_jpeg"]) if row else None

    def get_capture_image_orig(self, capture_id: int) -> Optional[bytes]:
        """JPEG original sin anotaciones; NULL en capturas antiguas."""
        row = self.conn.execute(
            "SELECT image_orig_jpeg FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        if not row or row["image_orig_jpeg"] is None:
            return None
        return bytes(row["image_orig_jpeg"])

    def get_capture_detections(self, capture_id: int) -> List[Dict[str, Any]]:
        """Detecciones de una captura (para exportar etiquetas YOLO)."""
        rows = self.conn.execute(
            """
            SELECT class_name, confidence, x1, y1, x2, y2
            FROM detections WHERE capture_id = ? ORDER BY confidence DESC
            """,
            (capture_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_captures_for_export(
        self, limit: int = 500, min_confidence: float = 0.0
    ) -> List[Dict[str, Any]]:
        """Capturas con su JPEG original (o anotado si no hay orig) para export."""
        rows = self.conn.execute(
            """
            SELECT c.id, c.ts, COALESCE(c.image_orig_jpeg, c.image_jpeg) AS img
            FROM captures c ORDER BY c.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            dets = self.get_capture_detections(r["id"])
            dets = [d for d in dets if d["confidence"] >= min_confidence]
            out.append({"id": r["id"], "ts": r["ts"], "image": bytes(r["img"]),
                        "detections": dets})
        return out

    def get_stats_timeseries(self, days: int = 14) -> List[Dict[str, Any]]:
        """
        Detecciones por día y clase + capturas/eventos, últimos `days` días.
        Devuelve una fila por (día, clase) más totales por día.
        """
        days = max(1, min(days, 365))
        det_rows = self.conn.execute(
            """
            SELECT substr(ts, 1, 10) AS day, class_name, COUNT(*) AS n
            FROM detections
            WHERE ts >= date('now', ?)
            GROUP BY day, class_name ORDER BY day
            """,
            (f"-{days} days",),
        ).fetchall()
        cap_rows = self.conn.execute(
            """
            SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n
            FROM captures WHERE ts >= date('now', ?) GROUP BY day
            """,
            (f"-{days} days",),
        ).fetchall()
        pump_rows = self.conn.execute(
            """
            SELECT substr(ts, 1, 10) AS day, COUNT(*) AS n
            FROM pump_events WHERE ts >= date('now', ?) GROUP BY day
            """,
            (f"-{days} days",),
        ).fetchall()

        by_day: Dict[str, Dict[str, Any]] = {}
        for r in det_rows:
            d = by_day.setdefault(r["day"], {"detections": {}, "captures": 0,
                                              "pump_events": 0})
            d["detections"][r["class_name"]] = r["n"]
        for r in cap_rows:
            d = by_day.setdefault(r["day"], {"detections": {}, "captures": 0,
                                              "pump_events": 0})
            d["captures"] = r["n"]
        for r in pump_rows:
            d = by_day.setdefault(r["day"], {"detections": {}, "captures": 0,
                                              "pump_events": 0})
            d["pump_events"] = r["n"]
        return [
            {"date": day, **vals}
            for day, vals in sorted(by_day.items())
        ]

    def count_captures(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM captures").fetchone()
        return int(row["total"])

    def capture_exists(self, capture_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        return row is not None

    def prune_captures(self, keep: int) -> int:
        """
        Borra las capturas más antiguas dejando solo las `keep` más recientes.

        Las detecciones ligadas quedan con capture_id=NULL (ON DELETE SET NULL)
        — la evidencia de clasificación se conserva aunque la foto se purgue.
        Devuelve cuántas filas se eliminaron.
        """
        if keep <= 0:
            return 0
        cur = self.conn.execute(
            """
            DELETE FROM captures
            WHERE id NOT IN (SELECT id FROM captures ORDER BY id DESC LIMIT ?)
            """,
            (int(keep),),
        )
        self.conn.commit()
        return int(cur.rowcount) if cur.rowcount and cur.rowcount > 0 else 0

    def insert_pump_event(
        self,
        photos_trigger: int,
        detail: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Registra un ciclo de bomba inferido (cada N fotos recibidas)."""
        ts = _utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO pump_events(ts, photos_trigger, detail)
            VALUES (?, ?, ?)
            """,
            (ts, int(photos_trigger), detail),
        )
        self.conn.commit()
        return {"id": int(cur.lastrowid), "ts": ts, "photos_trigger": int(photos_trigger)}

    def get_pump_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, ts, photos_trigger, detail
            FROM pump_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_pump_event(self) -> Optional[Dict[str, Any]]:
        events = self.get_pump_events(limit=1)
        return events[0] if events else None

    def get_counters(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT class_name, total FROM counters ORDER BY class_name"
        ).fetchall()
        return {r["class_name"]: int(r["total"]) for r in rows}

    def get_stats(self) -> Dict[str, Any]:
        counters = self.get_counters()
        total = sum(counters.values())
        percentages: Dict[str, float] = {}
        for name, count in counters.items():
            percentages[name] = (count / total * 100.0) if total else 0.0
        return {
            "counters": counters,
            "percentages": percentages,
            "total_objects": total,
        }

    def get_detection_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, ts, class_name, confidence, x1, y1, x2, y2, source, capture_id
            FROM detections ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- device config

    # Claves que el firmware entiende (ver fotos_nc.ino → /api/device/config)
    DEVICE_CONFIG_KEYS = (
        "wifi_ssid", "wifi_pass", "server_url",
        "pump_photos", "pump_seconds",
        "dist_min_cm", "dist_max_cm",
        # Comando de un solo uso hacia la ESP32: "<unix_ts>:<segundos>".
        # Lo crea POST /api/pump/run; el firmware lo ejecuta una vez y hace
        # ack por POST /api/device/ack. TTL de 10 min al servirse en config.
        "pump_cmd",
    )

    def get_device_config(self) -> Dict[str, str]:
        """Overrides de configuración para el ESP32 (solo claves conocidas)."""
        rows = self.conn.execute(
            "SELECT key, value FROM device_config"
        ).fetchall()
        allowed = set(self.DEVICE_CONFIG_KEYS)
        return {r["key"]: r["value"] for r in rows if r["key"] in allowed}

    def set_device_config(self, key: str, value: str) -> None:
        if key not in self.DEVICE_CONFIG_KEYS:
            raise ValueError(f"Clave no permitida: {key}")
        self.conn.execute(
            "INSERT INTO device_config(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def delete_device_config(self, key: str) -> None:
        self.conn.execute("DELETE FROM device_config WHERE key = ?", (key,))
        self.conn.commit()

    def export_json_snapshot(self) -> str:
        return json.dumps(
            {
                "stats": self.get_stats(),
                "recent_captures": self.get_captures(10),
                "recent_detections": self.get_detection_history(20),
                "recent_pump_events": self.get_pump_events(10),
            },
            indent=2,
        )

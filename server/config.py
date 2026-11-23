"""
Configuración central del servidor REBA.

Carga variables desde `.env` (ver `.env.example`). El servidor opera en modo
"push": la ESP32-CAM sube fotos por POST /upload, por lo que aquí solo viven
parámetros del servidor HTTP, del modelo ONNX y valores informativos que deben
ir espejados con las constantes del firmware (fotos_nc.ino).

Uso:
    from server.config import settings
    print(settings.pump_trigger_photos)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto REBA/ (padre de server/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Parámetros de runtime. Modifica `.env` para cambiar comportamiento."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Servidor HTTP ---
    # El puerto debe coincidir con SERVIDOR_URL del firmware ESP32.
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=5000)

    # --- Inferencia ---
    model_path: str = Field(default="models/best.onnx")
    inference_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    inference_iou: float = Field(default=0.45, ge=0.0, le=1.0)
    # Clasificador híbrido (YOLOv8-cls, 1 objeto por foto): decide la clase;
    # el detector solo aporta la caja. Vacío o archivo ausente = solo detector.
    cls_model_path: str = Field(default="models/cls_esp32.onnx")
    # Probabilidad mínima del top-1 para aceptar la clase (si no, sin detección).
    cls_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Clases YOLO (orden = class_id) ---
    class_names: str = Field(default="plastico,unicel,metal")

    # --- Bomba (informativo: espejar constantes del firmware) ---
    # Deben coincidir con FOTOS_PARA_BOMBA y TIEMPO_BOMBA_MS de fotos_nc.ino.
    # El conteo real ocurre en la ESP32; el servidor solo lo infiere por uploads.
    pump_trigger_photos: int = Field(default=3, ge=1)
    pump_run_seconds: float = Field(default=10.0, ge=0)

    # --- Rango ultrasónico del firmware (cm, informativo) ---
    detect_min_cm: float = Field(default=0.0)
    detect_max_cm: float = Field(default=23.0)

    # --- Persistencia ---
    database_path: str = Field(default="data/REBA.db")
    # Máximo de capturas JPEG conservadas en SQLite (0 = sin límite).
    # Las fotos son blobs ~5 KB; 2000 ≈ 10 MB. Evita crecimiento ilimitado
    # en operación continua de campo.
    capture_retention: int = Field(default=2000, ge=0)

    # --- Túnel zrok (informativo: lo usan los .bat / tools\zrok_tunnel.ps1) ---
    # Nombre reservado del share público (URL fija https://<nombre>.shares.zrok.io).
    # Vacío = share efímero. Se configura con setup_zrok.bat.
    zrok_reserved_name: str = Field(default="")
    # Objetivo local del túnel (uvicorn). La ESP32 NO usa el túnel: habla
    # HTTP plano directo con HOST:PORT en la LAN/hotspot.
    zrok_target_url: str = Field(default="http://127.0.0.1:5000")

    @property
    def class_list(self) -> List[str]:
        """Lista de nombres de clase en orden de class_id."""
        return [c.strip() for c in self.class_names.split(",") if c.strip()]

    @property
    def model_path_abs(self) -> Path:
        path = Path(self.model_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def cls_model_path_abs(self) -> Path | None:
        if not self.cls_model_path.strip():
            return None
        path = Path(self.cls_model_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def database_path_abs(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def static_dir(self) -> Path:
        return PROJECT_ROOT / "static"

    def public_dict(self) -> dict:
        """Config segura para exponer al dashboard."""
        return {
            "model_path": self.model_path,
            "model_exists": self.model_path_abs.is_file(),
            "inference_confidence": self.inference_confidence,
            "inference_iou": self.inference_iou,
            "class_names": self.class_list,
            "pump_trigger_photos": self.pump_trigger_photos,
            "pump_run_seconds": self.pump_run_seconds,
            "detect_min_cm": self.detect_min_cm,
            "detect_max_cm": self.detect_max_cm,
            "capture_retention": self.capture_retention,
        }


@lru_cache
def get_settings() -> Settings:
    """Singleton de settings (cacheado)."""
    return Settings()


settings = get_settings()

"""Monitor GUI del entrenamiento REBA (tkinter, solo lectura, Win).

UI en tema negro con dos columnas: izquierda = titulos, barras y textos
(progreso, hiperparametros, ETAs, telemetria); derecha = tablas de metricas
y estado de results.csv / hilos daemon. Una fila inferior muestra la
explicacion del estado actual del entrenamiento y un glosario de metricas
(definicion, formula, uso y estado en vivo). Los encabezados de las tablas
usan el nombre completo de la variable con su simbolo (p.ej.
"train/box_loss (tBOX)", "precision (P)").

Muestra: epoca/progreso/ETAs, ficha de hiperparametros, metricas por clase
reales (val_snapshot.py), salud del trainer y telemetria de hardware
(CPU freq/temp, bateria, RAM, swap, disco) con alertas semanticas.

Fuentes de datos:
  - runs/detect/<run>/results.csv        -> metricas por epoca (se lee con
    encoding utf-8/errors=replace: seguro mientras Ultralytics escribe)
  - runs/detect/<run>/args.yaml          -> hiperparametros (yaml.safe_load)
  - runs/detect/<run>/val_snapshot.json  -> cache metricas por clase
  - ai_pipeline/val_snapshot.py          -> subprocess: model.val() x epoca
  - stdout del entrenamiento             -> iter/s-it/ETA (solo si el log es
    accesible: shells Devin en TEMP/devin.exe-overflows, o --log PATH)
  - psutil + PowerShell CIM              -> trainer y telemetria hardware

Uso:
  python ai_pipeline/train_monitor.py [--run REBA_outdoor_v5] [--once]
                                      [--log ruta/al/train.log]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs" / "detect"
CLASSES = ["plastico", "unicel", "metal"]
VAL_SNAPSHOT_PY = ROOT / "ai_pipeline" / "val_snapshot.py"
# Segundos de init (imports + dataset scan) antes de la 1ra iter de una epoca.
# Solo se usa cuando el proceso trainer es mas nuevo que results.csv (resume).
EPOCH_STARTUP_S = 150.0
TEMP_QUERY_S = 20.0          # la temperatura cambia lento: consultar cada 20 s

OVERFLOW_DIR = Path(os.environ.get("TEMP", ".")) / "devin.exe-overflows"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# tqdm Ultralytics: "  123/573  1.23G   4.56it/s   0:12<0:32"  o  "s/it"
ITER_RE = re.compile(
    r"(\d+)/(\d+)\s+[^\n]*?([\d.]+)\s*(it/s|s/it)\s+(\S+)\s*<\s*(\S+)")
CLASS_RE = re.compile(
    r"^\s*(plastico|unicel|metal|all)\s+(\d+)\s+(\d+)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", re.M)
# linea de epoca: "     10/50      6.67G  ..."  (GPU_mem puede ser 0G en CPU)
EPOCH_LINE_RE = re.compile(r"^\s*(\d+)/(\d+)\s+[\d.]+G", re.M)

# ---------------- tema (fondo negro en toda la GUI) ----------------
BG = "#000000"        # fondo negro: el color de fuente resalta
FG = "#e8e8e8"
C_TITLE = "#7ec8ff"
C_INFO = "#9cdcfe"
C_WARN = "#ffd479"
C_BAD = "#ff7b72"
C_GOOD = "#7ee787"
C_DIM = "#8b949e"
C_PURPLE = "#c586c0"


# ---------------- lectura de archivos del run ----------------

def read_results(run_dir: Path) -> tuple[list[dict], float | None]:
    """Filas de results.csv + mtime. Claves stripped; seguro en escritura."""
    f = run_dir / "results.csv"
    if not f.exists():
        return [], None
    rows = []
    try:
        with f.open(newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                rows.append({(k or "").strip(): (v or "").strip()
                             for k, v in r.items()})
    except OSError:
        pass
    return rows, f.stat().st_mtime


def read_args(run_dir: Path) -> dict:
    """args.yaml completo via yaml.safe_load (fallback regex minimo)."""
    cfg: dict = {"epochs": 50, "patience": 20, "imgsz": 640, "batch": 8}
    f = run_dir / "args.yaml"
    if not f.exists():
        return cfg
    try:
        import yaml  # pyyaml viene con ultralytics
        data = yaml.safe_load(f.read_text(encoding="utf-8",
                                          errors="replace"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if v is not None})
        return cfg
    except Exception:  # noqa: BLE001 - yaml roto -> fallback
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(epochs|patience):\s*(\d+)", ln)
            if m:
                cfg[m.group(1)] = int(m.group(2))
        return cfg


def find_train_log(run_name: str, explicit: str | None = None) -> Path | None:
    """Log de stdout del entrenamiento: --log explicito > overflow Devin."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    if not OVERFLOW_DIR.is_dir():
        return None
    now = time.time()
    cands = [p for p in OVERFLOW_DIR.glob("*/content.txt")
             if now - p.stat().st_mtime < 900]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands[:20]:
        try:
            size = p.stat().st_size
            if size < 1000:
                continue
            with p.open("rb") as fh:
                head = fh.read(min(size, 400_000))
                fh.seek(max(0, size - 300_000))
                tail = fh.read()
        except OSError:
            continue
        text = (head + tail).decode("utf-8", "replace")
        if "Logging results to" in text and run_name in text:
            return p
    return None


def read_log_tail(path: Path | None, nbytes: int = 400_000) -> str:
    if path is None or not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - nbytes))
            data = fh.read()
    except OSError:
        return ""
    return ANSI_RE.sub("", data.decode("utf-8", "replace")).replace("\r", "\n")


def _parse_hms(s: str) -> float | None:
    """'H:MM:SS' | 'M:SS' | 'SS' -> segundos."""
    try:
        parts = [float(x) for x in s.strip().split(":")]
    except ValueError:
        return None
    if not parts or any(p < 0 for p in parts):
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_live(tail: str, epochs_cfg: int = 50) -> dict:
    """Ultima iteracion tqdm + ETA + metricas por clase de la ultima val."""
    out = {"epoch": None, "it": None, "tot": None, "sit": None,
           "eta_s": None, "classes": {}}
    iters = []
    for m in ITER_RE.finditer(tail):
        it, tot = int(m.group(1)), int(m.group(2))
        if tot > 50:  # iteraciones de train (las de val son 4/4, 8/8, ...)
            iters.append((it, tot, float(m.group(3)),
                          m.group(4), m.group(6)))
    if iters:
        it, tot, rate, unit, eta_str = iters[-1]
        sit = (1.0 / rate) if unit == "it/s" and rate else (
            rate if unit == "s/it" else None)
        eta = _parse_hms(eta_str)
        out.update(it=it, tot=tot, sit=sit, eta_s=eta)
        if sit and it < tot:
            out["eta_s"] = sit * (tot - it)  # ETA por iter mas preciso
        eps = EPOCH_LINE_RE.findall(tail)
        if eps:
            out["epoch"] = int(eps[-1][0])
        elif epochs_cfg:
            cand = re.findall(rf"^\s*(\d+)/{epochs_cfg}\s", tail, re.M)
            if cand:
                out["epoch"] = int(cand[-1])
    cls_lines = CLASS_RE.findall(tail)
    for name, _img, _inst, p, r, m50, m5095 in cls_lines[-4:]:
        out["classes"][name] = {
            "P": float(p), "R": float(r),
            "mAP50": float(m50), "mAP50-95": float(m5095)}
    return out


# ---------------- proceso trainer + hardware (psutil) ----------------

def find_train_pid() -> int | None:
    """PID del worker de train_outdoor.py (el de mayor RSS)."""
    best_pid, best_rss = None, -1
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            if "python" not in (p.info["name"] or "").lower():
                continue
            cmd = " ".join(p.info["cmdline"] or [])
            if "train_outdoor" not in cmd:
                continue
            rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
            if rss > best_rss:
                best_pid, best_rss = p.pid, rss
        except (psutil.NoSuchProcess, psutil.AccessDenied,
                psutil.ZombieProcess):
            continue
    return best_pid


_TRAINER_CACHE: dict[int, dict] = {}


def trainer_stats() -> dict:
    """{alive, pid, cpu_pct, rss_mb, io_mbs, threads, nice, create}."""
    pid = find_train_pid()
    if pid is None:
        return {"alive": False}
    try:
        p = psutil.Process(pid)
        ct = p.cpu_times()
        cpu_s = ct.user + ct.system
        now = time.time()
        ent = _TRAINER_CACHE.get(pid)
        pct = io_r = io_w = 0.0
        try:
            io = p.io_counters()
            io_read, io_write = (io.read_bytes, io.write_bytes) if io else (None, None)
        except (psutil.AccessDenied, AttributeError):
            io_read = io_write = None
        if ent and now > ent["t"]:
            dt = now - ent["t"]
            pct = 100.0 * (cpu_s - ent["cpu_s"]) / dt
            if io_read is not None and ent.get("io_read") is not None:
                io_r = (io_read - ent["io_read"]) / 1e6 / dt
                io_w = (io_write - ent["io_write"]) / 1e6 / dt
        _TRAINER_CACHE[pid] = {"cpu_s": cpu_s, "t": now,
                               "io_read": io_read, "io_write": io_write}
        return {"alive": True, "pid": pid, "cpu_pct": pct,
                "io_read_mbs": io_r, "io_write_mbs": io_w,
                "rss_mb": p.memory_info().rss / 1e6,
                "threads": p.num_threads(), "nice": p.nice(),
                "create": p.create_time()}
    except (psutil.NoSuchProcess, psutil.AccessDenied,
            psutil.ZombieProcess):
        _TRAINER_CACHE.pop(pid, None)
        return {"alive": False}


# ---------------- temperatura CPU (Cadena WMI/CIM con fallback) ----------------

def _cim_value(ps_cmd: str) -> list[str]:
    """Una llamada powershell -Command -> lineas de stdout no vacias."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def cpu_temp_c() -> float | None:
    """Temperatura CPU en C o None. Cadena: LibreHardwareMonitor /
    OpenHardwareMonitor (WMI) -> MSAcpi_ThermalZoneTemperature (root\\wmi).
    Sin wmi/pywin32 se usa PowerShell CIM (subprocess, ~0.5 s)."""
    # 1) OpenHardwareMonitor / LibreHardwareMonitor (sensores en Celsius)
    for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        vals = _cim_value(
            f"Get-CimInstance -Namespace '{ns}' -ClassName Hardware "
            "-ErrorAction SilentlyContinue | ForEach-Object { $_.Sensors } "
            "| Where-Object { $_.SensorType -eq 'Temperature' -and "
            "$_.Name -match 'CPU|Core|Package|Tctl|Tdie' } "
            "| Select-Object -First 3 -ExpandProperty Value")
        for v in vals:
            try:
                t = float(v)
                if 0.0 < t < 125.0:
                    return t
            except ValueError:
                continue
    # 2) MSAcpi_ThermalZoneTemperature: décimas de Kelvin -> Celsius.
    #    Se toma la zona mas caliente (suele ser la del CPU/socket).
    vals = _cim_value(
        "Get-CimInstance -Namespace root\\wmi "
        "-ClassName MSAcpi_ThermalZoneTemperature "
        "-ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty CurrentTemperature")
    temps = []
    for v in vals:
        try:
            temps.append(float(v) / 10.0 - 273.15)
        except ValueError:
            continue
    temps = [t for t in temps if -20.0 < t < 125.0]
    return max(temps) if temps else None


class HardwareMonitor(threading.Thread):
    """Muestrea telemetria cada `interval` s -> self.data (dict atomico).

    Nunca bloquea el mainloop: corre en daemon thread y la GUI solo lee
    el ultimo snapshot.
    """

    def __init__(self, interval: float = 2.0):
        super().__init__(daemon=True, name="hw-monitor")
        self.interval = interval
        self.data: dict = {}
        self._stop = threading.Event()
        self._prev_disk = None
        self._prev_t = None
        self._last_temp = 0.0
        self._temp = None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception:  # noqa: BLE001 - la telemetria nunca rompe la GUI
                pass
            self._stop.wait(self.interval)

    def _sample(self) -> None:
        now = time.time()
        d: dict = {}
        try:
            f = psutil.cpu_freq()
            if f:
                d["freq_cur"] = f.current
                d["freq_max"] = f.max
                d["freq_pct"] = (100.0 * f.current / f.max
                                 if f.max else None)
        except Exception:  # noqa: BLE001
            pass
        try:
            b = psutil.sensors_battery()
            d["batt"] = (None if b is None else
                         {"percent": b.percent, "plugged": b.power_plugged})
        except Exception:  # noqa: BLE001
            d["batt"] = None
        try:
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            d["ram_pct"] = vm.percent
            d["ram_gb"] = vm.used / 1e9
            d["swap_pct"] = sm.percent
            d["swap_gb"] = sm.used / 1e9
            d["cpu_pct"] = psutil.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            pass
        try:
            dio = psutil.disk_io_counters()
            if self._prev_disk is not None and self._prev_t:
                dt = max(now - self._prev_t, 1e-3)
                d["disk_read_mbs"] = ((dio.read_bytes -
                                       self._prev_disk.read_bytes)
                                      / 1e6 / dt)
                d["disk_write_mbs"] = ((dio.write_bytes -
                                        self._prev_disk.write_bytes)
                                       / 1e6 / dt)
            self._prev_disk, self._prev_t = dio, now
        except Exception:  # noqa: BLE001
            pass
        if self._temp is None or now - self._last_temp >= TEMP_QUERY_S:
            self._temp = cpu_temp_c()
            self._last_temp = now
        d["temp_c"] = self._temp
        d["t"] = now          # marca de tiempo de la ultima muestra
        self.data = d


# ---------------- val_snapshot (metricas por clase) ----------------

def read_val_cache(run_dir: Path, done: int) -> dict | None:
    f = run_dir / "val_snapshot.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("done") != done:
        return None
    return {"classes": data.get("classes"), "speed": data.get("speed")}


def write_val_cache(run_dir: Path, done: int, classes: dict,
                    speed: dict | None) -> None:
    try:
        (run_dir / "val_snapshot.json").write_text(
            json.dumps({"done": done, "classes": classes,
                        "speed": speed or {}}),
            encoding="utf-8")
    except OSError:
        pass


def run_val_snapshot(run_dir: Path) -> tuple[dict | None, dict | None]:
    """Subprocess: model.val() sobre last.pt -> (classes, speed) | (None,None)."""
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run(
            [sys.executable, str(VAL_SNAPSHOT_PY), str(run_dir)],
            capture_output=True, timeout=900, cwd=str(ROOT),
            encoding="utf-8", errors="replace", env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            return data.get("classes"), data.get("speed")
    return None, None


def val_worker(run_dir: Path, q: "queue.Queue[dict]") -> None:
    """Publica metricas por clase por epoca: cache -> val_snapshot -> retry."""
    last_sig = None
    last_try = 0.0
    while True:
        try:
            rows, _ = read_results(run_dir)
            done = len(rows)
            if done <= 0:
                time.sleep(15)
                continue
            cached = read_val_cache(run_dir, done)
            classes = speed = None
            if cached:
                classes = cached.get("classes")
                speed = cached.get("speed")
            elif time.time() - last_try > 60:  # reintento ~cada 60 s
                last_try = time.time()
                classes, speed = run_val_snapshot(run_dir)
                if classes:
                    write_val_cache(run_dir, done, classes, speed)
            sig = (done, bool(classes))
            if classes and sig != last_sig:
                last_sig = sig
                q.put({"type": "val", "done": done,
                       "classes": classes, "speed": speed})
        except Exception:  # noqa: BLE001 - el worker nunca debe morir
            pass
        time.sleep(15)


# ---------------- analisis de results.csv ----------------

def f1_score(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def detect_overfit(rows: list[dict], min_streak: int = 3) -> int:
    """Racha final donde val/box_loss SUBE y train/box_loss BAJA."""
    streak = 0
    for i in range(len(rows) - 1, 0, -1):
        try:
            vb, vbp = float(rows[i]["val/box_loss"]), \
                float(rows[i - 1]["val/box_loss"])
            tb, tbp = float(rows[i]["train/box_loss"]), \
                float(rows[i - 1]["train/box_loss"])
        except (KeyError, ValueError):
            break
        if vb > vbp and tb < tbp:
            streak += 1
        else:
            break
    return streak if streak >= min_streak else 0


def fmt_hm(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def fmt_num(v, nd=3):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def snapshot(run_name: str, log_path: str | None = None) -> dict:
    run_dir = RUNS / run_name
    rows, csv_mtime = read_results(run_dir)
    args = read_args(run_dir)
    epochs_cfg = int(args.get("epochs") or 50)
    patience = int(args.get("patience") or 20)
    done = len(rows)
    cur_epoch = done + 1

    times = []
    for r in rows:
        try:
            times.append(float(r.get("time") or 0))
        except ValueError:
            pass
    durs = [times[0]] + [b - a for a, b in zip(times, times[1:])]
    durs = [d for d in durs if d > 60]
    avg_ep = sum(durs) / len(durs) if durs else 51 * 60

    # inicio de la epoca en curso: mtime del csv (fin de epoca anterior).
    # Tras un resume el proceso es mas nuevo que el csv -> usar su create.
    tr = trainer_stats()
    ep_start = csv_mtime or 0.0
    if tr.get("alive") and tr.get("create"):
        est = tr["create"] + EPOCH_STARTUP_S
        if est > ep_start:
            ep_start = est
    elapsed_ep = time.time() - ep_start if ep_start else 0.0
    ep_frac = min(elapsed_ep / avg_ep, 0.98) if done else 0.0

    total_frac = min((done + ep_frac) / epochs_cfg, 1.0)
    remaining_ep_sec = max(0.0, avg_ep - elapsed_ep)
    eta_total = remaining_ep_sec + max(0, epochs_cfg - cur_epoch) * avg_ep

    best_i, best_map = -1, -1.0
    for i, r in enumerate(rows):
        try:
            v = float(r.get("metrics/mAP50-95(B)") or 0)
        except ValueError:
            v = 0.0
        if v > best_map:
            best_map, best_i = v, i
    since_best = done - (best_i + 1) if best_i >= 0 else done
    best_ep = best_i + 1
    best_row = rows[best_i] if best_i >= 0 else {}

    # proyeccion de early stop: si no hay mejora, corta tras best_ep+patience
    stop_ep = best_ep + patience if best_ep > 0 else epochs_cfg
    eta_stop = remaining_ep_sec + max(0, stop_ep - cur_epoch) * avg_ep

    live = parse_live(read_log_tail(find_train_log(run_name, log_path)),
                      epochs_cfg)
    val = read_val_cache(run_dir, done)
    val_classes = val["classes"] if val else None
    val_speed = val["speed"] if val else None
    return {
        "run": run_name, "done": done, "cur": cur_epoch,
        "args": args, "epochs_cfg": epochs_cfg, "patience": patience,
        "ep_frac": ep_frac, "total_frac": total_frac,
        "eta_ep": remaining_ep_sec, "eta_total": eta_total,
        "eta_stop": eta_stop, "stop_ep": stop_ep,
        "avg_ep": avg_ep, "best_ep": best_ep, "best_map": best_map,
        "best_row": best_row,
        "since_best": since_best, "rows": rows[-30:], "live": live,
        "csv_age": time.time() - csv_mtime if csv_mtime else None,
        "ep_elapsed": elapsed_ep, "trainer": tr,
        "val_classes": val_classes, "val_speed": val_speed,
        "overfit_streak": detect_overfit(rows),
    }


# ---------------- glosario de metricas (panel inferior) ----------------
# Cada entrada: label (nombre + simbolo), def, formula, use y mid.
# "mid" es la clave que _render_glossary usa para el estado en vivo.
GLOSSARY = [
    {"label": "Precisión (P)", "mid": "P",
     "def": "Fracción de las detecciones emitidas que son correctas.",
     "formula": "P = TP / (TP + FP)",
     "use": "Confianza del modelo: P alto = pocas falsas alarmas."},
    {"label": "Recall (R)", "mid": "R",
     "def": "Fracción de los objetos reales que el modelo logró detectar.",
     "formula": "R = TP / (TP + FN)",
     "use": "Cobertura: R alto = pocos objetos perdidos (FN)."},
    {"label": "F1-score (F1)", "mid": "F1",
     "def": "Media armónica de Precisión y Recall; resume ambos errores.",
     "formula": "F1 = 2·P·R / (P + R)",
     "use": "Un solo número para comparar modelos con P y R distintos."},
    {"label": "mAP50", "mid": "mAP50",
     "def": "Promedio por clase del AP con acierto si IoU ≥ 0.50.",
     "formula": "mAP50 = media_c(AP_c @ IoU=0.50)",
     "use": "Métrica principal legible del detector."},
    {"label": "mAP50-95", "mid": "mAP5095",
     "def": "AP promediada sobre 10 umbrales de IoU de 0.50 a 0.95.",
     "formula": "mAP50-95 = media AP @ IoU∈{0.50,…,0.95}",
     "use": "Estándar COCO; premia cajas mejor ajustadas."},
    {"label": "IoU", "mid": "iou",
     "def": "Solapamiento entre la caja predicha y la caja real (gt).",
     "formula": "IoU = |A∩B| / |A∪B|",
     "use": "Criterio de TP: IoU ≥ umbral; el NMS usa iou de args.yaml."},
    {"label": "box_loss (tBOX / vBOX)", "mid": "box_loss",
     "def": "Pérdida de regresión de cajas (CIoU) en train y validación.",
     "formula": "L_box = CIoU(pred, gt)",
     "use": "Baja = cajas mejor ubicadas; si val sube y train baja → overfit."},
    {"label": "cls_loss (tCLS / vCLS)", "mid": "cls_loss",
     "def": "Pérdida de clasificación (BCE) de la clase por detección.",
     "formula": "L_cls = BCE(p, y)",
     "use": "Baja = mejor asignación a plastico/unicel/metal."},
    {"label": "dfl_loss (tDFL / vDFL)", "mid": "dfl_loss",
     "def": "Distribution Focal Loss: regresión de coordenadas como "
            "distribución.",
     "formula": "L_dfl = −log S(y)",
     "use": "Afina el borde fino de las cajas; suele bajar más lento."},
    {"label": "lr/pg0–pg2 (LR)", "mid": "lr",
     "def": "Learning rate por grupo de parámetros (backbone/neck/head).",
     "formula": "lr_t = lr0 · calendario_coseno(t)",
     "use": "Tamaño del paso del optimizador AdamW; decae a ~0."},
    {"label": "it/s", "mid": "iter",
     "def": "Iteraciones de entrenamiento por segundo (tqdm del log).",
     "formula": "it/s = 1 / s/it",
     "use": "Velocidad real; con él se proyecta el ETA por iteración."},
    {"label": "ETA", "mid": "eta",
     "def": "Tiempo estimado restante de entrenamiento.",
     "formula": "ETA = épocas_restantes × duración_media_época",
     "use": "Planificación; se muestran época actual, early-stop y total."},
    {"label": "Épocas / patience", "mid": "patience",
     "def": "Conteo de épocas y paciencia sin mejora antes del early stop.",
     "formula": "stop si done − best_ep > patience",
     "use": "Evita sobreentrenar; patience viene de args.yaml."},
    {"label": "Temperatura CPU (temp_c)", "mid": "temp",
     "def": "Temperatura del procesador leída de sensores ACPI/CIM.",
     "formula": "T = CurrentTemperature/10 − 273.15 (K→°C)",
     "use": "≥70 °C aviso, ≥80 °C crítico; explica throttling."},
    {"label": "RAM (ram_pct / ram_gb)", "mid": "ram",
     "def": "Memoria RAM usada del sistema (psutil).",
     "formula": "ram_pct = used/total × 100",
     "use": ">85 % o swap alto puede paginar y ralentizar el train."},
    {"label": "Frecuencia CPU (freq_pct)", "mid": "freq",
     "def": "Frecuencia actual del CPU frente a su máxima (MHz).",
     "formula": "freq_pct = cur/max × 100",
     "use": "<80 % indica throttling térmico o de energía."},
    {"label": "Swap (swap_pct / swap_gb)", "mid": "swap",
     "def": "Memoria virtual usada en disco (psutil).",
     "formula": "swap_pct = used/total × 100",
     "use": "Swap alto = presión de RAM; penaliza mucho el entrenamiento."},
    {"label": "Disco I/O (disk MB/s)", "mid": "disk",
     "def": "Tasa de lectura/escritura del disco del sistema.",
     "formula": "MB/s = Δbytes / Δt",
     "use": "Cuello de botella si checkpoints/results.csv escriben mucho."},
    {"label": "Batería (batt)", "mid": "batt",
     "def": "Carga de la batería y si el equipo está conectado a AC.",
     "formula": "—",
     "use": "En batería el CPU puede throttlear; <20 % riesgo de suspensión."},
    {"label": "Trainer CPU/RSS", "mid": "trainer",
     "def": "Uso de CPU y memoria residente del proceso train_outdoor.py.",
     "formula": "cpu% = Δcpu_time/Δt × 100 · rss = memoria en uso",
     "use": "Confirma que el entrenamiento trabaja y cuánta RAM pide."},
]


# ---------------- GUI ----------------

def _color_for(temp_label: ttk.Label, value: str, warn: float,
               crit: float, unit: str = "") -> None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        temp_label.config(foreground=FG)
        return
    if v >= crit:
        c = C_BAD
    elif v >= warn:
        c = C_WARN
    else:
        c = C_INFO
    temp_label.config(foreground=c)


def draw_sparkline(canvas, rows: list[dict]) -> None:
    """Linea de mAP50-95 por epoca + punto en la mejor (ancho dinamico)."""
    canvas.delete("all")
    W = canvas.winfo_width()
    H = canvas.winfo_height()
    if W < 80 or H < 40:
        return
    vals = []
    for r in rows:
        try:
            vals.append((int(float(r.get("epoch") or 0)),
                         float(r.get("metrics/mAP50-95(B)") or 0)))
        except ValueError:
            continue
    if len(vals) < 2:
        return
    lo, hi = min(v for _, v in vals), max(v for _, v in vals)
    pad = max((hi - lo) * 0.15, 0.02)
    lo, hi = lo - pad, hi + pad
    n = len(vals)

    def xy(i: int, v: float):
        x = 10 + i * (W - 20) / max(n - 1, 1)
        y = H - 10 - (v - lo) / (hi - lo) * (H - 20)
        return x, y

    pts = []
    for i, (_, v) in enumerate(vals):
        pts += [xy(i, v)]
    canvas.create_line(*pts, fill=C_TITLE, width=2)
    best_local, bv = -1, -1.0
    for i, (_, v) in enumerate(vals):
        if v > bv:
            bv, best_local = v, i
    if best_local >= 0:
        x, y = xy(best_local, bv)
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                           fill=C_WARN, outline=BG)
        canvas.create_text(x, y - 10, text=f"{bv:.3f}",
                           fill=C_WARN, font=("Consolas", 8))
    canvas.create_text(6, 6, anchor="w",
                       text=f"mAP50-95  [{lo+pad:.3f} – {hi-pad:.3f}]",
                       fill=C_DIM, font=("Consolas", 8))


def build_gui(root, run_name: str, log_path: str | None = None) -> None:
    import tkinter as tk
    from tkinter import ttk

    root.title(f"REBA — Monitor {run_name}")
    root.geometry("1320x1060")
    root.minsize(1180, 620)
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TLabel", background=BG, foreground=FG,
                    font=("Consolas", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabelframe", background=BG, bordercolor="#333333",
                    lightcolor="#333333", darkcolor="#333333")
    style.configure("TLabelframe.Label", background=BG,
                    foreground=C_TITLE, font=("Consolas", 10, "bold"))
    style.configure("Title.TLabel", font=("Consolas", 13, "bold"),
                    foreground=C_TITLE)
    style.configure("Big.TLabel", font=("Consolas", 22, "bold"),
                    foreground=C_WARN)
    style.configure("Sub.TLabel", font=("Consolas", 10),
                    foreground=C_INFO)
    style.configure("Treeview", background=BG, fieldbackground=BG,
                    foreground=FG, rowheight=20,
                    font=("Consolas", 9))
    style.configure("Treeview.Heading", background=BG,
                    foreground=C_TITLE, font=("Consolas", 9, "bold"))
    style.map("Treeview", background=[("selected", "#14456e")])
    for _o in ("Vertical", "Horizontal"):
        style.configure(f"{_o}.TScrollbar", background="#262626",
                        troughcolor=BG, bordercolor=BG, lightcolor=BG,
                        darkcolor=BG, arrowcolor=FG)

    # --- contenedor con scroll vertical (mouse wheel) ---
    scroll_canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    outer_vsb = ttk.Scrollbar(root, orient="vertical",
                              command=scroll_canvas.yview)
    scroll_canvas.configure(yscrollcommand=outer_vsb.set)
    outer_vsb.pack(side="right", fill="y")
    scroll_canvas.pack(side="left", fill="both", expand=True)
    body = ttk.Frame(scroll_canvas)
    body_win = scroll_canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>",
              lambda e: scroll_canvas.configure(
                  scrollregion=scroll_canvas.bbox("all")))
    def _fit_body(e=None):
        """Alto del body = max(viewport, contenido). Asi la fila
        inferior no "salta" cuando cambian los textos de las columnas."""
        w = scroll_canvas.winfo_width()
        h = scroll_canvas.winfo_height()
        if w < 10 or h < 10:
            return
        scroll_canvas.itemconfigure(
            body_win, width=w,
            height=max(h, body.winfo_reqheight()))

    scroll_canvas.bind("<Configure>", _fit_body)

    def _on_mousewheel(e):
        scroll_canvas.yview_scroll(int(-e.delta / 120), "units")

    scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

    # --- layout: fila de 2 columnas + fila inferior de tablas ---
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(0, weight=1)
    body.grid_rowconfigure(1, weight=0)

    cols_f = ttk.Frame(body)
    cols_f.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 2))
    cols_f.grid_rowconfigure(0, weight=1)
    cols_f.grid_columnconfigure(0, weight=10)
    cols_f.grid_columnconfigure(1, weight=12)

    left_f = ttk.LabelFrame(cols_f,
                            text="Columna 1 · títulos, barras y textos")
    left_f.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    right_f = ttk.LabelFrame(
        cols_f, text="Columna 2 · estado actual y glosario de métricas")
    right_f.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

    bot_f = ttk.Frame(body)
    bot_f.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 6))
    bot_f.grid_columnconfigure(0, weight=1)

    FRM = {"padx": 10, "pady": 2}
    wraps = {"L": [], "R": []}

    def _reg(lb, side):
        wraps[side].append(lb)
        return lb

    def _twheel(w, e):
        w.yview_scroll(-1 * (e.delta // 120), "units")
        return "break"

    def _apply_wraps(side):
        frame = left_f if side == "L" else right_f
        w = frame.winfo_width()
        if w < 80:
            return
        for lb in wraps[side]:
            lb.configure(wraplength=max(140, w - 30))

    left_f.bind("<Configure>", lambda e: _apply_wraps("L"))
    right_f.bind("<Configure>", lambda e: _apply_wraps("R"))

    class HBar:
        """Barra de progreso en Canvas: fondo negro real + relleno color."""

        def __init__(self, parent, color, height=14):
            self.color = color
            self.frac = 0.0
            self.cv = tk.Canvas(parent, height=height, bg=BG,
                                highlightthickness=0)
            self.cv.pack(fill="x", padx=10, pady=(2, 0))
            self.cv.bind("<Configure>", lambda e: self._draw())

        def set(self, frac):
            self.frac = max(0.0, min(1.0, frac))
            self._draw()

        def _draw(self):
            cv = self.cv
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 20:
                return
            cv.create_rectangle(0, 0, w, h, fill="#000000",
                                outline="#1f1f1f")
            fw = int(w * self.frac)
            if fw > 1:
                cv.create_rectangle(0, 0, fw, h, fill=self.color,
                                    outline="")
            cv.create_text(w - 5, h / 2, anchor="e",
                           text=f"{self.frac * 100:.1f}%",
                           fill=C_DIM, font=("Consolas", 8))

    def make_tree(parent, spec, height=6):
        """spec = [(col_id, encabezado, ancho_px)]. Devuelve (frame, tree)."""
        f = ttk.Frame(parent)
        cols = [c[0] for c in spec]
        t = ttk.Treeview(f, columns=cols, show="headings", height=height)
        for cid, head, w in spec:
            t.heading(cid, text=head)
            t.column(cid, width=w, anchor="center", stretch=False)
        vsb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        hsb = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        t.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        f.grid_rowconfigure(0, weight=1)
        f.grid_columnconfigure(0, weight=1)
        return f, t

    # ================= columna 1: titulos, barras y textos =================
    ttk.Label(left_f, text=f"Entrenamiento {run_name} — YOLOv8n",
              style="Title.TLabel", justify="left").pack(anchor="w", **FRM)
    ttk.Label(left_f,
              text="detección · 3 clases: plastico, unicel, metal",
              style="Sub.TLabel", justify="left").pack(anchor="w", **FRM)

    hp_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                         foreground=C_PURPLE, justify="left"), "L")
    hp_f.pack(anchor="w", **FRM)

    ep_big = ttk.Label(left_f, text="--", style="Big.TLabel",
                       justify="left")
    ep_big.pack(anchor="w", padx=10)

    bar_ep = HBar(left_f, C_TITLE)
    lbl_ep = _reg(ttk.Label(left_f, text="", style="TLabel",
                           justify="left"), "L")
    lbl_ep.pack(anchor="w", **FRM)

    bar_tot = HBar(left_f, C_PURPLE)
    lbl_tot = _reg(ttk.Label(left_f, text="", style="TLabel",
                            justify="left"), "L")
    lbl_tot.pack(anchor="w", **FRM)

    eta_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                          justify="left"), "L")
    eta_f.pack(anchor="w", padx=10, pady=(6, 0))

    best_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                           foreground=C_WARN, justify="left"), "L")
    best_f.pack(anchor="w", **FRM)

    patience_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                               justify="left"), "L")
    patience_f.pack(anchor="w", **FRM)

    sys_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                          foreground=C_INFO, justify="left"), "L")
    sys_f.pack(anchor="w", padx=10, pady=(6, 0))

    hw_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                         justify="left"), "L")
    hw_f.pack(anchor="w", **FRM)

    ttk.Label(left_f, text="mAP50-95(B) por época:", style="Sub.TLabel",
              justify="left").pack(anchor="w", padx=10, pady=(6, 0))
    spark_data = [[]]
    spark = tk.Canvas(left_f, height=76, bg=BG, highlightthickness=0)
    spark.pack(fill="x", padx=10, pady=(0, 2))
    spark.bind("<Configure>",
               lambda e: draw_sparkline(spark, spark_data[0]))

    overfit_f = _reg(ttk.Label(left_f, text="", style="TLabel",
                              foreground=C_BAD, justify="left"), "L")
    overfit_f.pack(anchor="w", **FRM)

    # ================= columna 2: estado actual + glosario =================
    expl_lf = ttk.LabelFrame(
        right_f, text="Explicación del estado actual del entrenamiento")
    expl_lf.pack(fill="x", **FRM)
    expl_wrap = ttk.Frame(expl_lf)
    expl_wrap.pack(fill="both", expand=True, padx=4, pady=4)
    expl_txt = tk.Text(expl_wrap, height=12, wrap="word", bg=BG, fg=FG,
                       font=("Consolas", 9), relief="flat",
                       highlightthickness=1,
                       highlightbackground="#262626", state="disabled",
                       spacing1=3, spacing3=3)
    expl_sb = ttk.Scrollbar(expl_wrap, orient="vertical",
                            command=expl_txt.yview)
    expl_txt.configure(yscrollcommand=expl_sb.set)
    expl_txt.pack(side="left", fill="both", expand=True)
    expl_sb.pack(side="right", fill="y")
    expl_txt.bind("<MouseWheel>", lambda e: _twheel(expl_txt, e))

    gloss_lf = ttk.LabelFrame(
        right_f, text="Glosario de métricas · definición / fórmula / "
                      "uso / estado")
    gloss_lf.pack(fill="x", **FRM)
    gloss_wrap = ttk.Frame(gloss_lf)
    gloss_wrap.pack(fill="both", expand=True, padx=4, pady=4)
    gloss_txt = tk.Text(gloss_wrap, height=18, wrap="word", bg=BG, fg=FG,
                        font=("Consolas", 9), relief="flat",
                        highlightthickness=1,
                        highlightbackground="#262626", state="disabled",
                        spacing1=2, spacing3=2)
    gloss_txt.tag_configure("hdr", foreground=C_TITLE,
                            font=("Consolas", 9, "bold"))
    gloss_txt.tag_configure("key", foreground=C_PURPLE)
    for _tg, _c in (("good", C_GOOD), ("bad", C_BAD),
                    ("warn", C_WARN), ("info", C_INFO)):
        gloss_txt.tag_configure(_tg, foreground=_c)
    gloss_sb = ttk.Scrollbar(gloss_wrap, orient="vertical",
                             command=gloss_txt.yview)
    gloss_txt.configure(yscrollcommand=gloss_sb.set)
    gloss_txt.pack(side="left", fill="both", expand=True)
    gloss_sb.pack(side="right", fill="y")
    gloss_txt.bind("<MouseWheel>", lambda e: _twheel(gloss_txt, e))

    # ================= fila inferior: tablas a todo lo ancho =================
    hist_lf = ttk.LabelFrame(
        bot_f, text="Historial por época — results.csv")
    hist_lf.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 4))
    hist_f, tree_hist = make_tree(hist_lf, [
        ("ep", "época", 44),
        ("tbox", "train/box_loss·tBOX", 142),
        ("tcls", "train/cls_loss·tCLS", 142),
        ("tdfl", "train/dfl_loss·tDFL", 142),
        ("P", "precision·P", 92),
        ("R", "recall·R", 82),
        ("m50", "mAP50", 54),
        ("m5095", "mAP50-95", 68),
        ("vbox", "val/box_loss·vBOX", 130),
        ("vcls", "val/cls_loss·vCLS", 130),
        ("vdfl", "val/dfl_loss·vDFL", 130),
        ("lr", "lr/pg0·LR", 80),
    ], height=7)
    hist_f.pack(fill="both", expand=True, padx=4, pady=4)

    per_lf = ttk.LabelFrame(
        bot_f, text="Última validación por clase "
                    "(val_snapshot · model.val sobre last.pt)")
    per_lf.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
    per_wrap = ttk.Frame(per_lf)
    per_wrap.pack(fill="both", expand=True, padx=4, pady=4)
    per_f, tree = make_tree(per_wrap, [
        ("clase", "clase", 100),
        ("imgs", "imágenes", 150),
        ("inst", "instancias", 170),
        ("P", "precision·P", 180),
        ("R", "recall·R", 160),
        ("F1", "F1-score·F1", 200),
        ("m50", "mAP50", 120),
        ("m5095", "mAP50-95", 140),
    ], height=4)
    per_f.pack(fill="x")
    speed_txt = tk.Text(per_wrap, height=3, wrap="word", bg=BG,
                        fg=C_INFO, font=("Consolas", 9), relief="flat",
                        highlightthickness=0, state="disabled",
                        spacing1=1, spacing3=1)
    speed_txt.pack(fill="x", pady=(2, 0))

    src_lf = ttk.LabelFrame(bot_f, text="Fuentes de datos e hilos daemon")
    src_lf.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 4))
    src_wrap = ttk.Frame(src_lf)
    src_wrap.pack(fill="both", expand=True, padx=4, pady=4)
    src_txt = tk.Text(src_wrap, height=7, wrap="word", bg=BG, fg=C_INFO,
                      font=("Consolas", 9), relief="flat",
                      highlightthickness=1,
                      highlightbackground="#262626", state="disabled",
                      spacing1=2, spacing3=2)
    src_txt.tag_configure("bad", foreground=C_BAD)
    src_sb = ttk.Scrollbar(src_wrap, orient="vertical",
                           command=src_txt.yview)
    src_txt.configure(yscrollcommand=src_sb.set)
    src_txt.pack(side="left", fill="both", expand=True)
    src_sb.pack(side="right", fill="y")
    src_txt.bind("<MouseWheel>", lambda e: _twheel(src_txt, e))

    q: "queue.Queue[dict]" = queue.Queue()
    threading.Thread(target=val_worker, args=(RUNS / run_name, q),
                     daemon=True).start()
    hw = HardwareMonitor(interval=2.0)
    hw.start()
    val_state = {"classes": None, "speed": None, "done": -1,
                 "last": None}
    prev_best = [None]

    def drain_val() -> None:
        try:
            while True:
                msg = q.get_nowait()
                if msg.get("type") == "val":
                    val_state["classes"] = msg.get("classes")
                    val_state["speed"] = msg.get("speed")
                    val_state["done"] = msg.get("done", -1)
                    val_state["last"] = time.time()
        except queue.Empty:
            pass

    def _f1(p, r):
        try:
            return f1_score(float(p), float(r))
        except (TypeError, ValueError):
            return 0.0

    # ---------- panel inferior: explicacion del estado actual ----------
    def _render_explain(s: dict, tr: dict, hw_d: dict,
                        val_state: dict) -> None:
        done, cur, cfg = s["done"], s["cur"], s["epochs_cfg"]
        parts = []
        if done >= cfg:
            phase = "finalizado"
        elif tr.get("alive"):
            phase = "en curso"
        else:
            phase = "detenido"
        parts.append(
            f"Fase actual: {phase}. El run {s['run']} lleva {done} de "
            f"{cfg} épocas completadas; la época {cur} está "
            f"~{s['ep_frac']*100:.0f}% avanzada (~{fmt_hm(s['ep_elapsed'])} "
            f"transcurridos; el ritmo promedio es {fmt_hm(s['avg_ep'])} "
            f"por época).")
        if tr.get("alive"):
            parts.append(
                f"El proceso trainer está vivo (PID {tr['pid']}): CPU "
                f"{tr['cpu_pct']:.0f}%, RAM {tr['rss_mb']:.0f} MB, "
                f"{tr.get('threads', '?')} hilos.")
        else:
            parts.append(
                "No se detecta el proceso train_outdoor.py: el "
                "entrenamiento está detenido o todavía no arrancó.")
        age = s["csv_age"]
        parts.append(
            "results.csv registra una fila por época completada"
            + (f"; última escritura hace {fmt_hm(age)}."
               if age is not None else "; aún sin datos."))
        vd = val_state.get("done", -1)
        if val_state.get("classes") and vd >= done:
            parts.append(
                "Las métricas por clase provienen de val_snapshot "
                "(model.val real sobre last.pt), publicadas por el "
                "hilo daemon val_worker.")
        else:
            parts.append(
                "Las métricas por clase aún no están al día: "
                "val_worker las recalcula con model.val() cada ~60 s.")
        tc = hw_d.get("temp_c")
        parts.append(
            "La telemetría (frecuencia, temperatura, RAM, disco) la "
            "toma el hilo daemon hw-monitor cada 2 s"
            + (f"; temperatura CPU actual {tc:.0f} °C."
               if tc is not None else "."))
        live = s["live"]
        parts.append(
            "El log stdout de Ultralytics "
            + ("está disponible: iteración/s y ETA por iteración en vivo."
               if (live.get("it") or live.get("classes"))
               else "no está accesible: los ETA usan el promedio de "
                    "épocas."))
        ov = s["overfit_streak"]
        if ov:
            parts.append(
                f"Alerta: posible overfitting desde hace {ov} épocas "
                "(val/box_loss sube mientras train/box_loss baja).")
        expl_txt.config(state="normal")
        expl_txt.delete("1.0", "end")
        expl_txt.insert("1.0", " ".join(parts))
        expl_txt.config(state="disabled")

    # ---------- panel inferior: glosario de metricas ----------
    def _render_glossary(s: dict, live: dict, hw_d: dict) -> None:
        rows = s["rows"]
        last = rows[-1] if rows else {}
        prev = rows[-2] if len(rows) > 1 else {}
        cfg = s["epochs_cfg"]
        args = s["args"]
        tr = s["trainer"]

        def _f(row, key):
            try:
                return float(row.get(key))
            except (TypeError, ValueError):
                return None

        def _delta(key):
            a, b = _f(last, key), _f(prev, key)
            return (a - b) if (a is not None and b is not None) else None

        def _arrow(delta, higher_better=True):
            if delta is None:
                return "—", "info"
            if abs(delta) < 5e-5:
                return "→", "info"
            up = delta > 0
            good = up if higher_better else not up
            return ("▲" if up else "▼"), ("good" if good else "bad")

        def _metric(key, nd=3, higher_better=True):
            v = _f(last, key)
            a, tag = _arrow(_delta(key), higher_better)
            return f"{fmt_num(v, nd)} {a}", tag

        def _f1_of(r):
            if not r:
                return None
            p = _f(r, "metrics/precision(B)")
            rr = _f(r, "metrics/recall(B)")
            return (f1_score(p, rr)
                    if (p is not None and rr is not None) else None)

        def _loss(tkey, vkey):
            tv, vv = _f(last, tkey), _f(last, vkey)
            at, _ = _arrow(_delta(tkey), higher_better=False)
            av, tag = _arrow(_delta(vkey), higher_better=False)
            return (f"train {fmt_num(tv, 2)}{at} · "
                    f"val {fmt_num(vv, 2)}{av}", tag)

        st: dict = {}
        st["P"] = _metric("metrics/precision(B)")
        st["R"] = _metric("metrics/recall(B)")
        f1l, f1p = _f1_of(last), _f1_of(prev)
        df1 = ((f1l - f1p)
               if (f1l is not None and f1p is not None) else None)
        a, tag = _arrow(df1)
        st["F1"] = (f"{fmt_num(f1l)} {a}", tag)
        st["mAP50"] = _metric("metrics/mAP50(B)")
        txt, tag = _metric("metrics/mAP50-95(B)")
        best = f" · mejor ép {s['best_ep']} = {fmt_num(s['best_map'])}"
        if s["best_ep"] and s["done"] == s["best_ep"]:
            best += " ★"
            tag = "good"
        st["mAP5095"] = (txt + best, tag)
        st["iou"] = (f"NMS iou={args.get('iou', 0.7)} · "
                     "mAP evalúa IoU 0.50–0.95", "info")
        st["box_loss"] = _loss("train/box_loss", "val/box_loss")
        st["cls_loss"] = _loss("train/cls_loss", "val/cls_loss")
        st["dfl_loss"] = _loss("train/dfl_loss", "val/dfl_loss")
        lr = _f(last, "lr/pg0")
        st["lr"] = ((f"{lr:.2e} · pg0=pg1=pg2 · coseno→0"
                     if lr is not None else "—"), "info")
        sit = live.get("sit")
        st["iter"] = ((f"{1/sit:.2f} it/s" if sit else "ritmo —"),
                      "info")
        st["eta"] = (f"época {fmt_hm(s['eta_ep'])} · "
                     f"stop~{fmt_hm(s['eta_stop'])} · "
                     f"total {fmt_hm(s['eta_total'])}", "info")
        since = s["since_best"]
        st["patience"] = (
            f"{s['done']}/{cfg} ép · {since}/{s['patience']} sin mejora · "
            f"stop ~ép {s['stop_ep'] if s['best_ep'] else '?'}",
            "warn" if since >= max(1, s["patience"] // 2) else "info")
        # --- hardware / trainer ---
        tc = hw_d.get("temp_c")
        if tc is None:
            st["temp"] = ("N/D", "info")
        else:
            tg = "bad" if tc >= 80 else ("warn" if tc >= 70 else "info")
            st["temp"] = (f"{tc:.0f} °C", tg)
        if "ram_pct" in hw_d:
            tg = ("warn" if (hw_d["ram_pct"] > 85 or
                             hw_d.get("swap_pct", 0) > 85) else "info")
            st["ram"] = (f"{hw_d['ram_pct']:.0f}% "
                         f"({hw_d['ram_gb']:.1f} GB)", tg)
        else:
            st["ram"] = ("N/D", "info")
        fp = hw_d.get("freq_pct")
        if fp is None:
            st["freq"] = ("N/D", "info")
        else:
            thr = fp < 80.0
            st["freq"] = (
                f"{hw_d.get('freq_cur', 0):.0f}/"
                f"{hw_d.get('freq_max', 0):.0f} MHz ({fp:.0f}%)"
                + (" ⚠ THROTTLE" if thr else ""),
                "warn" if thr else "info")
        if "swap_pct" in hw_d:
            st["swap"] = (f"{hw_d['swap_pct']:.0f}% "
                          f"({hw_d['swap_gb']:.1f} GB)",
                          "warn" if hw_d["swap_pct"] > 85 else "info")
        else:
            st["swap"] = ("N/D", "info")
        if "disk_read_mbs" in hw_d:
            st["disk"] = (f"R{hw_d['disk_read_mbs']:.1f}/"
                          f"W{hw_d['disk_write_mbs']:.1f} MB/s", "info")
        else:
            st["disk"] = ("N/D", "info")
        b = hw_d.get("batt")
        st["batt"] = (
            (f"{b['percent']:.0f}% "
             f"{'(AC)' if b['plugged'] else '(batería)'}",
             "warn" if (not b["plugged"] and b["percent"] < 20)
             else "info")
            if b is not None else ("N/D", "info"))
        if tr.get("alive"):
            st["trainer"] = (
                f"PID {tr['pid']} · CPU {tr['cpu_pct']:.0f}% · "
                f"RAM {tr['rss_mb']:.0f} MB", "info")
        else:
            st["trainer"] = ("no detectado", "bad")

        y = gloss_txt.yview()
        gloss_txt.config(state="normal")
        gloss_txt.delete("1.0", "end")
        for e in GLOSSARY:
            gloss_txt.insert("end", e["label"] + "\n", "hdr")
            for p, key in (("Def", "def"), ("Fórm", "formula"),
                           ("Uso", "use")):
                gloss_txt.insert("end", f"  {p + ':':<5}", "key")
                gloss_txt.insert("end", e[key] + "\n")
            txt, tg = st.get(e["mid"], ("—", "info"))
            gloss_txt.insert("end", "  Est: ", "key")
            gloss_txt.insert("end", txt + "\n\n", tg)
        gloss_txt.config(state="disabled")
        gloss_txt.yview_moveto(y[0])

    def refresh() -> None:
        drain_val()
        try:
            s = snapshot(run_name, log_path)
        except Exception as e:  # noqa: BLE001 - GUI nunca debe morir
            expl_txt.config(state="normal")
            expl_txt.delete("1.0", "end")
            expl_txt.insert("1.0", f"error leyendo datos: {e}")
            expl_txt.config(state="disabled")
            root.after(5000, refresh)
            return

        done, cur, cfg = s["done"], s["cur"], s["epochs_cfg"]
        args = s["args"]
        tr = s["trainer"]
        live = s["live"]
        rows = s["rows"]
        hw_d = hw.data or {}
        if done >= cfg:
            ep_big.config(text="Finalizado")
        elif tr.get("alive"):
            ep_big.config(text=f"Época {cur}/{cfg}")
        else:
            ep_big.config(text=f"Época {cur}/{cfg} — DETENIDO")

        # ficha de hiperparametros
        imgsz = args.get("imgsz", 640)
        batch = args.get("batch", 8)
        opt = args.get("optimizer") or "auto"
        workers = args.get("workers", 0)
        close_mos = int(args.get("close_mosaic") or 10)
        mos_last = max(0, cfg - close_mos)
        mosaic_on = cur <= mos_last if done < cfg else False
        device = args.get("device") or "?"
        hp_f.config(
            text=(f"imgsz {imgsz}px · batch {batch} · optimizer {opt} · "
                  f"workers {workers} · patience {s['patience']} · "
                  f"device {device} · "
                  f"Mosaic {'ON hasta época ' + str(mos_last) if mosaic_on else 'OFF'}"))

        bar_ep.set(s["ep_frac"])
        live_fresh = live.get("it") and live.get("epoch") == cur
        if live_fresh:
            sit = live.get("sit")
            rate_txt = (f"{1/sit:.2f} it/s" if sit else "")
            it_txt = (f"iteración {live['it']}/{live['tot']} {rate_txt}"
                      ).strip()
            eta_ep_txt = fmt_hm(s["eta_ep"])
            if live.get("eta_s") is not None:
                eta_ep_txt += f" (log: {fmt_hm(live['eta_s'])})"
        else:
            it_txt = "iteración —"
            eta_ep_txt = fmt_hm(s["eta_ep"])
        ep_age = fmt_hm(s["ep_elapsed"])
        lbl_ep.config(
            text=f"{it_txt} · ~{s['ep_frac']*100:.0f}% de la época "
                 f"(~{ep_age} transcurridos) · ETA época: {eta_ep_txt}")

        bar_tot.set(s["total_frac"])
        lbl_tot.config(
            text=f"Total: {s['total_frac']*100:.1f}% · "
                 f"época prom. {fmt_hm(s['avg_ep'])} · "
                 f"ETA si completa {cfg} épocas: {fmt_hm(s['eta_total'])}")

        eta_f.config(
            text=(f"ETA época actual: {fmt_hm(s['eta_ep'])}\n"
                  f"ETA si early-stop (~época {s['stop_ep']}): "
                  f"{fmt_hm(s['eta_stop'])}\n"
                  f"ETA máximo ({cfg} épocas): {fmt_hm(s['eta_total'])}"))

        # mejor modelo destacado
        br = s["best_row"]
        if br:
            try:
                bp = float(br.get("metrics/precision(B)") or 0)
                br_ = float(br.get("metrics/recall(B)") or 0)
                bm50 = float(br.get("metrics/mAP50(B)") or 0)
            except ValueError:
                bp = br_ = bm50 = 0.0
            bf1 = _f1(bp, br_)
            best_f.config(
                text=(f"Mejor modelo: época {s['best_ep']} · "
                      f"mAP50-95 {fmt_num(s['best_map'])} · "
                      f"F1-score {fmt_num(bf1)} · "
                      f"precision (P) {fmt_num(bp)} · "
                      f"recall (R) {fmt_num(br_)} · "
                      f"mAP50 {fmt_num(bm50)}"))
        left = s["patience"] - s["since_best"]
        patience_f.config(
            text=f"Épocas sin mejora de mAP50-95: {s['since_best']} / "
                 f"{s['patience']} (quedan {max(0, left)}) · early stop "
                 f"~época {s['stop_ep'] if s['best_ep'] else '?'}")

        # --- telemetria trainer ---
        if tr.get("alive"):
            sys_f.config(
                text=(f"trainer PID {tr['pid']} · CPU "
                      f"{tr['cpu_pct']:.0f}% · RAM {tr['rss_mb']:.0f} MB · "
                      f"disco R{tr.get('io_read_mbs', 0):.1f}/"
                      f"W{tr.get('io_write_mbs', 0):.1f} MB/s · "
                      f"hilos {tr.get('threads', '?')} · "
                      f"nice {tr.get('nice', '?')} · activo "
                      f"{fmt_hm(time.time() - tr['create'])}"))
        else:
            sys_f.config(
                text="trainer NO detectado (proceso train_outdoor.py "
                     "ausente)")

        # --- telemetria hardware (snapshot del hilo) ---
        parts = []
        fp = hw_d.get("freq_pct")
        if fp is not None:
            throttle = fp < 80.0
            parts.append(
                f"CPU frecuencia {hw_d.get('freq_cur', 0):.0f}/"
                f"{hw_d.get('freq_max', 0):.0f} MHz ({fp:.0f}%)"
                + (" ⚠ THROTTLE" if throttle else ""))
        b = hw_d.get("batt")
        if b is not None:
            parts.append(f"{'AC' if b['plugged'] else 'BATERÍA'} "
                         f"{b['percent']:.0f}%")
        if "ram_pct" in hw_d:
            parts.append(f"RAM {hw_d['ram_pct']:.0f}% "
                         f"({hw_d['ram_gb']:.1f} GB)")
        if "swap_pct" in hw_d:
            parts.append(f"swap {hw_d['swap_pct']:.0f}% "
                         f"({hw_d['swap_gb']:.1f} GB)")
        if "disk_read_mbs" in hw_d:
            parts.append(f"disco R{hw_d['disk_read_mbs']:.1f}/"
                         f"W{hw_d['disk_write_mbs']:.1f} MB/s")
        tc = hw_d.get("temp_c")
        parts.append(f"temperatura CPU {tc:.0f}°C"
                     if tc is not None else "temperatura N/D")
        hw_f.config(text=" · ".join(parts))
        # colores semanticos
        _color_for(hw_f, str(tc) if tc is not None else "",
                   warn=70, crit=80)
        if fp is not None and fp < 80:
            hw_f.config(foreground=C_WARN)
        elif "ram_pct" in hw_d and (hw_d["ram_pct"] > 85 or
                                    hw_d.get("swap_pct", 0) > 85):
            hw_f.config(foreground=C_WARN)

        # sparkline mAP50-95
        spark_data[0] = rows
        draw_sparkline(spark, rows)

        # --- tabla metricas por clase (val_snapshot real) ---
        tree.delete(*tree.get_children())
        cl = val_state["classes"] or s["val_classes"] or live.get("classes")
        if cl:
            for name in CLASSES + ["all"]:
                d = cl.get(name)
                if not d:
                    continue
                try:
                    p_ = float(d.get("P", 0))
                    r_ = float(d.get("R", 0))
                except (TypeError, ValueError):
                    p_ = r_ = 0.0
                tree.insert("", "end", values=(
                    name, d.get("images", "-"), d.get("instances", "-"),
                    fmt_num(d.get("P")), fmt_num(d.get("R")),
                    fmt_num(_f1(p_, r_)),
                    fmt_num(d.get("mAP50")), fmt_num(d.get("mAP50-95"))))
        else:
            tree.insert("", "end", values=(
                "calculando…", "", "", "", "", "", "", ""))

        # speed/FPS desde val_snapshot (o live si existiera)
        spd = val_state["speed"] or s["val_speed"]
        speed_txt.config(state="normal")
        speed_txt.delete("1.0", "end")
        if spd:
            try:
                total = sum(float(spd.get(k, 0)) for k in
                            ("preprocess", "inference", "loss",
                             "postprocess"))
                inf = float(spd.get("inference", 0))
                fps = 1000.0 / inf if inf else 0.0
                speed_txt.insert(
                    "end",
                    f"preproceso {spd.get('preprocess', 0):.1f} ms · "
                    f"inferencia {inf:.1f} ms · pérdida "
                    f"{spd.get('loss', 0):.1f} ms · posproceso "
                    f"{spd.get('postprocess', 0):.1f} ms\n"
                    f"→ {total:.1f} ms/img · ~{fps:.1f} FPS "
                    f"(batch=8, CPU)")
            except (TypeError, ValueError):
                speed_txt.insert("end", "Velocidad de validación: —")
        else:
            speed_txt.insert(
                "end", "Velocidad de validación: — "
                      "(val_snapshot pendiente)")
        speed_txt.config(state="disabled")

        # --- historial por epoca (results.csv) ---
        tree_hist.delete(*tree_hist.get_children())
        for r in rows[-7:]:
            try:
                ep = int(float(r.get("epoch") or 0))
            except ValueError:
                continue
            try:
                lr_txt = f"{float(r.get('lr/pg0') or 0):.2e}"
            except ValueError:
                lr_txt = "-"
            tree_hist.insert("", "end", values=(
                ep,
                fmt_num(r.get("train/box_loss"), 2),
                fmt_num(r.get("train/cls_loss"), 2),
                fmt_num(r.get("train/dfl_loss"), 2),
                fmt_num(r.get("metrics/precision(B)")),
                fmt_num(r.get("metrics/recall(B)")),
                fmt_num(r.get("metrics/mAP50(B)")),
                fmt_num(r.get("metrics/mAP50-95(B)")),
                fmt_num(r.get("val/box_loss"), 2),
                fmt_num(r.get("val/cls_loss"), 2),
                fmt_num(r.get("val/dfl_loss"), 2),
                lr_txt))

        # --- fuentes de datos e hilos daemon ---
        age = s["csv_age"]
        age_txt = f"hace {fmt_hm(age)}" if age is not None else "n/d"
        stale = (age is not None and
                 age > max(600.0, s["avg_ep"] * 1.5))
        src_lines = [
            f"results.csv: {done} épocas completadas · última "
            f"escritura {age_txt}"
            + ("  ⚠ más viejo que el ritmo esperado" if stale else "")]
        if val_state.get("last"):
            va = time.time() - val_state["last"]
            vd = val_state.get("done", -1)
            if vd >= done > 0:
                src_lines.append(
                    f"hilo val_worker (daemon · cada 15 s): vivo · "
                    f"métricas por clase al día (época {vd}) · "
                    f"hace {fmt_hm(va)}")
            else:
                src_lines.append(
                    f"hilo val_worker (daemon · cada 15 s): activo · "
                    f"val_snapshot de la época {done} pendiente "
                    f"(último publicado ép {vd} hace {fmt_hm(va)})")
            if va > 180:
                src_lines[-1] += "  ⚠ sin publicaciones recientes"
        else:
            src_lines.append(
                "hilo val_worker (daemon · cada 15 s): esperando "
                "results.csv")
        if hw_d.get("t"):
            ha = time.time() - hw_d["t"]
            src_lines.append(
                f"hilo hw-monitor (daemon · cada 2 s): vivo · última "
                f"muestra hace {ha:.1f} s")
            if ha > 10:
                src_lines[-1] += "  ⚠ posiblemente detenido"
        else:
            src_lines.append(
                "hilo hw-monitor (daemon · cada 2 s): sin muestras aún")
        log_ok = bool(live.get("it") or live.get("classes"))
        src_lines.append(
            "log stdout: "
            + ("encontrado — iteración/s y ETA en vivo"
               if log_ok else
               "no encontrado — ETA por promedio de épocas"))
        src_lines.append("bucle de refresh de la GUI: cada 5 s")

        # alerta overfitting
        ov = s["overfit_streak"]
        if ov:
            overfit_f.config(
                text=f"⚠ Posible overfitting: val/box_loss sube y "
                     f"train/box_loss baja hace {ov} épocas")
        else:
            overfit_f.config(text="")

        # alerta nueva mejor epoca
        alert = ""
        bm = s["best_map"]
        if prev_best[0] is not None and bm > prev_best[0] + 1e-6:
            alert = "★ NUEVA MEJOR ÉPOCA ★"
        prev_best[0] = bm

        # fuentes de datos e hilos (texto de alto fijo + alerta)
        src_txt.config(state="normal")
        src_txt.delete("1.0", "end")
        for ln in src_lines:
            src_txt.insert("end", ln + "\n")
        if alert:
            src_txt.insert("end", alert + "\n", "bad")
        src_txt.config(state="disabled")

        # columna derecha: explicacion + glosario
        _render_explain(s, tr, hw_d, val_state)
        _render_glossary(s, live, hw_d)
        _fit_body()
        root.after(5000, refresh)

    refresh()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="REBA_outdoor_v5")
    ap.add_argument("--once", action="store_true",
                    help="snapshot por consola, sin GUI")
    ap.add_argument("--log", default=None,
                    help="ruta a un archivo de log del entrenamiento "
                         "(para iter/ETA en tiempo real si stdout no esta "
                         "en devin.exe-overflows)")
    args = ap.parse_args()

    if args.once:
        s = snapshot(args.run, args.log)
        live = s["live"]
        tr = s["trainer"]
        a = s["args"]
        print(f"run={s['run']} epocas_done={s['done']} "
              f"epoca_actual={s['cur']}/{s['epochs_cfg']}")
        print(f"args: imgsz={a.get('imgsz')} batch={a.get('batch')} "
              f"opt={a.get('optimizer')} workers={a.get('workers')} "
              f"patience={a.get('patience')} close_mosaic={a.get('close_mosaic')} "
              f"device={a.get('device')}")
        print(f"progreso_epoca={s['ep_frac']*100:.1f}% "
              f"progreso_total={s['total_frac']*100:.1f}% "
              f"ep_elapsed={fmt_hm(s['ep_elapsed'])}")
        print(f"eta_epoca={fmt_hm(s['eta_ep'])} "
              f"eta_early_stop(~ep{s['stop_ep']})={fmt_hm(s['eta_stop'])} "
              f"eta_total={fmt_hm(s['eta_total'])} "
              f"avg_epoca={fmt_hm(s['avg_ep'])}")
        print(f"mejor_epoca={s['best_ep']} mAP5095={s['best_map']:.4f} "
              f"sin_mejorar={s['since_best']}")
        if tr.get("alive"):
            print(f"trainer=PID{tr['pid']} cpu={tr['cpu_pct']:.0f}% "
                  f"rss={tr['rss_mb']:.0f}MB threads={tr.get('threads')}")
        else:
            print("trainer=NO DETECTADO")
        print(f"overfit_streak={s['overfit_streak']} "
              f"temp_cpu={cpu_temp_c()}")
        print(f"live_iter={live.get('it')}/{live.get('tot')} "
              f"sit={live.get('sit')} "
              f"val_classes={'si' if s['val_classes'] else 'no'}")
        return 0

    import tkinter as tk
    root = tk.Tk()
    build_gui(root, args.run, args.log)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

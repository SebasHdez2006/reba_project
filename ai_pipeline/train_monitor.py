"""Monitor GUI del entrenamiento REBA_outdoor_v5 (tkinter, solo lectura).

Muestra: epoca actual, progreso de epoca y total, ETAs, métricas por clase.

Fuentes de datos:
  - runs/detect/<run>/results.csv  -> metricas por epoca (se escribe al
    cerrar cada epoca; su mtime ≈ inicio de la epoca en curso)
  - runs/detect/<run>/args.yaml    -> epochs, patience
  - stdout del entrenamiento       -> iteraciones y s/it (bufferizado,
    puede tardar varios minutos en volcar; se usa solo como extra)

Uso:
  python ai_pipeline/train_monitor.py [--run REBA_outdoor_v5] [--once]
  (--once imprime un snapshot por consola y sale, para verificar)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs" / "detect"
CLASSES = ["plastico", "unicel", "metal"]

OVERFLOW_DIR = Path(os.environ.get("TEMP", ".")) / "devin.exe-overflows"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
ITER_RE = re.compile(r"(\d+)/(\d+)\s+[\d.]+s/it\s+(\d+):(\d+)<(\d+):(\d+)")
CLASS_RE = re.compile(
    r"^\s*(plastico|unicel|metal|all)\s+(\d+)\s+(\d+)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", re.M)


def read_results(run_dir: Path) -> tuple[list[dict], float | None]:
    """Filas de results.csv + mtime (epoch start aprox)."""
    f = run_dir / "results.csv"
    if not f.exists():
        return [], None
    rows = []
    try:
        with f.open(newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
    except OSError:
        pass
    return rows, f.stat().st_mtime


def read_args(run_dir: Path) -> tuple[int, int]:
    """(epochs, patience) desde args.yaml (parser minimo, sin yaml dep)."""
    f = run_dir / "args.yaml"
    epochs, patience = 50, 20
    if f.exists():
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*(epochs|patience):\s*(\d+)", ln)
            if m:
                if m.group(1) == "epochs":
                    epochs = int(m.group(2))
                else:
                    patience = int(m.group(2))
    return epochs, patience


def find_train_log(run_name: str) -> Path | None:
    """Localiza el content.txt del shell que corre el entrenamiento.

    Requiere 'Logging results to' + nombre del run (ambos quedan en la
    cabecera del log) y que el archivo se haya escrito hace <15 min,
    para no confundirlo con shells de monitoreo que tambien imprimen
    lineas de progreso.
    """
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


def parse_live(tail: str) -> dict:
    """Ultima iteracion visible + metricas por clase de la ultima val."""
    out = {"epoch": None, "it": None, "tot": None, "sit": None,
           "eta_s": None, "classes": {}}
    # solo iteraciones de train (tot>50); las de val son 4/4, 2/2, etc.
    iters = [t for t in ITER_RE.findall(tail) if int(t[1]) > 50]
    if iters:
        it, tot, mm, ss, em, es = iters[-1]
        out.update(it=int(it), tot=int(tot),
                   eta_s=int(em) * 60 + int(es))
        ep = re.findall(r"^\s*(\d+)/\d+\s+0G", tail, re.M)
        if ep:
            out["epoch"] = int(ep[-1])
    # bloque por clase: ultimas 4 lineas que casan (3 clases + all)
    cls_lines = CLASS_RE.findall(tail)
    for name, _img, _inst, p, r, m50, m5095 in cls_lines[-4:]:
        out["classes"][name] = {
            "P": float(p), "R": float(r),
            "mAP50": float(m50), "mAP50-95": float(m5095)}
    return out


def fmt_hm(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def snapshot(run_name: str) -> dict:
    run_dir = RUNS / run_name
    rows, csv_mtime = read_results(run_dir)
    epochs_cfg, patience = read_args(run_dir)
    done = len(rows)
    cur_epoch = done + 1

    # duracion media por epoca (columna time = segundos acumulados)
    times = []
    for r in rows:
        try:
            times.append(float(r.get("time") or 0))
        except ValueError:
            pass
    durs = [times[0]] + [b - a for a, b in zip(times, times[1:])]
    durs = [d for d in durs if d > 60]
    avg_ep = sum(durs) / len(durs) if durs else 51 * 60

    # progreso dentro de la epoca en curso (estimado por tiempo)
    elapsed_ep = time.time() - csv_mtime if csv_mtime else 0
    ep_frac = min(elapsed_ep / avg_ep, 0.98) if done else 0.0

    total_frac = min((done + ep_frac) / epochs_cfg, 1.0)
    remaining_ep_sec = max(0.0, avg_ep - elapsed_ep)
    eta_total = remaining_ep_sec + max(0, epochs_cfg - cur_epoch) * avg_ep

    # mejor epoca por mAP50-95
    best_i, best_map = -1, -1.0
    for i, r in enumerate(rows):
        try:
            v = float(r.get("metrics/mAP50-95(B)") or 0)
        except ValueError:
            v = 0.0
        if v > best_map:
            best_map, best_i = v, i
    since_best = done - (best_i + 1) if best_i >= 0 else done

    live = parse_live(read_log_tail(find_train_log(run_name)))
    return {
        "run": run_name, "done": done, "cur": cur_epoch,
        "epochs_cfg": epochs_cfg, "patience": patience,
        "ep_frac": ep_frac, "total_frac": total_frac,
        "eta_ep": remaining_ep_sec, "eta_total": eta_total,
        "avg_ep": avg_ep, "best_ep": best_i + 1, "best_map": best_map,
        "since_best": since_best, "rows": rows[-8:], "live": live,
        "csv_age": time.time() - csv_mtime if csv_mtime else None,
    }


def fmt_num(v, nd=3):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


# ---------------- GUI ----------------

def build_gui(root, run_name: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    root.title(f"REBA — Monitor {run_name}")
    root.geometry("560x720")
    root.configure(bg="#1e1e1e")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TLabel", background="#1e1e1e", foreground="#e8e8e8",
                    font=("Consolas", 10))
    style.configure("Title.TLabel", font=("Consolas", 13, "bold"),
                    foreground="#7ec8ff")
    style.configure("Big.TLabel", font=("Consolas", 22, "bold"),
                    foreground="#ffd479")
    style.configure("Treeview", background="#2d2d2d", fieldbackground="#2d2d2d",
                    foreground="#e8e8e8", rowheight=22)
    style.configure("Treeview.Heading", background="#3d3d3d",
                    foreground="#e8e8e8")
    style.map("Treeview", background=[("selected", "#3a6ea5")])

    FRM = {"padx": 8, "pady": 2}

    ttk.Label(root, text=f"Entrenamiento {run_name} (YOLOv8n, 3 clases)",
             style="Title.TLabel").pack(anchor="w", **FRM)

    ep_big = ttk.Label(root, text="--", style="Big.TLabel")
    ep_big.pack(anchor="w", padx=8)

    bar_ep = ttk.Progressbar(root, length=520, mode="determinate")
    bar_ep.pack(padx=8, pady=(0, 4))
    lbl_ep = ttk.Label(root, text="", style="TLabel")
    lbl_ep.pack(anchor="w", padx=8)

    bar_tot = ttk.Progressbar(root, length=520, mode="determinate")
    bar_tot.pack(padx=8, pady=(8, 2))
    lbl_tot = ttk.Label(root, text="", style="TLabel")
    lbl_tot.pack(anchor="w", padx=8)

    eta_f = ttk.Label(root, text="", style="TLabel", justify="left")
    eta_f.pack(anchor="w", padx=8, pady=(6, 0))

    # tabla metricas por clase (ultima val)
    ttk.Label(root, text="Última validación — por clase (mAP50-95):",
             style="TLabel").pack(anchor="w", padx=8, pady=(12, 0))
    tree = ttk.Treeview(root, columns=("P", "R", "mAP50", "mAP50-95"),
                        show="headings", height=5)
    for c, w in (("P", 70), ("R", 70), ("mAP50", 80), ("mAP50-95", 90)):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="center")
    tree.pack(padx=8, fill="x")

    ttk.Label(root, text="Historial (últimas épocas):",
             style="TLabel").pack(anchor="w", padx=8, pady=(10, 0))
    tree2 = ttk.Treeview(root, columns=("ep", "P", "R", "mAP50", "mAP50-95"),
                         show="headings", height=7)
    for c, w in (("ep", 45), ("P", 70), ("R", 70), ("mAP50", 80),
                 ("mAP50-95", 90)):
        tree2.heading(c, text=c)
        tree2.column(c, width=w, anchor="center")
    tree2.pack(padx=8, fill="x")

    status = ttk.Label(root, text="", style="TLabel", justify="left",
                       foreground="#9cdcfe")
    status.pack(anchor="w", padx=8, pady=(10, 0))

    def refresh() -> None:
        try:
            s = snapshot(run_name)
        except Exception as e:  # noqa: BLE001 - GUI nunca debe morir
            status.config(text=f"error leyendo datos: {e}")
            root.after(5000, refresh)
            return

        done, cur, cfg = s["done"], s["cur"], s["epochs_cfg"]
        ep_big.config(text=f"Época {cur}/{cfg}" if done < cfg else "Finalizado")
        bar_ep["value"] = s["ep_frac"] * 100
        live = s["live"]
        # solo mostrar iter si el log corresponde a la epoca en curso
        live_fresh = live.get("it") and live.get("epoch") == cur
        it_txt = (f"iter {live['it']}/{live['tot']}"
                  if live_fresh else "iter — (log bufferizado)")
        eta_ep_txt = fmt_hm(s["eta_ep"])
        if live_fresh and live.get("eta_s") is not None:
            eta_ep_txt += f" (log: {fmt_hm(live['eta_s'])})"
        lbl_ep.config(
            text=f"{it_txt} · ~{s['ep_frac']*100:.0f}% época · "
                 f"ETA época: {eta_ep_txt}")

        bar_tot["value"] = s["total_frac"] * 100
        lbl_tot.config(
            text=f"Total: {s['total_frac']*100:.1f}% · "
                 f"época prom. {fmt_hm(s['avg_ep'])} · "
                 f"ETA total (si llega a {cfg}): {fmt_hm(s['eta_total'])}")

        eta_f.config(
            text=(f"ETA época actual: {fmt_hm(s['eta_ep'])}\n"
                  f"ETA entrenamiento completo: {fmt_hm(s['eta_total'])}\n"
                  f"(early stop patience={s['patience']} puede recortarlo)"))

        # tabla clases
        tree.delete(*tree.get_children())
        cl = live.get("classes") or {}
        for name in CLASSES + ["all"]:
            d = cl.get(name)
            if d:
                tree.insert("", "end", values=(
                    name, fmt_num(d["P"]), fmt_num(d["R"]),
                    fmt_num(d["mAP50"]), fmt_num(d["mAP50-95"])))

        # historial
        tree2.delete(*tree2.get_children())
        for r in s["rows"]:
            try:
                ep = int(float(r.get("epoch") or 0))
            except ValueError:
                continue
            tree2.insert("", "end", values=(
                ep, fmt_num(r.get("metrics/precision(B)")),
                fmt_num(r.get("metrics/recall(B)")),
                fmt_num(r.get("metrics/mAP50(B)")),
                fmt_num(r.get("metrics/mAP50-95(B)"))))

        age = s["csv_age"]
        age_txt = f"hace {fmt_hm(age)}" if age is not None else "n/a"
        status.config(
            text=(f"Mejor época: {s['best_ep']} "
                  f"(mAP50-95={fmt_num(s['best_map'])}) · "
                  f"sin mejorar hace {s['since_best']} época(s) "
                  f"(early stop a {s['patience']}) · "
                  f"results.csv escrito {age_txt} · refresh 5s"))
        root.after(5000, refresh)

    refresh()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="REBA_outdoor_v5")
    ap.add_argument("--once", action="store_true",
                    help="snapshot por consola, sin GUI")
    args = ap.parse_args()

    if args.once:
        s = snapshot(args.run)
        live = s["live"]
        print(f"run={s['run']} epocas_done={s['done']} "
              f"epoca_actual={s['cur']}/{s['epochs_cfg']}")
        print(f"progreso_epoca={s['ep_frac']*100:.1f}% "
              f"progreso_total={s['total_frac']*100:.1f}%")
        print(f"eta_epoca={fmt_hm(s['eta_ep'])} "
              f"eta_total={fmt_hm(s['eta_total'])} "
              f"avg_epoca={fmt_hm(s['avg_ep'])}")
        print(f"mejor_epoca={s['best_ep']} mAP5095={s['best_map']:.4f} "
              f"sin_mejorar={s['since_best']}")
        print(f"live_iter={live.get('it')}/{live.get('tot')} "
              f"classes={live.get('classes')}")
        return 0

    import tkinter as tk
    root = tk.Tk()
    build_gui(root, args.run)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

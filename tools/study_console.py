"""IDE-style live console for the ZedBoard topology sweep.

Select a dataset, press Run, and the window fills as each topology finishes
on the FPGA. Every line is written under results/sweeps while the run is
still going.
"""

from __future__ import annotations

import csv
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_PATH = os.path.join(ROOT, "cases.json")
BUILD_DIR = os.path.join(ROOT, "build")
VVP_PATH = os.path.join(BUILD_DIR, "study_top.vvp")
RTL_SOURCES = [
    "rtl/study_top.v",
    "rtl/snn_engine.v",
    "rtl/study_rom.v",
    "rtl/uart_tx.v",
    "rtl/uart_rx.v",
    "tb/study_top_tb.v",
]

PAPER = "#1e1e1e"
INK = "#d4d4d4"
RULE = "#3c3c3c"
FAINT = "#858585"
CARD = "#252526"
RED = "#569cd6"
GREEN = "#4ec9b0"
LINE = "#3c3c3c"
ACCENT = "#007acc"
FIELD = "#3c3c3c"
SELECT = "#264f78"

WIDTHS = (2, 4, 6, 8)
LAYERS = (1, 2, 3)


def load_cases() -> list[dict]:
    with open(CASES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def shape_text(layers: int, widths: list[int]) -> str:
    return "   ".join(f"L{i + 1}:{widths[i]}" for i in range(layers))


def build_shapes() -> list[dict]:
    shapes = []

    def add(layers: int, widths: list[int]) -> None:
        shapes.append({"layers": layers, "widths": widths, "label": shape_text(layers, widths)})

    for width in WIDTHS:
        add(1, [width, 0, 0])
    for second in WIDTHS:
        for first in WIDTHS:
            add(2, [first, second, 0])
    for third in WIDTHS:
        for second in WIDTHS:
            for first in WIDTHS:
                add(3, [first, second, third])
    return shapes


def icarus_installed() -> bool:
    bundled = r"C:\iverilog\bin\iverilog.exe"
    bundled_vvp = r"C:\iverilog\bin\vvp.exe"
    if os.path.isfile(bundled) and os.path.isfile(bundled_vvp):
        return True
    return bool(shutil.which("iverilog") and shutil.which("vvp"))


def default_port() -> str:
    return "Icarus" if icarus_installed() else "COM5"


def neurons(layers: int, widths: list[int]) -> int:
    return sum(widths[:layers])


def energy_of(row: dict) -> int:
    """Mux events plus one output addition per class for each last-layer spike."""
    hidden = row.get("spikes_h") or [0, 0, 0]
    layers = row.get("layers") or 0
    mux_events = sum(hidden[:layers])
    last = hidden[layers - 1] if 1 <= layers <= 3 else 0
    return mux_events + last * int(row.get("n_class") or 0)


class Logger:
    def __init__(self, folder: str):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.raw = open(os.path.join(folder, "uart_log.txt"), "w", encoding="utf-8", newline="\n")
        self.metrics_path = os.path.join(folder, "metrics.csv")
        self.trials_path = os.path.join(folder, "trials.csv")
        self.mux_path = os.path.join(folder, "mux.csv")
        self.raster_path = os.path.join(folder, "rasters.csv")
        self._metrics = open(self.metrics_path, "w", newline="", encoding="utf-8")
        self._trials = open(self.trials_path, "w", newline="", encoding="utf-8")
        self._mux = open(self.mux_path, "w", newline="", encoding="utf-8")
        self._raster = open(self.raster_path, "w", newline="", encoding="utf-8")
        self.metrics = csv.writer(self._metrics)
        self.trials = csv.writer(self._trials)
        self.mux = csv.writer(self._mux)
        self.raster = csv.writer(self._raster)
        self.metrics.writerow([
            "case", "mode", "layers", "h0", "h1", "h2", "n_in", "n_class", "n_test", "correct",
            "util0", "util1", "util2", "spikes_in", "spikes_h0", "spikes_h1", "spikes_h2", "spikes_out",
            "neurons", "energy",
        ])
        self.trials.writerow(["case", "mode", "layers", "h0", "h1", "h2", "sample", "group", "label", "pred"])
        self.mux.writerow(["case", "mode", "layers", "h0", "h1", "h2"] + [f"m{i}" for i in range(24)])
        self.raster.writerow(["case", "mode", "layers", "h0", "h1", "h2", "sample", "step", "inputs", "hidden", "outputs"])
        self._flush()

    def _flush(self) -> None:
        for handle in (self.raw, self._metrics, self._trials, self._mux, self._raster):
            handle.flush()

    def line(self, text: str) -> None:
        self.raw.write(text + "\n")
        self.raw.flush()

    def close(self) -> None:
        for handle in (self.raw, self._metrics, self._trials, self._mux, self._raster):
            handle.close()


def parse_line(text: str) -> dict | None:
    part = text.strip().split(",")
    if not part or not part[0]:
        return None
    kind = part[0]
    try:
        if kind == "H" and len(part) == 7:
            return {"kind": "H", "case": int(part[1], 16), "mode": int(part[2], 16),
                    "layers": int(part[3], 16),
                    "widths": [int(part[4], 16), int(part[5], 16), int(part[6], 16)]}
        if kind == "M" and len(part) == 19:
            return {
                "kind": "M", "case": int(part[1], 16), "mode": int(part[2], 16),
                "layers": int(part[3], 16),
                "widths": [int(part[4], 16), int(part[5], 16), int(part[6], 16)],
                "n_in": int(part[7], 16), "n_class": int(part[8], 16),
                "n_test": int(part[9], 16), "correct": int(part[10], 16),
                "util": [int(part[11], 16), int(part[12], 16), int(part[13], 16)],
                "spikes_in": int(part[14], 16),
                "spikes_h": [int(part[15], 16), int(part[16], 16), int(part[17], 16)],
                "spikes_out": int(part[18], 16),
            }
        if kind == "R" and len(part) == 7:
            return {"kind": "R", "case": int(part[1], 16), "mode": int(part[2], 16),
                    "sample": int(part[3], 16), "group": int(part[4], 16),
                    "label": int(part[5], 16), "pred": int(part[6], 16)}
        if kind == "T" and len(part) == 8:
            return {"kind": "T", "case": int(part[1], 16), "mode": int(part[2], 16),
                    "sample": int(part[3], 16), "step": int(part[4], 16),
                    "inputs": int(part[5], 16), "hidden": int(part[6], 16),
                    "outputs": int(part[7], 16)}
        if kind == "X" and len(part) == 8:
            hexes = part[7]
            mux = [int(hexes[i:i + 2], 16) for i in range(0, min(len(hexes), 48), 2)]
            return {"kind": "X", "case": int(part[1], 16), "mode": int(part[2], 16),
                    "layers": int(part[3], 16),
                    "widths": [int(part[4], 16), int(part[5], 16), int(part[6], 16)],
                    "mux": mux}
        if kind == "Z":
            return {"kind": "Z"}
    except ValueError:
        return {"kind": "bad", "text": text}
    return None


class PaperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("topology sweep study v1.1")
        self.geometry("1180x760")
        self.configure(bg=PAPER)
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", INK)
        self.option_add("*TCombobox*Listbox.selectBackground", SELECT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "TCombobox",
            fieldbackground=FIELD,
            background=CARD,
            foreground=INK,
            arrowcolor=INK,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", INK)],
            selectbackground=[("readonly", SELECT)],
            selectforeground=[("readonly", "#ffffff")],
        )
        self.cases = load_cases()
        self.by_id = {row["id"]: row for row in self.cases}
        self.shapes = build_shapes()
        self.events: queue.Queue = queue.Queue()
        self.reader = None
        self.stop = threading.Event()
        self.logger: Logger | None = None
        self.current = None
        self.rows: list[dict] = []
        self.trials: list[dict] = []
        self.rasters: list[dict] = []
        self.pending_topo = None
        self.sample_rows: dict[tuple, list[dict]] = {}
        self.shown_key = None
        self.now = None
        self._build()
        self._show_example()
        self.after(80, self._poll)

    def _font(self, size: int, bold: bool = False) -> tuple:
        return ("Segoe UI", size, "bold" if bold else "normal")

    def _build(self) -> None:
        mast = tk.Frame(self, bg=PAPER)
        mast.pack(fill="x", padx=28, pady=(18, 0))
        tk.Label(mast, text="topology sweep study v1.1", bg=PAPER, fg=INK, font=self._font(16)).pack(anchor="w")
        tk.Frame(self, bg=INK, height=1).pack(fill="x", padx=28, pady=(8, 0))
        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill="x", padx=28, pady=(10, 0))
        self.tabs = {}
        self.pages = {}
        for name in ("Run", "Topology", "Results"):
            button = tk.Button(
                bar, text=name, command=lambda n=name: self._show(n),
                bg=CARD, fg=FAINT, activebackground=PAPER, activeforeground="#ffffff",
                relief="flat", bd=0, font=self._font(13), padx=12, pady=6,
            )
            button.pack(side="left")
            self.tabs[name] = button
            page = tk.Frame(self, bg=PAPER)
            self.pages[name] = page
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=28, pady=(4, 0))
        self._build_run()
        self._build_topology()
        self._build_results()
        if icarus_installed():
            idle = "Idle. Port Icarus simulates here. A COM name, such as COM5, runs the ZedBoard and does not use Icarus."
        else:
            idle = "Idle. Type the USB-serial COM port. This PC runs the ZedBoard. Icarus is not required."
        self.status = tk.Label(
            self, text=idle,
            bg=ACCENT, fg="#ffffff", font=("Segoe UI", 10), anchor="w", padx=12, pady=4,
        )
        self.status.pack(side="bottom", fill="x")
        self._show("Run")

    def _show(self, name: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True, padx=28, pady=12)
        for key, button in self.tabs.items():
            button.configure(
                fg="#ffffff" if key == name else FAINT,
                bg=PAPER if key == name else CARD,
                font=self._font(13, key == name),
            )
        if name == "Results":
            self._fill_results()
        if name == "Topology":
            self._fill_topology_choices()

    def _build_run(self) -> None:
        page = self.pages["Run"]
        top = tk.Frame(page, bg=PAPER)
        top.pack(fill="x")
        tk.Label(page, text="1    Experiment", bg=PAPER, fg=INK, font=self._font(16)).pack(anchor="w", pady=(0, 6))
        tk.Label(top, text="Dataset", bg=PAPER, fg=FAINT, font=self._font(11)).pack(side="left")
        self.dataset = ttk.Combobox(top, state="readonly", width=28, font=self._font(12))
        names = ["All datasets"] + [row["name"] for row in self.cases]
        self.dataset["values"] = names
        self.dataset.current(0)
        self.dataset.pack(side="left", padx=(8, 16))
        self.dataset.bind("<<ComboboxSelected>>", self._preview_selection)
        tk.Label(top, text="Topology", bg=PAPER, fg=FAINT, font=self._font(11)).pack(side="left")
        self.topology = ttk.Combobox(top, state="readonly", width=24, font=self._font(12))
        self.topology["values"] = ["All topologies"] + [shape["label"] for shape in self.shapes]
        self.topology.current(0)
        self.topology.pack(side="left", padx=(8, 16))
        self.topology.bind("<<ComboboxSelected>>", self._preview_selection)
        tk.Label(top, text="Port", bg=PAPER, fg=FAINT, font=self._font(11)).pack(side="left")
        self.port = tk.Entry(
            top, width=12, bg=FIELD, fg=INK, insertbackground=INK, relief="flat",
            highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
            font=("Consolas", 12),
        )
        self.port.insert(0, default_port())
        self.port.pack(side="left", padx=(8, 16))
        self.run_button = tk.Button(
            top, text="Run", command=self._start,
            bg=ACCENT, fg="#ffffff", activebackground="#1177bb", activeforeground="#ffffff",
            relief="flat", font=self._font(12, True), padx=16, pady=4,
        )
        self.run_button.pack(side="left")
        body = tk.Frame(page, bg=PAPER)
        body.pack(fill="both", expand=True, pady=(16, 0))
        left = tk.Frame(body, bg=PAPER)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="Topologies, as the board finishes them", bg=PAPER, fg=FAINT, font=self._font(11)).pack(anchor="w")
        self.live = tk.Listbox(
            left, bg=CARD, fg=INK, selectbackground=SELECT, selectforeground="#ffffff",
            relief="flat", highlightthickness=1, highlightbackground=LINE,
            font=("Consolas", 11), activestyle="none",
        )
        self.live.pack(fill="both", expand=True, pady=(4, 0))
        self.live.bind("<<ListboxSelect>>", self._select_live)
        right = tk.Frame(body, bg=PAPER)
        right.pack(side="right", fill="both", expand=True, padx=(16, 0))
        self.net = tk.Canvas(right, bg=CARD, height=420, highlightthickness=1, highlightbackground=LINE)
        self.net.pack(fill="both", expand=True)
        self.net.bind("<Configure>", lambda _e: self._draw_live())
        tk.Label(right, text="Test inputs    label  →  prediction", bg=PAPER, fg=FAINT, font=self._font(11)).pack(anchor="w", pady=(8, 2))
        self.samples = tk.Canvas(right, bg=CARD, height=118, highlightthickness=1, highlightbackground=LINE)
        self.samples.pack(fill="x")
        self.sample_scroll = tk.Scrollbar(right, orient="horizontal", command=self.samples.xview)
        self.samples.configure(xscrollcommand=self.sample_scroll.set)
        self.sample_scroll.pack(fill="x")
        inner = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        inner.pack(fill="x", pady=(8, 0))
        tk.Label(inner, text="Score", bg=CARD, fg=RED, font=self._font(11, True)).pack(anchor="w", padx=12, pady=(8, 0))
        self.score_title = tk.Label(inner, text="—", bg=CARD, fg=INK, font=self._font(14), wraplength=460, justify="left")
        self.score_title.pack(anchor="w", padx=12, pady=(4, 4))
        self.score_body = tk.Label(inner, text="No topology yet.", bg=CARD, fg=INK, font=self._font(12), justify="left", wraplength=460)
        self.score_body.pack(anchor="w", padx=12, pady=(0, 10))

    def _build_topology(self) -> None:
        page = self.pages["Topology"]
        tk.Label(page, text="2    One topology", bg=PAPER, fg=INK, font=self._font(16)).pack(anchor="w")
        row = tk.Frame(page, bg=PAPER)
        row.pack(fill="x", pady=(8, 8))
        self.topo_pick = ttk.Combobox(row, state="readonly", width=42, font=self._font(12))
        self.topo_pick.pack(side="left")
        self.topo_pick.bind("<<ComboboxSelected>>", lambda _e: self._draw_topology())
        self.topo_text = tk.Label(page, text="", bg=PAPER, fg=INK, font=self._font(12), justify="left", anchor="w")
        self.topo_text.pack(fill="x")
        self.topo_net = tk.Canvas(page, bg=CARD, height=280, highlightthickness=1, highlightbackground=LINE)
        self.topo_net.pack(fill="x", pady=(8, 0))
        self.topo_net.bind("<Configure>", lambda _e: self._draw_topology())
        self.canvas = tk.Canvas(page, bg=CARD, height=220, highlightthickness=1, highlightbackground=LINE)
        self.canvas.pack(fill="both", expand=True, pady=(8, 0))

    def _build_results(self) -> None:
        page = self.pages["Results"]
        tk.Label(page, text="3    Which topology to keep", bg=PAPER, fg=INK, font=self._font(16)).pack(anchor="w")
        tk.Label(
            page,
            text="Best learned shape by accuracy, then the cheapest learned shape that reaches that same score. Energy counts one mux event per hidden spike and one addition into each class for every last-layer spike.",
            bg=PAPER, fg=FAINT, font=self._font(12), wraplength=1080, justify="left",
        ).pack(anchor="w", pady=(4, 8))
        self.results = tk.Text(
            page, bg=CARD, fg=INK, insertbackground=INK, relief="flat", font=("Consolas", 11),
            highlightthickness=1, highlightbackground=LINE, wrap="none",
            selectbackground=SELECT, selectforeground="#ffffff",
        )
        self.results.pack(fill="both", expand=True)

    def _start(self) -> None:
        if self.reader and self.reader.is_alive():
            return
        choice = self.dataset.get()
        if choice == "All datasets":
            case_byte = ord("A")
            label = "all datasets"
        else:
            case_id = next(row["id"] for row in self.cases if row["name"] == choice)
            case_byte = ord("0") + case_id
            label = choice
        topo_name = self.topology.get()
        if topo_name == "All topologies":
            topo_byte = 0xFF
            label = f"{label}, all topologies"
        else:
            topo_byte = next(index for index, shape in enumerate(self.shapes) if shape["label"] == topo_name)
            label = f"{label}, {topo_name}, learned then frozen"
        command = bytes([0xFE, case_byte, topo_byte])
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(ROOT, "results", "sweeps", stamp)
        try:
            self.logger = Logger(folder)
        except OSError as exc:
            self.status.configure(text=str(exc))
            return
        self.rows.clear()
        self.trials.clear()
        self.rasters.clear()
        self.sample_rows.clear()
        self.shown_key = None
        self._draw_samples()
        self.live.delete(0, "end")
        self.current = None
        self.pending_topo = None
        self.now = {
            "title": "Waiting for the first shape",
            "layers": 0, "widths": [0, 0, 0], "n_in": 0, "n_class": 0,
            "mux": None, "learned": False,
        }
        self._draw_live()
        self._set_score(None)
        self.stop.clear()
        port = self.port.get().strip()
        target = self._read_icarus if port.lower() == "icarus" else self._read_serial
        self.reader = threading.Thread(target=target, args=(port, command), daemon=True)
        self.reader.start()
        self.run_button.configure(state="disabled")
        self.status.configure(text=f"Running {label}. Writing {folder}")

    def _read_serial(self, port: str, command: bytes) -> None:
        try:
            import serial
        except ImportError:
            self.events.put(("status", "pyserial is not installed. pip install pyserial"))
            self.events.put(("done", None))
            return
        try:
            ser = serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:
            self.events.put(("status", f"Could not open {port}: {exc}"))
            self.events.put(("done", None))
            return
        ser.reset_input_buffer()
        time.sleep(0.2)
        ser.write(command)
        buf = ""
        quiet = time.time()
        try:
            while not self.stop.is_set():
                data = ser.read(4096)
                if not data:
                    if not self.rows and time.time() - quiet > 20:
                        self.events.put(("status", "No UART yet. JA1 goes to adapter RX, JA2 to adapter TX. No switch is used."))
                        break
                    if self.rows and time.time() - quiet > 8:
                        break
                    continue
                quiet = time.time()
                buf += data.decode("ascii", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip("\r")
                    if not line:
                        continue
                    if self.logger:
                        self.logger.line(line)
                    parsed = parse_line(line)
                    if parsed:
                        self.events.put(("line", parsed))
                    if parsed and parsed.get("kind") == "Z":
                        self.events.put(("done", None))
                        ser.close()
                        return
        finally:
            try:
                ser.close()
            except Exception:
                pass
        self.events.put(("done", None))

    def _icarus_tool(self, names: tuple[str, ...]) -> str | None:
        for name in names:
            if os.path.isfile(name):
                return name
            found = shutil.which(name)
            if found:
                return found
        return None

    def _ensure_vvp(self) -> str | None:
        iverilog = self._icarus_tool((r"C:\iverilog\bin\iverilog.exe", "iverilog"))
        vvp = self._icarus_tool((r"C:\iverilog\bin\vvp.exe", "vvp"))
        if not iverilog or not vvp:
            self.events.put(("status", "Icarus is not installed. Expected C:\\iverilog\\bin\\iverilog.exe"))
            return None
        sources = [os.path.join(ROOT, rel) for rel in RTL_SOURCES]
        newest = max(os.path.getmtime(path) for path in sources)
        if not os.path.isfile(VVP_PATH) or os.path.getmtime(VVP_PATH) < newest:
            self.events.put(("status", "Compiling the sweep with Icarus."))
            os.makedirs(BUILD_DIR, exist_ok=True)
            done = subprocess.run(
                [iverilog, "-g2012", "-o", VVP_PATH, *sources],
                cwd=ROOT, capture_output=True, text=True,
            )
            if done.returncode != 0:
                detail = (done.stderr or done.stdout or "compile failed").strip().splitlines()
                self.events.put(("status", detail[-1][:180]))
                return None
        return vvp

    def _take_line(self, line: str) -> bool:
        if self.logger:
            self.logger.line(line)
        parsed = parse_line(line)
        if parsed:
            self.events.put(("line", parsed))
        return bool(parsed and parsed.get("kind") == "Z")

    def _read_icarus(self, _port: str, command: bytes) -> None:
        vvp = self._ensure_vvp()
        if not vvp:
            self.events.put(("done", None))
            return
        self.events.put(("status", "Icarus is running this selection. The list fills as each shape finishes."))
        proc = subprocess.Popen(
            [vvp, VVP_PATH, f"+CASE={command[1]}", f"+TOPO={command[2]}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if self.stop.is_set():
                    break
                line = raw.strip("\r\n")
                if not line:
                    continue
                if self._take_line(line):
                    break
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.terminate()
        self.events.put(("done", None))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "line":
                    self._on_line(payload)
                elif kind == "status":
                    self.status.configure(text=payload)
                elif kind == "done":
                    self._finish()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _same_shape(self, left: dict, right: dict) -> bool:
        return (
            left.get("case") == right.get("case")
            and left.get("mode") == right.get("mode")
            and left.get("layers") == right.get("layers")
            and list(left.get("widths", [])) == list(right.get("widths", []))
        )

    def _on_line(self, row: dict) -> None:
        kind = row["kind"]
        if kind == "H":
            self.pending_topo = row
            case = self.by_id.get(row["case"], {})
            name = case.get("name", str(row["case"]))
            mode = "reconfig" if row["mode"] else "fixed"
            self.status.configure(text=f"{name}   {shape_text(row['layers'], row['widths'])}   {mode}")
            self.now = {
                "title": f"{name}    {shape_text(row['layers'], row['widths'])}    {mode}",
                "layers": row["layers"],
                "widths": list(row["widths"]),
                "n_in": case.get("n_in", 4),
                "n_class": case.get("n_class", 4),
                "mux": None,
                "learned": False,
                "case": row["case"],
                "mode": row["mode"],
            }
            self.shown_key = self._shape_key(row)
            self.sample_rows[self.shown_key] = []
            self._draw_live()
            self._draw_samples()
        elif kind == "M":
            if self.pending_topo and (
                self.pending_topo["case"] == row["case"] and self.pending_topo["mode"] == row["mode"]
            ):
                row["layers"] = self.pending_topo["layers"]
                row["widths"] = list(self.pending_topo["widths"])
            row.setdefault("mux", None)
            self.rows.append(row)
            if self.logger:
                widths = row["widths"]
                self.logger.metrics.writerow([
                    row["case"], "reconfig" if row["mode"] else "fixed", row["layers"], *widths,
                    row["n_in"], row["n_class"], row["n_test"], row["correct"],
                    *row["util"], row["spikes_in"], *row["spikes_h"], row["spikes_out"],
                    neurons(row["layers"], row["widths"]), energy_of(row),
                ])
                self.logger._flush()
            self._append_live(row)
            self._set_score(row)
        elif kind == "R":
            if self.pending_topo:
                row["layers"] = self.pending_topo["layers"]
                row["widths"] = list(self.pending_topo["widths"])
            self.trials.append(row)
            key = self._shape_key(row)
            case = self.by_id.get(row["case"], {})
            shown = {
                "label": row["label"], "pred": row["pred"], "bits": None,
                "sample": row["sample"], "n_in": case.get("n_in", 4),
            }
            self.sample_rows.setdefault(key, []).append(shown)
            row["_shown"] = shown
            if self.logger:
                widths = row.get("widths", [0, 0, 0])
                self.logger.trials.writerow([
                    row["case"], row["mode"], row.get("layers", ""), *widths,
                    row["sample"], row["group"], row["label"], row["pred"],
                ])
            if key == self.shown_key:
                self._draw_samples()
        elif kind == "T":
            if self.pending_topo:
                row["layers"] = self.pending_topo["layers"]
                row["widths"] = list(self.pending_topo["widths"])
            self.rasters.append(row)
            if self.logger:
                widths = row.get("widths", [0, 0, 0])
                self.logger.raster.writerow([
                    row["case"], row["mode"], row.get("layers", ""), *widths,
                    row["sample"], row["step"],
                    f"{row['inputs']:016x}", f"{row['hidden']:06x}", f"{row['outputs']:03x}",
                ])
            if row.get("step") == 0 and self.pending_topo:
                key = self._shape_key(row)
                for shown in reversed(self.sample_rows.get(key, [])):
                    if shown["sample"] == row["sample"] and shown["bits"] is None:
                        shown["bits"] = row["inputs"]
                        break
                if key == self.shown_key:
                    self._draw_samples()
        elif kind == "X":
            mux = row.get("mux", [])
            for saved in reversed(self.rows):
                if self._same_shape(saved, row):
                    saved["mux"] = mux
                    break
            if self.now and self.now.get("layers") == row["layers"] and list(self.now.get("widths", [])) == list(row["widths"]):
                self.now["mux"] = mux
                self.now["learned"] = row["mode"] == 1
                self._draw_live()
            if self.logger:
                widths = row["widths"]
                self.logger.mux.writerow([row["case"], row["mode"], row["layers"], *widths, *mux])
                self.logger._flush()
        elif kind == "Z":
            self.status.configure(text="The board finished this sweep.")

    def _append_live(self, row: dict) -> None:
        name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
        mode = "reconfig" if row["mode"] else "fixed"
        acc = f"{row['correct']}/{row['n_test']}"
        used = row["util"][0] + row["util"][1] + row["util"][2]
        cost = energy_of(row)
        text = f"{name:<18} {shape_text(row['layers'], row['widths']):<18} {mode:<9} {acc:>6}   energy {cost:<6} used {used}"
        self.live.insert("end", text)
        self.live.see("end")

    def _set_score(self, row: dict | None) -> None:
        if row is None:
            self.score_title.configure(text="—")
            self.score_body.configure(text="No topology yet.")
            return
        name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
        mode = "Reconfigurable" if row["mode"] else "Fixed"
        self.score_title.configure(text=f"{name}    {shape_text(row['layers'], row['widths'])}    {mode}")
        shape_n = neurons(row["layers"], row["widths"])
        used = sum(row["util"])
        cost = energy_of(row)
        per = f"{cost / row['correct']:.0f}" if row["correct"] else "—"
        hidden = sum(row["spikes_h"][:row["layers"]])
        self.score_body.configure(
            text=(
                f"Correct   {row['correct']} / {row['n_test']}\n"
                f"Neurons   {shape_n} in the shape, {used} spiked\n"
                f"Spikes   {row['spikes_in']} input, {hidden} hidden, {row['spikes_out']} output\n"
                f"Energy   {cost}    {per} per correct answer\n"
                f"One mux event per hidden spike, plus one addition into each class for every last-layer spike."
            )
        )

    def _select_live(self, _event) -> None:
        sel = self.live.curselection()
        if not sel or sel[0] >= len(self.rows):
            return
        row = self.rows[sel[0]]
        self._set_score(row)
        name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
        mode = "reconfig" if row["mode"] else "fixed"
        self.now = {
            "title": f"{name}    {shape_text(row['layers'], row['widths'])}    {mode}",
            "layers": row["layers"],
            "widths": list(row["widths"]),
            "n_in": row["n_in"],
            "n_class": row["n_class"],
            "mux": row.get("mux"),
            "learned": bool(row.get("mux")) and row["mode"] == 1,
        }
        self._draw_live()
        self.shown_key = self._shape_key(row)
        self._draw_samples()

    def _selected_case(self) -> dict | None:
        choice = self.dataset.get()
        if choice == "All datasets":
            return None
        return next(row for row in self.cases if row["name"] == choice)

    def _preview_selection(self, _event=None) -> None:
        if self.reader and self.reader.is_alive():
            return
        case = self._selected_case()
        topo_name = self.topology.get() if hasattr(self, "topology") else "All topologies"
        if topo_name == "All topologies":
            layers, widths = 2, [4, 2, 0]
            title = "All topologies    preview L1:4   L2:2"
        else:
            shape = next(item for item in self.shapes if item["label"] == topo_name)
            layers, widths = shape["layers"], list(shape["widths"])
            title = topo_name
        if case:
            title = f"{case['name']}    {title}"
        self.now = {
            "title": title,
            "layers": layers,
            "widths": widths,
            "n_in": case["n_in"] if case else 4,
            "n_class": case["n_class"] if case else 4,
            "mux": None,
            "learned": False,
        }
        self._draw_live()

    def _show_example(self) -> None:
        self.after(60, self._preview_selection)
        self.after(80, self._draw_samples)

    def _shape_key(self, row: dict) -> tuple:
        return (row.get("case"), row.get("mode"), row.get("layers"), tuple(row.get("widths") or []))

    def _draw_samples(self) -> None:
        canvas = getattr(self, "samples", None)
        if canvas is None:
            return
        canvas.delete("all")
        rows = self.sample_rows.get(self.shown_key, []) if self.shown_key else []
        if not rows:
            canvas.create_text(12, 24, anchor="w", text="Each test input appears here, with its label and the class the network chose.", fill=FAINT, font=self._font(11))
            canvas.configure(scrollregion=(0, 0, 400, 118))
            return
        n_in = rows[0].get("n_in") or 4
        x = 12
        for trial in rows:
            span = self._draw_input(canvas, x, 8, trial.get("bits"), n_in)
            ok = trial.get("pred") == trial.get("label")
            color = "#4ec9b0" if ok else "#f14c4c"
            pred = trial.get("pred")
            text = f"{trial.get('label')} → {pred if pred is not None else '…'}"
            canvas.create_text(x, 96, anchor="w", text=text, fill=color, font=("Consolas", 11, "bold"))
            x += span + 18
        canvas.configure(scrollregion=(0, 0, max(x, 400), 118))

    def _draw_input(self, canvas: tk.Canvas, x: int, y: int, bits: int | None, n_in: int) -> int:
        on = "#4fc1ff"
        off = "#2d2d2d"
        if n_in <= 8:
            cell = 16
            for index in range(n_in):
                lit = bits is not None and ((bits >> index) & 1)
                canvas.create_rectangle(
                    x + index * (cell + 3), y, x + index * (cell + 3) + cell, y + cell,
                    fill=on if lit else off, outline=off,
                )
            return n_in * (cell + 3)
        cell = 7
        for index in range(64):
            lit = bits is not None and ((bits >> index) & 1)
            col, row = index % 8, index // 8
            canvas.create_rectangle(
                x + col * cell, y + row * cell, x + (col + 1) * cell - 1, y + (row + 1) * cell - 1,
                fill=on if lit else off, outline=off,
            )
        return 8 * cell

    def _draw_live(self, _event=None) -> None:
        if not self.now or not hasattr(self, "net"):
            return
        self._paint_net(self.net, self.now)

    def _reset_mux(self, layers: int, widths: list[int], n_in: int) -> list[int]:
        mux = [0] * 24
        if layers >= 1 and n_in:
            for neuron in range(widths[0]):
                mux[neuron] = neuron % n_in
        for layer in range(1, layers):
            for neuron in range(widths[layer]):
                mux[layer * 8 + neuron] = neuron
        return mux

    def _paint_net(self, canvas: tk.Canvas, spec: dict) -> None:
        if getattr(canvas, "_painting", False):
            return
        canvas._painting = True
        try:
            canvas.delete("all")
            width = max(canvas.winfo_width(), 520)
            height = max(canvas.winfo_height(), 240)
            canvas.create_text(16, 16, anchor="w", text=spec.get("title", ""), fill=INK, font=self._font(12, True))
            layers = spec.get("layers", 0)
            if layers <= 0:
                canvas.create_text(16, 48, anchor="w", text="The diagram appears when the board starts a shape.", fill=FAINT, font=self._font(12))
                return
            widths = spec["widths"]
            n_in = max(int(spec["n_in"]), 1)
            n_class = max(int(spec["n_class"]), 1)
            learned = spec.get("mux") is not None
            mux = spec["mux"] if learned else self._reset_mux(layers, widths, n_in)
            canvas.create_text(width - 16, 16, anchor="e", text="learned mux" if learned else "reset wiring", fill=FAINT, font=self._font(10))

            hidden_rows = max(widths[:layers])
            row_count = max(hidden_rows, n_class, n_in if n_in <= 8 else hidden_rows, 1)
            top = 58
            bottom = 28
            pitch = min(42, (height - top - bottom) / max(row_count, 1))
            radius = max(9, min(15, pitch * 0.38))
            node_font = 9 if radius >= 12 else 7

            def row_y(row: int) -> float:
                return top + row * pitch + radius

            labels = ["inputs"] + [f"L{layer + 1}" for layer in range(layers)] + ["outputs"]
            gap = (width - 80) / max(len(labels) - 1, 1)
            xs = [40 + index * gap for index in range(len(labels))]
            for x, label in zip(xs, labels):
                canvas.create_text(x, 38, text=label, fill=FAINT, font=("Consolas", 11))

            sources = []
            for neuron in range(widths[0]):
                source = mux[neuron]
                if source not in sources:
                    sources.append(source)
            if n_in <= 8:
                input_at = {index: row_y(index) for index in range(n_in)}
                input_ids = list(range(n_in))
            elif sources and max(sources) < row_count:
                input_at = {source: row_y(source) for source in sources}
                input_ids = sorted(sources)
            else:
                ordered = sorted(sources)
                input_at = {source: row_y(index) for index, source in enumerate(ordered)}
                input_ids = ordered

            hidden_at = []
            for layer in range(layers):
                hidden_at.append({neuron: (xs[layer + 1], row_y(neuron)) for neuron in range(widths[layer])})
            output_at = {class_i: (xs[-1], row_y(class_i)) for class_i in range(n_class)}

            def arrow(x1: float, y1: float, x2: float, y2: float, color: str) -> None:
                dx = x2 - x1
                dy = y2 - y1
                dist = math.hypot(dx, dy) or 1.0
                ux, uy = dx / dist, dy / dist
                canvas.create_line(
                    x1 + ux * (radius + 2), y1 + uy * (radius + 2),
                    x2 - ux * (radius + 8), y2 - uy * (radius + 8),
                    fill=color, width=2, arrow="last", arrowshape=(10, 12, 4),
                )

            for neuron in range(widths[0]):
                source = mux[neuron]
                dx, dy = hidden_at[0][neuron]
                if source in input_at:
                    arrow(xs[0], input_at[source], dx, dy, "#569cd6")
                else:
                    canvas.create_text(dx - radius - 8, dy, anchor="e", text=f"←{source}", fill="#ce9178", font=("Consolas", 8))
            for layer in range(1, layers):
                previous = widths[layer - 1]
                for neuron in range(widths[layer]):
                    source = mux[layer * 8 + neuron]
                    dx, dy = hidden_at[layer][neuron]
                    if source < previous:
                        sx, sy = hidden_at[layer - 1][source]
                        arrow(sx, sy, dx, dy, "#569cd6")
                    else:
                        canvas.create_text(dx - radius - 8, dy, anchor="e", text=f"←{source}", fill="#ce9178", font=("Consolas", 8))
            for neuron in range(widths[layers - 1]):
                sx, sy = hidden_at[layers - 1][neuron]
                target = neuron % n_class
                ox, oy = output_at[target]
                arrow(sx, sy, ox, oy, "#4ec9b0")

            for source in input_ids:
                y = input_at[source]
                canvas.create_oval(xs[0] - radius, y - radius, xs[0] + radius, y + radius, outline="#4fc1ff", fill="#1e1e1e", width=2)
                canvas.create_text(xs[0], y, text=str(source), fill=INK, font=("Consolas", node_font))
            for layer in range(layers):
                for neuron, (x, y) in hidden_at[layer].items():
                    canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="#dcdcaa", fill="#1e1e1e", width=2)
                    canvas.create_text(x, y, text=str(neuron), fill=INK, font=("Consolas", node_font))
            for class_i, (x, y) in output_at.items():
                canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="#4ec9b0", fill="#1e1e1e", width=2)
                canvas.create_text(x, y, text=str(class_i), fill=INK, font=("Consolas", node_font))
            canvas.create_text(
                16, height - 10, anchor="w",
                text="Same index, same row. Blue arrow: the one source that neuron listens to. Green arrow: its strong output class.",
                fill=FAINT, font=("Segoe UI", 9),
            )
        finally:
            canvas._painting = False

    def _finish(self) -> None:
        self.run_button.configure(state="normal")
        if self.logger:
            self._write_summary(self.logger.folder)
            folder = self.logger.folder
            self.logger.close()
            self.logger = None
            self.status.configure(text=f"Saved {folder}")
        self._fill_topology_choices()
        self._fill_results()

    def _write_summary(self, folder: str) -> None:
        lines = ["# Sweep log", "", f"Folder: `{folder}`", ""]
        lines.append("| Dataset | Shape | Pass | Correct | Neurons used |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in self.rows:
            name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
            mode = "reconfig" if row["mode"] else "fixed"
            used = row["util"][0] + row["util"][1] + row["util"][2]
            lines.append(f"| {name} | {shape_text(row['layers'], row['widths'])} | {mode} | {row['correct']}/{row['n_test']} | {used} |")
        lines.append("")
        path = os.path.join(folder, "summary.md")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")

    def _fill_topology_choices(self) -> None:
        labels = []
        for row in self.rows:
            name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
            mode = "reconfig" if row["mode"] else "fixed"
            labels.append(f"{name}  {shape_text(row['layers'], row['widths'])}  {mode}  {row['correct']}/{row['n_test']}")
        self.topo_pick["values"] = labels
        if labels and not self.topo_pick.get():
            self.topo_pick.current(0)
            self._draw_topology()

    def _draw_topology(self) -> None:
        index = self.topo_pick.current()
        if index < 0 or index >= len(self.rows):
            return
        row = self.rows[index]
        name = self.by_id.get(row["case"], {}).get("name", str(row["case"]))
        mode = "reconfigurable" if row["mode"] else "fixed"
        note = self.by_id.get(row["case"], {}).get("note", "")
        same = self._match(row["case"], row["layers"], row["widths"], 0 if row["mode"] else 1)
        extra = ""
        if same:
            extra = f"  The other pass scored {same['correct']}/{same['n_test']}."
        self.topo_text.configure(
            text=f"{name}, {shape_text(row['layers'], row['widths'])}, {mode}. {row['correct']}/{row['n_test']}.{extra}\n{note}"
        )
        self._paint_net(self.topo_net, {
            "title": f"{name}    {shape_text(row['layers'], row['widths'])}",
            "layers": row["layers"],
            "widths": row["widths"],
            "n_in": row["n_in"],
            "n_class": row["n_class"],
            "mux": row.get("mux"),
            "learned": bool(row.get("mux")) and row["mode"] == 1,
        })
        self.canvas.delete("all")
        steps = [
            item for item in self.rasters
            if item["case"] == row["case"] and item["mode"] == row["mode"]
            and item.get("layers") == row["layers"] and item.get("widths") == row["widths"]
        ]
        if not steps:
            self.canvas.create_text(24, 24, anchor="w", text="No raster for this topology.", fill=FAINT, font=self._font(12))
            return
        sample = min(item["sample"] for item in steps)
        chosen = [item for item in steps if item["sample"] == sample]
        chosen.sort(key=lambda item: item["step"])
        labels = []
        matrix = []
        n_in = row["n_in"]
        active_in = [bit for bit in range(n_in) if any((item["inputs"] >> bit) & 1 for item in chosen)]
        if n_in <= 4:
            active_in = list(range(n_in))
        for bit in active_in[:12]:
            labels.append(f"I{bit}")
            matrix.append([(item["inputs"] >> bit) & 1 for item in chosen])
        for layer in range(row["layers"]):
            for neuron in range(row["widths"][layer]):
                index_bit = layer * 8 + neuron
                labels.append(f"L{layer}.{neuron}")
                matrix.append([(item["hidden"] >> index_bit) & 1 for item in chosen])
        for bit in range(min(row["n_class"], 10)):
            labels.append(f"Y{bit}")
            matrix.append([(item["outputs"] >> bit) & 1 for item in chosen])
        top = 16
        left = 64
        cell = 18
        for r, label in enumerate(labels):
            self.canvas.create_text(left - 8, top + r * cell + 8, anchor="e", text=label, fill=FAINT, font=("Consolas", 9))
            for c, bit in enumerate(matrix[r]):
                color = "#4fc1ff" if bit else "#2d2d2d"
                self.canvas.create_rectangle(
                    left + c * cell, top + r * cell, left + (c + 1) * cell - 2, top + (r + 1) * cell - 2,
                    fill=color, outline=color,
                )
        self.canvas.create_text(left, top + len(labels) * cell + 16, anchor="w", text=f"Test sample {sample}, eight steps.", fill=FAINT, font=self._font(11))

    def _match(self, case_id: int, layers: int, widths: list[int], mode: int) -> dict | None:
        wanted = list(widths)[:layers]
        for row in self.rows:
            if row["case"] == case_id and row["layers"] == layers and row["mode"] == mode and row["widths"][:layers] == wanted:
                return row
        return None

    def _fill_results(self) -> None:
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        if not self.rows:
            self.results.insert("end", "Run a dataset first. This page compares the shapes after the sweep.\n")
            self.results.configure(state="disabled")
            return
        header = f"{'dataset':<20} {'best reconfig':<24} {'score':>7} {'same fixed':>11} {'fixed L1:8':>10}  reading\n"
        self.results.insert("end", header)
        self.results.insert("end", "—" * 96 + "\n")
        case_ids = []
        for row in self.rows:
            if row["case"] not in case_ids:
                case_ids.append(row["case"])
        for case_id in case_ids:
            learned = [row for row in self.rows if row["case"] == case_id and row["mode"] == 1 and row["n_test"]]
            if not learned:
                continue
            best = max(learned, key=lambda row: (row["correct"] / row["n_test"], -neurons(row["layers"], row["widths"])))
            same = self._match(case_id, best["layers"], best["widths"], 0)
            default = self._match(case_id, 1, [8, 0, 0], 0)
            name = self.by_id.get(case_id, {}).get("name", str(case_id))
            best_s = shape_text(best["layers"], best["widths"])
            best_a = f"{best['correct']}/{best['n_test']}"
            same_a = f"{same['correct']}/{same['n_test']}" if same else "—"
            def_a = f"{default['correct']}/{default['n_test']}" if default else "—"
            reading = self._reading(best, same, default)
            best_e = energy_of(best)
            tied = [item for item in learned if item["correct"] == best["correct"]]
            cheap = min(tied, key=lambda item: (energy_of(item), neurons(item["layers"], item["widths"])))
            cheaper = ""
            if cheap is not best:
                cheaper = f"   cheaper tie {shape_text(cheap['layers'], cheap['widths'])} energy {energy_of(cheap)}"
            self.results.insert("end", f"{name:<20} {best_s:<24} {best_a:>7} {same_a:>11} {def_a:>10}  energy {best_e}{cheaper}  {reading}\n")
        self.results.insert("end", "\n")
        self.results.insert("end", self._closing(case_ids) + "\n")
        self.results.configure(state="disabled")

    def _rate(self, row: dict | None) -> float | None:
        if not row or not row["n_test"]:
            return None
        return row["correct"] / row["n_test"]

    def _reading(self, best: dict, same: dict | None, default: dict | None) -> str:
        best_r = self._rate(best) or 0
        same_r = self._rate(same)
        def_r = self._rate(default)
        if def_r is not None and best_r > def_r + 0.15:
            return "a different shape beats the default wiring"
        if same_r is not None and best_r > same_r + 0.15:
            return "learning helps at this size"
        if same_r is not None and same_r > best_r + 0.15:
            return "freezing this shape is better"
        return "reconfiguration does not move the score"

    def _closing(self, case_ids: list[int]) -> str:
        helped = []
        hurt = []
        flat = []
        for case_id in case_ids:
            learned = [row for row in self.rows if row["case"] == case_id and row["mode"] == 1 and row["n_test"]]
            if not learned:
                continue
            best = max(learned, key=lambda row: (row["correct"] / row["n_test"], -neurons(row["layers"], row["widths"])))
            default = self._match(case_id, 1, [8, 0, 0], 0)
            name = self.by_id.get(case_id, {}).get("name", str(case_id))
            best_r = self._rate(best) or 0
            def_r = self._rate(default)
            if def_r is None:
                flat.append(name)
            elif best_r > def_r + 0.15:
                helped.append(f"{name} ({shape_text(best['layers'], best['widths'])}, {best['correct']}/{best['n_test']} vs {default['correct']}/{default['n_test']})")
            elif def_r > best_r + 0.15:
                hurt.append(name)
            else:
                flat.append(name)
        parts = []
        if helped:
            parts.append("Reconfiguration is worth it on " + "; ".join(helped) + ".")
        if flat:
            parts.append("No gain on " + ", ".join(flat) + ".")
        if hurt:
            parts.append("The default frozen network is ahead on " + ", ".join(hurt) + ".")
        parts.append("The csv files in the sweep folder are the per-topology scores, the per-sample predictions, the mux map, and the rasters. Those are the traces a policy can be fit to.")
        return " ".join(parts)


if __name__ == "__main__":
    PaperApp().mainloop()

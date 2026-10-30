"""Tkinter front end: settings form, live plots, start/stop, re-analysis."""
import json
import os
import queue
import tkinter as tk
from dataclasses import asdict, fields
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import analysis
from .engine import MeasurementRun, RunSettings, ramp_minutes, validate_settings
from .sim import SIM_SPEED

SETTINGS_PATH = Path.home() / ".cvt_settings.json"
BAUD_RATES = ["9600", "19200", "38400", "57600", "115200"]
FREQUENCIES = ["50", "60", "100", "120", "1k", "10k"]  # as listed for the FREQ command
FIELD_TYPES = {f.name: f.type for f in fields(RunSettings)}


def list_ports():
    try:
        from serial.tools import list_ports as lp
        return sorted(p.device for p in lp.comports())
    except Exception:
        return []


class App:
    def __init__(self, root):
        self.root = root
        self.vars = {}       # key -> (tk variable, label)
        self.inputs = []     # widgets disabled during a run
        self.port_boxes = []
        self.run = None
        self.events = queue.Queue()
        self.rows = []
        self.dirty = False
        self.current_settings = RunSettings()

        root.title("C–T Measurement")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(root, padding=(0, 8, 8, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_form(left)
        self._build_plots(right)
        self._load_settings()
        for var, _ in self.vars.values():
            var.trace_add("write", lambda *a: self._update_estimate())
        self._update_estimate()

    # -- layout -------------------------------------------------------------

    def _section(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=6)
        frame.pack(fill="x", pady=(0, 6))
        frame.columnconfigure(1, weight=1)
        return frame

    def _field(self, frame, key, label, values=None, width=12):
        row = frame.grid_size()[1]
        if FIELD_TYPES[key] is bool:
            var = tk.BooleanVar()
            w = ttk.Checkbutton(frame, text=label, variable=var)
            w.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        else:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar()
            if values is not None:
                w = ttk.Combobox(frame, textvariable=var, values=values, width=width)
            else:
                w = ttk.Entry(frame, textvariable=var, width=width)
            w.grid(row=row, column=1, sticky="ew", pady=2)
        self.vars[key] = (var, label)
        self.inputs.append(w)
        return w

    def _build_form(self, parent):
        f = self._section(parent, "Instruments")
        for key, label in (("tec1_port", "TEC 1 port"), ("tec2_port", "TEC 2 port"),
                           ("lcr_port", "LCR port")):
            self.port_boxes.append(self._field(f, key, label, values=list_ports()))
        refresh = ttk.Button(f, text="↻", width=3, command=self._refresh_ports)
        refresh.grid(row=0, column=2, padx=(4, 0))
        self.inputs.append(refresh)
        self._field(f, "tec_baud", "TEC baud", BAUD_RATES)
        self._field(f, "lcr_baud", "LCR baud", BAUD_RATES)
        self._field(f, "simulate", f"Simulate instruments ({SIM_SPEED:.0f}× speed)")

        f = self._section(parent, "LCR meter")
        self._field(f, "frequency", "Frequency (Hz)", FREQUENCIES)
        self._field(f, "level", "Level", ["0.1V", "0.3V", "1.0V"])
        self._field(f, "speed", "Speed", ["SLOW", "MED", "FAST"])
        self._field(f, "circuit", "Circuit", ["parallel", "series"])
        self._field(f, "average", "Averaging (1–255)")

        f = self._section(parent, "Temperature program (cooling)")
        self._field(f, "start_temp", "Start (°C)")
        self._field(f, "stop_temp", "Stop (°C)")
        self._field(f, "ramp_rate", "Ramp rate (°C/min)")
        self._field(f, "sample_interval", "Sample every (s)")
        self._field(f, "soak_min", "Soak at start (min)")
        self._field(f, "settle_tol", "Settle tolerance (°C)")

        f = self._section(parent, "Fine window")
        self._field(f, "fine_enabled", "Slow down inside window")
        self._field(f, "fine_upper", "Upper (°C)")
        self._field(f, "fine_lower", "Lower (°C)")
        self._field(f, "fine_rate", "Ramp rate (°C/min)")
        self._field(f, "fine_interval", "Sample every (s)")

        f = self._section(parent, "Output")
        self._field(f, "output_dir", "Folder", width=22)
        browse = ttk.Button(f, text="…", width=3, command=self._browse)
        browse.grid(row=0, column=2, padx=(4, 0))
        self.inputs.append(browse)
        self._field(f, "sample_name", "Sample name", width=22)
        self._field(f, "smoothing_C", "Peak smoothing (°C)")

        self.estimate = ttk.Label(parent, foreground="#52514e")
        self.estimate.pack(anchor="w", pady=(0, 6))

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x")
        self.start_btn = ttk.Button(buttons, text="Start run", command=self.on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.reanalyze_btn = ttk.Button(buttons, text="Re-analyze CSV…", command=self.on_reanalyze)
        self.reanalyze_btn.pack(side="left")
        ttk.Button(buttons, text="Open folder", command=self._open_folder).pack(side="left", padx=4)

    def _build_plots(self, parent):
        self.fig = Figure(figsize=(8, 6), facecolor=analysis.COLORS["surface"])
        self.fig.subplots_adjust(left=0.1, right=0.97, top=0.93, bottom=0.1)
        self.ax_c = self.fig.add_subplot()
        self.ax_c.set_facecolor(analysis.COLORS["surface"])
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self._draw(None, None, None)

        self.status = ttk.Label(parent, text="Idle.", font=("Segoe UI", 10, "bold"))
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 2))
        self.logbox = ScrolledText(parent, height=8, font=("Consolas", 9), state="disabled")
        self.logbox.grid(row=2, column=0, sticky="ew")

    # -- settings -----------------------------------------------------------

    def _load_settings(self):
        s = RunSettings()
        try:
            s = RunSettings.from_dict(json.loads(SETTINGS_PATH.read_text()))
        except Exception:
            pass
        if not s.output_dir:
            s.output_dir = str(Path.home() / "Documents" / "CvT_data")
        for key, (var, _) in self.vars.items():
            var.set(getattr(s, key))

    def _save_settings(self, s):
        try:
            SETTINGS_PATH.write_text(json.dumps(asdict(s), indent=2))
        except OSError:
            pass

    def collect(self):
        """Settings from the form, or raise ValueError naming the bad field."""
        values = {}
        for key, (var, label) in self.vars.items():
            kind = FIELD_TYPES[key]
            try:
                raw = var.get()
                values[key] = kind(raw) if kind in (float, int) else raw
            except (ValueError, tk.TclError):
                raise ValueError(f"'{label}' must be a number.")
        return RunSettings(**values)

    def _update_estimate(self):
        try:
            s = self.collect()
            errs = validate_settings(s)
            if errs:
                self.estimate.config(text=errs[0])
                return
            self.estimate.config(text=f"Ramp ≈ {ramp_minutes(s):.0f} min "
                                      f"(+ equilibration and {s.soak_min:g} min soak)")
        except ValueError as e:
            self.estimate.config(text=str(e))

    # -- actions ------------------------------------------------------------

    def _refresh_ports(self):
        ports = list_ports()
        for box in self.port_boxes:
            box.config(values=ports)
        self.log("Ports: " + (", ".join(ports) or "none found"))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.vars["output_dir"][0].get() or None)
        if d:
            self.vars["output_dir"][0].set(d)

    def _open_folder(self):
        d = self.vars["output_dir"][0].get()
        if d and Path(d).is_dir():
            os.startfile(d)

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for w in self.inputs + [self.start_btn, self.reanalyze_btn]:
            w.config(state=state)
        self.stop_btn.config(state="normal" if running else "disabled")

    def on_start(self):
        try:
            s = self.collect()
        except ValueError as e:
            messagebox.showerror("Invalid setting", str(e))
            return
        errs = validate_settings(s)
        if errs:
            messagebox.showerror("Invalid settings", "\n".join(errs))
            return
        self._save_settings(s)
        self.current_settings = s
        self.rows = []
        self.dirty = True
        self._clear_log()
        self.run = MeasurementRun(s, self.events)
        self._set_running(True)
        self.status.config(text="Connecting…")
        self.run.start()
        self.root.after(200, self._poll)
        self.root.after(1000, self._refresh_plot)

    def on_stop(self):
        if self.run and messagebox.askyesno("Stop run", "Stop the run? TEC outputs will be turned "
                                            "off and the data so far will be saved and analyzed."):
            self.run.request_stop()
            self.status.config(text="Stopping…")

    def on_reanalyze(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")],
                                          initialdir=self.vars["output_dir"][0].get() or None)
        if not path:
            return
        try:
            smoothing = float(self.vars["smoothing_C"][0].get())
            peak, png = analysis.analyze_file(path, smoothing)
            rows = analysis.load_csv(path)
            meta_path = Path(path).with_suffix(".json")
            settings = json.loads(meta_path.read_text()).get("settings", {}) if meta_path.exists() else {}
        except Exception as e:
            messagebox.showerror("Re-analysis failed", f"{type(e).__name__}: {e}")
            return
        self.rows = [{k: (v if k in ("timestamp", "phase") else float(v)) for k, v in r.items()}
                     for r in rows]
        self._draw(peak, analysis.fine_window_of(settings), smoothing)
        self.log(f"Re-analyzed {path}: " + (peak.describe() if peak else "no peak found."))
        self.log(f"Plot saved to {png}")

    def _poll(self):
        done = None
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "sample":
                self.rows.append(payload)
                self.dirty = True
            elif kind == "status":
                self.status.config(text=payload)
            elif kind == "log":
                self.log(payload)
            elif kind == "done":
                done = payload
        if done is not None:
            self._on_done(done)
        elif self.run is not None:
            self.root.after(200, self._poll)

    def _refresh_plot(self):
        if self.run is None:
            return
        if self.dirty:
            s = self.current_settings
            self._draw(None, analysis.fine_window_of(asdict(s)), None)
        self.root.after(1000, self._refresh_plot)

    def _on_done(self, result):
        self.run = None
        self._set_running(False)
        s = self.current_settings
        self._draw(result["peak"], analysis.fine_window_of(asdict(s)), s.smoothing_C)
        if result["error"]:
            self.status.config(text="Run failed: TEC outputs off.")
            messagebox.showerror("Run failed", result["error"])
        else:
            word = "stopped" if result["aborted"] else "complete"
            peak = result["peak"]
            self.status.config(text=f"Run {word}. " + (peak.describe() if peak else "No peak found."))
        for key in ("csv", "png"):
            if result[key]:
                self.log(f"Saved {result[key]}")

    # -- plotting -----------------------------------------------------------

    def _draw(self, peak, fine_window, smoothing):
        self.dirty = False
        data = analysis.arrays_from_rows(self.rows)
        if len(data["T"]):
            analysis.draw_ct(self.ax_c, None, data, peak, fine_window, smoothing)
            self._show_ticks(self.ax_c)
        else:
            self._placeholder(self.ax_c, "Waiting for the ramp to start…" if self.run is not None
                              else "Capacitance vs. temperature appears here during a run")
            self.ax_c.set_ylabel("Capacitance")
            self.ax_c.set_xlabel("Temperature (°C)")
        self.canvas.draw_idle()

    @staticmethod
    def _placeholder(ax, msg):
        ax.clear()
        analysis.style_axes(ax)
        ax.grid(False)
        ax.tick_params(labelleft=False, labelbottom=False, length=0)
        if msg:
            ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha="center", va="center",
                    fontsize=10, color=analysis.COLORS["muted"])

    @staticmethod
    def _show_ticks(*axes):
        for ax in axes:
            ax.tick_params(labelleft=True, labelbottom=True, length=3.5)

    # -- log ----------------------------------------------------------------

    def log(self, msg):
        self.logbox.config(state="normal")
        self.logbox.insert("end", msg.rstrip() + "\n")
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def _clear_log(self):
        self.logbox.config(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.config(state="disabled")

    def on_close(self):
        if self.run is not None:
            if not messagebox.askyesno("Run in progress", "Stop the run (TEC outputs will be "
                                       "turned off) and exit?"):
                return
            self.run.request_stop()
            self.run.join(timeout=15)
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()

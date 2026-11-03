"""Measurement run: equilibrate at the start temperature, ramp down, log to CSV, analyze.

Runs in a worker thread and reports to the GUI through a queue of (kind, payload)
events: "log" (str), "status" (str), "sample" (row dict), "done" (result dict).
"""
import csv
import json
import math
import re
import threading
import traceback
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path

from . import analysis, ramp
from .sim import Clock, SimClock, SimLCR, SimTEC, SimThermal

EQUILIBRATE_TIMEOUT_MIN = 45   # give up if the start temperature is not reached
TAIL_TIMEOUT_MIN = 10          # max time to let the sample catch up to the stop temperature
MAX_LCR_FAILURES = 5           # consecutive failed LCR reads before aborting


@dataclass
class RunSettings:
    # Instruments
    tec1_port: str = ""
    tec2_port: str = ""
    tec_baud: int = 38400
    lcr_port: str = ""
    lcr_baud: int = 9600
    simulate: bool = False
    # LCR
    frequency: str = "1k"
    level: str = "1.0V"
    speed: str = "SLOW"
    circuit: str = "parallel"
    average: int = 1
    stray_pF: float = 0.0         # parallel stray capacitance subtracted from every reading
    stray_info: str = ""          # how stray_pF was obtained (set by the GUI's Measure button)
    # Temperature program
    start_temp: float = 40.0
    stop_temp: float = 15.0
    ramp_rate: float = 0.5        # deg C / min
    sample_interval: float = 2.0  # s
    soak_min: float = 5.0         # hold at start once within tolerance
    settle_tol: float = 0.2       # deg C
    limit_margin: float = 5.0     # TEC over/under-temperature limits beyond start/stop
    # Fine window
    fine_enabled: bool = False
    fine_upper: float = 30.0
    fine_lower: float = 25.0
    fine_rate: float = 0.1
    fine_interval: float = 1.0
    # Output / analysis
    output_dir: str = ""
    sample_name: str = "sample"
    smoothing_C: float = 0.2      # deg C, width of the peak-finding smoothing window

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def validate_settings(s):
    errs = ramp.validate(s)
    if not s.simulate:
        ports = [s.tec1_port.strip(), s.tec2_port.strip(), s.lcr_port.strip()]
        if not all(ports):
            errs.append("Select COM ports for both TEC controllers and the LCR meter.")
        elif len({p.upper() for p in ports}) < 3:
            errs.append("Each instrument needs its own COM port.")
    if not s.output_dir.strip():
        errs.append("Choose an output folder.")
    if s.soak_min < 0:
        errs.append("Soak time cannot be negative.")
    if s.settle_tol <= 0:
        errs.append("Settle tolerance must be positive.")
    if s.smoothing_C <= 0:
        errs.append("Smoothing width must be positive.")
    if s.speed not in ("SLOW", "MED", "FAST"):
        errs.append("LCR speed must be SLOW, MED or FAST.")
    if not 1 <= s.average <= 255:
        errs.append("LCR averaging must be 1-255.")
    if s.stray_pF < 0:
        errs.append("Stray capacitance cannot be negative.")
    elif s.stray_pF > 0:
        try:
            analysis.parse_frequency(s.frequency)
        except (ValueError, IndexError):
            errs.append(f"Frequency '{s.frequency}' is not a number (needed for the stray correction).")
    return errs


def ramp_minutes(s):
    return ramp.RampSchedule(ramp.build_segments(s)).total_s / 60.0


class StopRequested(Exception):
    pass


class MeasurementRun(threading.Thread):
    def __init__(self, settings, events):
        super().__init__(daemon=True)
        self.s = settings
        self.events = events
        self.stop_event = threading.Event()
        self.clock = Clock()
        self.tecs = []
        self.lcr = None
        self.idns = {}
        self.rows = []
        self.csv_path = None
        self._csv_file = None
        self._lcr_failures = 0
        self._last_split_warn = -math.inf
        self.orig_limits = {}         # TEC number -> (low, high) found on the controller
        self._limits_changed = set()  # TEC numbers whose limits this run overwrote
        self.limits_restored = None   # None: never changed; True/False after shutdown

    # -- plumbing ---------------------------------------------------------

    def emit(self, kind, payload):
        self.events.put((kind, payload))

    def log(self, msg):
        self.emit("log", msg)

    def request_stop(self):
        self.stop_event.set()

    def _wait_until(self, t):
        if self.clock.wait(self.stop_event, t - self.clock.time()):
            raise StopRequested

    def run(self):
        aborted, error = False, None
        try:
            self._open()
            self._prepare_files()
            self._equilibrate()
            self._ramp()
            self._tail()
        except StopRequested:
            aborted = True
            self.log("Run stopped by user.")
        except Exception as e:
            aborted, error = True, f"{type(e).__name__}: {e}"
            self.log("ERROR: " + error)
            self.log(traceback.format_exc())
        finally:
            self._shutdown()
            self.emit("done", self._finish(aborted, error))

    # -- setup / teardown -------------------------------------------------

    def _open(self):
        s = self.s
        if s.simulate:
            self.clock = SimClock()
            thermal = SimThermal(self.clock)
            self.tecs = [SimTEC(thermal, +0.03), SimTEC(thermal, -0.03)]
            self.lcr = SimLCR(thermal)
            self.log(f"Simulation mode ({self.clock.speed:.0f}x speed).")
        else:
            from .instruments import MCR5200, Arroyo5240
            for port in (s.tec1_port, s.tec2_port):
                self.tecs.append(Arroyo5240(port.strip(), s.tec_baud))
            self.lcr = MCR5200(s.lcr_port.strip(), s.lcr_baud)

        for i, tec in enumerate(self.tecs, 1):
            self.idns[f"tec{i}"] = tec.connect()
            self.log(f"TEC {i}: {self.idns[f'tec{i}']}")
        self.idns["lcr"] = self.lcr.connect()
        fetch = self.lcr.configure(frequency=s.frequency, level=s.level, speed=s.speed,
                                   circuit=s.circuit, average=s.average)
        self.idns["lcr_fetch_command"] = fetch
        self.log(f"LCR: {self.idns['lcr']} (reading with {fetch})")
        self._freq_hz = analysis.parse_frequency(s.frequency) if s.stray_pF else None
        if s.stray_pF:
            self.log(f"Subtracting {s.stray_pF:g} pF stray capacitance from every reading "
                     f"({s.stray_info or 'entered manually'}). Raw values are saved as "
                     "C_meas_F and D_meas.")

        # Remember each controller's limits so _shutdown can put them back.
        lo, hi = s.stop_temp - s.limit_margin, s.start_temp + s.limit_margin
        for i, tec in enumerate(self.tecs, 1):
            try:
                self.orig_limits[i] = tec.get_limits()
                self.log(f"TEC {i} limits were {self.orig_limits[i][0]:.2f} / "
                         f"{self.orig_limits[i][1]:.2f} °C.")
            except Exception as e:
                self.log(f"WARNING: could not read TEC {i} limits ({e}); "
                         "they cannot be restored after the run.")
            self._limits_changed.add(i)
            tec.set_limits(lo, hi)
        self.log(f"TEC temperature limits set to {lo:.1f} / {hi:.1f} °C for this run "
                 "(restored when the run ends).")

    def _prepare_files(self):
        out = Path(self.s.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w\-.]+", "_", self.s.sample_name.strip()) or "sample"
        self.started = datetime.now()
        self.csv_path = out / f"{name}_{self.started:%Y%m%d_%H%M%S}.csv"
        self._csv_file = open(self.csv_path, "w", newline="")
        self._writer = csv.DictWriter(self._csv_file, analysis.CSV_FIELDS)
        self._writer.writeheader()
        self._write_meta(ended=None, aborted=None, error=None, peak=None)
        self.t0 = self.clock.time()
        self.log(f"Writing {self.csv_path}")

    def _write_meta(self, **extra):
        meta = {"settings": asdict(self.s), "instruments": self.idns,
                "tec_limits_original": {f"tec{i}": list(v) for i, v in self.orig_limits.items()},
                "tec_limits_restored": self.limits_restored,
                "started": self.started.isoformat(timespec="seconds"), **extra}
        self.csv_path.with_suffix(".json").write_text(json.dumps(meta, indent=2))

    def _shutdown(self):
        for i, tec in enumerate(self.tecs, 1):
            try:
                tec.set_output(False)
            except Exception as e:
                self.log(f"WARNING: could not turn off TEC {i} output: {e}")
        if self.tecs:
            self.log("TEC outputs off.")
        if self._limits_changed:
            self.limits_restored = self._restore_limits()
        for inst in [*self.tecs, self.lcr]:
            if inst is not None:
                inst.close()
        if self._csv_file:
            self._csv_file.close()

    def _restore_limits(self):
        """Put back the limits found on connect and read them back.
        Returns True only if every TEC this run changed is verified."""
        ok = True
        for i in sorted(self._limits_changed):
            orig = self.orig_limits.get(i)
            if orig is None:  # could not be read on connect (already warned)
                ok = False
                continue
            tec = self.tecs[i - 1]
            try:
                tec.set_limits(*orig)
                now = tec.get_limits()
                if any(abs(a - b) > 0.01 for a, b in zip(now, orig)):
                    raise RuntimeError(f"controller reports {now[0]:.2f} / {now[1]:.2f} °C")
                self.log(f"TEC {i} limits restored to {orig[0]:.2f} / {orig[1]:.2f} °C.")
            except Exception as e:
                ok = False
                self.log(f"WARNING: could not restore TEC {i} limits to {orig[0]:.2f} / "
                         f"{orig[1]:.2f} °C ({e}). Set them on the controller's front panel.")
        return ok

    def _finish(self, aborted, error):
        result = {"csv": None, "png": None, "peak": None, "aborted": aborted, "error": error,
                  "limits_restored": self.limits_restored}
        if self.csv_path is None:
            return result
        result["csv"] = str(self.csv_path)
        s = self.s
        data = analysis.arrays_from_rows(self.rows)
        peak = analysis.find_peak(data, s.smoothing_C)
        if len(data["T"]):
            png = self.csv_path.with_suffix(".png")
            try:
                analysis.save_plot(png, data, peak, analysis.plot_title(asdict(s)),
                                   analysis.fine_window_of(asdict(s)), s.smoothing_C)
                result["png"] = str(png)
            except Exception as e:
                self.log(f"WARNING: plot failed: {e}")
        self._write_meta(ended=datetime.now().isoformat(timespec="seconds"),
                         aborted=aborted, error=error, peak=asdict(peak) if peak else None)
        result["peak"] = peak
        self.log(peak.describe() if peak else "Not enough ramp data to locate a peak.")
        return result

    # -- measurement ------------------------------------------------------

    def _sample(self, phase, setpoint, set_tecs=True):
        if set_tecs:
            for tec in self.tecs:
                tec.set_temperature(setpoint)
        t1 = self.tecs[0].read_temperature()
        t2 = self.tecs[1].read_temperature()
        try:
            c, d = self.lcr.read()
            self._lcr_failures = 0
        except Exception as e:
            self._lcr_failures += 1
            if self._lcr_failures >= MAX_LCR_FAILURES:
                raise RuntimeError(f"LCR meter failed {MAX_LCR_FAILURES} reads in a row: {e}") from e
            self.log(f"LCR read failed ({e}); recording NaN.")
            c = d = math.nan
        c_meas, d_meas = c, d
        if self.s.stray_pF:
            c, d = analysis.remove_stray(c, d, self.s.stray_pF * 1e-12, self.s.circuit, self._freq_hz)

        elapsed = self.clock.time() - self.t0
        row = {
            "timestamp": (self.started + timedelta(seconds=elapsed)).isoformat(timespec="seconds"),
            "elapsed_s": elapsed, "phase": phase, "setpoint_C": setpoint,
            "T1_C": t1, "T2_C": t2, "T_avg_C": (t1 + t2) / 2, "C_F": c, "D": d,
            "C_meas_F": c_meas, "D_meas": d_meas,
        }
        self._writer.writerow({
            **row, "elapsed_s": f"{elapsed:.2f}", "setpoint_C": f"{setpoint:.4f}",
            "T1_C": f"{t1:.4f}", "T2_C": f"{t2:.4f}", "T_avg_C": f"{(t1 + t2) / 2:.4f}",
            "C_F": f"{c:.6e}", "D": f"{d:.6g}", "C_meas_F": f"{c_meas:.6e}", "D_meas": f"{d_meas:.6g}",
        })
        self._csv_file.flush()
        self.rows.append(row)
        self.emit("sample", row)

        if abs(t1 - t2) > 2.0 and elapsed - self._last_split_warn > 60:
            self._last_split_warn = elapsed
            self.log(f"WARNING: TEC sensors differ by {abs(t1 - t2):.2f} °C.")
        return row

    def _status(self, label, row, remaining_s=None):
        scale, unit = analysis.cap_units([row["C_F"]])
        text = (f"{label}   set {row['setpoint_C']:.2f} °C   T1 {row['T1_C']:.2f}   "
                f"T2 {row['T2_C']:.2f}   C {row['C_F'] / scale:.5g} {unit}   D {row['D']:.4g}")
        if remaining_s is not None:
            text += f"   ~{remaining_s / 60:.0f} min left"
        self.emit("status", text)

    def _equilibrate(self):
        s = self.s
        for tec in self.tecs:
            tec.set_temperature(s.start_temp)
            tec.set_output(True)
        self.log(f"Outputs on. Equilibrating at {s.start_temp} °C "
                 f"(±{s.settle_tol} °C, then {s.soak_min} min soak).")
        t_begin = next_t = self.clock.time()
        in_tol_since = None
        while True:
            row = self._sample("equilibrate", s.start_temp, set_tecs=False)
            now = self.clock.time()
            if all(abs(row[k] - s.start_temp) <= s.settle_tol for k in ("T1_C", "T2_C")):
                if in_tol_since is None:
                    in_tol_since = now
            else:
                in_tol_since = None
            soaked = 0.0 if in_tol_since is None else now - in_tol_since
            if soaked >= s.soak_min * 60:
                break
            if now - t_begin > EQUILIBRATE_TIMEOUT_MIN * 60:
                raise RuntimeError(f"Did not reach {s.start_temp} °C ±{s.settle_tol} within "
                                   f"{EQUILIBRATE_TIMEOUT_MIN} min.")
            label = "Equilibrating" if in_tol_since is None else f"Soaking {soaked / 60:.1f}/{s.soak_min} min"
            self._status(label, row)
            next_t = max(next_t + s.sample_interval, self.clock.time())
            self._wait_until(next_t)

    def _ramp(self):
        sched = ramp.RampSchedule(ramp.build_segments(self.s))
        self.log(f"Ramping {self.s.start_temp} → {self.s.stop_temp} °C, {sched.total_s / 60:.1f} min.")
        t_ramp0 = next_t = self.clock.time()
        while True:
            t = self.clock.time() - t_ramp0
            at = sched.at(t)
            if at is None:
                break
            setpoint, seg = at
            row = self._sample(seg.phase, setpoint)
            self._status(f"{seg.phase.capitalize()} {100 * t / sched.total_s:.0f}%", row,
                         sched.total_s - t)
            next_t = max(next_t + seg.interval, self.clock.time())
            self._wait_until(next_t)

    def _tail(self):
        # The set point has reached stop; keep logging while the sample catches up.
        s = self.s
        t_begin = next_t = self.clock.time()
        while self.clock.time() - t_begin < TAIL_TIMEOUT_MIN * 60:
            row = self._sample("tail", s.stop_temp)
            if all(abs(row[k] - s.stop_temp) <= s.settle_tol for k in ("T1_C", "T2_C")):
                self.log("Reached stop temperature.")
                return
            self._status("Settling at stop", row)
            next_t = max(next_t + s.sample_interval, self.clock.time())
            self._wait_until(next_t)
        self.log(f"WARNING: sample not within ±{s.settle_tol} °C of stop after {TAIL_TIMEOUT_MIN} min.")

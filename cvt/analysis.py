"""Peak finding and plotting for capacitance vs. temperature data."""
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

CSV_FIELDS = ["timestamp", "elapsed_s", "phase", "setpoint_C",
              "T1_C", "T2_C", "T_avg_C", "C_F", "D"]

# Rows used for peak finding; "equilibrate" rows are the approach to the start temperature.
ANALYSIS_PHASES = ("ramp", "fine", "tail")

COLORS = {
    "series1": "#2a78d6",  # blue
    "series2": "#eb6834",  # orange
    "series3": "#1baf7a",  # aqua
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#8a8984",
    "grid": "#e6e5e0",
    "band": "#efeee9",
    "surface": "#fcfcfb",
}


@dataclass
class Peak:
    T_peak: float
    C_peak: float
    at_edge: bool
    n_points: int
    smoothing_C: float

    def describe(self, cap_unit=None):
        scale, unit = cap_unit or cap_units([self.C_peak])
        text = f"Peak at {self.T_peak:.2f} °C, C = {self.C_peak / scale:.4g} {unit}"
        if self.at_edge:
            text += " (at edge of range: transition may lie outside it)"
        return text


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def arrays_from_rows(rows, phases=ANALYSIS_PHASES):
    sel = [r for r in rows if r["phase"] in phases]
    col = lambda k: np.array([float(r[k]) for r in sel], dtype=float)
    return {"T": col("T_avg_C"), "C": col("C_F"), "D": col("D")}


def smooth_vs_temperature(T, y, width):
    """Boxcar mean of y over a window `width` deg C wide, centred on each point.
    Smoothing in temperature (not sample index) keeps fine-window and
    coarse-ramp regions comparable. Returns (T sorted, smoothed y)."""
    ok = np.isfinite(T) & np.isfinite(y)
    T, y = T[ok], y[ok]
    order = np.argsort(T)
    T, y = T[order], y[order]
    cs = np.concatenate([[0.0], np.cumsum(y)])
    lo = np.searchsorted(T, T - width / 2, side="left")
    hi = np.searchsorted(T, T + width / 2, side="right")
    return T, (cs[hi] - cs[lo]) / (hi - lo)


def find_peak(data, width):
    Ts, Cs = smooth_vs_temperature(data["T"], data["C"], width)
    if len(Ts) < 5:
        return None
    i = int(np.argmax(Cs))
    at_edge = (Ts[i] - Ts[0] < width) or (Ts[-1] - Ts[i] < width)
    return Peak(float(Ts[i]), float(Cs[i]), bool(at_edge), int(len(Ts)), float(width))


def cap_units(values):
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    m = float(np.median(np.abs(v))) if v.size else 1e-12
    for scale, unit in ((1e-12, "pF"), (1e-9, "nF"), (1e-6, "µF")):
        if m < scale * 1000:
            return scale, unit
    return 1.0, "F"


def style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(COLORS["muted"])
    ax.tick_params(colors=COLORS["ink2"], labelsize=9)
    ax.grid(True, color=COLORS["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(COLORS["ink2"])
    ax.yaxis.label.set_color(COLORS["ink2"])


def draw_ct(ax_c, ax_d, data, peak=None, fine_window=None, smoothing=None):
    """Draw C(T) on ax_c and D(T) on ax_d (either may be None)."""
    scale, unit = cap_units(data["C"])
    for ax in (ax_c, ax_d):
        if ax is None:
            continue
        ax.clear()
        style_axes(ax)
        if fine_window:
            ax.axvspan(min(fine_window), max(fine_window), color=COLORS["band"], zorder=0)
    if ax_c is not None:
        ax_c.scatter(data["T"], data["C"] / scale, s=6, color=COLORS["series1"],
                     alpha=0.35, linewidths=0)
        if smoothing and len(data["T"]) >= 5:
            Ts, Cs = smooth_vs_temperature(data["T"], data["C"], smoothing)
            ax_c.plot(Ts, Cs / scale, color=COLORS["series1"], linewidth=2)
        ax_c.set_ylabel(f"Capacitance ({unit})")
        if fine_window:
            ax_c.text(sum(fine_window) / 2, 1.0, "fine window", transform=ax_c.get_xaxis_transform(),
                      ha="center", va="bottom", fontsize=8, color=COLORS["muted"])
        if peak is not None:
            ax_c.axvline(peak.T_peak, color=COLORS["ink2"], linewidth=1, linestyle="--")
            ax_c.scatter([peak.T_peak], [peak.C_peak / scale], s=70, color=COLORS["ink"],
                         edgecolors=COLORS["surface"], linewidths=2, zorder=5)
            label = f"Peak  {peak.T_peak:.2f} °C\n{peak.C_peak / scale:.4g} {unit}"
            if peak.at_edge:
                label += "\n(edge of range)"
            ax_c.text(0.99, 0.97, label, transform=ax_c.transAxes, ha="right", va="top",
                      fontsize=10, color=COLORS["ink"],
                      bbox={"facecolor": COLORS["surface"], "edgecolor": COLORS["grid"],
                            "boxstyle": "round,pad=0.4"})
    if ax_d is not None:
        ax_d.scatter(data["T"], data["D"], s=6, color=COLORS["series2"], alpha=0.5, linewidths=0)
        ax_d.set_ylabel("D")
        ax_d.set_xlabel("Temperature (°C)")
    elif ax_c is not None:
        ax_c.set_xlabel("Temperature (°C)")
    return scale, unit


def save_plot(path, data, peak, title="", fine_window=None, smoothing=None):
    fig = Figure(figsize=(8, 5.5), dpi=150, facecolor=COLORS["surface"])
    FigureCanvasAgg(fig)
    ax_c = fig.subplots()
    ax_c.set_facecolor(COLORS["surface"])
    draw_ct(ax_c, None, data, peak, fine_window, smoothing)
    if title:
        ax_c.set_title(title, loc="left", fontsize=11, color=COLORS["ink"], pad=14)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())


def plot_title(settings):
    return (f"{settings.get('sample_name', '')}  ·  {settings.get('frequency', '')}Hz, "
            f"{settings.get('level', '')}, {settings.get('ramp_rate', '')} °C/min")


def fine_window_of(settings):
    if settings.get("fine_enabled"):
        return (settings["fine_upper"], settings["fine_lower"])
    return None


def analyze_file(csv_path, smoothing=None):
    """Re-analyze a saved run: recompute the peak, rewrite the PNG and the JSON's peak entry.
    Returns (peak, png_path)."""
    csv_path = Path(csv_path)
    meta_path = csv_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    settings = meta.get("settings", {})
    width = smoothing or settings.get("smoothing_C", 0.2)
    data = arrays_from_rows(load_csv(csv_path))
    peak = find_peak(data, width)
    png_path = csv_path.with_suffix(".png")
    save_plot(png_path, data, peak, plot_title(settings) if settings else csv_path.stem,
              fine_window_of(settings), width)
    if meta:
        meta["peak"] = asdict(peak) if peak else None
        meta_path.write_text(json.dumps(meta, indent=2))
    return peak, png_path

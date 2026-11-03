"""Measure the stray (open-circuit) capacitance of the leads and cable with the LCR meter.

The holder's leads are unplugged, so the meter sees only the clips, adapter and cable.
That capacitance is in parallel with the sample during a run and is subtracted
from every reading (see analysis.remove_stray).
"""
import math
import statistics
import time
from datetime import datetime

from . import analysis

N_DISCARD = 2      # readings thrown away after each reconfigure
N_CHECK = 10       # readings at the check frequency
N_MAIN = 20        # readings at the run frequency
# Seconds between reads: the meter's measurement time per reading (x averaging).
READ_INTERVAL_S = {"SLOW": 0.4, "MED": 0.15, "FAST": 0.06}


def check_frequency(freq):
    """Second frequency used to confirm the open reading is a real capacitance."""
    return "1k" if analysis.parse_frequency(freq) >= 5e3 else "10k"


def _readings(lcr, s, freq, n, interval, log):
    lcr.configure(frequency=freq, level=s.level, speed=s.speed, circuit="parallel",
                  average=s.average)
    cs, ds, failures = [], [], 0
    for i in range(N_DISCARD + n):
        time.sleep(interval)
        try:
            c, d = lcr.read()
        except Exception as e:
            failures += 1
            if failures > n // 2:
                raise RuntimeError(f"Too many failed LCR reads at {freq}: {e}") from e
            continue
        if i >= N_DISCARD and not math.isnan(c):
            cs.append(c * 1e12)
            ds.append(d)
    if len(cs) < 2:
        raise RuntimeError(f"Not enough LCR readings at {freq}.")
    log(f"Open at {freq}: {statistics.mean(cs):.2f} ± {statistics.stdev(cs):.2f} pF, "
        f"D {statistics.mean(ds):.3g} ({len(cs)} readings)")
    return cs, ds


def measure_stray(s, log):
    """Measure the open-circuit Cp. `s` is a RunSettings; `log` takes a string.
    Returns a dict with the value in pF, its spread, D, the check reading,
    warnings, and a one-line record for the run metadata."""
    if s.simulate:
        from .sim import SimOpenLCR
        lcr, interval = SimOpenLCR(), 0.02
    else:
        from .instruments import MCR5200
        lcr = MCR5200(s.lcr_port.strip(), s.lcr_baud)
        interval = READ_INTERVAL_S.get(s.speed, 0.4) * s.average
    check_f = check_frequency(s.frequency)
    try:
        log(f"LCR: {lcr.connect()}")
        # Check frequency first so the meter is left at the run frequency.
        check_cs, _ = _readings(lcr, s, check_f, N_CHECK, interval, log)
        cs, ds = _readings(lcr, s, s.frequency, N_MAIN, interval, log)
    finally:
        lcr.close()

    mean, std = statistics.mean(cs), statistics.stdev(cs)
    d, check = statistics.mean(ds), statistics.mean(check_cs)
    warnings = []
    if mean <= 0:
        warnings.append("The reading is zero or negative: the clips may be shorted.")
    if mean > 1000:
        warnings.append("More than 1 nF: check that the holder's leads are unplugged.")
    if std > max(0.5, 0.01 * abs(mean)):
        warnings.append("The readings are noisy. Try more averaging, and keep the cable still.")
    if abs(check - mean) > max(1.0, 0.05 * abs(mean)):
        warnings.append(f"The reading at {check_f} ({check:.1f} pF) differs from {s.frequency}: "
                        "it may not be a real capacitance (meter noise floor or a stale "
                        "correction stored on the meter).")
    if abs(d) > 0.05:
        warnings.append(f"D is high ({d:.2g}) for a cable: something lossy may be connected.")
    info = (f"measured {datetime.now():%Y-%m-%d %H:%M} at {s.frequency}: {mean:.1f} ± {std:.1f} pF, "
            f"D {d:.2g}; check at {check_f}: {check:.1f} pF")
    return {"pF": mean, "std_pF": std, "D": d, "check_freq": check_f, "check_pF": check,
            "warnings": warnings, "info": info}

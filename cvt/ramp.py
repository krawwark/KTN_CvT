"""Cooling ramp schedule: piecewise-linear set point with an optional fine window."""
from dataclasses import dataclass


@dataclass
class Segment:
    t_from: float      # deg C
    t_to: float        # deg C (t_to < t_from)
    rate: float        # deg C / min, positive
    interval: float    # s between samples
    phase: str         # "ramp" or "fine"

    @property
    def duration_s(self):
        return (self.t_from - self.t_to) / self.rate * 60.0


def validate(s):
    """Return a list of problems with the temperature program in settings `s`."""
    errs = []
    if s.start_temp <= s.stop_temp:
        errs.append("Start temperature must be above stop temperature (runs cool from high to low).")
    if s.ramp_rate <= 0:
        errs.append("Ramp rate must be positive.")
    if s.sample_interval < 0.5:
        errs.append("Sample interval must be at least 0.5 s.")
    if s.fine_enabled:
        if s.fine_upper <= s.fine_lower:
            errs.append("Fine window upper temperature must be above its lower temperature.")
        if s.fine_upper > s.start_temp or s.fine_lower < s.stop_temp:
            errs.append("Fine window must lie inside the start/stop range.")
        if s.fine_rate <= 0:
            errs.append("Fine ramp rate must be positive.")
        if s.fine_interval < 0.5:
            errs.append("Fine sample interval must be at least 0.5 s.")
    return errs


def build_segments(s):
    """Segments from start to stop. Assumes `validate(s)` returned no errors."""
    ramp = lambda a, b: Segment(a, b, s.ramp_rate, s.sample_interval, "ramp")
    if not s.fine_enabled:
        return [ramp(s.start_temp, s.stop_temp)]
    segs = [
        ramp(s.start_temp, s.fine_upper),
        Segment(s.fine_upper, s.fine_lower, s.fine_rate, s.fine_interval, "fine"),
        ramp(s.fine_lower, s.stop_temp),
    ]
    return [seg for seg in segs if seg.t_from > seg.t_to]


class RampSchedule:
    def __init__(self, segments):
        self.segments = segments
        self._starts = []
        t = 0.0
        for seg in segments:
            self._starts.append(t)
            t += seg.duration_s
        self.total_s = t

    def at(self, t):
        """(set point, segment) at `t` seconds after ramp start, or None when done."""
        if t >= self.total_s:
            return None
        for start, seg in zip(reversed(self._starts), reversed(self.segments)):
            if t >= start:
                return seg.t_from - seg.rate * (t - start) / 60.0, seg
        seg = self.segments[0]
        return seg.t_from, seg

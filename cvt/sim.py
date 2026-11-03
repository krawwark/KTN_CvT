"""Simulated instruments and an accelerated clock for testing without hardware."""
import math
import random
import time

SIM_SPEED = 30.0  # simulated seconds per real second


class Clock:
    """Real time. `wait` returns True if the stop event was set."""

    def time(self):
        return time.monotonic()

    def wait(self, event, seconds):
        return event.wait(max(0.0, seconds))


class SimClock(Clock):
    def __init__(self, speed=SIM_SPEED):
        self.speed = speed
        self._t0 = time.monotonic()

    def time(self):
        return self._t0 + (time.monotonic() - self._t0) * self.speed

    def wait(self, event, seconds):
        return event.wait(max(0.0, seconds) / self.speed)


class SimThermal:
    """Sample stage: first-order lag toward the mean of the TEC set points."""

    AMBIENT = 22.0
    TAU_S = 60.0

    def __init__(self, clock):
        self.clock = clock
        self.temp = self.AMBIENT
        self._last = clock.time()
        self.tecs = []

    def update(self):
        now = self.clock.time()
        dt, self._last = now - self._last, now
        active = [t.setpoint for t in self.tecs if t.output]
        target = sum(active) / len(active) if active else self.AMBIENT
        self.temp += (target - self.temp) * (1 - math.exp(-dt / self.TAU_S))
        return self.temp


class SimTEC:
    def __init__(self, thermal, offset):
        self.thermal = thermal
        self.offset = offset
        self.setpoint = thermal.AMBIENT
        self.output = False
        self.limits = (-10.0, 80.0)
        thermal.tecs.append(self)

    def connect(self):
        return "Simulated Arroyo 5240"

    def get_limits(self):
        return self.limits

    def set_limits(self, t_low, t_high):
        self.limits = (t_low, t_high)

    def set_temperature(self, temp_c):
        self.setpoint = temp_c

    def read_temperature(self):
        return self.thermal.update() + self.offset + random.gauss(0, 0.005)

    def set_output(self, on):
        self.output = bool(on)

    def close(self):
        pass


class SimOpenLCR:
    """LCR meter with the holder unplugged: ~170 pF of cable, nearly lossless."""

    STRAY = 170e-12

    def connect(self):
        return "Simulated MCR5200 (open)"

    def configure(self, **kwargs):
        return "SIM"

    def read(self):
        return self.STRAY * (1 + random.gauss(0, 1e-3)), 5e-4 + random.gauss(0, 1e-4)

    def close(self):
        pass


class SimLCR:
    """Lorentzian capacitance peak at TC on a 100 pF background."""

    TC = 27.3
    WIDTH = 1.0

    def __init__(self, thermal):
        self.thermal = thermal

    def connect(self):
        return "Simulated MCR5200"

    def configure(self, **kwargs):
        return "SIM"

    def read(self):
        x = (self.thermal.update() - self.TC) / self.WIDTH
        peak = 1.0 / (1.0 + x * x)
        c = 100e-12 * (1 + 1.5 * peak) * (1 + random.gauss(0, 3e-4))
        d = 0.01 + 0.02 * peak + random.gauss(0, 2e-4)
        return c, d

    def close(self):
        pass

"""Serial drivers for the Arroyo 5240 TECSource and the Matrix MCR5200 LCR meter.

Command sets follow instrument_docs/ArroyoComputerInterfacingManual.pdf and
instrument_docs/MCR5000-programming-manual-1.pdf.
"""
import time

import serial


class SerialInstrument:
    """Line-oriented ASCII instrument on a serial port (8N1, no flow control)."""

    def __init__(self, port, baud, write_term, timeout=1.0):
        self.port = port
        self.write_term = write_term
        self.timeout = timeout
        self.ser = serial.Serial(
            port, baud, bytesize=8, parity="N", stopbits=1, timeout=timeout,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )

    def write(self, cmd):
        self.ser.write((cmd + self.write_term).encode("ascii"))

    def query(self, cmd, timeout=None):
        self.ser.reset_input_buffer()
        self.write(cmd)
        return self._readline(self.timeout if timeout is None else timeout, cmd)

    def _readline(self, timeout, cmd):
        # Accept CR, LF or CRLF as the response terminator.
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b in (b"\r", b"\n"):
                if buf:
                    return buf.decode("ascii", errors="replace").strip()
                continue
            buf += b
        raise TimeoutError(f"No response to {cmd!r} on {self.port}")

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class Arroyo5240(SerialInstrument):
    """Arroyo 5240 TECSource. USB virtual COM ports run at a fixed 38400 baud;
    the RS-232 port defaults to 9600 (set on the front panel)."""

    def __init__(self, port, baud=38400):
        super().__init__(port, baud, write_term="\r\n")

    def connect(self):
        idn = self.query("*IDN?")
        self.write("TEC:MODE:T")
        return idn

    def get_limits(self):
        """(low, high) temperature limits currently stored on the controller, in deg C."""
        return float(self.query("TEC:LIM:TLO?")), float(self.query("TEC:LIM:THI?"))

    def set_limits(self, t_low, t_high):
        self.write(f"TEC:LIM:TLO {t_low:.2f}")
        self.write(f"TEC:LIM:THI {t_high:.2f}")

    def set_temperature(self, temp_c):
        self.write(f"TEC:T {temp_c:.3f}")

    def read_temperature(self):
        return float(self.query("TEC:T?"))

    def set_output(self, on):
        self.write(f"TEC:OUT {1 if on else 0}")

    def errors(self):
        return self.query("ERR?")


class MCR5200(SerialInstrument):
    """Matrix MCR5200 LCR meter, measuring C (primary) and D (secondary)."""

    # The manual lists the fetch query as "*FETC?"; try the common spellings.
    FETCH_CANDIDATES = ("FETC?", "*FETC?", "FETCH?")

    def __init__(self, port, baud=9600):
        super().__init__(port, baud, write_term="\n", timeout=2.0)
        self._fetch_cmd = None

    def connect(self):
        try:
            return self.query("*IDN?")
        except TimeoutError:
            return "(no *IDN? response)"

    def configure(self, frequency="1k", level="1.0V", speed="SLOW",
                  circuit="parallel", average=1):
        for cmd in (
            "PRIN OFF",  # no auto-send stream; we poll
            "PARA cd",
            "EQU " + ("PAR" if circuit == "parallel" else "SER"),
            f"FREQ {frequency}",
            f"LEV {level}",
            f"SPEED {speed}",
            "RANG AUTO",
            "TRIG INT",
            f"CALC:AVER {int(average)}",
        ):
            self.write(cmd)
            time.sleep(0.1)
        time.sleep(1.0)  # let a fresh reading complete with the new settings
        self._fetch_cmd = self._detect_fetch()
        return self._fetch_cmd

    def _detect_fetch(self):
        for cmd in self.FETCH_CANDIDATES:
            try:
                self._parse(self.query(cmd))
                return cmd
            except (TimeoutError, ValueError, IndexError):
                continue
        raise RuntimeError(
            "MCR5200 did not answer any fetch query "
            f"({', '.join(self.FETCH_CANDIDATES)}). Check port, baud rate and "
            "that Bus Mode is RS232C in the meter's COMM SETUP page."
        )

    def read(self):
        """Return (capacitance_F, dissipation)."""
        return self._parse(self.query(self._fetch_cmd))

    @staticmethod
    def _parse(resp):
        # Response format: <primary>,<secondary>[,<comp>]
        parts = resp.split(",")
        return float(parts[0]), float(parts[1])

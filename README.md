# CvT — capacitance vs. temperature

Measures capacitance (and loss, D) on a Matrix MCR5200 LCR meter while two Arroyo
5240 TECSource controllers, one on each side of the sample, cool it from a start
temperature to a stop temperature. Saves a CSV and a plot with the phase-transition
peak marked.

## Setup

The drivers follow the Arroyo *Computer Interfacing Manual* and the Matrix
*MCR5000 Series* user and programming manuals. Keep local copies in
`instrument_docs/` (not tracked in git).

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Run

```
.venv\Scripts\python -m cvt                    # GUI
.venv\Scripts\python -m cvt analyze RUN.csv    # re-find peak, rewrite RUN.png
.venv\Scripts\python -m cvt analyze RUN.csv --smoothing 0.5
```

Tick **Simulate instruments** to try the whole workflow without hardware. It
runs 30× faster than real time and has a fake transition at 27.3 °C.

## Before the first real run

- **MCR5200:** set SYSTEM → COMM SETUP → Bus Mode to **RS232C**, and make the baud
  rate match the GUI (default 9600). Do an open/short correction on the fixture.
  Let the meter warm up for 15 minutes.
- **Arroyo 5240:** a USB connection always runs at **38400** baud. An RS-232 connection
  uses whatever is set on the front panel (factory default 9600). Both TECs must use
  the same baud setting in the GUI.
- **Ports:** use ↻ to list COM ports. Device Manager shows which port belongs to which
  instrument.

## What a run does

1. **Connect:** reads `*IDN?` from all three instruments and configures the LCR
   meter (C–D, parallel or series, frequency, level, speed, auto-range, averaging).
   Sets the TEC temperature limits to *stop − 5 °C* and *start + 5 °C* (`TEC:LIM:TLO/THI`).
   These limits stay stored on the controllers after the run.
2. **Equilibrate:** turns both TEC outputs on at the start temperature. Waits
   until both sensors are within the settle tolerance, then holds for the soak time.
   Gives up after 45 minutes.
3. **Ramp:** moves the setpoint down linearly at the ramp rate, sending the new value
   to both TECs at every sample. This is done in software rather than with `TEC:TRATE`,
   because the 5240 may not support that command. If the fine window is enabled,
   the rate and sample interval switch to the fine values inside it.
4. **Tail:** holds at the stop temperature and keeps logging until the sensors catch
   up (at most 10 minutes).
5. **Finish:** turns both TEC outputs **off**, then finds the peak and saves the outputs.
   The outputs are also turned off if you press Stop, if an error occurs, or if you
   close the window.

## Output (in the chosen folder)

| File | Contents |
|---|---|
| `<sample>_<YYYYmmdd_HHMMSS>.csv` | `timestamp, elapsed_s, phase, setpoint_C, T1_C, T2_C, T_avg_C, C_F, D` (capacitance in farads) |
| `….png` | Capacitance vs. temperature, with the fine window shaded and the peak marked (D stays in the CSV) |
| `….json` | Run settings, instrument IDs, and the peak result |

Peak finding uses only the `ramp`, `fine` and `tail` rows. C is smoothed over a window
**Peak smoothing** °C wide (in temperature, so coarse and fine regions are treated alike),
and the peak is the maximum of the smoothed curve. If the peak is within one smoothing
width of either end of the data, it is labelled "at edge of range", which usually means
the transition lies outside the scanned range.

## Notes on accuracy

- **Temperature source:** the temperature is the average of the two TEC control sensors,
  not a sensor on the sample. While cooling, the sample is warmer than the reading, so the
  apparent peak shifts toward lower temperatures as the ramp gets faster. Use the
  bottom-right plot (setpoint vs. TEC 1/TEC 2) to judge lag. A second pass with a slow
  fine window around the peak is the intended fix.
- **Sensor disagreement:** a warning is logged if the two TEC sensors differ by more
  than 2 °C.

## Building an .exe

```
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\pyinstaller --onefile --windowed --name CvT run_cvt.py
```

## Not yet verified on hardware

The drivers were written from the manuals and tested only against the simulator.
On the first real connection, check these:

- **LCR fetch query:** the manual lists it as `*FETC?`. The driver tries `FETC?`,
  `*FETC?` and `FETCH?` in turn and logs which one worked.
- **LCR set commands:** sent as SCPI short forms (`PARA cd`, `EQU PAR`, `FREQ 1k`,
  `LEV 1.0V`, `SPEED SLOW`, `RANG AUTO`, `TRIG INT`, `CALC:AVER n`, `PRIN OFF`).
  Check the meter's screen after connecting to confirm they took effect.
- **Frequencies:** the frequency list in the GUI is the one the programming manual gives
  for `FREQ` (50 Hz–10 kHz). The box is editable, so you can try other MCR5200
  frequencies (for example `100k`).

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

Run these from the `KTN_CvT` folder, after the Setup step has created `.venv`:

```
.venv\Scripts\python -m cvt                    # GUI
.venv\Scripts\python -m cvt analyze RUN.csv    # re-find peak, rewrite RUN.png
.venv\Scripts\python -m cvt analyze RUN.csv --smoothing 0.5
```

Tick **Simulate instruments** to try the whole workflow without hardware. It
runs 30× faster than real time and has a fake transition at 27.3 °C.

## Stray capacitance (open correction)

The clips, BNC adapter and BNC cable add capacitance in parallel with the sample (about
30 pF per foot of cable). To remove it, unplug the holder's leads from the adapter and
press **Measure…** next to *Stray C to subtract*. The program reads the meter at a check
frequency and at the run frequency, shows the value and any warnings, and fills in the
field. Every reading in a run then has it subtracted (C and D, either circuit model).
The measurement record is saved in each run's JSON (`stray_info`), and the raw readings
are kept in the CSV.

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
   Reads each TEC's current temperature limits, then sets them to *stop − 5 °C* and
   *start + 5 °C* (`TEC:LIM:TLO/THI`) for the run. If a reading leaves this window, the
   controller acts on its own, normally by turning its output off.
2. **Equilibrate:** turns both TEC outputs on at the start temperature. Waits
   until both sensors are within the settle tolerance, then holds for the soak time.
   Gives up after 45 minutes.
3. **Ramp:** moves the setpoint down linearly at the ramp rate, sending the new value
   to both TECs at every sample. This is done in software rather than with `TEC:TRATE`,
   because the 5240 may not support that command. If the fine window is enabled,
   the rate and sample interval switch to the fine values inside it.
4. **Tail:** holds at the stop temperature and keeps logging until the sensors catch
   up (at most 10 minutes).
5. **Finish:** turns both TEC outputs **off**, restores the original TEC limits and reads
   them back to check, then finds the peak and saves the outputs. The same shutdown
   (outputs off, limits restored) runs if you press Stop, if an error occurs, or if you
   close the window.

## If a run ends abnormally

The limits are restored only if the program shuts the run down itself. They can be left
at the run's values (stop − 5 / start + 5 °C) if:

- the program or PC crashed, or the program was killed from Task Manager
- a USB or serial cable was unplugged, or a controller lost power, during the run
- a warning box says *TEC limits not restored* (the log names the TEC)

To check and fix this:

1. Open the run's `.json` file. If `tec_limits_restored` is not `true`, the limits were
   not restored. The values to put back are under `tec_limits_original` (`[low, high]`
   in °C for `tec1` and `tec2`).
2. On each Arroyo 5240, set the low and high temperature limits back to those values
   from the front panel, or over serial with `TEC:LIM:TLO <low>` and `TEC:LIM:THI <high>`.
3. If the outputs might still be on (crash, killed program), turn them off on the front
   panels first.

Fix the limits before the next run. The next run reads whatever limits it finds as the
"original" ones, so leftover run limits would be kept.

## Output (in the chosen folder)

| File | Contents |
|---|---|
| `<sample>_<YYYYmmdd_HHMMSS>.csv` | `timestamp, elapsed_s, phase, setpoint_C, T1_C, T2_C, T_avg_C, C_F, D, C_meas_F, D_meas` (capacitance in farads). `C_F`/`D` have the **Stray C** setting subtracted; `C_meas_F`/`D_meas` are the meter's raw readings |
| `….png` | Capacitance vs. temperature, with the fine window shaded and the peak marked (D stays in the CSV) |
| `….json` | Run settings, instrument IDs, the original TEC limits and whether they were restored, and the peak result |

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
- **TEC limit queries:** the original limits are read with `TEC:LIM:TLO?` and
  `TEC:LIM:THI?`. Check that the log shows sensible "limits were …" values and
  "limits restored" at the end of the first run.
- **Frequencies:** the frequency list in the GUI is the one the programming manual gives
  for `FREQ` (50 Hz–10 kHz). The box is editable, so you can try other MCR5200
  frequencies (for example `100k`).

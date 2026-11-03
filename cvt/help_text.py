"""Short explanations shown by the ⓘ buttons next to each GUI input."""

HELP = {
    # Instruments
    "tec1_port": "COM port of the first Arroyo 5240. Find it in Windows Device Manager → "
                 "Ports (COM & LPT). Both TECs get the same set point; this one's sensor is "
                 "logged as T1_C.",
    "tec2_port": "COM port of the second Arroyo 5240. Its sensor is logged as T2_C. The sample "
                 "temperature used for the plot is the average of T1 and T2.",
    "lcr_port": "COM port of the MCR5200 LCR meter (RS-232). Press ↻ to rescan after plugging "
                "in a USB-serial adapter.",
    "tec_baud": "Serial speed for both TECs. Over USB the Arroyo always uses 38400. Over RS-232 "
                "it uses whatever is set on the controller's front panel (factory default 9600).",
    "lcr_baud": "Serial speed for the MCR5200. Must match SYSTEM → COMM SETUP → BaudRate on the "
                "meter, and the meter's Bus Mode must be RS232C.",
    "simulate": "Runs the whole workflow with fake instruments (a transition at 27.3 °C) at "
                "30× speed. Ports are ignored. Use it to try settings without hardware.",

    # LCR meter
    "frequency": "Frequency of the AC test signal. Some materials' peak position depends on "
                 "frequency, so keep it the same between runs you want to compare. Default 1 kHz.",
    "level": "Amplitude of the AC test signal across the sample. 1.0 V gives the best accuracy; "
             "use a lower level if the sample's response depends on field strength.",
    "speed": "How long the meter takes per measurement (fixed presets). Slower = less noise. "
             "The program reads the latest value once per sample interval, so SLOW is plenty "
             "for intervals of 0.5 s or longer.",
    "circuit": "Model used to turn the measured impedance into a capacitance. Parallel (Cp) "
               "suits small, high-impedance samples such as most dielectrics. Series (Cs) suits "
               "large, low-impedance capacitors.",
    "average": "The meter averages this many measurements into each reading. Lowers noise, but "
               "each reading takes N times longer: at SLOW keep N × 0.33 s under the sample "
               "interval. Leave at 1 unless the trace is noisy.",

    "stray_pF": "Capacitance of the leads and holder, subtracted from every reading as a "
                "capacitance in parallel with the sample (D is corrected too). Press Measure… "
                "with the holder's banana leads unplugged from the BNC adapter (clips and cable "
                "left in place) and the program reads it from the meter. Expect a few pF, plus "
                "about 30 pF per foot of BNC cable. Leave at 0 if the meter's own open "
                "correction already removes it. A "
                "constant stray does not move the peak temperature. Raw readings are kept in the "
                "CSV as C_meas_F and D_meas.",

    # Temperature program
    "start_temp": "Where the run begins (the high end). The TECs bring the sample here and hold "
                  "it before cooling starts. Set it above the transition.",
    "stop_temp": "Where the run ends (the low end). Must be below the start temperature. Set it "
                 "below the transition.",
    "ramp_rate": "How fast the set point cools, in °C per minute. Slower ramps keep the sample "
                 "closer to the sensor temperature, so the peak position is more accurate.",
    "sample_interval": "Time between data points during the normal ramp. At 0.5 °C/min, every "
                       "2 s gives a point roughly every 0.017 °C.",
    "soak_min": "Once both sensors are within the settle tolerance of the start temperature, "
                "hold this long before cooling so the whole sample reaches temperature.",
    "settle_tol": "How close (± °C) both TEC sensors must be to the target to count as "
                  "\"at temperature\": checked before the soak and before the run finishes.",

    # Fine window
    "fine_enabled": "Use a slower ramp and faster sampling inside a temperature range, "
                    "typically around a peak found in an earlier, faster run.",
    "fine_upper": "Top of the fine window (°C). Cooling switches to the fine rate once the set "
                  "point passes this temperature.",
    "fine_lower": "Bottom of the fine window (°C). Cooling returns to the normal rate below "
                  "this. The window must lie inside the start/stop range.",
    "fine_rate": "Cooling rate inside the fine window, in °C per minute (e.g. 0.1).",
    "fine_interval": "Time between data points inside the fine window (minimum 0.5 s).",

    # Output
    "output_dir": "Folder where each run's CSV (data), PNG (plot) and JSON (settings and peak) "
                  "are saved. Created if it doesn't exist.",
    "sample_name": "Used in the file names (<name>_<date>_<time>.csv) and the plot title.",
    "smoothing_C": "Width (°C) of the averaging window used to smooth C(T) before picking the "
                   "peak. Wider ignores more noise but can flatten a sharp peak. Analysis only: "
                   "use Re-analyze CSV to try another value later.",
}

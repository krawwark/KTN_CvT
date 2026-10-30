"""python -m cvt                      launch the GUI
python -m cvt analyze RUN.csv      re-find the peak and rewrite RUN.png
"""
import argparse
import sys


def main():
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="cvt", description="Capacitance vs. temperature measurement.")
    sub = parser.add_subparsers(dest="cmd")
    a = sub.add_parser("analyze", help="re-analyze a saved run CSV and rewrite its plot")
    a.add_argument("csv")
    a.add_argument("--smoothing", type=float, help="smoothing width in °C (default: value used for the run)")
    args = parser.parse_args()

    if args.cmd == "analyze":
        from .analysis import analyze_file
        peak, png = analyze_file(args.csv, args.smoothing)
        print(peak.describe() if peak else "No peak found.")
        print(f"Plot: {png}")
    else:
        from .gui import main as gui_main
        gui_main()


if __name__ == "__main__":
    main()

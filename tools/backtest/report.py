"""Generacion de informe de backtest (esqueleto)."""
import argparse
from datetime import date


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = args.out or f"reports/backtest_{date.today().isoformat()}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Backtest report\n\nPendiente de implementacion completa.\n")
    print("written", out)


if __name__ == "__main__":
    main()
PY
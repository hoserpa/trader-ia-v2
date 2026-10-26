"""Matriz de experimentos (esqueleto)."""
import argparse


def run_all():
    print("E0 baseline")
    print("E1 rango x umbral x niveles")
    print("E2 V1 shift")
    print("E3 V2 trend filter")
    print("E4 V3 long-only")
    print("E5 V4/V5")
    print("E6 combinacion")
    print("Implementar barridos con tools.backtest.harness")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        run_all()
PY
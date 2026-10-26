"""Matriz de experimentos."""
import argparse
import asyncio
import json
from datetime import datetime

from tools.backtest.harness import run_backtest
from tools.backtest.metrics import compute_metrics


def run_e0():
    res = asyncio.run(run_backtest(start="2025-10-01", end="2026-09-30", resolution="5m"))
    m = compute_metrics(res["equity"], res["trades"], [])
    print("E0", m)
    return m


def run_all():
    results = {"E0": run_e0()}
    with open("reports/experiments_latest.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if args.all:
        run_all()
PY
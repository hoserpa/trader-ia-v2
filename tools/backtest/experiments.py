"""Matriz de experimentos del backtest."""
import argparse
import asyncio
import json
import os
from datetime import datetime

from tools.backtest.harness import run_backtest
from tools.backtest.metrics import compute_metrics


def _run(overrides, start, end, resolution="5m"):
    return asyncio.run(run_backtest(config_overrides=overrides, start=start, end=end, resolution=resolution))


def run_e0(start, end):
    res = _run({}, start, end)
    return {"id": "E0", "overrides": {}, "metrics": compute_metrics(res["equity"], res["trades"], [])}


def run_e1(start, end):
    results = []
    for rng in (0.06, 0.08, 0.10, 0.12):
        for thr in (0.04, 0.06, 0.08, 0.10, 0.12):
            for levels in (10, 15, 20):
                spacing = 2 * rng / (levels - 1)
                if spacing < 1.5 * (0.0016 + 0.0026):
                    continue
                if thr > rng:
                    continue
                res = _run({"range_pct": rng, "rebalance_threshold": thr, "levels_per_pair": levels}, start, end)
                m = compute_metrics(res["equity"], res["trades"], [])
                m["params"] = {"range_pct": rng, "rebalance_threshold": thr, "levels": levels}
                results.append(m)
    return results


def run_all(start, end):
    out = {"E0": run_e0(start, end), "E1": run_e1(start, end)}
    os.makedirs("reports", exist_ok=True)
    with open("reports/experiments_latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    # markdown summary
    lines = ["# Experimentos backtest", "", f"Periodo: {start} a {end}", "", "## E0", str(out["E0"]["metrics"]), "", "## E1 top 5 por retorno"]
    e1 = sorted(out["E1"], key=lambda x: x["metrics"].get("total_return_pct", -999), reverse=True)[:5]
    for r in e1:
        lines.append(f"- {r['params']}: {r['metrics']}")
    with open("reports/experiments_latest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done", len(out["E1"]), "cells")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-09-30")
    args = ap.parse_args()
    if args.all:
        run_all(args.start, args.end)

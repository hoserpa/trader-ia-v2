"""Matriz de experimentos del backtest."""
import argparse
import asyncio
import json
import os

from loguru import logger

logger.remove()

from tools.backtest.harness import run_backtest
from tools.backtest.metrics import compute_metrics


def cells():
    out = []
    for rng in (0.06, 0.08, 0.10, 0.12):
        for thr in (0.04, 0.06, 0.08, 0.10, 0.12):
            for levels in (10, 15, 20):
                spacing = 2 * rng / (levels - 1)
                if spacing < 1.5 * (0.0016 + 0.0026):
                    continue
                if thr > rng:
                    continue
                out.append({"range_pct": rng, "rebalance_threshold": thr, "levels_per_pair": levels})
    return out


def run_cell(ov, start, end):
    res = asyncio.run(run_backtest(config_overrides=ov, start=start, end=end, resolution="5m"))
    m = compute_metrics(res["equity"], res["trades"], [])
    return {"params": ov, "metrics": m}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-idx", type=int, default=0)
    ap.add_argument("--end-idx", type=int, default=None)
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--out", default="reports/experiments_chunk.json")
    args = ap.parse_args()

    all_cells = cells()
    end = args.end_idx if args.end_idx is not None else len(all_cells)
    chunk = all_cells[args.start_idx:end]
    results = []
    for i, ov in enumerate(chunk, start=args.start_idx):
        print(f"cell {i+1}/{len(all_cells)} {ov}", flush=True)
        results.append(run_cell(ov, args.start, args.end))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print("done", len(results))


if __name__ == "__main__":
    main()

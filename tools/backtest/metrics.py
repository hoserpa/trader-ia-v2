"""Metricas de backtest."""
import numpy as np


def compute_metrics(equity_series, trades, rebalances):
    eq = np.asarray(equity_series, dtype=float)
    if len(eq) == 0:
        return {}
    returns = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    monthly = {}
    # equity indexed by step; caller can pass monthly buckets separately
    return {
        "final_equity": float(eq[-1]),
        "total_return_pct": float((eq[-1] / eq[0] - 1) * 100),
        "max_drawdown_pct": float(dd.max() * 100),
        "n_trades": len(trades),
        "n_rebalances": len(rebalances),
    }

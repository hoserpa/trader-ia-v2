"""Comparacion forward test (demo vs backtest)."""
import argparse
import json
import sqlite3
from datetime import datetime


def real_summary(db_path, start, end):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM trades WHERE timestamp>=? AND timestamp<=?", (start, end)
    ).fetchall()
    rows = [dict(r) for r in rows]
    pnl = 0.0
    for r in rows:
        if r["pnl_eur"] is not None:
            pnl += r["pnl_eur"] or 0.0
        else:
            pnl -= r["fee_eur"] or 0.0
            if (r["side"] or "").upper() == "SELL":
                pnl -= (r["price"] or 0) * (r["amount_crypto"] or 0) * 0.0002
    reb = [r for r in rows if r.get("reason") == "rebalance" and r.get("pnl_eur") is not None]
    return {
        "trades": len(rows),
        "closed": len([r for r in rows if r["pnl_eur"] is not None]),
        "pnl_eur": round(pnl, 4),
        "rebalances": len(reb),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--backtest", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="reports/forward_latest.md")
    args = ap.parse_args()

    real = real_summary(args.db, args.start, args.end)
    bt = json.load(open(args.backtest))
    bt_metrics = bt.get("metrics", bt)

    lines = [
        "# Forward test",
        "",
        f"Periodo: {args.start} a {args.end}",
        "",
        "## Real (demo)",
        f"- Trades: {real['trades']}",
        f"- Cierres: {real['closed']}",
        f"- PnL: {real['pnl_eur']} EUR",
        f"- Rebalanceos: {real['rebalances']}",
        "",
        "## Backtest",
        f"- {json.dumps(bt_metrics, indent=2)}",
        "",
        "## Tolerancia",
        "- PnL real vs backtest: <= 30% o <= 1 pp, el mayor.",
        "- Rebalanceos: mismos pares y fechas +-24 h.",
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("written", args.out)


if __name__ == "__main__":
    main()
PY
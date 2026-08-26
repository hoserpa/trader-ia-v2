import json

bt = json.load(open("training/output/model_kraken_fresh/backtest_results.json"))
pnl = bt["total_pnl_eur"]
wr = bt["win_rate"]
prec = bt.get("precision", {})
prec_buy = prec.get("precision_buy", 0)
prec_sell = prec.get("precision_sell", 0)
print(f"PnL: {pnl:+.2f} EUR  WR: {wr:.1%}  Prec_BUY: {prec_buy:.3f}  Prec_SELL: {prec_sell:.3f}")

checks = [
    ("pnl_positive", pnl > 0),
    ("wr_ok", wr >= 0.48),
    ("prec_buy_ok", prec_buy >= 0.45),
    ("prec_sell_ok", prec_sell >= 0.45),
]
for k, v in checks:
    status = "PASS" if v else "FAIL"
    print(f"  {k}: {status}")
all_pass = all(v for _, v in checks)
print(f"Overall: {'GATE PASSED' if all_pass else 'GATE FAILED'}")

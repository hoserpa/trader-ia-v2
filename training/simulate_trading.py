"""Simulador de 30 dias de trading usando datos historicos y el modelo entrenado."""
import sys
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import asyncio
import argparse

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker

from bot.config import config
from bot.database.models import Base, Candle, Trade, Position
from bot.database.crud import upsert_candles, get_candle_count
from bot.database.init_db import init_db, SessionLocal
from bot.indicators.technical import calculate_indicators, get_atr
from bot.indicators.features import FeatureBuilder
from bot.model.predictor import ModelPredictor


async def fetch_historical_data(pair: str, days: int = 90) -> int:
    """Descarga datos historicos para un par."""
    import ccxt.async_support as ccxt
    
    exchange_id = config.exchange.name.lower()
    exchange = getattr(ccxt, exchange_id)({
        "apiKey": config.exchange.api_key,
        "secret": config.exchange.api_secret,
        "enableRateLimit": True,
        "timeout": 30000,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    timeframe = config.trading.timeframe
    all_candles = []
    limit = 300

    print(f"Descargando {pair} ({days} dias, {timeframe})...")
    try:
        await exchange.load_markets()
        while True:
            try:
                ohlcv = await exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            except Exception as e:
                print(f"  Error: {e}")
                break
            if not ohlcv:
                break
            all_candles.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < limit:
                break
            await asyncio.sleep(0.5)
    finally:
        await exchange.close()

    if not all_candles:
        print(f"  Sin datos para {pair}")
        return 0

    candles_data = [{
        "pair": pair,
        "timeframe": timeframe,
        "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
        "open": row[1], "high": row[2], "low": row[3],
        "close": row[4], "volume": row[5],
    } for row in all_candles]

    db = SessionLocal()
    try:
        inserted = upsert_candles(db, candles_data)
    finally:
        db.close()

    print(f"  {inserted} velas almacenadas")
    return inserted


async def ensure_historical_data(db_path: str, days: int = 90) -> bool:
    """Descarga datos si no existen suficientes."""
    init_db()
    required_candles = int((days * 24 * 60) / 5)
    
    needs_download = False
    for pair in config.trading.pairs:
        db = SessionLocal()
        try:
            existing = get_candle_count(db, pair, config.trading.timeframe)
        finally:
            db.close()
        
        if existing < required_candles:
            print(f"Datos insuficientes para {pair}: {existing} < {required_candles}")
            needs_download = True
        else:
            print(f"Datos OK para {pair}: {existing} velas")

    if needs_download:
        print("\nDescargando datos historicos...")
        for pair in config.trading.pairs:
            await fetch_historical_data(pair, days)
        return True
    return False


@dataclass
class SimPosition:
    pair: str
    amount_crypto: float
    entry_price: float
    entry_timestamp: datetime
    stop_loss_price: float
    take_profit_price: float
    amount_eur_invested: float


@dataclass
class SimTrade:
    pair: str
    side: str
    amount_crypto: float
    amount_eur: float
    price: float
    fee_eur: float
    timestamp: datetime
    pnl_eur: float = 0.0


@dataclass
class SimPortfolio:
    balance_eur: float
    initial_balance: float
    positions: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    equity_history: list = field(default_factory=list)

    def total_value(self, prices: dict) -> float:
        crypto_value = sum(
            pos.amount_crypto * prices.get(pos.pair, pos.entry_price)
            for pos in self.positions.values()
        )
        return self.balance_eur + crypto_value

    def add_trade(self, trade: SimTrade):
        self.trades.append(trade)

    def open_position(self, pair: str, pos: SimPosition):
        self.positions[pair] = pos

    def close_position(self, pair: str) -> Optional[SimPosition]:
        return self.positions.pop(pair, None)


class TradingSimulator:
    def __init__(self, db_path: str, days: int = 30):
        self.db_path = db_path
        self.days = days
        self.initial_balance = config.trading.demo_initial_balance
        self.portfolio = SimPortfolio(
            balance_eur=self.initial_balance,
            initial_balance=self.initial_balance,
        )
        self.predictor = ModelPredictor()
        self.feature_builder = FeatureBuilder()
        self.pairs = config.trading.pairs
        self.timeframe = config.trading.timeframe
        self.candles_required = config.trading.candles_required

    def run(self):
        print("=" * 70)
        print("SIMULADOR DE TRADING - 30 DIAS")
        print("=" * 70)
        print(f"Fecha inicio: {datetime.utcnow() - timedelta(days=self.days)}")
        print(f"Fecha fin:    {datetime.utcnow()}")
        print(f"Pares:        {', '.join(self.pairs)}")
        print(f"Timeframe:    {self.timeframe}")
        print(f"Balance ini:  {self.initial_balance:.2f} EUR")
        print(f"Buy thresh:   {config.risk.buy_threshold:.0%}")
        print(f"Sell thresh:  {config.risk.sell_threshold:.0%}")
        print(f"Max posiciones: {config.risk.max_open_positions}")
        print(f"Stop loss ATR: {config.risk.stop_loss_atr_multiplier}x")
        print(f"Take profit ATR: {config.risk.take_profit_atr_multiplier}x")
        print("=" * 70)

        if not self.predictor.is_model_loaded():
            print("ERROR: Modelo no cargado. Ejecuta primero el entrenamiento.")
            return

        engine = create_engine(f"sqlite:///{self.db_path}")
        Session = sessionmaker(bind=engine)
        session = Session()

        candles_data = {}
        for pair in self.pairs:
            candles = self._load_candles(session, pair)
            if candles is None or len(candles) < self.candles_required + 100:
                print(f"AVISO: Datos insuficientes para {pair}, saltando.")
                continue
            candles_data[pair] = candles
            print(f"Datos {pair}: {len(candles)} velas")

        if not candles_data:
            print("ERROR: No hay datos suficientes para ningun par.")
            return

        candles_by_ts = self._get_timestamps_to_simulate(candles_data)
        print(f"\nSimulando {len(candles_by_ts)} ciclos de trading...")
        print("-" * 70)

        progress_interval = max(1, len(candles_by_ts) // 20)
        simulated_trades = 0
        simulated_days = set()

        for i, ts in enumerate(candles_by_ts):
            simulated_days.add(ts.date())
            prices = {}
            candles_for_analysis = {}

            for pair, candles in candles_data.items():
                idx = candles["timestamp"].searchsorted(ts)
                if idx >= len(candles):
                    continue
                candles_for_analysis[pair] = candles.iloc[:idx+1].copy()
                prices[pair] = float(candles.iloc[idx]["close"])

            if not prices:
                continue

            self._process_analysis(prices, candles_for_analysis)

            equity = self.portfolio.total_value(prices)
            self.portfolio.equity_history.append({
                "timestamp": ts,
                "equity": equity,
                "positions": len(self.portfolio.positions),
            })

            if i % progress_interval == 0:
                pct = (i / len(candles_by_ts)) * 100
                print(f"Progreso: {pct:5.1f}% | Equity: {equity:10.2f} EUR | "
                      f"Posiciones: {len(self.portfolio.positions)} | Trades: {len(self.portfolio.trades)}")

        session.close()

        print("-" * 70)
        print("SIMULACION COMPLETADA")
        print("=" * 70)

        self._print_report()

    def _load_candles(self, session: sessionmaker, pair: str) -> Optional[pd.DataFrame]:
        candles = session.query(Candle).filter(
            Candle.pair == pair,
            Candle.timeframe == self.timeframe,
        ).order_by(Candle.timestamp).all()

        if not candles:
            return None

        df = pd.DataFrame([{
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        } for c in candles])

        cutoff = datetime.utcnow() - timedelta(days=self.days)
        df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
        return df

    def _get_timestamps_to_simulate(self, candles_data: dict) -> list:
        all_timestamps = set()
        for candles in candles_data.values():
            all_timestamps.update(candles["timestamp"].tolist())

        cutoff = datetime.utcnow() - timedelta(days=self.days)
        timestamps = sorted([ts for ts in all_timestamps if ts >= cutoff])
        return timestamps

    def _process_analysis(self, prices: dict, candles_for_analysis: dict):
        for pair, candles in candles_for_analysis.items():
            if len(candles) < self.candles_required:
                continue

            current_price = prices.get(pair)
            if not current_price:
                continue

            df_with_indicators = calculate_indicators(candles)
            features = self.feature_builder.build_features(df_with_indicators)

            if features is None:
                continue

            signal = self.predictor.predict(features)
            if signal is None:
                continue

            if pair in self.portfolio.positions:
                self._check_exit(pair, current_price, signal)
            else:
                self._check_entry(pair, current_price, signal, df_with_indicators)

    def _check_entry(self, pair: str, current_price: float, signal: dict, df: pd.DataFrame):
        if signal["signal"] != "BUY":
            return
        if signal["confidence"] < config.risk.buy_threshold:
            return
        if len(self.portfolio.positions) >= config.risk.max_open_positions:
            return

        atr = get_atr(df)
        atr_pct = atr / current_price if current_price > 0 else 0

        if atr_pct > config.risk.high_volatility_atr_threshold:
            return

        total_value = self.portfolio.total_value({pair: current_price})
        risk_amount = total_value * config.risk.max_risk_per_trade_pct

        if atr > 0:
            stop_distance_pct = atr_pct * config.risk.stop_loss_atr_multiplier
            position_size = risk_amount / stop_distance_pct
        else:
            position_size = risk_amount * 5

        position_size = min(position_size, total_value * 0.20)
        position_size = min(position_size, self.portfolio.balance_eur * 0.95)

        if position_size < config.risk.min_trade_eur:
            return

        amount_crypto = position_size / current_price
        stop_loss = current_price - (atr * config.risk.stop_loss_atr_multiplier)
        take_profit = current_price + (atr * config.risk.take_profit_atr_multiplier)

        fee = position_size * config.exchange.taker_fee
        self.portfolio.balance_eur -= position_size

        pos = SimPosition(
            pair=pair,
            amount_crypto=amount_crypto,
            entry_price=current_price,
            entry_timestamp=datetime.utcnow(),
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            amount_eur_invested=position_size,
        )
        self.portfolio.open_position(pair, pos)

        trade = SimTrade(
            pair=pair,
            side="buy",
            amount_crypto=amount_crypto,
            amount_eur=position_size,
            price=current_price,
            fee_eur=fee,
            timestamp=datetime.utcnow(),
        )
        self.portfolio.add_trade(trade)

    def _check_exit(self, pair: str, current_price: float, signal: dict):
        pos = self.portfolio.positions.get(pair)
        if not pos:
            return

        should_exit = False
        reason = ""

        if current_price <= pos.stop_loss_price:
            should_exit = True
            reason = "stop_loss"
        elif current_price >= pos.take_profit_price:
            should_exit = True
            reason = "take_profit"
        elif signal["signal"] == "SELL" and signal["confidence"] >= config.risk.sell_threshold:
            should_exit = True
            reason = f"signal ({signal['confidence']:.0%})"

        if should_exit:
            amount_eur = pos.amount_crypto * current_price
            fee = amount_eur * config.exchange.taker_fee
            net_proceeds = amount_eur - fee
            pnl = net_proceeds - pos.amount_eur_invested

            self.portfolio.balance_eur += net_proceeds

            trade = SimTrade(
                pair=pair,
                side="sell",
                amount_crypto=pos.amount_crypto,
                amount_eur=amount_eur,
                price=current_price,
                fee_eur=fee,
                timestamp=datetime.utcnow(),
                pnl_eur=pnl,
            )
            self.portfolio.add_trade(trade)
            self.portfolio.close_position(pair)

    def _print_report(self):
        trades = self.portfolio.trades
        buys = [t for t in trades if t.side == "buy"]
        sells = [t for t in trades if t.side == "sell"]

        final_equity = self.portfolio.equity_history[-1]["equity"] if self.portfolio.equity_history else self.initial_balance
        total_return = (final_equity - self.initial_balance) / self.initial_balance * 100

        closed_positions = [t for t in trades if t.side == "sell"]
        winners = [t for t in closed_positions if t.pnl_eur > 0]
        losers = [t for t in closed_positions if t.pnl_eur <= 0]

        win_rate = len(winners) / len(closed_positions) * 100 if closed_positions else 0

        max_dd, peak, peak_time = self._calculate_max_drawdown()

        sharpe = self._calculate_sharpe()

        days_simulated = len(set(e["timestamp"].date() for e in self.portfolio.equity_history))

        print("\n" + "=" * 70)
        print(" INFORME DE SIMULACION")
        print("=" * 70)
        print(f"\n[RENTABILIDAD]")
        print(f"  Balance inicial:     {self.initial_balance:>10.2f} EUR")
        print(f"  Balance final:       {final_equity:>10.2f} EUR")
        print(f"  Return total:        {total_return:>10.2f}%")
        print(f"  Mejor trade:         {max((t.pnl_eur for t in closed_positions), default=0):>10.2f} EUR")
        print(f"  Peor trade:          {min((t.pnl_eur for t in closed_positions), default=0):>10.2f} EUR")

        print(f"\n[RIESGO]")
        print(f"  Max drawdown:        {max_dd:>10.2f}%")
        print(f"  Sharpe ratio:       {sharpe:>10.2f}")

        print(f"\n[OPERATIVA]")
        print(f"  Dias simulados:      {days_simulated:>10d}")
        print(f"  Total trades:        {len(trades):>10d}")
        print(f"  Compras:             {len(buys):>10d}")
        print(f"  Ventas:              {len(sells):>10d}")
        print(f"  Posiciones abiertas: {len(self.portfolio.positions):>10d}")
        print(f"  Ganadores:          {len(winners):>10d}")
        print(f"  Perdedores:          {len(losers):>10d}")
        print(f"  Win rate:           {win_rate:>10.1f}%")
        if closed_positions:
            avg_pnl = sum(t.pnl_eur for t in closed_positions) / len(closed_positions)
            print(f"  PnL medio:          {avg_pnl:>10.2f} EUR")

        print(f"\n[COMISIONES]")
        total_fees = sum(t.fee_eur for t in trades)
        print(f"  Total comisiones:   {total_fees:>10.2f} EUR")

        if trades:
            print(f"\n[DETALLE DE TRADES]")
            print(f"  {'Fecha':<20} {'Par':<10} {'Side':<6} {'Precio':<12} {'PnL':<10}")
            print(f"  {'-'*20} {'-'*10} {'-'*6} {'-'*12} {'-'*10}")
            for trade in sorted(trades, key=lambda t: t.timestamp):
                pnl_str = f"{trade.pnl_eur:+.2f}" if trade.side == "sell" else ""
                print(f"  {trade.timestamp.strftime('%Y-%m-%d %H:%M'):<20} "
                      f"{trade.pair:<10} {trade.side:<6} "
                      f"{trade.price:<12.2f} {pnl_str:<10}")

        print("\n" + "=" * 70)

    def _calculate_max_drawdown(self) -> tuple:
        if len(self.portfolio.equity_history) < 2:
            return 0.0, self.initial_balance, None

        values = [e["equity"] for e in self.portfolio.equity_history]
        peak = values[0]
        max_dd = 0.0
        peak_time = self.portfolio.equity_history[0]["timestamp"]

        for i, value in enumerate(values):
            if value > peak:
                peak = value
                peak_time = self.portfolio.equity_history[i]["timestamp"]
            dd = (peak - value) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return max_dd, peak, peak_time

    def _calculate_sharpe(self) -> float:
        if len(self.portfolio.equity_history) < 10:
            return 0.0

        returns = []
        for i in range(1, len(self.portfolio.equity_history)):
            prev = self.portfolio.equity_history[i-1]["equity"]
            curr = self.portfolio.equity_history[i]["equity"]
            if prev > 0:
                ret = (curr - prev) / prev
                returns.append(ret)

        if not returns:
            return 0.0

        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        if std_ret == 0:
            return 0.0

        sharpe = (mean_ret / std_ret) * np.sqrt(252 * 24)
        return sharpe


def main():
    parser = argparse.ArgumentParser(description="Simulador de trading")
    parser.add_argument("--days", type=int, default=30, help="Dias a simular (default: 30)")
    parser.add_argument("--download", action="store_true", help="Descargar datos si no existen")
    args = parser.parse_args()

    db_path = os.path.join(os.path.dirname(__file__), "data", "crypto_trader.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(os.path.dirname(__file__), "..", "data", "crypto_trader.db")

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    config.database.sqlite_path = db_path

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        if args.download or not os.path.exists(db_path):
            loop.run_until_complete(ensure_historical_data(db_path, days=90))
        
        if os.path.exists(db_path):
            simulator = TradingSimulator(db_path=db_path, days=args.days)
            simulator.run()
        else:
            print("ERROR: No se pudo preparar la base de datos.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()

"""Stub de IBKR MarketDataProvider.

Se activa cuando IBKR TWS/Gateway esté configurado.
Usa ib_insync para datos históricos OHLCV diarios.
"""
from datetime import date
from typing import Optional

import pandas as pd
from loguru import logger


class IBKRMarketDataProvider:
    """Proveedor de datos IBKR via ib_insync (stub)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None

    def _connect(self) -> bool:
        """Conecta a TWS/Gateway. Retorna True si exitoso."""
        if self._ib is not None:
            return True
        try:
            from ib_insync import IB
            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=self.client_id)
            logger.info(f"IBKR conectado: {self.host}:{self.port}")
            return True
        except ImportError:
            logger.warning("ib_insync no instalado. IBKR no disponible.")
            return False
        except Exception as e:
            logger.warning(f"IBKR no disponible: {e}")
            return False

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Descarga OHLCV diarios desde IBKR. Requiere TWS/Gateway activo."""
        if not self._connect():
            return pd.DataFrame()

        try:
            from ib_insync import Stock, util
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)

            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=end.isoformat(),
                durationStr=f"{(end - start).days + 1} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                return pd.DataFrame()

            df = util.df(bars)
            df = df.rename(columns={
                "date": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
            })
            df.index = pd.to_datetime(df["date"])
            df.index.name = "date"
            df = df[["open", "high", "low", "close", "volume"]]
            df = df.loc[start.isoformat():end.isoformat()]
            return df

        except Exception as e:
            logger.error(f"IBKR error descargando {symbol}: {e}")
            return pd.DataFrame()

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Último precio de cierre desde IBKR."""
        if not self._connect():
            return None
        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract, genericTickList="225")
            self._ib.sleep(2)
            price = ticker.close
            self._ib.cancelMktData(contract)
            return float(price) if price else None
        except Exception as e:
            logger.warning(f"IBKR latest price error {symbol}: {e}")
            return None

    def disconnect(self):
        """Desconecta de TWS/Gateway."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._ib = None

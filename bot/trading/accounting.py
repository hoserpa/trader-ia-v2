"""Contabilidad unica del grid (balance realizado).

Concentra las comisiones de margen y el efecto de cada fila de `trades` sobre
el balance. Cualquier reconstruccion del saldo debe pasar por estas funciones.
"""


def margin_short_open(notional_eur: float, rate: float) -> float:
    """Fee de apertura de margen sobre el importe prestado (Kraken 0.01-0.05%)."""
    return round(notional_eur * rate, 4)


def margin_short_rollover(
    notional_eur: float, elapsed_hours: float, rate: float, per_hours: float = 4.0
) -> float:
    """Rollover acumulado de margen, proporcional al tiempo abierto (Kraken cada 4h)."""
    return round(notional_eur * rate * (elapsed_hours / per_hours), 4)


def trade_margin_fee(
    side, pnl_eur, margin_fee_eur, amount, price, fallback_open_fee_pct
) -> float:
    """Comision de margen efectiva de una fila.

    En cierres el rollover ya va dentro de `pnl_eur`, asi que no se cuenta dos
    veces. En aperturas antiguas sin `margin_fee_eur` se deriva la fee de
    apertura del corto con el porcentaje de fallback.
    """
    if pnl_eur is not None:
        return 0.0
    margin_fee = margin_fee_eur
    if margin_fee is None and str(side).upper() == "SELL":
        margin_fee = margin_short_open(
            float(price or 0.0) * float(amount or 0.0), fallback_open_fee_pct
        )
    return float(margin_fee or 0.0)


def trade_ledger_delta(
    side, pnl_eur, fee_eur, margin_fee_eur, amount, price, fallback_open_fee_pct
) -> float:
    """Efecto de un trade sobre el balance realizado. Unica fuente de verdad."""
    if pnl_eur is not None:  # cierre: ya neto de su comision (y rollover)
        return float(pnl_eur)
    margin_fee = trade_margin_fee(
        side, pnl_eur, margin_fee_eur, amount, price, fallback_open_fee_pct
    )
    return -float(fee_eur or 0.0) - margin_fee

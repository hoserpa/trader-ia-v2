"""Test de validacion de variables criticas del grid al arrancar.

Una config invalida (leverage<1, levels<2, capital fuera de (0,1], rango o
umbral <=0, poll<=0) debe parar el arranque con ValueError en lugar de operar
con valores absurdos. Los pares se normalizan (strip + descartar vacios).
"""
import sys

sys.path.insert(0, "bot")

from config import AppConfig, _split_pairs


def _base():
    c = AppConfig()
    c.trading.mode = "demo"
    c.grid.leverage = 1
    c.grid.levels_per_pair = 15
    c.grid.capital_pct = 0.9
    c.grid.range_pct = 0.08
    c.grid.rebalance_threshold = 0.08
    c.grid.poll_interval = 15
    c.grid.stop_loss_pct = 0.05
    return c


def _rejects(c) -> bool:
    try:
        c.validate()
        return False
    except ValueError:
        return True


def test_validate_rechaza_leverage_cero():
    c = _base()
    c.grid.leverage = 0
    assert _rejects(c)


def test_validate_rechaza_niveles_menores_a_2():
    c = _base()
    c.grid.levels_per_pair = 1
    assert _rejects(c)


def test_validate_rechaza_capital_fuera_de_rango():
    for bad in (0.0, 1.5, -0.1):
        c = _base()
        c.grid.capital_pct = bad
        assert _rejects(c)


def test_validate_rechaza_rango_cero():
    c = _base()
    c.grid.range_pct = 0.0
    assert _rejects(c)


def test_validate_rechaza_umbral_rebalance_cero():
    c = _base()
    c.grid.rebalance_threshold = 0.0
    assert _rejects(c)


def test_validate_rechaza_poll_interval_cero():
    c = _base()
    c.grid.poll_interval = 0
    assert _rejects(c)


def test_validate_rechaza_espaciado_menor_que_comisiones():
    c = _base()
    c.grid.range_pct = 0.001
    assert _rejects(c)


def test_validate_acepta_config_valida():
    assert not _rejects(_base())


def test_pairs_se_normalizan():
    assert _split_pairs("BTC/EUR, ETH/EUR ,,SOL/EUR") == \
        ["BTC/EUR", "ETH/EUR", "SOL/EUR"]


if __name__ == "__main__":
    for fn in (
        test_validate_rechaza_leverage_cero,
        test_validate_rechaza_niveles_menores_a_2,
        test_validate_rechaza_capital_fuera_de_rango,
        test_validate_rechaza_rango_cero,
        test_validate_rechaza_umbral_rebalance_cero,
        test_validate_rechaza_poll_interval_cero,
        test_validate_acepta_config_valida,
        test_pairs_se_normalizan,
    ):
        fn()
    print("OK: validacion de config")
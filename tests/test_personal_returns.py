from datetime import datetime

import pytest

from vnpy.dca.personal import PersonalTradeRecord, calculate_personal_investment_analysis
from vnpy.trader.constant import Direction, Exchange, Interval
from vnpy.trader.object import BarData


def trade(day: int, price: float, volume: int, cash: float) -> PersonalTradeRecord:
    return PersonalTradeRecord(
        record_id=str(day), datetime=f"2026-07-{day:02d} 14:00:00",
        vt_symbol="159792.SZSE", symbol="159792", exchange="SZSE", name="测试ETF",
        direction=Direction.LONG.value if volume > 0 else Direction.SHORT.value,
        price=price, volume=abs(volume), turnover=abs(volume * price), cash_flow=cash, fee=0,
    )


def bars(prices: list[float]) -> list[BarData]:
    return [BarData(
        gateway_name="TEST", symbol="159792", exchange=Exchange.SZSE,
        datetime=datetime(2026, 7, day), interval=Interval.DAILY,
        open_price=price, high_price=price, low_price=price, close_price=price,
    ) for day, price in enumerate(prices, 1)]


def test_daily_returns_include_capital_and_sell_proceeds_without_future_trades() -> None:
    records = [trade(1, 10, 100, -1001), trade(3, 8, 100, -801), trade(4, 9, -100, 899)]
    analysis = calculate_personal_investment_analysis("159792.SZSE", records, bars([10, 11, 8, 9, 10]))
    daily = analysis.daily_returns
    assert daily[0].buy_cash == 1001  # The day-three purchase is not known on day one.
    assert daily[0].change_pct_points is None
    assert daily[1].position_volume == 100
    assert daily[1].trade_count == 0
    assert daily[1].change_pct_points == pytest.approx(100 / 1001 * 100)
    assert daily[2].pnl == -202
    assert daily[3].position_volume == 100
    assert daily[3].sell_cash == 899
    assert daily[3].pnl == -3
    assert daily[-1].pnl == analysis.pnl == 97
    assert daily[-1].return_pct == pytest.approx(analysis.return_pct)
    assert analysis.worst_return_days[0].date == "2026-07-03"
    assert analysis.best_return_days[0].date == "2026-07-04"
    assert analysis.best_points  # Existing purchase-contribution rankings survive.


def test_return_ranking_does_not_invent_losses_or_prices() -> None:
    records = [trade(1, 10, 100, -1000)]
    analysis = calculate_personal_investment_analysis("159792.SZSE", records, bars([10, 11, 12]))
    assert analysis.worst_return_days == []
    assert len(analysis.best_return_days) == 2
    assert all(point.change_pct_points > 0 for point in analysis.best_return_days)
    no_prices = calculate_personal_investment_analysis("159792.SZSE", records, [])
    assert no_prices.daily_returns == no_prices.best_return_days == no_prices.worst_return_days == []


def test_rankings_use_dates_not_record_order_and_stop_after_liquidation() -> None:
    records = [trade(3, 12, -100, 1200), trade(1, 10, 100, -1000)]
    data = bars([10, 11, 12, 13, 14])
    analysis = calculate_personal_investment_analysis("159792.SZSE", records, list(reversed(data)))
    assert [point.date for point in analysis.daily_returns] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert analysis.daily_returns[-1].pnl == 200
    assert analysis.daily_returns[-1].position_volume == 0


def test_missing_day_is_explicit_and_first_day_is_not_ranked() -> None:
    records = [trade(1, 10, 100, -1000)]
    data = bars([10, 11, 9])
    analysis = calculate_personal_investment_analysis("159792.SZSE", records, [data[0], data[2]])
    assert len(analysis.worst_return_days) == 1
    assert analysis.worst_return_days[0].previous_date == "2026-07-01"
    assert analysis.worst_return_days[0].date == "2026-07-03"
    assert analysis.worst_return_days[0].change_pct_points == -10

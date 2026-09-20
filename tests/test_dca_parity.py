"""Desktop and headless strategies must agree for identical data and settings."""
from datetime import date, datetime, timedelta

from scripts import dca_reminder as reminder
from vnpy.dca import engine
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData


def test_periodic_strategies_match_on_same_price_history_and_anchor() -> None:
    start = date(2025, 1, 1)
    prices = [10 + ((index * 7) % 55) / 10 for index in range(241)]
    data = [reminder.PriceBar(start + timedelta(days=i), price, price + 0.2)
            for i, price in enumerate(prices)]
    bars = [BarData(
        gateway_name="TEST", symbol="159792", exchange=Exchange.SZSE,
        datetime=datetime.combine(item.trade_date, datetime.min.time()), interval=Interval.DAILY,
        close_price=item.close, high_price=item.high,
    ) for item in data]
    settings = engine.BacktestStrategyConfig(
        base_amount=4000, target_growth=4000,
        drawdown_lookback_days=120, bias_lookback_days=120,
        drawdown_bias_cycle_days=20, value_averaging_cycle_days=20,
    )
    cloud_settings = reminder.normalize_etf_config(
        {"symbol": "159792"}, {"cycle_anchor_date": start.isoformat(), "cycle_days": 20},
    )
    for key, cloud_index in [(engine.BACKTEST_STRATEGY_DRAWDOWN_DCA, 0),
                             (engine.BACKTEST_STRATEGY_BIAS_DCA, 1),
                             (engine.BACKTEST_STRATEGY_VALUE_AVERAGING, 2)]:
        records = engine.simulate_daily_investment("159792.SZSE", 100, bars, key, strategy_config=settings)
        actual = {datetime.fromisoformat(record.datetime).date(): record.volume for record in records}
        for i in range(0, len(data), 20):
            dashboard = reminder.calculate_dashboard(data[:i + 1], cloud_settings)
            assert dashboard.cycle_due
            assert dashboard.signals[cloud_index].volume == actual.get(data[i].trade_date, 0)

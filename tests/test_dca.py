from datetime import datetime
from pathlib import Path
from threading import Lock

from vnpy.dca import engine as dca_engine
from vnpy.dca.personal import parse_trade_statement_file
from vnpy.trader.constant import Exchange, Interval, Product
from vnpy.trader.object import BarData, ContractData


class FakeMainEngine:
    def __init__(self) -> None:
        self.contract_scan_count: int = 0
        self.contracts: dict[str, ContractData] = {}

    def get_all_contracts(self) -> list:
        self.contract_scan_count += 1
        return []

    def get_contract(self, vt_symbol: str):
        return self.contracts.get(vt_symbol)


def create_engine() -> tuple[dca_engine.DailyInvestmentEngine, FakeMainEngine, list[str]]:
    main_engine = FakeMainEngine()
    engine = object.__new__(dca_engine.DailyInvestmentEngine)
    engine.main_engine = main_engine
    engine.backtest_bar_cache = {}
    engine.backtest_cache_lock = Lock()
    engine.fund_name_cache = {}
    engine.fund_name_cache_lock = Lock()
    logs: list[str] = []
    engine.write_log = logs.append
    return engine, main_engine, logs


def create_bar(dt: datetime, close_price: float, interval: Interval) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="159792",
        exchange=Exchange.SZSE,
        datetime=dt,
        interval=interval,
        close_price=close_price,
    )


def create_us_bar(dt: datetime, close_price: float, interval: Interval) -> BarData:
    return BarData(
        gateway_name="YFINANCE",
        symbol="AAPL",
        exchange=Exchange.SMART,
        datetime=dt,
        interval=interval,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
    )


def write_trade_file(path: Path, rows: list[list[str]]) -> None:
    headers = [
        "清算日期",
        "交收日期",
        "操作日期",
        "发生日期",
        "发生时间",
        "业务说明",
        "资金发生数",
        "资金本次余额",
        "股份发生数",
        "股份本次余额",
        "合同序号",
        "证券代码",
        "证券名称",
        "买卖类别",
        "成交数量",
        "成交价格",
        "成交金额",
        "港股通结算汇率",
        "货币代码",
        "市场代码",
        "证券全称",
        "备注",
        "",
    ]
    content = "\n".join(["\t".join(headers), *["\t".join(row) for row in rows]]) + "\n"
    path.write_bytes(content.encode("gbk"))


def trade_row(
    date_text: str,
    time_text: str,
    contract_id: str,
    symbol: str,
    name: str,
    volume: str,
    price: str,
    amount: str,
    cash_flow: str,
    market_code: str,
) -> list[str]:
    return [
        date_text,
        date_text,
        date_text,
        date_text,
        time_text,
        "证券买入",
        cash_flow,
        "0",
        volume,
        volume,
        contract_id,
        symbol,
        name,
        "买入",
        volume,
        price,
        amount,
        "0.00000000",
        "人民币",
        market_code,
        name,
        "证券买入",
        "",
    ]


def test_resolve_known_fund_code_without_scanning_contracts() -> None:
    engine, main_engine, _ = create_engine()

    assert engine.resolve_vt_symbol("159792") == "159792.SZSE"
    assert engine.resolve_vt_symbol("510300") == "510300.SSE"
    assert engine.resolve_vt_symbol("159792.sz") == "159792.SZSE"
    assert main_engine.contract_scan_count == 0
    assert engine.describe_vt_symbol("159792.SZSE") == "159792.SZSE（深圳证券交易所）"


def test_resolve_us_backtest_symbol_without_scanning_contracts() -> None:
    engine, main_engine, _ = create_engine()

    assert engine.resolve_backtest_vt_symbol("AAPL", dca_engine.BACKTEST_MARKET_US) == "AAPL.SMART"
    assert engine.resolve_backtest_vt_symbol("msft.nasdaq", dca_engine.BACKTEST_MARKET_US) == "MSFT.NASDAQ"
    assert engine.resolve_backtest_vt_symbol("TSLA.US", dca_engine.BACKTEST_MARKET_US) == "TSLA.SMART"
    assert main_engine.contract_scan_count == 0
    assert engine.describe_vt_symbol("AAPL.SMART") == "AAPL.SMART（美股智能路由）"


def test_describe_vt_symbol_uses_local_contract_name_first() -> None:
    engine, main_engine, _ = create_engine()
    main_engine.contracts["159632.SZSE"] = ContractData(
        gateway_name="TEST",
        symbol="159632",
        exchange=Exchange.SZSE,
        name="纳斯达克ETF华安",
        product=Product.ETF,
        size=1,
        pricetick=0.001,
    )

    assert engine.get_cached_fund_name("159632.SZSE") == "纳斯达克ETF华安"
    assert engine.describe_vt_symbol("159632.SZSE") == "159632.SZSE（深圳证券交易所：纳斯达克ETF华安）"


def test_query_fund_name_uses_akshare_fallback_and_cache(monkeypatch) -> None:
    engine, _, _ = create_engine()
    calls: list[str] = []

    def load_name(vt_symbol: str) -> str:
        calls.append(vt_symbol)
        return "纳斯达克ETF华安"

    monkeypatch.setattr(dca_engine, "load_akshare_etf_name", load_name)

    assert engine.query_fund_name("159632") == "纳斯达克ETF华安"
    assert engine.query_fund_name("159632.SZSE") == "纳斯达克ETF华安"
    assert calls == ["159632.SZSE"]
    assert engine.describe_vt_symbol("159632.SZSE") == "159632.SZSE（深圳证券交易所：纳斯达克ETF华安）"


def test_query_fund_name_failure_keeps_symbol_description(monkeypatch) -> None:
    engine, _, logs = create_engine()

    def load_name(vt_symbol: str) -> str:
        raise RuntimeError("network failed")

    monkeypatch.setattr(dca_engine, "load_akshare_etf_name", load_name)

    assert engine.query_fund_name("159632") == ""
    assert engine.describe_vt_symbol("159632.SZSE") == "159632.SZSE（深圳证券交易所）"
    assert any("基金名称查询失败" in log for log in logs)


def test_live_setting_floors_order_volume_to_board_lot() -> None:
    engine, _, _ = create_engine()
    engine.setting = dca_engine.InvestmentSetting()
    engine.save_setting = lambda: None
    engine.put_update_event = lambda: None
    engine.subscribe = lambda vt_symbol, gateway_name: None

    resolved_vt_symbol = engine.update_setting(
        "159792",
        250,
        "TEST",
        False,
    )

    assert resolved_vt_symbol == "159792.SZSE"
    assert engine.setting.volume == 200


def test_parse_galaxy_trade_statement_file(tmp_path: Path) -> None:
    file_path = tmp_path / "table.xls"
    write_trade_file(
        file_path,
        [
            trade_row("20260513", "14:56:59", "DS1", "159632", "纳斯达克", "100", "2.335", "233.50", "-233.60", "1"),
            trade_row("20260514", "14:56:59", "SH1", "518850", "黄金9999", "100", "9.882", "988.20", "-988.30", "2"),
        ],
    )

    records = parse_trade_statement_file(file_path)

    assert len(records) == 2
    assert records[0].vt_symbol == "159632.SZSE"
    assert records[0].name == "纳斯达克"
    assert records[0].volume == 100
    assert records[0].price == 2.335
    assert records[0].fee == 0.1
    assert records[1].vt_symbol == "518850.SSE"


def test_import_personal_trade_file_is_idempotent_and_detects_changes(tmp_path: Path) -> None:
    engine, _, _ = create_engine()
    engine.personal_records = []
    engine.personal_imports = {}
    engine.save_personal_records = lambda: None
    engine.put_update_event = lambda: None

    file_path = tmp_path / "table.xls"
    row1 = trade_row("20260513", "14:56:59", "DS1", "159632", "纳斯达克", "100", "2.335", "233.50", "-233.60", "1")
    row2 = trade_row("20260514", "14:56:59", "DS2", "159632", "纳斯达克", "100", "2.346", "234.60", "-234.70", "1")
    write_trade_file(file_path, [row1])

    first = engine.import_personal_trades_from_file(file_path)
    repeated = engine.import_personal_trades_from_file(file_path)
    write_trade_file(file_path, [row1, row2])
    changed = engine.import_personal_trades_from_file(file_path)

    assert first.parsed_count == 1
    assert first.added_count == 1
    assert repeated.parsed_count == 1
    assert repeated.added_count == 0
    assert repeated.skipped_count == 1
    assert not repeated.changed
    assert changed.changed
    assert changed.added_count == 1
    assert len(engine.personal_records) == 2


def test_personal_investment_analysis_best_and_worst_points(monkeypatch) -> None:
    engine, _, _ = create_engine()
    engine.personal_records = [
        parse_trade_statement_file_row("20260513", "14:56:59", "DS1", "159632", "纳斯达克", "100", "2.50", "250.00", "-250.10", "1"),
        parse_trade_statement_file_row("20260514", "14:56:59", "DS2", "159632", "纳斯达克", "100", "2.00", "200.00", "-200.10", "1"),
        parse_trade_statement_file_row("20260515", "14:56:59", "DS3", "159632", "纳斯达克", "100", "1.50", "150.00", "-150.10", "1"),
    ]

    bars = [
        BarData(
            gateway_name="TEST",
            symbol="159632",
            exchange=Exchange.SZSE,
            datetime=datetime(2026, 5, 13),
            interval=Interval.DAILY,
            open_price=2.5,
            high_price=2.6,
            low_price=2.4,
            close_price=2.5,
        ),
        BarData(
            gateway_name="TEST",
            symbol="159632",
            exchange=Exchange.SZSE,
            datetime=datetime(2026, 5, 15),
            interval=Interval.DAILY,
            open_price=2.0,
            high_price=2.1,
            low_price=1.9,
            close_price=2.0,
        ),
    ]

    def load_bars(vt_symbol: str, start: datetime, end: datetime, interval: Interval) -> list[BarData]:
        return bars

    monkeypatch.setattr(dca_engine, "load_akshare_etf_bars", load_bars)

    analysis = engine.analyze_personal_investment("159632")

    assert analysis.vt_symbol == "159632.SZSE"
    assert analysis.buy_volume == 300
    assert analysis.latest_price == 2.0
    assert analysis.worst_points[0].price == 2.5
    assert analysis.best_points[0].price == 1.5


def parse_trade_statement_file_row(
    date_text: str,
    time_text: str,
    contract_id: str,
    symbol: str,
    name: str,
    volume: str,
    price: str,
    amount: str,
    cash_flow: str,
    market_code: str,
):
    file_path = Path(".vntrader") / f"unit_{contract_id}.xls"
    file_path.parent.mkdir(exist_ok=True)
    write_trade_file(
        file_path,
        [trade_row(date_text, time_text, contract_id, symbol, name, volume, price, amount, cash_flow, market_code)],
    )
    return parse_trade_statement_file(file_path)[0]


def test_backtesting_reuses_cached_bars_and_can_refresh(monkeypatch) -> None:
    engine, _, logs = create_engine()
    calls: list[str] = []
    bars = [
        create_bar(datetime(2026, 5, 25), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 1.2, Interval.DAILY),
    ]

    def load_bars(vt_symbol: str, start: datetime, end: datetime, interval: Interval) -> list[BarData]:
        calls.append(vt_symbol)
        return bars

    monkeypatch.setattr(dca_engine, "load_akshare_etf_bars", load_bars)
    start = datetime(2026, 5, 25)
    end = datetime(2026, 5, 26, 23, 59, 59)

    first = engine.run_backtesting("159792", 100, start, end, Interval.DAILY)
    second = engine.run_backtesting("159792.SZSE", 200, start, end, Interval.DAILY)
    refreshed = engine.run_backtesting("159792", 100, start, end, Interval.DAILY, refresh=True)

    assert first.vt_symbol == "159792.SZSE"
    assert not first.from_cache
    assert first.summary.total_cost == 220
    assert second.from_cache
    assert second.summary.total_cost == 440
    assert not refreshed.from_cache
    assert calls == ["159792.SZSE", "159792.SZSE"]
    assert any("使用缓存回测数据" in log for log in logs)


def test_minute_backtest_selects_one_record_after_trigger_each_day() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25, 14, 56), 1.0, Interval.MINUTE),
        create_bar(datetime(2026, 5, 25, 14, 57), 1.1, Interval.MINUTE),
        create_bar(datetime(2026, 5, 25, 15, 0), 1.2, Interval.MINUTE),
        create_bar(datetime(2026, 5, 26, 14, 57), 1.3, Interval.MINUTE),
    ]

    records = dca_engine.simulate_daily_investment("159792.SZSE", 100, bars)
    summary = dca_engine.calculate_summary(records, latest_price=1.3)

    assert [record.price for record in records] == [1.1, 1.3]
    assert summary.count == 2
    assert summary.total_cost == 240
    assert summary.market_value == 260
    assert summary.pnl == 20


def test_drop_add_strategy_increases_after_previous_down_day() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 1.2, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 1.1, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 1.3, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_DROP_ADD,
        50,
    )

    assert [record.volume for record in records] == [100, 100, 100, 200]
    assert records[-1].turnover == 260


def test_up_reduce_strategy_reduces_after_previous_up_day_by_board_lot() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 1.2, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 1.1, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 1.3, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        300,
        bars,
        dca_engine.BACKTEST_STRATEGY_UP_REDUCE,
        50,
    )

    assert [record.volume for record in records] == [300, 300, 100, 300]
    assert round(records[2].turnover, 2) == 110


def test_ma_temperature_strategy_uses_previous_close_against_moving_average() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 29), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 1), 0.8, Interval.DAILY),
        create_bar(datetime(2026, 6, 2), 0.9, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_MA_TEMPERATURE,
        50,
    )

    assert [record.volume for record in records] == [100, 100, 100, 100, 100, 100, 200]


def test_backtesting_returns_strategy_comparison(monkeypatch) -> None:
    engine, _, _ = create_engine()
    bars = [
        create_bar(datetime(2026, 5, 25), 1.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 1.2, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 1.1, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 1.3, Interval.DAILY),
    ]

    def load_bars(vt_symbol: str, start: datetime, end: datetime, interval: Interval) -> list[BarData]:
        return bars

    monkeypatch.setattr(dca_engine, "load_akshare_etf_bars", load_bars)
    result = engine.run_backtesting(
        "159792",
        100,
        datetime(2026, 5, 25),
        datetime(2026, 5, 28, 23, 59, 59),
        Interval.DAILY,
        strategy_key=dca_engine.BACKTEST_STRATEGY_DROP_ADD,
        adjustment_pct=50,
        drawdown_bias_cycle_days=2,
        value_averaging_cycle_days=3,
        backtest_fee_rate_pct=0.03,
        backtest_min_fee=0.05,
    )

    assert result.strategy_key == dca_engine.BACKTEST_STRATEGY_DROP_ADD
    assert result.drawdown_bias_cycle_days == 2
    assert result.value_averaging_cycle_days == 3
    assert result.backtest_fee_rate_pct == 0.03
    assert result.backtest_min_fee == 0.05
    assert [record.volume for record in result.records] == [100, 100, 100, 200]
    assert result.strategy_results
    assert {
        strategy_result.strategy_key
        for strategy_result in result.strategy_results
    } == {
        dca_engine.BACKTEST_STRATEGY_FIXED,
        dca_engine.BACKTEST_STRATEGY_DROP_ADD,
        dca_engine.BACKTEST_STRATEGY_UP_REDUCE,
        dca_engine.BACKTEST_STRATEGY_MA_TEMPERATURE,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        dca_engine.BACKTEST_STRATEGY_VALUE_AVERAGING,
        dca_engine.BACKTEST_STRATEGY_GRID_DCA,
    }


def test_us_backtesting_uses_yfinance_and_one_share_lot(monkeypatch) -> None:
    engine, _, _ = create_engine()
    calls: list[tuple[str, Interval]] = []
    bars = [
        create_us_bar(datetime(2026, 5, 25), 100.0, Interval.DAILY),
        create_us_bar(datetime(2026, 5, 26), 110.0, Interval.DAILY),
    ]

    def load_bars(vt_symbol: str, start: datetime, end: datetime, interval: Interval) -> list[BarData]:
        calls.append((vt_symbol, interval))
        return bars

    monkeypatch.setattr(dca_engine, "load_yfinance_us_bars", load_bars)

    result = engine.run_backtesting(
        "AAPL",
        3,
        datetime(2026, 5, 25),
        datetime(2026, 5, 26, 23, 59, 59),
        Interval.DAILY,
        market_type=dca_engine.BACKTEST_MARKET_US,
    )

    assert result.market_type == dca_engine.BACKTEST_MARKET_US
    assert result.vt_symbol == "AAPL.SMART"
    assert calls == [("AAPL.SMART", Interval.DAILY)]
    assert [record.volume for record in result.records] == [3, 3]
    assert result.records[0].fee == 1.5


def test_us_amount_strategies_do_not_use_cn_board_lot() -> None:
    bars = [
        create_us_bar(datetime(2026, 5, 25), 100.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "AAPL.SMART",
        1,
        bars,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            market_type=dca_engine.BACKTEST_MARKET_US,
            base_amount=1_000,
            drawdown_bias_cycle_days=1,
        ),
    )

    assert records[0].volume == 10


def test_drawdown_dca_uses_250_day_high_tiers_and_amount_to_lot_volume() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 9.5, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 8.5, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 7.5, Interval.DAILY),
        create_bar(datetime(2026, 5, 29), 6.5, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=10_000,
            drawdown_bias_cycle_days=1,
        ),
    )

    assert [record.volume for record in records] == [1000, 1500, 2300, 4000, 4600]


def test_drawdown_multiplier_uses_required_boundary_inclusivity() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 100.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 96.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 95.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 85.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 29), 75.0, Interval.DAILY),
    ]

    assert [
        dca_engine.get_drawdown_multiplier(bars, index)
        for index in range(len(bars))
    ] == [1, 1, 1.5, 2, 3]


def test_calculate_drawdown_rates_uses_rolling_high() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 8.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 12.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 9.0, Interval.DAILY),
    ]

    rates = dca_engine.calculate_drawdown_rates(bars, lookback=3)

    assert [round(rate, 4) for rate in rates] == [0, -0.2, 0, -0.25]


def test_drawdown_dca_exposes_configurable_rolling_high_lookback() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 8.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 12.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 9.0, Interval.DAILY),
    ]

    short_lookback_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=12_000,
            drawdown_bias_cycle_days=1,
            drawdown_lookback_days=1,
        ),
    )
    rolling_lookback_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=12_000,
            drawdown_bias_cycle_days=1,
            drawdown_lookback_days=2,
        ),
    )

    assert short_lookback_records[-1].volume == 1300
    assert rolling_lookback_records[-1].volume == 4000


def test_bias_dca_adjusts_amount_by_long_moving_average_deviation() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 28), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 29), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 1), 8.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 2), 12.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=10_000,
            drawdown_bias_cycle_days=1,
        ),
    )

    assert records[-2].volume == 3700
    assert records[-2].price == 8
    assert records[-1].price == 0
    assert records[-1].volume == 0
    assert records[-1].turnover == 0
    assert records[-1].fee == 0
    assert records[-1].tradeid.endswith(".N")

    summary = dca_engine.calculate_summary(records, latest_price=12)
    assert summary.count == len([record for record in records if record.volume])


def test_bias_multiplier_uses_required_five_tiers() -> None:
    def multiplier_for_bias(bias: float) -> float:
        previous_price: float = 100
        current_price: float = previous_price * (1 + bias) / (1 - bias)
        bars = [
            create_bar(datetime(2026, 5, 25), previous_price, Interval.DAILY),
            create_bar(datetime(2026, 5, 26), current_price, Interval.DAILY),
        ]
        return dca_engine.get_bias_multiplier(bars, 1)

    assert multiplier_for_bias(0.16) == 0
    assert multiplier_for_bias(0.10) == 0.5
    assert multiplier_for_bias(0) == 1
    assert multiplier_for_bias(-0.10) == 2
    assert multiplier_for_bias(-0.16) == 3


def test_calculate_bias_rates_uses_rolling_moving_average() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 12.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 8.0, Interval.DAILY),
    ]

    rates = dca_engine.calculate_bias_rates(bars, lookback=2)

    assert [round(rate, 4) for rate in rates] == [0, 0.0909, -0.2]


def test_bias_dca_exposes_configurable_rolling_average_lookback() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 100.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 50.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 100.0, Interval.DAILY),
    ]

    one_day_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=10_000,
            drawdown_bias_cycle_days=1,
            bias_lookback_days=1,
        ),
    )
    two_day_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=10_000,
            drawdown_bias_cycle_days=1,
            bias_lookback_days=2,
        ),
    )

    assert one_day_records[-1].volume == 100
    assert two_day_records[-1].volume == 0
    assert two_day_records[-1].tradeid.endswith(".N")


def test_value_averaging_targets_periodic_position_value_growth() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 12.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 5.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_VALUE_AVERAGING,
        strategy_config=dca_engine.BacktestStrategyConfig(
            target_growth=10_000,
            value_averaging_cycle_days=1,
        ),
    )

    assert [record.volume for record in records] == [1000, 600, 4400]


def test_value_averaging_sells_existing_position_when_value_exceeds_target() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 30.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_VALUE_AVERAGING,
        strategy_config=dca_engine.BacktestStrategyConfig(
            target_growth=10_000,
            value_averaging_cycle_days=1,
        ),
    )

    assert [record.volume for record in records] == [1000, -300]
    assert sum(record.volume for record in records) == 700


def test_strategy_execution_cycles_are_independent_and_count_trading_days() -> None:
    bars = [
        create_bar(datetime(2026, 5, 29), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 1), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 2), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 3), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 6, 4), 10.0, Interval.DAILY),
    ]
    config = dca_engine.BacktestStrategyConfig(
        base_amount=10_000,
        target_growth=1_000,
        drawdown_bias_cycle_days=2,
        value_averaging_cycle_days=3,
    )

    drawdown_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_DRAWDOWN_DCA,
        strategy_config=config,
    )
    bias_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        strategy_config=config,
    )
    value_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_VALUE_AVERAGING,
        strategy_config=config,
    )

    drawdown_bias_dates = ["2026-05-29", "2026-06-02", "2026-06-04"]
    value_averaging_dates = ["2026-05-29", "2026-06-03"]
    assert [record.datetime[:10] for record in drawdown_records] == drawdown_bias_dates
    assert [record.datetime[:10] for record in bias_records] == drawdown_bias_dates
    assert [record.datetime[:10] for record in value_records] == value_averaging_dates


def test_legacy_shared_execution_cycle_remains_compatible() -> None:
    config = dca_engine.BacktestStrategyConfig(execution_cycle_days=7)

    assert dca_engine.get_drawdown_bias_cycle_days(config) == 7
    assert dca_engine.get_value_averaging_cycle_days(config) == 7


def test_all_backtest_orders_are_floored_to_board_lots() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 9.7, Interval.DAILY),
    ]

    fixed_records = dca_engine.simulate_daily_investment("159792.SZSE", 250, bars)
    grid_records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        250,
        bars,
        dca_engine.BACKTEST_STRATEGY_GRID_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            grid_step_pct=3,
            grid_volume=550,
        ),
    )

    assert [record.volume for record in fixed_records] == [200, 200]
    assert all(abs(record.volume) % dca_engine.BOARD_LOT_SIZE == 0 for record in grid_records)


def test_backtest_fee_rate_charges_buy_orders_and_reduces_pnl() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        strategy_config=dca_engine.BacktestStrategyConfig(
            backtest_fee_rate_pct=0.03,
        ),
    )
    summary = dca_engine.calculate_summary(records, latest_price=10)

    assert records[0].turnover == 1_000
    assert records[0].fee == 0.3
    assert summary.total_fee == 0.3
    assert summary.total_cost == 1_000.3
    assert round(summary.pnl, 2) == -0.3


def test_backtest_min_fee_applies_when_rate_fee_is_smaller() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        strategy_config=dca_engine.BacktestStrategyConfig(
            backtest_fee_rate_pct=0.03,
            backtest_min_fee=5,
        ),
    )
    summary = dca_engine.calculate_summary(records, latest_price=10)

    assert records[0].turnover == 1_000
    assert records[0].fee == 5
    assert summary.total_fee == 5
    assert summary.total_cost == 1_005
    assert summary.pnl == -5


def test_backtest_fee_rate_is_used_when_above_min_fee() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        10_000,
        bars,
        strategy_config=dca_engine.BacktestStrategyConfig(
            backtest_fee_rate_pct=0.1,
            backtest_min_fee=5,
        ),
    )

    assert records[0].turnover == 100_000
    assert records[0].fee == 100


def test_backtest_fee_rate_charges_sell_orders_by_absolute_turnover() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 30.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_VALUE_AVERAGING,
        strategy_config=dca_engine.BacktestStrategyConfig(
            target_growth=10_000,
            value_averaging_cycle_days=1,
            backtest_fee_rate_pct=0.1,
        ),
    )

    assert [record.volume for record in records] == [1000, -300]
    assert [record.fee for record in records] == [10, 9]
    assert all(record.fee >= 0 for record in records)


def test_backtest_min_fee_does_not_apply_to_no_trade_records() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 100.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 50.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 100.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_BIAS_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(
            base_amount=10_000,
            drawdown_bias_cycle_days=1,
            bias_lookback_days=2,
            backtest_fee_rate_pct=0.03,
            backtest_min_fee=5,
        ),
    )

    assert records[-1].volume == 0
    assert records[-1].turnover == 0
    assert records[-1].fee == 0


def test_grid_dca_combines_base_buy_and_grid_buy_sell() -> None:
    bars = [
        create_bar(datetime(2026, 5, 25), 10.0, Interval.DAILY),
        create_bar(datetime(2026, 5, 26), 9.7, Interval.DAILY),
        create_bar(datetime(2026, 5, 27), 10.0, Interval.DAILY),
    ]

    records = dca_engine.simulate_daily_investment(
        "159792.SZSE",
        100,
        bars,
        dca_engine.BACKTEST_STRATEGY_GRID_DCA,
        strategy_config=dca_engine.BacktestStrategyConfig(grid_step_pct=3, grid_volume=500),
    )

    assert [record.volume for record in records] == [100, 100, 500, 100, -500]
    assert records[-1].turnover == -5000


def test_strategy_names_do_not_include_recommendation_prefixes() -> None:
    names = [strategy.name for strategy in dca_engine.BACKTEST_STRATEGIES]

    assert "跌后加投" in names
    assert "涨后少投" in names
    assert "均线温度" in names
    assert not any(name.startswith(("策略1：", "策略2：", "建议策略：")) for name in names)

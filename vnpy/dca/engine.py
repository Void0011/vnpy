from dataclasses import asdict, dataclass
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
from threading import Lock
from time import sleep
from typing import Any, cast

from vnpy.event import Event, EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, OrderType
from vnpy.trader.engine import BaseEngine, MainEngine
from vnpy.trader.event import EVENT_TIMER, EVENT_TICK, EVENT_TRADE
from vnpy.trader.object import BarData, ContractData, OrderRequest, SubscribeRequest, TickData, TradeData
from vnpy.trader.utility import extract_vt_symbol, load_json, save_json

from .personal import (
    PersonalInvestmentAnalysis,
    PersonalProductSummary,
    PersonalTradeImportResult,
    PersonalTradeRecord,
    calculate_file_hash,
    calculate_personal_investment_analysis,
    get_personal_product_summaries,
    parse_trade_statement_file,
)


APP_NAME = "DailyInvestment"

EVENT_DAILY_INVESTMENT_LOG = "eDailyInvestmentLog"
EVENT_DAILY_INVESTMENT_UPDATE = "eDailyInvestmentUpdate"

SETTING_FILENAME = "daily_investment_setting.json"
RECORD_FILENAME = "daily_investment_records.json"
PERSONAL_RECORD_FILENAME = "personal_investment_records.json"

TRIGGER_TIME = time(14, 57)
ORDER_REFERENCE = "DailyInvestment"
AKSHARE_RETRY_TIMES = 3
AKSHARE_RETRY_DELAY = 1.0
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
EXCHANGE_ALIASES = {
    "SH": "SSE",
    "SHSE": "SSE",
    "SZ": "SZSE",
    "US": "SMART",
}
US_EXCHANGES = {Exchange.SMART, Exchange.NYSE, Exchange.NASDAQ, Exchange.ARCA, Exchange.AMEX}
SSE_SYMBOL_PREFIXES = ("50", "51", "52", "56", "58", "60", "68", "90", "11")
SZSE_SYMBOL_PREFIXES = ("00", "12", "13", "15", "16", "18", "20", "30")
EXCHANGE_DISPLAY_NAMES = {
    Exchange.SSE: "上海证券交易所",
    Exchange.SZSE: "深圳证券交易所",
    Exchange.SMART: "美股智能路由",
    Exchange.NYSE: "纽约证券交易所",
    Exchange.NASDAQ: "纳斯达克交易所",
    Exchange.ARCA: "NYSE Arca",
    Exchange.AMEX: "美国证券交易所",
}
TRANSIENT_AKSHARE_ERROR_KEYWORDS = (
    "Connection aborted",
    "ConnectionError",
    "Max retries exceeded",
    "ProxyError",
    "Read timed out",
    "RemoteDisconnected",
    "Timeout",
    "Unable to connect to proxy",
)

BACKTEST_STRATEGY_FIXED = "fixed"
BACKTEST_STRATEGY_DROP_ADD = "drop_add"
BACKTEST_STRATEGY_UP_REDUCE = "up_reduce"
BACKTEST_STRATEGY_MA_TEMPERATURE = "ma_temperature"
BACKTEST_STRATEGY_DRAWDOWN_DCA = "drawdown_dca"
BACKTEST_STRATEGY_BIAS_DCA = "bias_dca"
BACKTEST_STRATEGY_VALUE_AVERAGING = "value_averaging"
BACKTEST_STRATEGY_GRID_DCA = "grid_dca"
DEFAULT_BACKTEST_STRATEGY = BACKTEST_STRATEGY_FIXED
BACKTEST_MARKET_CN = "cn"
BACKTEST_MARKET_US = "us"
BACKTEST_MARKET_NAMES = {
    BACKTEST_MARKET_CN: "A股市场",
    BACKTEST_MARKET_US: "美股市场",
}
BOARD_LOT_SIZE = 100
US_LOT_SIZE = 1
MA_TEMPERATURE_LOOKBACK = 20
MA_TEMPERATURE_MIN_PERIODS = 5
DRAWDOWN_LOOKBACK = 250
BIAS_LOOKBACK = 250
DEFAULT_DRAWDOWN_LOOKBACK_DAYS = DRAWDOWN_LOOKBACK
DEFAULT_BIAS_LOOKBACK_DAYS = BIAS_LOOKBACK
DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS = 20
DEFAULT_VALUE_AVERAGING_CYCLE_DAYS = 20
DEFAULT_EXECUTION_CYCLE_DAYS = DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS
DEFAULT_VALUE_AVERAGING_PERIOD_DAYS = DEFAULT_VALUE_AVERAGING_CYCLE_DAYS
DEFAULT_TARGET_GROWTH = 10_000
DEFAULT_BACKTEST_FEE_RATE_PCT = 0
DEFAULT_BACKTEST_MIN_FEE = 0
DEFAULT_US_COMMISSION_PER_SHARE = 0.003
DEFAULT_US_COMMISSION_MIN = 0.50
DEFAULT_US_COMMISSION_MAX_RATE_PCT = 0.50
DEFAULT_US_PLATFORM_FEE_PER_SHARE = 0.005
DEFAULT_US_PLATFORM_FEE_MIN = 1.00
DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT = 0.50
DEFAULT_GRID_STEP_PCT = 3
DEFAULT_GRID_VOLUME = 500


@dataclass(frozen=True)
class BacktestStrategy:
    """"""

    key: str
    name: str
    description: str


BACKTEST_STRATEGIES: tuple[BacktestStrategy, ...] = (
    BacktestStrategy(
        BACKTEST_STRATEGY_FIXED,
        "固定份额",
        "每天按基础份额买入，成交价取回测K线收盘价。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_DROP_ADD,
        "跌后加投",
        "若前一交易日下跌，今日按调仓比例增加份额；若前一交易日上涨，今日买入基础份额。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_UP_REDUCE,
        "涨后少投",
        "若前一交易日上涨，今日按调仓比例减少份额；若前一交易日下跌，今日买入基础份额。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_MA_TEMPERATURE,
        "均线温度",
        "用前一交易日相对20日均线的位置调仓：低于均线加投，高于均线少投。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_DRAWDOWN_DCA,
        "动态回撤阶梯",
        "每个执行周期按当前价格相对指定交易日数内滑动高点的回撤幅度分档投入基础金额。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_BIAS_DCA,
        "长均线偏离度",
        "每个执行周期按当前价格相对指定交易日数滑动均线的BIAS决定暂停、少投、正常定投或加倍投入。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_VALUE_AVERAGING,
        "价值平均",
        "每个执行周期增加目标市值，低于目标时买入、高于目标时卖出部分已有仓位。",
    ),
    BacktestStrategy(
        BACKTEST_STRATEGY_GRID_DCA,
        "网格叠加定投",
        "每天买入基础份额，并在价格按网格间距下跌/上涨时额外买入或卖出网格份额。",
    ),
)
BACKTEST_STRATEGY_MAP: dict[str, BacktestStrategy] = {
    strategy.key: strategy
    for strategy in BACKTEST_STRATEGIES
}


@dataclass
class InvestmentSetting:
    """"""

    vt_symbol: str = ""
    volume: float = 0
    gateway_name: str = ""
    enabled: bool = False


@dataclass
class InvestmentRecord:
    """"""

    datetime: str
    vt_symbol: str
    price: float
    volume: float
    turnover: float
    tradeid: str = ""
    vt_orderid: str = ""
    fee: float = 0


@dataclass
class InvestmentSummary:
    """"""

    count: int = 0
    total_volume: float = 0
    total_cost: float = 0
    total_fee: float = 0
    average_cost: float = 0
    latest_price: float = 0
    market_value: float = 0
    pnl: float = 0
    return_pct: float = 0


@dataclass
class BacktestStrategyResult:
    """"""

    strategy_key: str
    strategy_name: str
    records: list[InvestmentRecord]
    summary: InvestmentSummary


@dataclass
class BacktestStrategyConfig:
    """"""

    market_type: str = BACKTEST_MARKET_CN
    adjustment_pct: float = 0
    base_amount: float = 0
    drawdown_bias_cycle_days: int = DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS
    drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS
    bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS
    value_averaging_cycle_days: int = DEFAULT_VALUE_AVERAGING_CYCLE_DAYS
    target_growth: float = DEFAULT_TARGET_GROWTH
    backtest_fee_rate_pct: float = DEFAULT_BACKTEST_FEE_RATE_PCT
    backtest_min_fee: float = DEFAULT_BACKTEST_MIN_FEE
    us_commission_per_share: float = DEFAULT_US_COMMISSION_PER_SHARE
    us_commission_min: float = DEFAULT_US_COMMISSION_MIN
    us_commission_max_rate_pct: float = DEFAULT_US_COMMISSION_MAX_RATE_PCT
    us_platform_fee_per_share: float = DEFAULT_US_PLATFORM_FEE_PER_SHARE
    us_platform_fee_min: float = DEFAULT_US_PLATFORM_FEE_MIN
    us_platform_fee_max_rate_pct: float = DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT
    grid_step_pct: float = DEFAULT_GRID_STEP_PCT
    grid_volume: float = DEFAULT_GRID_VOLUME
    execution_cycle_days: int | None = None
    value_period_days: int | None = None


@dataclass
class BacktestResult:
    """"""

    vt_symbol: str
    records: list[InvestmentRecord]
    summary: InvestmentSummary
    bars: list[BarData]
    market_type: str = BACKTEST_MARKET_CN
    from_cache: bool = False
    strategy_key: str = DEFAULT_BACKTEST_STRATEGY
    strategy_name: str = ""
    adjustment_pct: float = 0
    base_amount: float = 0
    drawdown_bias_cycle_days: int = DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS
    drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS
    bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS
    value_averaging_cycle_days: int = DEFAULT_VALUE_AVERAGING_CYCLE_DAYS
    target_growth: float = DEFAULT_TARGET_GROWTH
    backtest_fee_rate_pct: float = DEFAULT_BACKTEST_FEE_RATE_PCT
    backtest_min_fee: float = DEFAULT_BACKTEST_MIN_FEE
    us_commission_per_share: float = DEFAULT_US_COMMISSION_PER_SHARE
    us_commission_min: float = DEFAULT_US_COMMISSION_MIN
    us_commission_max_rate_pct: float = DEFAULT_US_COMMISSION_MAX_RATE_PCT
    us_platform_fee_per_share: float = DEFAULT_US_PLATFORM_FEE_PER_SHARE
    us_platform_fee_min: float = DEFAULT_US_PLATFORM_FEE_MIN
    us_platform_fee_max_rate_pct: float = DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT
    grid_step_pct: float = DEFAULT_GRID_STEP_PCT
    grid_volume: float = DEFAULT_GRID_VOLUME
    execution_cycle_days: int = DEFAULT_EXECUTION_CYCLE_DAYS
    value_period_days: int = DEFAULT_VALUE_AVERAGING_PERIOD_DAYS
    strategy_results: list[BacktestStrategyResult] | None = None


class DailyInvestmentEngine(BaseEngine):
    """"""

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        """"""
        super().__init__(main_engine, event_engine, APP_NAME)

        self.setting: InvestmentSetting = self.load_setting()
        self.records: list[InvestmentRecord] = self.load_records()
        self.personal_records: list[PersonalTradeRecord] = []
        self.personal_imports: dict[str, dict] = {}
        self.load_personal_records()

        self.active_orderids: set[str] = set()
        self.last_order_date: date | None = None
        self.latest_ticks: dict[str, TickData] = {}
        self.backtest_bar_cache: dict[tuple[str, str, datetime, datetime, Interval], list[BarData]] = {}
        self.backtest_cache_lock = Lock()
        self.fund_name_cache: dict[str, str] = {}
        self.fund_name_cache_lock = Lock()

        self.register_event()

    def register_event(self) -> None:
        """"""
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)

    def close(self) -> None:
        """"""
        self.save_setting()
        self.save_records()
        self.save_personal_records()

    def load_setting(self) -> InvestmentSetting:
        """"""
        data: dict = load_json(SETTING_FILENAME)
        return InvestmentSetting(**data) if data else InvestmentSetting()

    def save_setting(self) -> None:
        """"""
        save_json(SETTING_FILENAME, asdict(self.setting))

    def load_records(self) -> list[InvestmentRecord]:
        """"""
        data: dict = load_json(RECORD_FILENAME)
        records: list = data.get("records", [])
        return [InvestmentRecord(**record) for record in records]

    def save_records(self) -> None:
        """"""
        data: dict = {"records": [asdict(record) for record in self.records]}
        save_json(RECORD_FILENAME, data)

    def load_personal_records(self) -> None:
        """"""
        data: dict = load_json(PERSONAL_RECORD_FILENAME)
        self.personal_records = [
            PersonalTradeRecord.from_dict(record)
            for record in data.get("records", [])
        ]
        self.personal_imports = data.get("imports", {})

    def save_personal_records(self) -> None:
        """"""
        data: dict = {
            "records": [record.to_dict() for record in self.personal_records],
            "imports": self.personal_imports,
        }
        save_json(PERSONAL_RECORD_FILENAME, data)

    def write_log(self, msg: str) -> None:
        """"""
        event: Event = Event(EVENT_DAILY_INVESTMENT_LOG, msg)
        self.event_engine.put(event)
        self.main_engine.write_log(msg, APP_NAME)

    def put_update_event(self) -> None:
        """"""
        event: Event = Event(EVENT_DAILY_INVESTMENT_UPDATE)
        self.event_engine.put(event)

    def update_setting(
        self,
        vt_symbol: str,
        volume: float,
        gateway_name: str,
        enabled: bool
    ) -> str:
        """"""
        vt_symbol = self.resolve_vt_symbol(vt_symbol)
        volume = floor_to_lot(volume, minimum=0)
        if volume <= 0:
            raise ValueError(f"委托份数必须不少于{BOARD_LOT_SIZE}股，并按整手输入")

        self.setting = InvestmentSetting(vt_symbol, volume, gateway_name, enabled)
        self.save_setting()

        if vt_symbol and gateway_name:
            self.subscribe(vt_symbol, gateway_name)

        self.write_log("每日定投配置已保存")
        self.put_update_event()
        return vt_symbol

    def resolve_vt_symbol(self, code: str) -> str:
        """"""
        code = code.strip().upper()
        if not code:
            raise ValueError("产品代码不能为空")

        if "." in code:
            symbol, exchange_str = code.rsplit(".", 1)
            exchange_text: str = EXCHANGE_ALIASES.get(exchange_str.upper(), exchange_str.upper())
            try:
                exchange: Exchange = Exchange(exchange_text)
            except ValueError as exc:
                raise ValueError(
                    f"交易所代码不支持：{exchange_str}。"
                    "A股/场内基金请使用 SSE 或 SZSE，例如 159632.SZSE。"
                ) from exc
            return f"{symbol}.{exchange.value}"

        inferred_exchange: Exchange | None = infer_cn_exchange(code)
        if inferred_exchange:
            return f"{code}.{inferred_exchange.value}"

        contracts: list[ContractData] = self.main_engine.get_all_contracts()
        matches: list[ContractData] = [
            contract
            for contract in contracts
            if contract.symbol == code or contract.symbol.upper() == code.upper()
        ]
        vt_symbols: list[str] = sorted({contract.vt_symbol for contract in matches})

        if not vt_symbols:
            raise ValueError(
                f"本地合约缓存中未找到产品代码：{code}，且无法根据代码规则推断交易所。"
                "请先连接交易接口并等待合约列表加载完成，或输入完整代码如 159632.SZSE。"
            )

        if len(vt_symbols) > 1:
            joined: str = "、".join(vt_symbols)
            raise ValueError(
                f"产品代码 {code} 匹配到多个合约：{joined}。"
                "请改用完整交易代码。"
            )

        return vt_symbols[0]

    def resolve_backtest_vt_symbol(self, code: str, market_type: str = BACKTEST_MARKET_CN) -> str:
        """"""
        if market_type == BACKTEST_MARKET_US:
            return resolve_us_vt_symbol(code)

        return self.resolve_vt_symbol(code)

    def get_contract_name(self, vt_symbol: str) -> str:
        """"""
        contract: ContractData | None = self.main_engine.get_contract(vt_symbol)
        if contract and contract.name:
            return contract.name.strip()

        return ""

    def get_cached_fund_name(self, vt_symbol: str) -> str:
        """"""
        contract_name: str = self.get_contract_name(vt_symbol)
        if contract_name:
            self.cache_fund_name(vt_symbol, contract_name)
            return contract_name

        with self.fund_name_cache_lock:
            return self.fund_name_cache.get(vt_symbol, "")

    def cache_fund_name(self, vt_symbol: str, fund_name: str) -> None:
        """"""
        fund_name = fund_name.strip()
        if not fund_name:
            return

        with self.fund_name_cache_lock:
            self.fund_name_cache[vt_symbol] = fund_name

    def query_fund_name(self, vt_symbol: str) -> str:
        """"""
        vt_symbol = self.resolve_vt_symbol(vt_symbol)

        fund_name: str = self.get_cached_fund_name(vt_symbol)
        if fund_name:
            return fund_name

        try:
            fund_name = load_akshare_etf_name(vt_symbol)
        except Exception as exc:
            msg: str = format_akshare_error(exc)
            self.write_log(f"AkShare基金名称查询失败：{msg}")
            return ""

        if fund_name:
            self.cache_fund_name(vt_symbol, fund_name)
        else:
            self.write_log(f"AkShare未查询到基金名称：{vt_symbol}")

        return fund_name

    def query_backtest_product_name(
        self,
        vt_symbol: str,
        market_type: str = BACKTEST_MARKET_CN,
    ) -> str:
        """"""
        if market_type == BACKTEST_MARKET_US:
            product_name: str = self.get_cached_fund_name(vt_symbol)
            if product_name:
                return product_name

            try:
                product_name = load_yfinance_us_name(vt_symbol)
            except Exception as exc:
                self.write_log(f"yfinance美股名称查询失败：{exc}")
                return ""

            if product_name:
                self.cache_fund_name(vt_symbol, product_name)

            return product_name

        return self.query_fund_name(vt_symbol)

    def describe_vt_symbol(self, vt_symbol: str, fund_name: str | None = None) -> str:
        """"""
        if fund_name is None:
            fund_name = self.get_cached_fund_name(vt_symbol)

        _, exchange = extract_vt_symbol(vt_symbol)
        exchange_name: str = EXCHANGE_DISPLAY_NAMES.get(exchange, exchange.value)
        if fund_name:
            return f"{vt_symbol}（{exchange_name}：{fund_name}）"

        return f"{vt_symbol}（{exchange_name}）"

    def start(self) -> bool:
        """"""
        if not self.setting.vt_symbol:
            self.write_log("启动失败：请先配置产品代码")
            return False

        if not self.setting.volume:
            self.write_log("启动失败：请先配置委托份数")
            return False

        if not self.setting.gateway_name:
            self.write_log("启动失败：请先选择交易接口")
            return False

        self.setting.enabled = True
        self.save_setting()
        self.subscribe(self.setting.vt_symbol, self.setting.gateway_name)
        self.write_log("每日定投已启动")
        self.put_update_event()
        return True

    def stop(self) -> None:
        """"""
        self.setting.enabled = False
        self.save_setting()
        self.write_log("每日定投已停止")
        self.put_update_event()

    def subscribe(self, vt_symbol: str, gateway_name: str) -> None:
        """"""
        try:
            symbol, exchange = extract_vt_symbol(vt_symbol)
        except ValueError:
            self.write_log(f"订阅失败：产品代码格式不正确 {vt_symbol}")
            return

        req: SubscribeRequest = SubscribeRequest(symbol=symbol, exchange=exchange)
        self.main_engine.subscribe(req, gateway_name)

    def process_tick_event(self, event: Event) -> None:
        """"""
        tick: TickData = event.data
        self.latest_ticks[tick.vt_symbol] = tick

        if tick.vt_symbol == self.setting.vt_symbol:
            self.put_update_event()

    def process_trade_event(self, event: Event) -> None:
        """"""
        trade: TradeData = event.data
        if trade.vt_orderid not in self.active_orderids:
            return

        record: InvestmentRecord = InvestmentRecord(
            datetime=trade.datetime.isoformat(sep=" ") if trade.datetime else datetime.now().isoformat(sep=" "),
            vt_symbol=trade.vt_symbol,
            price=trade.price,
            volume=trade.volume,
            turnover=trade.price * trade.volume,
            tradeid=trade.vt_tradeid,
            vt_orderid=trade.vt_orderid,
        )
        self.records.append(record)
        self.save_records()

        self.write_log(f"定投成交：{trade.vt_symbol} {trade.volume:g}@{trade.price:g}")
        self.put_update_event()

    def process_timer_event(self, event: Event) -> None:
        """"""
        if not self.setting.enabled:
            return

        now: datetime = datetime.now()
        if now.weekday() >= 5:
            return

        if now.time().hour != TRIGGER_TIME.hour or now.time().minute != TRIGGER_TIME.minute:
            return

        today: date = now.date()
        if self.last_order_date == today:
            return

        self.last_order_date = today
        self.send_daily_order()

    def send_daily_order(self) -> str:
        """"""
        setting: InvestmentSetting = self.setting
        tick: TickData | None = self.latest_ticks.get(setting.vt_symbol)

        if not tick:
            self.write_log(f"定投委托失败：尚未收到行情 {setting.vt_symbol}")
            return ""

        price: float = tick.bid_price_1
        if price <= 0:
            self.write_log(f"定投委托失败：买一价无效 {setting.vt_symbol}")
            return ""

        volume: float = floor_to_lot(setting.volume, minimum=0)
        if volume <= 0:
            self.write_log(f"定投委托失败：委托份数不足{BOARD_LOT_SIZE}股")
            return ""

        symbol, exchange = extract_vt_symbol(setting.vt_symbol)
        req: OrderRequest = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=volume,
            price=price,
            offset=Offset.NONE,
            reference=ORDER_REFERENCE,
        )

        vt_orderid: str = self.main_engine.send_order(req, setting.gateway_name)
        if vt_orderid:
            self.active_orderids.add(vt_orderid)
            self.write_log(f"定投委托已发出：{setting.vt_symbol} {volume:g}@{price:g}")
        else:
            self.write_log("定投委托失败：交易接口未返回委托号")

        self.put_update_event()
        return vt_orderid

    def get_summary(self, latest_price: float | None = None) -> InvestmentSummary:
        """"""
        if latest_price is None:
            tick: TickData | None = self.latest_ticks.get(self.setting.vt_symbol)
            latest_price = tick.last_price if tick else 0

        return calculate_summary(self.records, latest_price)

    def get_records(self) -> list[InvestmentRecord]:
        """"""
        return list(self.records)

    def clear_records(self) -> None:
        """"""
        self.records.clear()
        self.active_orderids.clear()
        self.save_records()
        self.write_log("定投记录已清空")
        self.put_update_event()

    def run_backtesting(
        self,
        vt_symbol: str,
        volume: float,
        start: datetime,
        end: datetime,
        interval: Interval = Interval.MINUTE,
        refresh: bool = False,
        strategy_key: str = DEFAULT_BACKTEST_STRATEGY,
        market_type: str = BACKTEST_MARKET_CN,
        adjustment_pct: float = 0,
        base_amount: float = 0,
        drawdown_bias_cycle_days: int = DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS,
        drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS,
        bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS,
        value_averaging_cycle_days: int = DEFAULT_VALUE_AVERAGING_CYCLE_DAYS,
        grid_step_pct: float = DEFAULT_GRID_STEP_PCT,
        grid_volume: float = DEFAULT_GRID_VOLUME,
        target_growth: float = DEFAULT_TARGET_GROWTH,
        execution_cycle_days: int | None = None,
        value_period_days: int | None = None,
        backtest_fee_rate_pct: float = DEFAULT_BACKTEST_FEE_RATE_PCT,
        backtest_min_fee: float = DEFAULT_BACKTEST_MIN_FEE,
        us_commission_per_share: float = DEFAULT_US_COMMISSION_PER_SHARE,
        us_commission_min: float = DEFAULT_US_COMMISSION_MIN,
        us_commission_max_rate_pct: float = DEFAULT_US_COMMISSION_MAX_RATE_PCT,
        us_platform_fee_per_share: float = DEFAULT_US_PLATFORM_FEE_PER_SHARE,
        us_platform_fee_min: float = DEFAULT_US_PLATFORM_FEE_MIN,
        us_platform_fee_max_rate_pct: float = DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT,
    ) -> BacktestResult:
        """"""
        vt_symbol = self.resolve_backtest_vt_symbol(vt_symbol, market_type)
        return self.run_resolved_backtesting(
            vt_symbol,
            volume,
            start,
            end,
            interval,
            refresh,
            strategy_key,
            market_type,
            adjustment_pct,
            base_amount,
            drawdown_bias_cycle_days,
            drawdown_lookback_days,
            bias_lookback_days,
            value_averaging_cycle_days,
            grid_step_pct,
            grid_volume,
            target_growth,
            execution_cycle_days,
            value_period_days,
            backtest_fee_rate_pct,
            backtest_min_fee,
            us_commission_per_share,
            us_commission_min,
            us_commission_max_rate_pct,
            us_platform_fee_per_share,
            us_platform_fee_min,
            us_platform_fee_max_rate_pct,
        )

    def run_resolved_backtesting(
        self,
        vt_symbol: str,
        volume: float,
        start: datetime,
        end: datetime,
        interval: Interval = Interval.MINUTE,
        refresh: bool = False,
        strategy_key: str = DEFAULT_BACKTEST_STRATEGY,
        market_type: str = BACKTEST_MARKET_CN,
        adjustment_pct: float = 0,
        base_amount: float = 0,
        drawdown_bias_cycle_days: int = DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS,
        drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS,
        bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS,
        value_averaging_cycle_days: int = DEFAULT_VALUE_AVERAGING_CYCLE_DAYS,
        grid_step_pct: float = DEFAULT_GRID_STEP_PCT,
        grid_volume: float = DEFAULT_GRID_VOLUME,
        target_growth: float = DEFAULT_TARGET_GROWTH,
        execution_cycle_days: int | None = None,
        value_period_days: int | None = None,
        backtest_fee_rate_pct: float = DEFAULT_BACKTEST_FEE_RATE_PCT,
        backtest_min_fee: float = DEFAULT_BACKTEST_MIN_FEE,
        us_commission_per_share: float = DEFAULT_US_COMMISSION_PER_SHARE,
        us_commission_min: float = DEFAULT_US_COMMISSION_MIN,
        us_commission_max_rate_pct: float = DEFAULT_US_COMMISSION_MAX_RATE_PCT,
        us_platform_fee_per_share: float = DEFAULT_US_PLATFORM_FEE_PER_SHARE,
        us_platform_fee_min: float = DEFAULT_US_PLATFORM_FEE_MIN,
        us_platform_fee_max_rate_pct: float = DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT,
    ) -> BacktestResult:
        """"""
        if execution_cycle_days is not None:
            drawdown_bias_cycle_days = execution_cycle_days
            value_averaging_cycle_days = execution_cycle_days

        if value_period_days is not None:
            value_averaging_cycle_days = value_period_days

        market_type = normalize_backtest_market_type(market_type)
        cache_key: tuple[str, str, datetime, datetime, Interval] = (
            market_type,
            vt_symbol,
            start,
            end,
            interval,
        )
        bars: list[BarData] | None = None
        from_cache: bool = False

        if not refresh:
            with self.backtest_cache_lock:
                cached_bars: list[BarData] | None = self.backtest_bar_cache.get(cache_key)
                if cached_bars is not None:
                    bars = list(cached_bars)
                    from_cache = True

        if from_cache:
            self.write_log(f"使用缓存回测数据：{vt_symbol} {len(bars or [])}根K线")

        if bars is None:
            try:
                bars = load_backtest_bars(vt_symbol, start, end, interval, market_type)
            except Exception as exc:
                msg: str = format_backtest_data_error(exc, market_type)
                source_name: str = get_backtest_data_source_name(market_type)
                self.write_log(f"{source_name}回测数据加载失败：{msg}")
                raise RuntimeError(msg) from exc

            with self.backtest_cache_lock:
                self.backtest_bar_cache[cache_key] = list(bars)

        if bars:
            data_interval: Interval | None = bars[0].interval
            interval_text: str = data_interval.value if data_interval else ""
            if not from_cache:
                source_name = get_backtest_data_source_name(market_type)
                self.write_log(f"{source_name}回测数据加载完成：{vt_symbol} {interval_text} {len(bars)}根K线")
        else:
            if not from_cache:
                source_name = get_backtest_data_source_name(market_type)
                self.write_log(f"{source_name}回测数据为空：{vt_symbol}")

        latest_price: float = bars[-1].close_price if bars else 0
        strategy_config: BacktestStrategyConfig = BacktestStrategyConfig(
            market_type=market_type,
            adjustment_pct=adjustment_pct,
            base_amount=base_amount,
            drawdown_bias_cycle_days=drawdown_bias_cycle_days,
            drawdown_lookback_days=max(1, int(drawdown_lookback_days)),
            bias_lookback_days=max(1, int(bias_lookback_days)),
            value_averaging_cycle_days=value_averaging_cycle_days,
            target_growth=target_growth,
            backtest_fee_rate_pct=max(backtest_fee_rate_pct, 0),
            backtest_min_fee=max(backtest_min_fee, 0),
            us_commission_per_share=max(us_commission_per_share, 0),
            us_commission_min=max(us_commission_min, 0),
            us_commission_max_rate_pct=max(us_commission_max_rate_pct, 0),
            us_platform_fee_per_share=max(us_platform_fee_per_share, 0),
            us_platform_fee_min=max(us_platform_fee_min, 0),
            us_platform_fee_max_rate_pct=max(us_platform_fee_max_rate_pct, 0),
            grid_step_pct=grid_step_pct,
            grid_volume=grid_volume,
        )
        strategy_results: list[BacktestStrategyResult] = run_backtest_strategies(
            vt_symbol,
            volume,
            bars,
            latest_price,
            strategy_config,
        )
        selected_result: BacktestStrategyResult = select_backtest_strategy_result(
            strategy_results,
            strategy_key,
        )

        return BacktestResult(
            vt_symbol=vt_symbol,
            records=selected_result.records,
            summary=selected_result.summary,
            bars=bars,
            market_type=market_type,
            from_cache=from_cache,
            strategy_key=selected_result.strategy_key,
            strategy_name=selected_result.strategy_name,
            adjustment_pct=adjustment_pct,
            base_amount=base_amount,
            drawdown_bias_cycle_days=drawdown_bias_cycle_days,
            drawdown_lookback_days=max(1, int(drawdown_lookback_days)),
            bias_lookback_days=max(1, int(bias_lookback_days)),
            value_averaging_cycle_days=value_averaging_cycle_days,
            target_growth=target_growth,
            backtest_fee_rate_pct=max(backtest_fee_rate_pct, 0),
            backtest_min_fee=max(backtest_min_fee, 0),
            us_commission_per_share=max(us_commission_per_share, 0),
            us_commission_min=max(us_commission_min, 0),
            us_commission_max_rate_pct=max(us_commission_max_rate_pct, 0),
            us_platform_fee_per_share=max(us_platform_fee_per_share, 0),
            us_platform_fee_min=max(us_platform_fee_min, 0),
            us_platform_fee_max_rate_pct=max(us_platform_fee_max_rate_pct, 0),
            grid_step_pct=grid_step_pct,
            grid_volume=grid_volume,
            execution_cycle_days=drawdown_bias_cycle_days,
            value_period_days=value_averaging_cycle_days,
            strategy_results=strategy_results,
        )

    def clear_backtest_cache(self) -> None:
        """"""
        with self.backtest_cache_lock:
            self.backtest_bar_cache.clear()

    def get_personal_records(self) -> list[PersonalTradeRecord]:
        """"""
        return list(self.personal_records)

    def get_personal_product_summaries(self) -> list[PersonalProductSummary]:
        """"""
        return get_personal_product_summaries(self.personal_records)

    def import_personal_trades_from_file(self, file_path: str | Path) -> PersonalTradeImportResult:
        """"""
        path: Path = Path(file_path)
        source_key: str = str(path.resolve())
        file_hash: str = calculate_file_hash(path)
        old_hash: str = self.personal_imports.get(source_key, {}).get("file_hash", "")

        parsed_records: list[PersonalTradeRecord] = parse_trade_statement_file(path)
        new_ids: set[str] = {record.record_id for record in parsed_records}
        existing_by_id: dict[str, PersonalTradeRecord] = {
            record.record_id: record
            for record in self.personal_records
        }
        old_source_ids: set[str] = {
            record.record_id
            for record in self.personal_records
            if record.source_key == source_key
        }

        result: PersonalTradeImportResult = PersonalTradeImportResult(
            source_key=source_key,
            file_hash=file_hash,
            parsed_count=len(parsed_records),
            changed=file_hash != old_hash,
        )

        for record_id in old_source_ids.difference(new_ids):
            existing_by_id.pop(record_id, None)
            result.removed_count += 1

        for record in parsed_records:
            old_record: PersonalTradeRecord | None = existing_by_id.get(record.record_id)
            if not old_record:
                result.added_count += 1
            elif old_record.to_dict() != record.to_dict():
                result.updated_count += 1
            else:
                result.skipped_count += 1

            existing_by_id[record.record_id] = record

        self.personal_records = sorted(
            existing_by_id.values(),
            key=lambda record: (record.datetime, record.vt_symbol, record.record_id),
        )
        self.personal_imports[source_key] = {
            "file_hash": file_hash,
            "last_imported": datetime.now().isoformat(sep=" "),
            "parsed_count": len(parsed_records),
        }
        self.save_personal_records()
        self.write_log(
            f"个人流水导入完成：{path.name}，解析{result.parsed_count}条，"
            f"新增{result.added_count}条，更新{result.updated_count}条，移除{result.removed_count}条"
        )
        self.put_update_event()
        return result

    def import_personal_trades_from_folder(self, folder_path: str | Path) -> list[PersonalTradeImportResult]:
        """"""
        folder: Path = Path(folder_path)
        if not folder.exists():
            raise FileNotFoundError(f"目录不存在：{folder}")

        results: list[PersonalTradeImportResult] = []
        for file_path in sorted(folder.iterdir()):
            if file_path.suffix.lower() not in {".xls", ".xlsx", ".csv", ".txt"}:
                continue

            results.append(self.import_personal_trades_from_file(file_path))

        return results

    def analyze_personal_investment(
        self,
        vt_symbol: str,
        refresh: bool = False,
    ) -> PersonalInvestmentAnalysis:
        """"""
        vt_symbol = self.resolve_vt_symbol(vt_symbol)
        records: list[PersonalTradeRecord] = [
            record for record in self.personal_records
            if record.vt_symbol == vt_symbol
        ]
        if not records:
            raise ValueError(f"没有找到产品交易记录：{vt_symbol}")

        start: datetime = datetime.fromisoformat(records[0].datetime)
        end: datetime = datetime.now()
        cache_key: tuple[str, str, datetime, datetime, Interval] = (
            BACKTEST_MARKET_CN,
            vt_symbol,
            start.replace(hour=0, minute=0, second=0, microsecond=0),
            end.replace(hour=23, minute=59, second=59, microsecond=0),
            Interval.DAILY,
        )
        bars: list[BarData] | None = None

        if not refresh:
            with self.backtest_cache_lock:
                cached_bars: list[BarData] | None = self.backtest_bar_cache.get(cache_key)
                if cached_bars is not None:
                    bars = list(cached_bars)

        if bars is None:
            try:
                bars = load_akshare_etf_bars(vt_symbol, cache_key[2], cache_key[3], Interval.DAILY)
            except Exception as exc:
                msg: str = format_akshare_error(exc)
                self.write_log(f"个人收益K线加载失败：{msg}")
                bars = []
            else:
                with self.backtest_cache_lock:
                    self.backtest_bar_cache[cache_key] = list(bars)

        return calculate_personal_investment_analysis(vt_symbol, records, bars)


def infer_cn_exchange(symbol: str) -> Exchange | None:
    """"""
    if len(symbol) != 6 or not symbol.isdigit():
        return None

    if symbol.startswith(SZSE_SYMBOL_PREFIXES):
        return Exchange.SZSE

    if symbol.startswith(SSE_SYMBOL_PREFIXES):
        return Exchange.SSE

    return None


def resolve_us_vt_symbol(code: str) -> str:
    """"""
    code = code.strip().upper()
    if not code:
        raise ValueError("产品代码不能为空")

    if "." in code:
        symbol, exchange_str = code.rsplit(".", 1)
        exchange_text: str = EXCHANGE_ALIASES.get(exchange_str.upper(), exchange_str.upper())
        try:
            exchange: Exchange = Exchange(exchange_text)
        except ValueError as exc:
            raise ValueError(
                f"美股交易所代码不支持：{exchange_str}。"
                "美股请使用 SMART、NASDAQ、NYSE、ARCA 或 AMEX，例如 AAPL.SMART。"
            ) from exc

        if exchange not in US_EXCHANGES:
            raise ValueError(
                f"美股回测不支持交易所：{exchange.value}。"
                "请使用 SMART、NASDAQ、NYSE、ARCA 或 AMEX。"
            )

        return f"{symbol}.{exchange.value}"

    return f"{code}.{Exchange.SMART.value}"


def get_yfinance_symbol(vt_symbol: str) -> str:
    """"""
    symbol, exchange = extract_vt_symbol(vt_symbol)
    if exchange not in US_EXCHANGES:
        raise ValueError("yfinance美股行情仅支持 SMART/NASDAQ/NYSE/ARCA/AMEX 代码")

    return symbol


def calculate_summary(records: list[InvestmentRecord], latest_price: float) -> InvestmentSummary:
    """"""
    total_volume: float = sum(record.volume for record in records)
    total_fee: float = sum(record.fee for record in records)
    total_cost: float = sum(record.turnover + record.fee for record in records)
    trade_count: int = sum(1 for record in records if record.volume)

    average_cost: float = total_cost / total_volume if total_volume else 0
    market_value: float = total_volume * latest_price
    pnl: float = market_value - total_cost
    return_pct: float = pnl / total_cost * 100 if total_cost else 0

    return InvestmentSummary(
        count=trade_count,
        total_volume=total_volume,
        total_cost=total_cost,
        total_fee=total_fee,
        average_cost=average_cost,
        latest_price=latest_price,
        market_value=market_value,
        pnl=pnl,
        return_pct=return_pct,
    )


def normalize_backtest_market_type(market_type: str) -> str:
    """"""
    return BACKTEST_MARKET_US if market_type == BACKTEST_MARKET_US else BACKTEST_MARKET_CN


def get_backtest_market_name(market_type: str) -> str:
    """"""
    return BACKTEST_MARKET_NAMES.get(normalize_backtest_market_type(market_type), "A股市场")


def get_backtest_data_source_name(market_type: str) -> str:
    """"""
    if normalize_backtest_market_type(market_type) == BACKTEST_MARKET_US:
        return "yfinance"

    return "AkShare"


def format_backtest_data_error(exc: Exception, market_type: str) -> str:
    """"""
    if normalize_backtest_market_type(market_type) == BACKTEST_MARKET_US:
        return str(exc)

    return format_akshare_error(exc)


def load_backtest_bars(
    vt_symbol: str,
    start: datetime,
    end: datetime,
    interval: Interval,
    market_type: str = BACKTEST_MARKET_CN,
) -> list[BarData]:
    """"""
    if normalize_backtest_market_type(market_type) == BACKTEST_MARKET_US:
        return load_yfinance_us_bars(vt_symbol, start, end, interval)

    return load_akshare_etf_bars(vt_symbol, start, end, interval)


def load_akshare_etf_bars(
    vt_symbol: str,
    start: datetime,
    end: datetime,
    interval: Interval
) -> list[BarData]:
    """"""
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("请先安装 AkShare：pip install akshare") from exc

    symbol, exchange = extract_vt_symbol(vt_symbol)
    if exchange not in {Exchange.SSE, Exchange.SZSE}:
        raise ValueError("AkShare ETF行情仅支持 SSE/SZSE 场内基金代码")

    with direct_requests_env():
        if interval is Interval.MINUTE:
            try:
                bars: list[BarData] = load_akshare_etf_minute_bars(ak, symbol, exchange, start, end)
            except Exception:
                bars = []

            if bars:
                return bars

        return load_akshare_etf_daily_bars(ak, symbol, exchange, start, end)


def load_akshare_etf_name(vt_symbol: str) -> str:
    """"""
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("请先安装 AkShare：pip install akshare") from exc

    symbol, exchange = extract_vt_symbol(vt_symbol)
    if exchange not in {Exchange.SSE, Exchange.SZSE}:
        return ""

    if not hasattr(ak, "fund_etf_spot_em"):
        return ""

    with direct_requests_env():
        df = call_akshare_with_retry(ak.fund_etf_spot_em)

    return find_fund_name_in_dataframe(df, symbol)


def load_yfinance_us_bars(
    vt_symbol: str,
    start: datetime,
    end: datetime,
    interval: Interval,
) -> list[BarData]:
    """"""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("请先安装 yfinance：pip install yfinance") from exc

    symbol, exchange = extract_vt_symbol(vt_symbol)
    if exchange not in US_EXCHANGES:
        raise ValueError("yfinance美股行情仅支持 SMART/NASDAQ/NYSE/ARCA/AMEX 代码")

    yfinance_interval: str = "1m" if interval is Interval.MINUTE else "1d"
    end_for_download: datetime = end
    if interval is Interval.DAILY:
        end_for_download = end + timedelta(days=1)

    with direct_requests_env():
        df = yf.download(
            symbol,
            start=start,
            end=end_for_download,
            interval=yfinance_interval,
            auto_adjust=False,
            progress=False,
            actions=False,
        )

    return filter_bars_by_datetime(
        yfinance_dataframe_to_bars(df, symbol, exchange, interval),
        start,
        end,
    )


def load_yfinance_us_name(vt_symbol: str) -> str:
    """"""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("请先安装 yfinance：pip install yfinance") from exc

    symbol: str = get_yfinance_symbol(vt_symbol)
    with direct_requests_env():
        ticker = yf.Ticker(symbol)
        info: dict = ticker.get_info() or {}

    return str(info.get("shortName") or info.get("longName") or "").strip()


@contextmanager
def direct_requests_env() -> Iterator[None]:
    """"""
    old_values: dict[str, str | None] = {
        key: os.environ.get(key)
        for key in (*PROXY_ENV_KEYS, "NO_PROXY", "no_proxy")
    }
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)

    try:
        import requests as requests_module
    except ImportError:
        requests: Any = None
    else:
        # This adapter intentionally patches requests' runtime methods temporarily.
        requests = requests_module

    if requests:
        old_merge = requests.sessions.Session.merge_environment_settings
        old_get_proxies = requests.utils.get_environ_proxies

        def merge_environment_settings(self: Any, url: str, proxies: Any, stream: Any, verify: Any, cert: Any) -> Any:
            settings = old_merge(self, url, {}, stream, verify, cert)
            settings["proxies"] = {}
            return settings

        requests.sessions.Session.merge_environment_settings = merge_environment_settings
        requests.utils.get_environ_proxies = lambda url, no_proxy=None: {}
    else:
        old_merge = None
        old_get_proxies = None

    try:
        yield
    finally:
        if requests:
            requests.sessions.Session.merge_environment_settings = old_merge
            requests.utils.get_environ_proxies = old_get_proxies

        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def format_akshare_error(exc: Exception) -> str:
    """"""
    text: str = str(exc)
    detail: str = f"{type(exc).__name__}: {exc!r}"
    if "ProxyError" in detail or "Unable to connect to proxy" in detail:
        return (
            "AkShare访问东方财富失败：当前请求仍被代理拦截或代理连接不可用。"
            "请重启GUI后重试；如仍失败，请在代理软件中关闭系统代理，"
            "或为 push2his.eastmoney.com 设置直连规则。"
        )

    if is_transient_akshare_error(exc):
        return (
            "AkShare访问东方财富时连接被远端断开或请求超时。"
            f"系统已尝试直连并重试 {AKSHARE_RETRY_TIMES} 次，仍未成功。"
            "这通常和代理规则、网络波动或东方财富临时限流有关；"
            "请稍后重试，或在代理软件中为 push2his.eastmoney.com 设置直连规则。"
        )

    return text


def is_transient_akshare_error(exc: Exception) -> bool:
    """"""
    detail: str = f"{type(exc).__name__}: {exc!r}"
    return any(keyword in detail for keyword in TRANSIENT_AKSHARE_ERROR_KEYWORDS)


def call_akshare_with_retry(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """"""
    last_exc: Exception | None = None

    for attempt in range(1, AKSHARE_RETRY_TIMES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= AKSHARE_RETRY_TIMES or not is_transient_akshare_error(exc):
                raise

            sleep(AKSHARE_RETRY_DELAY * attempt)

    assert last_exc is not None
    raise last_exc


def load_akshare_etf_minute_bars(
    ak: Any,
    symbol: str,
    exchange: Exchange,
    start: datetime,
    end: datetime
) -> list[BarData]:
    """"""
    df = call_akshare_with_retry(
        ak.fund_etf_hist_min_em,
        symbol=symbol,
        period="1",
        adjust="",
        start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
        end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
    )

    return filter_bars_by_datetime(
        dataframe_to_bars(df, symbol, exchange, Interval.MINUTE, "时间"),
        start,
        end,
    )


def load_akshare_etf_daily_bars(
    ak: Any,
    symbol: str,
    exchange: Exchange,
    start: datetime,
    end: datetime
) -> list[BarData]:
    """"""
    eastmoney_exc: Exception | None = None

    try:
        df = call_akshare_with_retry(
            ak.fund_etf_hist_em,
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="",
        )
        bars: list[BarData] = dataframe_to_bars(df, symbol, exchange, Interval.DAILY, "日期")
        if bars:
            return filter_bars_by_datetime(bars, start, end)
    except Exception as exc:
        eastmoney_exc = exc

    if hasattr(ak, "fund_etf_hist_sina"):
        try:
            df = call_akshare_with_retry(
                ak.fund_etf_hist_sina,
                symbol=to_sina_etf_symbol(symbol, exchange),
            )
            bars = dataframe_to_bars(df, symbol, exchange, Interval.DAILY, "date")
            if bars:
                return filter_bars_by_datetime(bars, start, end)
        except Exception as fallback_exc:
            if eastmoney_exc:
                raise eastmoney_exc from fallback_exc
            raise

    if eastmoney_exc:
        raise eastmoney_exc

    return []


def to_sina_etf_symbol(symbol: str, exchange: Exchange) -> str:
    """"""
    prefix: str = "sh" if exchange == Exchange.SSE else "sz"
    return f"{prefix}{symbol}"


def filter_bars_by_datetime(
    bars: list[BarData],
    start: datetime,
    end: datetime
) -> list[BarData]:
    """"""
    return sorted(
        [bar for bar in bars if start <= bar.datetime <= end],
        key=lambda bar: bar.datetime,
    )


def dataframe_to_bars(
    df: Any,
    symbol: str,
    exchange: Exchange,
    interval: Interval,
    datetime_column: str
) -> list[BarData]:
    """"""
    if df is None or df.empty:
        return []

    bars: list[BarData] = []
    for _, row in df.iterrows():
        datetime_value = get_row_value(
            row,
            (datetime_column, "时间", "日期", "date", "Date", "day", "datetime"),
        )
        if datetime_value is None:
            continue

        bars.append(
            BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=parse_akshare_datetime(datetime_value),
                interval=interval,
                volume=safe_float(get_row_value(row, ("成交量", "volume", "Volume"), 0)),
                turnover=safe_float(get_row_value(row, ("成交额", "turnover", "amount", "Amount"), 0)),
                open_interest=0,
                open_price=safe_float(get_row_value(row, ("开盘", "open", "Open"), 0)),
                high_price=safe_float(get_row_value(row, ("最高", "high", "High"), 0)),
                low_price=safe_float(get_row_value(row, ("最低", "low", "Low"), 0)),
                close_price=safe_float(get_row_value(row, ("收盘", "close", "Close"), 0)),
                gateway_name="AKSHARE",
            )
        )

    return bars


def yfinance_dataframe_to_bars(
    df: Any,
    symbol: str,
    exchange: Exchange,
    interval: Interval,
) -> list[BarData]:
    """"""
    if df is None or df.empty:
        return []

    if getattr(df.columns, "nlevels", 1) > 1:
        df = df.copy()
        df.columns = [
            column[0] if isinstance(column, tuple) else column
            for column in df.columns
        ]

    bars: list[BarData] = []
    for index, row in df.iterrows():
        dt: datetime = parse_yfinance_datetime(index)
        close_price: float = safe_float(get_row_value(row, ("Close", "close"), 0))
        if close_price <= 0:
            continue

        bars.append(
            BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=dt,
                interval=interval,
                volume=safe_float(get_row_value(row, ("Volume", "volume"), 0)),
                turnover=0,
                open_interest=0,
                open_price=safe_float(get_row_value(row, ("Open", "open"), close_price)),
                high_price=safe_float(get_row_value(row, ("High", "high"), close_price)),
                low_price=safe_float(get_row_value(row, ("Low", "low"), close_price)),
                close_price=close_price,
                gateway_name="YFINANCE",
            )
        )

    return bars


def get_row_value(row: Any, columns: tuple[str, ...], default: Any = None) -> Any:
    """"""
    for column in columns:
        if column in row:
            return row[column]

    return default


def parse_yfinance_datetime(value: Any) -> datetime:
    """"""
    if hasattr(value, "to_pydatetime"):
        dt: datetime = value.to_pydatetime()
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))

    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)

    return dt


def find_fund_name_in_dataframe(df: Any, symbol: str) -> str:
    """"""
    if df is None or df.empty:
        return ""

    normalized_symbol: str = normalize_fund_code(symbol)
    for _, row in df.iterrows():
        row_symbol: str = normalize_fund_code(
            get_row_value(row, ("代码", "基金代码", "symbol", "Symbol", "fund_code"), "")
        )
        if row_symbol != normalized_symbol:
            continue

        fund_name = get_row_value(row, ("名称", "基金简称", "name", "Name", "fund_name"), "")
        return str(fund_name).strip()

    return ""


def normalize_fund_code(value: Any) -> str:
    """"""
    text: str = str(value).strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]

    if text.isdigit() and len(text) < 6:
        return text.zfill(6)

    return text


def parse_akshare_datetime(value: Any) -> datetime:
    """"""
    if isinstance(value, datetime):
        return value

    if hasattr(value, "to_pydatetime"):
        return cast(datetime, value.to_pydatetime())

    text: str = str(value)
    if len(text) == 10:
        return datetime.strptime(text, "%Y-%m-%d")

    return datetime.fromisoformat(text)


def safe_float(value: Any) -> float:
    """"""
    if value is None:
        return 0

    try:
        result: float = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0

    if result != result:
        return 0

    return result


def simulate_daily_investment(
    vt_symbol: str,
    volume: float,
    bars: list[BarData],
    strategy_key: str = DEFAULT_BACKTEST_STRATEGY,
    adjustment_pct: float = 0,
    strategy_config: BacktestStrategyConfig | None = None,
) -> list[InvestmentRecord]:
    """"""
    selected_bars: list[BarData] = select_daily_investment_bars(bars)
    if strategy_config is None:
        strategy_config = BacktestStrategyConfig(adjustment_pct=adjustment_pct)
    strategy_config.market_type = normalize_backtest_market_type(strategy_config.market_type)
    lot_size: int = get_backtest_lot_size(strategy_config.market_type)
    volume = floor_to_lot(volume, lot_size=lot_size, minimum=0)

    if strategy_key == BACKTEST_STRATEGY_VALUE_AVERAGING:
        return simulate_value_averaging(vt_symbol, volume, selected_bars, strategy_config)

    if strategy_key == BACKTEST_STRATEGY_GRID_DCA:
        return simulate_grid_dca(vt_symbol, volume, selected_bars, strategy_config)

    records: list[InvestmentRecord] = []

    for index, bar in enumerate(selected_bars):
        trade_volume: float = calculate_backtest_trade_volume(
            selected_bars,
            index,
            volume,
            strategy_key,
            strategy_config,
        )
        if trade_volume <= 0:
            if is_bias_pause_signal(selected_bars, index, strategy_key, strategy_config):
                records.append(
                    create_backtest_no_trade_record(
                        vt_symbol,
                        bar,
                        len(records) + 1,
                    )
                )
            continue

        records.append(
            create_backtest_record(
                vt_symbol,
                bar,
                trade_volume,
                len(records) + 1,
                strategy_config,
            )
        )

    return records


def is_bias_pause_signal(
    selected_bars: list[BarData],
    index: int,
    strategy_key: str,
    strategy_config: BacktestStrategyConfig,
) -> bool:
    """"""
    if strategy_key != BACKTEST_STRATEGY_BIAS_DCA:
        return False

    if not is_execution_cycle_due(index, get_drawdown_bias_cycle_days(strategy_config)):
        return False

    return get_bias_multiplier(selected_bars, index, strategy_config.bias_lookback_days) == 0


def select_daily_investment_bars(bars: list[BarData]) -> list[BarData]:
    """"""
    selected_bars: list[BarData] = []
    selected_dates: set[date] = set()

    for bar in sorted(bars, key=lambda data: data.datetime):
        bar_date: date = bar.datetime.date()
        if bar_date in selected_dates:
            continue

        if bar.interval == Interval.DAILY or bar.datetime.time() >= TRIGGER_TIME:
            selected_dates.add(bar_date)
            selected_bars.append(bar)

    return selected_bars


def run_backtest_strategies(
    vt_symbol: str,
    volume: float,
    bars: list[BarData],
    latest_price: float,
    strategy_config: BacktestStrategyConfig,
) -> list[BacktestStrategyResult]:
    """"""
    results: list[BacktestStrategyResult] = []

    for strategy in BACKTEST_STRATEGIES:
        records: list[InvestmentRecord] = simulate_daily_investment(
            vt_symbol,
            volume,
            bars,
            strategy.key,
            strategy_config.adjustment_pct,
            strategy_config,
        )
        summary: InvestmentSummary = calculate_summary(records, latest_price)
        results.append(
            BacktestStrategyResult(
                strategy_key=strategy.key,
                strategy_name=strategy.name,
                records=records,
                summary=summary,
            )
        )

    return results


def select_backtest_strategy_result(
    results: list[BacktestStrategyResult],
    strategy_key: str,
) -> BacktestStrategyResult:
    """"""
    for result in results:
        if result.strategy_key == strategy_key:
            return result

    return results[0]


def calculate_backtest_trade_volume(
    selected_bars: list[BarData],
    index: int,
    base_volume: float,
    strategy_key: str,
    strategy_config: BacktestStrategyConfig,
) -> float:
    """"""
    lot_size: int = get_backtest_lot_size(strategy_config.market_type)
    if strategy_key == BACKTEST_STRATEGY_FIXED:
        return base_volume

    adjustment_pct: float = strategy_config.adjustment_pct
    adjustment_pct = max(adjustment_pct, 0)
    if adjustment_pct <= 0 and strategy_key in {
        BACKTEST_STRATEGY_DROP_ADD,
        BACKTEST_STRATEGY_UP_REDUCE,
        BACKTEST_STRATEGY_MA_TEMPERATURE,
    }:
        return base_volume

    if strategy_key == BACKTEST_STRATEGY_DROP_ADD:
        direction: int = get_previous_day_direction(selected_bars, index)
        if direction < 0:
            return increase_volume_by_pct(base_volume, adjustment_pct, lot_size)
        return base_volume

    if strategy_key == BACKTEST_STRATEGY_UP_REDUCE:
        direction = get_previous_day_direction(selected_bars, index)
        if direction > 0:
            return decrease_volume_by_pct(base_volume, adjustment_pct, lot_size)
        return base_volume

    if strategy_key == BACKTEST_STRATEGY_MA_TEMPERATURE:
        temperature_signal: int = get_ma_temperature_signal(selected_bars, index)
        if temperature_signal < 0:
            return increase_volume_by_pct(base_volume, adjustment_pct, lot_size)
        if temperature_signal > 0:
            return decrease_volume_by_pct(base_volume, adjustment_pct, lot_size)
        return base_volume

    if strategy_key == BACKTEST_STRATEGY_DRAWDOWN_DCA:
        if not is_execution_cycle_due(index, get_drawdown_bias_cycle_days(strategy_config)):
            return 0

        base_amount: float = get_base_investment_amount(base_volume, selected_bars, strategy_config)
        multiplier: float = get_drawdown_multiplier(
            selected_bars,
            index,
            strategy_config.drawdown_lookback_days,
        )
        return amount_to_lot_volume(base_amount * multiplier, selected_bars[index].close_price, lot_size)

    if strategy_key == BACKTEST_STRATEGY_BIAS_DCA:
        if not is_execution_cycle_due(index, get_drawdown_bias_cycle_days(strategy_config)):
            return 0

        base_amount = get_base_investment_amount(base_volume, selected_bars, strategy_config)
        multiplier = get_bias_multiplier(
            selected_bars,
            index,
            strategy_config.bias_lookback_days,
        )
        return amount_to_lot_volume(base_amount * multiplier, selected_bars[index].close_price, lot_size)

    return base_volume


def get_previous_day_direction(selected_bars: list[BarData], index: int) -> int:
    """"""
    if index < 2:
        return 0

    previous_close: float = selected_bars[index - 1].close_price
    before_previous_close: float = selected_bars[index - 2].close_price

    if previous_close > before_previous_close:
        return 1
    if previous_close < before_previous_close:
        return -1
    return 0


def get_ma_temperature_signal(selected_bars: list[BarData], index: int) -> int:
    """"""
    if index < MA_TEMPERATURE_MIN_PERIODS:
        return 0

    lookback_bars: list[BarData] = selected_bars[max(0, index - MA_TEMPERATURE_LOOKBACK):index]
    if not lookback_bars:
        return 0

    moving_average: float = sum(bar.close_price for bar in lookback_bars) / len(lookback_bars)
    previous_close: float = selected_bars[index - 1].close_price

    if previous_close < moving_average:
        return -1
    if previous_close > moving_average:
        return 1
    return 0


def simulate_value_averaging(
    vt_symbol: str,
    base_volume: float,
    selected_bars: list[BarData],
    strategy_config: BacktestStrategyConfig,
) -> list[InvestmentRecord]:
    """"""
    records: list[InvestmentRecord] = []
    target_growth: float = max(strategy_config.target_growth, 0)
    lot_size: int = get_backtest_lot_size(strategy_config.market_type)
    position_volume: float = 0
    target_value: float = 0

    for index, bar in enumerate(selected_bars):
        if not is_execution_cycle_due(index, get_value_averaging_cycle_days(strategy_config)):
            continue

        target_value += target_growth
        current_value: float = position_volume * bar.close_price
        diff: float = target_value - current_value

        if diff > 0:
            trade_volume: float = amount_to_lot_volume(diff, bar.close_price, lot_size)
            if trade_volume <= 0:
                continue

            records.append(
                create_backtest_record(
                    vt_symbol,
                    bar,
                    trade_volume,
                    len(records) + 1,
                    strategy_config,
                )
            )
            position_volume += trade_volume
        elif diff < 0:
            volume_to_sell: float = amount_to_lot_volume(abs(diff), bar.close_price, lot_size)
            if volume_to_sell <= 0 or position_volume < volume_to_sell:
                continue

            records.append(
                create_backtest_record(
                    vt_symbol,
                    bar,
                    -volume_to_sell,
                    len(records) + 1,
                    strategy_config,
                )
            )
            position_volume -= volume_to_sell

    return records


def simulate_grid_dca(
    vt_symbol: str,
    base_volume: float,
    selected_bars: list[BarData],
    strategy_config: BacktestStrategyConfig,
) -> list[InvestmentRecord]:
    """"""
    records: list[InvestmentRecord] = []
    lot_size: int = get_backtest_lot_size(strategy_config.market_type)
    base_volume = floor_to_lot(base_volume, lot_size=lot_size, minimum=0)
    grid_step: float = min(max(strategy_config.grid_step_pct, 0) / 100, 0.99)
    grid_volume: float = floor_to_lot(strategy_config.grid_volume, lot_size=lot_size, minimum=0)
    position_volume: float = 0
    grid_reference_price: float = 0

    for bar in selected_bars:
        if base_volume > 0:
            records.append(
                create_backtest_record(
                    vt_symbol,
                    bar,
                    base_volume,
                    len(records) + 1,
                    strategy_config,
                )
            )
            position_volume += base_volume

        if grid_reference_price <= 0:
            grid_reference_price = bar.close_price
            continue

        if grid_step <= 0 or grid_volume <= 0:
            continue

        while bar.close_price <= grid_reference_price * (1 - grid_step):
            records.append(
                create_backtest_record(
                    vt_symbol,
                    bar,
                    grid_volume,
                    len(records) + 1,
                    strategy_config,
                )
            )
            position_volume += grid_volume
            grid_reference_price *= 1 - grid_step

        while bar.close_price >= grid_reference_price * (1 + grid_step):
            sell_volume: float = min(grid_volume, floor_to_lot(position_volume, lot_size=lot_size, minimum=0))
            if sell_volume <= 0:
                break

            records.append(
                create_backtest_record(
                    vt_symbol,
                    bar,
                    -sell_volume,
                    len(records) + 1,
                    strategy_config,
                )
            )
            position_volume -= sell_volume
            grid_reference_price *= 1 + grid_step

    return records


def create_backtest_record(
    vt_symbol: str,
    bar: BarData,
    volume: float,
    sequence: int,
    strategy_config: BacktestStrategyConfig | None = None,
) -> InvestmentRecord:
    """"""
    tradeid: str = f"BACKTEST.{sequence}"
    turnover: float = bar.close_price * volume
    fee: float = calculate_backtest_fee(turnover, volume, strategy_config)
    return InvestmentRecord(
        datetime=bar.datetime.isoformat(sep=" "),
        vt_symbol=vt_symbol,
        price=bar.close_price,
        volume=volume,
        turnover=turnover,
        fee=fee,
        tradeid=tradeid,
        vt_orderid=tradeid,
    )


def calculate_backtest_fee(
    turnover: float,
    volume: float = 0,
    strategy_config: BacktestStrategyConfig | None = None,
) -> float:
    """"""
    turnover_abs: float = abs(turnover)
    if turnover_abs <= 0:
        return 0

    if strategy_config and normalize_backtest_market_type(strategy_config.market_type) == BACKTEST_MARKET_US:
        commission_fee: float = calculate_usmart_us_fee_component(
            turnover_abs,
            abs(volume),
            strategy_config.us_commission_per_share,
            strategy_config.us_commission_min,
            strategy_config.us_commission_max_rate_pct,
        )
        platform_fee: float = calculate_usmart_us_fee_component(
            turnover_abs,
            abs(volume),
            strategy_config.us_platform_fee_per_share,
            strategy_config.us_platform_fee_min,
            strategy_config.us_platform_fee_max_rate_pct,
        )
        return commission_fee + platform_fee

    fee_rate_pct: float = strategy_config.backtest_fee_rate_pct if strategy_config else DEFAULT_BACKTEST_FEE_RATE_PCT
    min_fee: float = strategy_config.backtest_min_fee if strategy_config else DEFAULT_BACKTEST_MIN_FEE
    rate_fee: float = turnover_abs * max(fee_rate_pct, 0) / 100
    return max(rate_fee, max(min_fee, 0))


def calculate_usmart_us_fee_component(
    turnover_abs: float,
    volume_abs: float,
    per_share_fee: float,
    min_fee: float,
    max_rate_pct: float,
) -> float:
    """"""
    if turnover_abs <= 0 or volume_abs <= 0:
        return 0

    fee: float = max(volume_abs * max(per_share_fee, 0), max(min_fee, 0))
    max_fee: float = turnover_abs * max(max_rate_pct, 0) / 100
    if max_fee > 0:
        fee = min(fee, max_fee)

    return fee


def create_backtest_no_trade_record(
    vt_symbol: str,
    bar: BarData,
    sequence: int,
) -> InvestmentRecord:
    """"""
    tradeid: str = f"BACKTEST.{sequence}.N"
    return InvestmentRecord(
        datetime=bar.datetime.isoformat(sep=" "),
        vt_symbol=vt_symbol,
        price=0,
        volume=0,
        turnover=0,
        fee=0,
        tradeid=tradeid,
        vt_orderid=tradeid,
    )


def get_base_investment_amount(
    base_volume: float,
    selected_bars: list[BarData],
    strategy_config: BacktestStrategyConfig,
) -> float:
    """"""
    if strategy_config.base_amount > 0:
        return strategy_config.base_amount

    if not selected_bars:
        return 0

    return base_volume * selected_bars[0].close_price


def get_drawdown_multiplier(
    selected_bars: list[BarData],
    index: int,
    lookback: int = DRAWDOWN_LOOKBACK,
) -> float:
    """"""
    drawdown: float = calculate_drawdown_rate(selected_bars, index, lookback)
    if drawdown <= -0.25:
        return 3
    if drawdown <= -0.15:
        return 2
    if drawdown <= -0.05:
        return 1.5
    return 1


def calculate_drawdown_rates(
    selected_bars: list[BarData],
    lookback: int = DRAWDOWN_LOOKBACK,
) -> list[float]:
    """"""
    return [
        calculate_drawdown_rate(selected_bars, index, lookback)
        for index in range(len(selected_bars))
    ]


def calculate_drawdown_rate(
    selected_bars: list[BarData],
    index: int,
    lookback: int = DRAWDOWN_LOOKBACK,
) -> float:
    """"""
    lookback = max(1, lookback)
    current_price: float = selected_bars[index].close_price
    lookback_bars: list[BarData] = selected_bars[max(0, index - lookback + 1):index + 1]
    high_price: float = max((get_bar_high_price(item) for item in lookback_bars), default=0)
    if high_price <= 0:
        return 0

    return (current_price - high_price) / high_price


def get_bias_multiplier(
    selected_bars: list[BarData],
    index: int,
    lookback: int = BIAS_LOOKBACK,
) -> float:
    """"""
    bias: float = calculate_bias_rate(selected_bars, index, lookback)
    if bias > 0.15:
        return 0
    if bias > 0.05:
        return 0.5
    if bias >= -0.05:
        return 1
    if bias >= -0.15:
        return 2
    return 3


def calculate_bias_rates(
    selected_bars: list[BarData],
    lookback: int = BIAS_LOOKBACK,
) -> list[float]:
    """"""
    return [
        calculate_bias_rate(selected_bars, index, lookback)
        for index in range(len(selected_bars))
    ]


def calculate_bias_rate(
    selected_bars: list[BarData],
    index: int,
    lookback: int = BIAS_LOOKBACK,
) -> float:
    """"""
    lookback = max(1, lookback)
    current_price: float = selected_bars[index].close_price
    lookback_bars: list[BarData] = selected_bars[max(0, index - lookback + 1):index + 1]
    if not lookback_bars:
        return 0

    moving_average: float = sum(bar.close_price for bar in lookback_bars) / len(lookback_bars)
    if moving_average <= 0:
        return 0

    return (current_price - moving_average) / moving_average


def get_bar_high_price(bar: BarData) -> float:
    """"""
    return bar.high_price if bar.high_price > 0 else bar.close_price


def amount_to_lot_volume(
    amount: float,
    price: float,
    lot_size: int = BOARD_LOT_SIZE,
) -> float:
    """"""
    if amount <= 0 or price <= 0:
        return 0

    return int((amount / price) / lot_size) * lot_size


def is_execution_cycle_due(index: int, execution_cycle_days: int) -> bool:
    """"""
    cycle_days: int = max(1, int(execution_cycle_days))
    return index % cycle_days == 0


def get_drawdown_bias_cycle_days(strategy_config: BacktestStrategyConfig) -> int:
    """"""
    if strategy_config.execution_cycle_days is not None:
        return strategy_config.execution_cycle_days

    return strategy_config.drawdown_bias_cycle_days


def get_value_averaging_cycle_days(strategy_config: BacktestStrategyConfig) -> int:
    """"""
    if strategy_config.value_period_days is not None:
        return strategy_config.value_period_days

    if strategy_config.execution_cycle_days is not None:
        return strategy_config.execution_cycle_days

    return strategy_config.value_averaging_cycle_days


def get_backtest_lot_size(market_type: str) -> int:
    """"""
    if normalize_backtest_market_type(market_type) == BACKTEST_MARKET_US:
        return US_LOT_SIZE

    return BOARD_LOT_SIZE


def increase_volume_by_pct(
    base_volume: float,
    adjustment_pct: float,
    lot_size: int = BOARD_LOT_SIZE,
) -> float:
    """"""
    adjusted_volume: float = base_volume * (1 + adjustment_pct / 100)
    return ceil_to_lot(adjusted_volume, lot_size)


def decrease_volume_by_pct(
    base_volume: float,
    adjustment_pct: float,
    lot_size: int = BOARD_LOT_SIZE,
) -> float:
    """"""
    adjusted_volume: float = base_volume * max(0, 1 - adjustment_pct / 100)
    return floor_to_lot(adjusted_volume, lot_size=lot_size, minimum=lot_size)


def ceil_to_lot(value: float, lot_size: int = BOARD_LOT_SIZE) -> float:
    """"""
    if value <= 0:
        return 0

    lots: int = int((value + lot_size - 1) // lot_size)
    return lots * lot_size


def floor_to_lot(
    value: float,
    lot_size: int = BOARD_LOT_SIZE,
    minimum: int = 0,
) -> float:
    """"""
    if value <= 0:
        return minimum

    lots: int = int(value // lot_size)
    result: int = lots * lot_size

    if minimum and result < minimum:
        return minimum

    return result

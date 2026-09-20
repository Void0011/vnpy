import csv
import hashlib
import io
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from vnpy.trader.constant import Direction, Exchange
from vnpy.trader.object import BarData


SSE_SYMBOL_PREFIXES = ("50", "51", "52", "56", "58", "60", "68", "90", "11")
SZSE_SYMBOL_PREFIXES = ("00", "12", "13", "15", "16", "18", "20", "30")
MARKET_CODE_EXCHANGES = {
    "1": Exchange.SZSE,
    "2": Exchange.SSE,
}

REQUIRED_TRADE_COLUMNS = {
    "发生日期",
    "发生时间",
    "业务说明",
    "资金发生数",
    "合同序号",
    "证券代码",
    "证券名称",
    "买卖类别",
    "成交数量",
    "成交价格",
    "成交金额",
}


@dataclass
class PersonalTradeRecord:
    """"""

    record_id: str
    datetime: str
    vt_symbol: str
    symbol: str
    exchange: str
    name: str
    direction: str
    price: float
    volume: float
    turnover: float
    cash_flow: float
    fee: float
    contract_id: str = ""
    source_key: str = ""
    source_row: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalTradeRecord":
        """"""
        return cls(**data)

    def to_dict(self) -> dict:
        """"""
        return asdict(self)


@dataclass
class PersonalTradeImportResult:
    """"""

    source_key: str
    file_hash: str
    parsed_count: int = 0
    added_count: int = 0
    updated_count: int = 0
    removed_count: int = 0
    skipped_count: int = 0
    changed: bool = False


@dataclass
class PersonalProductSummary:
    """"""

    vt_symbol: str
    name: str
    first_datetime: str
    trade_count: int
    buy_volume: float
    sell_volume: float
    position_volume: float
    buy_cash: float
    sell_cash: float


@dataclass
class PersonalInvestmentPoint:
    """"""

    datetime: str
    vt_symbol: str
    name: str
    direction: str
    price: float
    volume: float
    turnover: float
    latest_price: float
    pnl: float
    return_pct: float
    contribution_pct: float


@dataclass
class PersonalDailyReturn:
    """End-of-day account return and its change in percentage points."""

    date: str
    previous_date: str | None
    close_price: float
    position_volume: float
    buy_cash: float
    sell_cash: float
    market_value: float
    pnl: float
    return_pct: float
    change_pct_points: float | None
    trade_count: int


@dataclass
class PersonalInvestmentAnalysis:
    """"""

    vt_symbol: str
    name: str
    start_datetime: str
    latest_price: float
    trade_count: int
    buy_volume: float
    sell_volume: float
    position_volume: float
    buy_cash: float
    sell_cash: float
    market_value: float
    pnl: float
    return_pct: float
    average_cost: float
    worst_points: list[PersonalInvestmentPoint]
    best_points: list[PersonalInvestmentPoint]
    bars: list[BarData]
    records: list[PersonalTradeRecord]
    daily_returns: list[PersonalDailyReturn] = field(default_factory=list)
    worst_return_days: list[PersonalDailyReturn] = field(default_factory=list)
    best_return_days: list[PersonalDailyReturn] = field(default_factory=list)


def read_trade_statement_rows(file_path: Path) -> list[dict[str, str]]:
    """"""
    data: bytes = file_path.read_bytes()

    if data.startswith(b"PK"):
        import pandas as pd

        frame = pd.read_excel(file_path, dtype=str).fillna("")
        return normalize_dataframe_rows(frame)

    text: str | None = None
    for encoding in ("gbk", "gb18030", "utf-8-sig", "utf-8"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        text = data.decode("gb18030", errors="replace")

    delimiter: str = "\t" if "\t" in text.splitlines()[0] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in reader
        if any(str(value or "").strip() for value in row.values())
    ]


def normalize_dataframe_rows(frame: Any) -> list[dict[str, str]]:
    """"""
    rows: list[dict[str, str]] = []
    for row in frame.to_dict(orient="records"):
        rows.append({
            str(key or "").strip(): str(value or "").strip()
            for key, value in row.items()
        })
    return rows


def parse_trade_statement_file(file_path: str | Path) -> list[PersonalTradeRecord]:
    """"""
    path: Path = Path(file_path)
    rows: list[dict[str, str]] = read_trade_statement_rows(path)

    if rows and not REQUIRED_TRADE_COLUMNS.issubset(rows[0]):
        missing: set[str] = REQUIRED_TRADE_COLUMNS.difference(rows[0])
        raise ValueError(f"交易流水缺少必要字段：{', '.join(sorted(missing))}")

    source_key: str = str(path.resolve())
    records: list[PersonalTradeRecord] = []
    for row_index, row in enumerate(rows, start=2):
        record = parse_trade_statement_row(row, source_key, row_index)
        if record:
            records.append(record)

    records.sort(key=lambda record: (record.datetime, record.vt_symbol, record.record_id))
    return records


def parse_trade_statement_row(
    row: dict[str, str],
    source_key: str,
    row_index: int,
) -> PersonalTradeRecord | None:
    """"""
    symbol: str = normalize_symbol(row.get("证券代码", ""))
    if not symbol:
        return None

    side_text: str = row.get("买卖类别", "")
    business_text: str = row.get("业务说明", "")
    direction: Direction | None = parse_trade_direction(side_text or business_text)
    if not direction:
        return None

    volume: float = safe_float(row.get("成交数量", 0))
    price: float = safe_float(row.get("成交价格", 0))
    turnover: float = safe_float(row.get("成交金额", 0))
    if volume <= 0 or price <= 0:
        return None

    exchange: Exchange = resolve_trade_exchange(symbol, row.get("市场代码", ""))
    vt_symbol: str = f"{symbol}.{exchange.value}"
    cash_flow: float = safe_float(row.get("资金发生数", 0))
    cash_abs: float = abs(cash_flow)
    fee: float = round(max(cash_abs - turnover, 0), 6)
    trade_dt: datetime = parse_trade_datetime(
        row.get("发生日期", "") or row.get("成交日期", ""),
        row.get("发生时间", "") or row.get("成交时间", ""),
    )
    contract_id: str = row.get("合同序号", "").strip()
    name: str = row.get("证券名称", "").strip() or row.get("证券全称", "").strip()
    record_id: str = build_trade_record_id(
        contract_id,
        vt_symbol,
        trade_dt,
        direction,
        volume,
        price,
        turnover,
    )

    return PersonalTradeRecord(
        record_id=record_id,
        datetime=trade_dt.isoformat(sep=" "),
        vt_symbol=vt_symbol,
        symbol=symbol,
        exchange=exchange.value,
        name=name,
        direction=direction.value,
        price=price,
        volume=volume,
        turnover=turnover,
        cash_flow=cash_flow,
        fee=fee,
        contract_id=contract_id,
        source_key=source_key,
        source_row=row_index,
    )


def parse_trade_direction(text: str) -> Direction | None:
    """"""
    if "买入" in text:
        return Direction.LONG

    if "卖出" in text:
        return Direction.SHORT

    return None


def resolve_trade_exchange(symbol: str, market_code: str = "") -> Exchange:
    """"""
    market_code = market_code.strip()
    if market_code in MARKET_CODE_EXCHANGES:
        return MARKET_CODE_EXCHANGES[market_code]

    inferred_exchange: Exchange | None = infer_cn_exchange(symbol)
    if inferred_exchange:
        return inferred_exchange

    raise ValueError(f"无法识别证券代码交易所：{symbol}")


def infer_cn_exchange(symbol: str) -> Exchange | None:
    """"""
    if len(symbol) != 6 or not symbol.isdigit():
        return None

    if symbol.startswith(SZSE_SYMBOL_PREFIXES):
        return Exchange.SZSE

    if symbol.startswith(SSE_SYMBOL_PREFIXES):
        return Exchange.SSE

    return None


def parse_trade_datetime(date_text: str, time_text: str = "") -> datetime:
    """"""
    date_text = str(date_text).strip()
    time_text = str(time_text).strip()
    if "." in date_text:
        date_text = date_text.split(".", 1)[0]

    if len(date_text) == 8 and date_text.isdigit():
        date_part: date = datetime.strptime(date_text, "%Y%m%d").date()
    else:
        date_part = datetime.fromisoformat(date_text).date()

    if not time_text:
        return datetime.combine(date_part, datetime.min.time())

    if "." in time_text:
        time_text = time_text.split(".", 1)[0]

    return datetime.combine(date_part, datetime.strptime(time_text, "%H:%M:%S").time())


def normalize_symbol(value: Any) -> str:
    """"""
    text: str = str(value or "").strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]

    if text.isdigit() and len(text) < 6:
        text = text.zfill(6)

    return text


def safe_float(value: Any) -> float:
    """"""
    if value is None:
        return 0

    try:
        result: float = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return 0

    if result != result:
        return 0

    return result


def build_trade_record_id(
    contract_id: str,
    vt_symbol: str,
    trade_dt: datetime,
    direction: Direction,
    volume: float,
    price: float,
    turnover: float,
) -> str:
    """"""
    raw: str = "|".join([
        contract_id.strip(),
        vt_symbol,
        trade_dt.isoformat(sep=" "),
        direction.value,
        f"{volume:.8f}",
        f"{price:.8f}",
        f"{turnover:.8f}",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def calculate_file_hash(file_path: str | Path) -> str:
    """"""
    data: bytes = Path(file_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def get_personal_product_summaries(
    records: list[PersonalTradeRecord],
) -> list[PersonalProductSummary]:
    """"""
    grouped: dict[str, list[PersonalTradeRecord]] = {}
    for record in records:
        grouped.setdefault(record.vt_symbol, []).append(record)

    summaries: list[PersonalProductSummary] = []
    for vt_symbol, product_records in grouped.items():
        product_records.sort(key=lambda record: record.datetime)
        buy_records: list[PersonalTradeRecord] = [
            record for record in product_records if record.direction == Direction.LONG.value
        ]
        sell_records: list[PersonalTradeRecord] = [
            record for record in product_records if record.direction == Direction.SHORT.value
        ]

        buy_volume: float = sum(record.volume for record in buy_records)
        sell_volume: float = sum(record.volume for record in sell_records)
        summaries.append(PersonalProductSummary(
            vt_symbol=vt_symbol,
            name=product_records[0].name,
            first_datetime=product_records[0].datetime,
            trade_count=len(product_records),
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            position_volume=buy_volume - sell_volume,
            buy_cash=sum(abs(record.cash_flow) or record.turnover for record in buy_records),
            sell_cash=sum(abs(record.cash_flow) or record.turnover for record in sell_records),
        ))

    summaries.sort(key=lambda summary: (summary.first_datetime, summary.vt_symbol))
    return summaries


def calculate_personal_investment_analysis(
    vt_symbol: str,
    records: list[PersonalTradeRecord],
    bars: list[BarData],
) -> PersonalInvestmentAnalysis:
    """"""
    product_records: list[PersonalTradeRecord] = sorted(
        [record for record in records if record.vt_symbol == vt_symbol],
        key=lambda record: record.datetime,
    )
    if not product_records:
        raise ValueError(f"没有找到产品交易记录：{vt_symbol}")

    buy_records: list[PersonalTradeRecord] = [
        record for record in product_records if record.direction == Direction.LONG.value
    ]
    sell_records: list[PersonalTradeRecord] = [
        record for record in product_records if record.direction == Direction.SHORT.value
    ]

    latest_price: float = get_latest_price(product_records, bars)
    buy_cash: float = sum(abs(record.cash_flow) or record.turnover for record in buy_records)
    sell_cash: float = sum(abs(record.cash_flow) or record.turnover for record in sell_records)
    buy_volume: float = sum(record.volume for record in buy_records)
    sell_volume: float = sum(record.volume for record in sell_records)
    position_volume: float = buy_volume - sell_volume
    market_value: float = position_volume * latest_price
    pnl: float = market_value + sell_cash - buy_cash
    return_pct: float = pnl / buy_cash * 100 if buy_cash else 0
    average_cost: float = buy_cash / buy_volume if buy_volume else 0

    points: list[PersonalInvestmentPoint] = []
    for record in buy_records:
        point_pnl: float = (latest_price - record.price) * record.volume
        record_cash: float = abs(record.cash_flow) or record.turnover
        point_return: float = (latest_price - record.price) / record.price * 100 if record.price else 0
        contribution_pct: float = point_pnl / buy_cash * 100 if buy_cash else 0
        points.append(PersonalInvestmentPoint(
            datetime=record.datetime,
            vt_symbol=record.vt_symbol,
            name=record.name,
            direction=record.direction,
            price=record.price,
            volume=record.volume,
            turnover=record_cash,
            latest_price=latest_price,
            pnl=point_pnl,
            return_pct=point_return,
            contribution_pct=contribution_pct,
        ))

    worst_points: list[PersonalInvestmentPoint] = sorted(
        points,
        key=lambda point: (point.contribution_pct, point.return_pct, point.datetime),
    )[:3]
    best_points: list[PersonalInvestmentPoint] = sorted(
        points,
        key=lambda point: (point.contribution_pct, point.return_pct, point.datetime),
        reverse=True,
    )[:3]

    daily_returns = calculate_personal_daily_returns(product_records, bars)
    worst_return_days = sorted(
        (point for point in daily_returns
         if point.change_pct_points is not None and point.change_pct_points < 0),
        key=lambda point: (point.change_pct_points, point.date),
    )[:3]
    best_return_days = sorted(
        (point for point in daily_returns
         if point.change_pct_points is not None and point.change_pct_points > 0),
        key=lambda point: (-(point.change_pct_points or 0), point.date),
    )[:3]

    return PersonalInvestmentAnalysis(
        vt_symbol=vt_symbol,
        name=product_records[0].name,
        start_datetime=product_records[0].datetime,
        latest_price=latest_price,
        trade_count=len(product_records),
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        position_volume=position_volume,
        buy_cash=buy_cash,
        sell_cash=sell_cash,
        market_value=market_value,
        pnl=pnl,
        return_pct=return_pct,
        average_cost=average_cost,
        worst_points=worst_points,
        best_points=best_points,
        bars=bars,
        records=product_records,
        daily_returns=daily_returns,
        worst_return_days=worst_return_days,
        best_return_days=best_return_days,
    )


def calculate_personal_daily_returns(
    records: list[PersonalTradeRecord], bars: list[BarData],
) -> list[PersonalDailyReturn]:
    """Value actual cash flows at each daily close without using future trades.

    Return = (market value + cumulative sell proceeds - cumulative buy cost)
    / cumulative buy cost. Changes include the effect of added capital and fees;
    they are not a time-weighted return or a causal score for an individual buy.
    The first observation has no change, and absent prices are not fabricated.
    """
    if not records or not bars:
        return []
    ordered = sorted(records, key=lambda item: item.datetime)
    record_dates = [datetime.fromisoformat(item.datetime).date() for item in ordered]
    last_trade_date = record_dates[-1]
    daily_bars = {bar.datetime.date(): bar for bar in sorted(bars, key=lambda item: item.datetime)}
    result: list[PersonalDailyReturn] = []
    position = buy_cash = sell_cash = 0.0
    record_index = 0
    for day, bar in sorted(daily_bars.items()):
        if day < record_dates[0] or bar.close_price <= 0:
            continue
        count = 0
        while record_index < len(ordered) and record_dates[record_index] <= day:
            record = ordered[record_index]
            cash = abs(record.cash_flow) or record.turnover
            if record.direction == Direction.LONG.value:
                position += record.volume
                buy_cash += cash
            else:
                position -= record.volume
                sell_cash += cash
            if record_dates[record_index] == day:
                count += 1
            record_index += 1
        if not buy_cash:
            continue
        market_value = position * bar.close_price
        pnl = market_value + sell_cash - buy_cash
        return_pct = pnl / buy_cash * 100
        previous = result[-1] if result else None
        result.append(PersonalDailyReturn(
            date=day.isoformat(),
            previous_date=previous.date if previous else None,
            close_price=bar.close_price,
            position_volume=position,
            buy_cash=buy_cash,
            sell_cash=sell_cash,
            market_value=market_value,
            pnl=pnl,
            return_pct=return_pct,
            change_pct_points=return_pct - previous.return_pct if previous else None,
            trade_count=count,
        ))
        # Once fully liquidated and no future records remain, no later exposure exists.
        if day >= last_trade_date and abs(position) < 1e-8:
            break
    return result


def get_latest_price(records: list[PersonalTradeRecord], bars: list[BarData]) -> float:
    """"""
    if bars:
        return max(bars, key=lambda bar: bar.datetime).close_price

    return records[-1].price

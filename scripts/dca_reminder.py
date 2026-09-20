"""Headless ETF periodic-strategy reminder for GitHub Actions.

The module deliberately depends only on the Python standard library at import
time.  AkShare is imported lazily when live market data is requested, keeping
the reminder independent from the desktop UI and TA-Lib runtime.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import Any
from collections.abc import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_CONFIG_PATH = Path(".github/dca-reminder.json")
DEFAULT_STATE_PATH = Path(".github/dca-reminder-state.json")
DEFAULT_CYCLE_DAYS = 20
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_INVESTMENT_AMOUNT = 4_000
BOARD_LOT_SIZE = 100


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    close: float
    high: float


@dataclass(frozen=True)
class StrategySignal:
    strategy: str
    action: str
    volume: int
    reference_price: float
    estimated_amount: float
    metric_name: str
    metric_value: str
    note: str = ""


@dataclass(frozen=True)
class ETFDashboard:
    symbol: str
    name: str
    signal_date: date
    anchor_date: date
    cycle_days: int
    trading_day_index: int
    cycle_due: bool
    signals: tuple[StrategySignal, ...]


class ReminderError(RuntimeError):
    """Expected reminder failure with a user-facing message."""


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReminderError(f"{field_name} 必须使用 YYYY-MM-DD 格式") from exc


def positive_int(value: Any, field_name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ReminderError(f"{field_name} 必须是整数") from exc
    if result <= 0:
        raise ReminderError(f"{field_name} 必须大于 0")
    return result


def non_negative_float(value: Any, field_name: str, default: float = 0) -> float:
    if value in (None, ""):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReminderError(f"{field_name} 必须是数字") from exc
    if result < 0:
        raise ReminderError(f"{field_name} 不能小于 0")
    return result


def load_config(path: Path) -> dict[str, Any]:
    inline_config = os.getenv("DCA_REMINDER_CONFIG_JSON", "").strip()
    if inline_config:
        try:
            data = json.loads(inline_config)
        except json.JSONDecodeError as exc:
            raise ReminderError("DCA_REMINDER_CONFIG_JSON 不是有效 JSON") from exc
    else:
        if not path.exists():
            raise ReminderError(f"提醒配置不存在：{path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReminderError(f"提醒配置不是有效 JSON：{path}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("etfs"), list):
        raise ReminderError("提醒配置必须包含 etfs 数组")
    if not data["etfs"]:
        raise ReminderError("至少需要配置一个 ETF")
    if len(data["etfs"]) > 10:
        raise ReminderError("第一版最多支持 10 个 ETF")
    return data


def normalize_etf_config(raw: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReminderError("每个 ETF 配置必须是 JSON 对象")
    symbol = str(raw.get("symbol", "")).strip()
    if not symbol.isdigit() or len(symbol) != 6:
        raise ReminderError(f"ETF 代码必须是 6 位数字：{symbol or '<空>'}")

    anchor_override = os.getenv("DCA_CYCLE_ANCHOR", "").strip()
    anchor_value = anchor_override or raw.get("cycle_anchor_date") or root.get("cycle_anchor_date")
    if not anchor_value:
        raise ReminderError(f"{symbol} 未配置 cycle_anchor_date")

    cycle_override = os.getenv("DCA_CYCLE_DAYS", "").strip()
    cycle_value = cycle_override or raw.get("cycle_days") or root.get("cycle_days")
    mode = str(raw.get("value_averaging_mode", "simulated")).strip().lower()
    if mode not in {"simulated", "actual"}:
        raise ReminderError(f"{symbol} value_averaging_mode 只能是 simulated 或 actual")
    adjustment = str(raw.get("price_adjustment", "qfq")).strip().lower()
    if adjustment in {"none", "raw"}:
        adjustment = ""
    if adjustment not in {"", "qfq", "hfq"}:
        raise ReminderError(f"{symbol} price_adjustment 只能是 qfq、hfq 或 none")

    return {
        "symbol": symbol,
        "name": str(raw.get("name", symbol)).strip() or symbol,
        "price_adjustment": adjustment,
        "cycle_anchor_date": parse_date(anchor_value, f"{symbol}.cycle_anchor_date"),
        "cycle_days": positive_int(cycle_value, f"{symbol}.cycle_days", DEFAULT_CYCLE_DAYS),
        "base_amount": non_negative_float(
            raw.get("base_amount"),
            f"{symbol}.base_amount",
            DEFAULT_INVESTMENT_AMOUNT,
        ),
        "drawdown_lookback_days": positive_int(
            raw.get("drawdown_lookback_days"),
            f"{symbol}.drawdown_lookback_days",
            DEFAULT_LOOKBACK_DAYS,
        ),
        "bias_lookback_days": positive_int(
            raw.get("bias_lookback_days"),
            f"{symbol}.bias_lookback_days",
            DEFAULT_LOOKBACK_DAYS,
        ),
        "target_growth": non_negative_float(
            raw.get("target_growth"),
            f"{symbol}.target_growth",
            DEFAULT_INVESTMENT_AMOUNT,
        ),
        "value_averaging_mode": mode,
        "position_volume": non_negative_float(
            raw.get("position_volume"),
            f"{symbol}.position_volume",
        ),
        "completed_periods": positive_int_allow_zero(
            raw.get("completed_periods"),
            f"{symbol}.completed_periods",
        ),
    }


def positive_int_allow_zero(value: Any, field_name: str) -> int:
    if value in (None, ""):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ReminderError(f"{field_name} 必须是整数") from exc
    if result < 0:
        raise ReminderError(f"{field_name} 不能小于 0")
    return result


def floor_to_lot(amount: float, price: float, lot_size: int = BOARD_LOT_SIZE) -> int:
    if amount <= 0 or price <= 0:
        return 0
    return int(amount / price / lot_size) * lot_size


def calculate_drawdown(bars: list[PriceBar], lookback: int) -> float:
    window = bars[-max(1, lookback):]
    high_price = max((bar.high if bar.high > 0 else bar.close for bar in window), default=0)
    if high_price <= 0:
        return 0
    return (bars[-1].close - high_price) / high_price


def drawdown_multiplier(drawdown: float) -> float:
    if drawdown <= -0.25:
        return 3
    if drawdown <= -0.15:
        return 2
    if drawdown <= -0.05:
        return 1.5
    return 1


def calculate_bias(bars: list[PriceBar], lookback: int) -> float:
    window = bars[-max(1, lookback):]
    moving_average = sum(bar.close for bar in window) / len(window)
    if moving_average <= 0:
        return 0
    return (bars[-1].close - moving_average) / moving_average


def bias_multiplier(bias: float) -> float:
    if bias > 0.15:
        return 0
    if bias > 0.05:
        return 0.5
    if bias >= -0.05:
        return 1
    if bias >= -0.15:
        return 2
    return 3


def action_for_volume(volume: int) -> str:
    if volume > 0:
        return "BUY"
    if volume < 0:
        return "SELL"
    return "HOLD"


def make_amount_signal(
    strategy: str,
    amount: float,
    price: float,
    metric_name: str,
    metric_value: str,
    note: str = "",
) -> StrategySignal:
    volume = floor_to_lot(abs(amount), price)
    if amount < 0:
        volume = -volume
    return StrategySignal(
        strategy=strategy,
        action=action_for_volume(volume),
        volume=volume,
        reference_price=price,
        estimated_amount=round(abs(volume) * price, 2),
        metric_name=metric_name,
        metric_value=metric_value,
        note=note,
    )


def cycle_context(bars: list[PriceBar], anchor: date, cycle_days: int) -> tuple[int, bool]:
    dates = [bar.trade_date for bar in bars if bar.trade_date >= anchor]
    if not dates:
        raise ReminderError(f"周期锚点 {anchor.isoformat()} 晚于最新行情日期")
    index = len(dates) - 1
    return index, index % cycle_days == 0


def value_averaging_signal(
    bars: list[PriceBar],
    config: dict[str, Any],
    force_send: bool,
) -> StrategySignal:
    anchor: date = config["cycle_anchor_date"]
    cycle_days: int = config["cycle_days"]
    target_growth: float = config["target_growth"]
    mode: str = config["value_averaging_mode"]
    latest = bars[-1]

    if mode == "actual":
        position = config["position_volume"]
        next_period = config["completed_periods"] + 1
        target_value = next_period * target_growth
        diff = target_value - position * latest.close
        volume = signed_value_volume(diff, position, latest.close)
        return StrategySignal(
            strategy="价值平均",
            action=action_for_volume(volume),
            volume=volume,
            reference_price=latest.close,
            estimated_amount=round(abs(volume) * latest.close, 2),
            metric_name="目标/当前市值",
            metric_value=f"￥{target_value:,.2f} / ￥{position * latest.close:,.2f}",
            note=f"真实持仓输入；拟执行第 {next_period} 期",
        )

    position = config["position_volume"]
    completed = config["completed_periods"]
    scheduled = [bar for bar in bars if bar.trade_date >= anchor]
    if not scheduled:
        raise ReminderError(f"{config['symbol']} 没有锚点后的行情")

    due_bars = [bar for index, bar in enumerate(scheduled) if index % cycle_days == 0]
    latest_is_due = due_bars and due_bars[-1].trade_date == latest.trade_date
    history_due_bars = due_bars[:-1] if latest_is_due else due_bars
    for bar in history_due_bars:
        completed += 1
        target_value = completed * target_growth
        diff = target_value - position * bar.close
        position += signed_value_volume(diff, position, bar.close)

    completed += 1
    target_value = completed * target_growth
    diff = target_value - position * latest.close
    volume = signed_value_volume(diff, position, latest.close)
    force_note = "；测试日即时估算" if force_send and not latest_is_due else ""
    return StrategySignal(
        strategy="价值平均",
        action=action_for_volume(volume),
        volume=volume,
        reference_price=latest.close,
        estimated_amount=round(abs(volume) * latest.close, 2),
        metric_name="目标/模拟市值",
        metric_value=f"￥{target_value:,.2f} / ￥{position * latest.close:,.2f}",
        note=f"模拟持仓结果；假设历史建议全部成交{force_note}",
    )


def signed_value_volume(diff: float, position: float, price: float) -> int:
    if diff > 0:
        return floor_to_lot(diff, price)
    if diff < 0:
        sell_volume = floor_to_lot(abs(diff), price)
        if sell_volume <= position:
            return -sell_volume
    return 0


def calculate_dashboard(
    bars: list[PriceBar],
    config: dict[str, Any],
    force_send: bool = False,
) -> ETFDashboard:
    if not bars:
        raise ReminderError(f"{config['symbol']} 没有可用行情")
    bars = sorted(bars, key=lambda item: item.trade_date)
    latest = bars[-1]
    anchor: date = config["cycle_anchor_date"]
    cycle_days: int = config["cycle_days"]
    index, due = cycle_context(bars, anchor, cycle_days)

    drawdown = calculate_drawdown(bars, config["drawdown_lookback_days"])
    drawdown_factor = drawdown_multiplier(drawdown)
    drawdown_signal = make_amount_signal(
        "动态回撤阶梯",
        config["base_amount"] * drawdown_factor,
        latest.close,
        "回撤 / 倍数",
        f"{drawdown:.2%} / {drawdown_factor:g}×",
    )

    bias = calculate_bias(bars, config["bias_lookback_days"])
    bias_factor = bias_multiplier(bias)
    bias_signal = make_amount_signal(
        "长均线偏离度",
        config["base_amount"] * bias_factor,
        latest.close,
        "BIAS / 倍数",
        f"{bias:.2%} / {bias_factor:g}×",
        "BIAS 超过 15% 时暂停" if bias_factor == 0 else "",
    )
    value_signal = value_averaging_signal(bars, config, force_send)

    return ETFDashboard(
        symbol=config["symbol"],
        name=config["name"],
        signal_date=latest.trade_date,
        anchor_date=anchor,
        cycle_days=cycle_days,
        trading_day_index=index,
        cycle_due=due,
        signals=(drawdown_signal, bias_signal, value_signal),
    )


def dataframe_to_bars(frame: Any) -> list[PriceBar]:
    if frame is None or getattr(frame, "empty", True):
        return []
    date_column = find_column(frame.columns, ("日期", "date"))
    close_column = find_column(frame.columns, ("收盘", "close"))
    high_column = find_column(frame.columns, ("最高", "high"))
    if not date_column or not close_column:
        raise ReminderError("AkShare 行情字段缺少日期或收盘价")

    bars: list[PriceBar] = []
    for _, row in frame.iterrows():
        raw_date = row[date_column]
        trade_date = raw_date.date() if hasattr(raw_date, "date") else date.fromisoformat(str(raw_date)[:10])
        close = float(row[close_column])
        high = float(row[high_column]) if high_column else close
        if close > 0:
            bars.append(PriceBar(trade_date, close, high if high > 0 else close))
    return sorted({bar.trade_date: bar for bar in bars}.values(), key=lambda item: item.trade_date)


def find_column(columns: Iterable[Any], names: tuple[str, ...]) -> Any | None:
    normalized = {str(column).strip().lower(): column for column in columns}
    for name in names:
        column = normalized.get(name.lower())
        if column is not None:
            return column
    return None


def load_etf_bars(symbol: str, start: date, end: date, adjustment: str = "qfq") -> list[PriceBar]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise ReminderError("缺少 AkShare，请安装 requirements-dca-reminder.txt") from exc

    try:
        frame = ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=adjustment,
        )
    except Exception as exc:
        raise ReminderError(f"{symbol} AkShare 行情下载失败：{type(exc).__name__}: {exc}") from exc
    bars = dataframe_to_bars(frame)
    if not bars:
        raise ReminderError(f"{symbol} 行情为空")
    return bars


def is_exchange_trade_day(day: date) -> bool:
    try:
        import akshare as ak
        frame = ak.tool_trade_date_hist_sina()
        date_column = find_column(frame.columns, ("trade_date", "日期", "date"))
        if date_column is None:
            raise ValueError("missing trade_date")
        return day in {
            value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10])
            for value in frame[date_column]
        }
    except Exception:
        # The market data date is checked again below.  This fallback avoids
        # treating normal weekends as stale when the calendar endpoint fails.
        return day.weekday() < 5


def validate_market_freshness(
    bars: list[PriceBar],
    as_of: date,
    require_current_trade_date: bool,
) -> None:
    latest = bars[-1].trade_date
    if latest > as_of:
        raise ReminderError(f"行情日期 {latest} 晚于运行日期 {as_of}")
    if require_current_trade_date and is_exchange_trade_day(as_of) and latest != as_of:
        raise ReminderError(f"行情尚未更新：最新 {latest}，预期 {as_of}")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"last_message_key": "", "last_sent_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReminderError(f"幂等状态文件损坏：{path}") from exc
    return data if isinstance(data, dict) else {"last_message_key": "", "last_sent_at": ""}


def save_state(path: Path, message_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = load_state(path)
    sent_keys = list(dict.fromkeys([
        *previous.get("sent_message_keys", []),
        previous.get("last_message_key", ""),
        message_key,
    ]))
    data = {
        "last_message_key": message_key,
        "last_sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sent_message_keys": [key for key in sent_keys if key],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dashboard_message_key(dashboards: list[ETFDashboard], config_hash: str) -> str:
    parts = [
        f"{item.symbol}:{item.signal_date.isoformat()}:{item.anchor_date.isoformat()}:{item.cycle_days}"
        for item in dashboards
    ]
    raw = "|".join(parts) + "|" + config_hash
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def config_digest(config: dict[str, Any]) -> str:
    sanitized = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:16]


def action_label(action: str) -> str:
    return {"BUY": "【买入】", "SELL": "【卖出】", "HOLD": "【不操作】"}.get(action, action)


def build_feishu_card(dashboards: list[ETFDashboard], forced: bool = False) -> dict[str, Any]:
    latest_dates = sorted({item.signal_date.isoformat() for item in dashboards})
    subtitle = "测试强制推送" if forced else "周期到期提醒"
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**信号日期：** {' / '.join(latest_dates)}　**状态：** {subtitle}\n"
                    "金额按收盘价估算，实际成交以券商为准。"
                ),
            },
        }
    ]
    for dashboard in dashboards:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"### {dashboard.name}（{dashboard.symbol}）\n"
                        f"参考价 **￥{dashboard.signals[0].reference_price:,.3f}** · "
                        f"周期 **{dashboard.cycle_days} 个交易日** · "
                        f"锚点 **{dashboard.anchor_date.isoformat()}**"
                    ),
                },
            }
        )
        for signal in dashboard.signals:
            signed_volume = f"{signal.volume:+,}" if signal.volume else "0"
            content = (
                f"**{signal.strategy}**　{action_label(signal.action)}\n"
                f"份数：**{signed_volume}**　参考金额：**￥{signal.estimated_amount:,.2f}**\n"
                f"{signal.metric_name}：{signal.metric_value}"
            )
            if signal.note:
                content += f"\n<font color='grey'>{signal.note}</font>"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "策略信号仅供个人决策参考；价值平均的模拟结果不代表真实持仓。",
                }
            ],
        }
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "ETF 定投策略看板"},
                "subtitle": {"tag": "plain_text", "content": subtitle},
            },
            "elements": elements,
        },
    }


def build_failure_card(message: str) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": "ETF 定投提醒运行失败"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**错误：** {message[:1000]}"}},
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": "请检查 GitHub Actions 运行日志。"}],
                },
            ],
        },
    }


def sign_payload(payload: dict[str, Any], secret: str) -> None:
    timestamp = str(int(datetime.now().timestamp()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    payload["timestamp"] = timestamp
    payload["sign"] = base64.b64encode(digest).decode("utf-8")


def send_feishu(payload: dict[str, Any], webhook_url: str, signing_secret: str = "") -> None:
    if not webhook_url.startswith("https://open.feishu.cn/open-apis/bot/"):
        raise ReminderError("FEISHU_WEBHOOK_URL 不是受支持的飞书机器人地址")
    payload = dict(payload)
    if signing_secret:
        sign_payload(payload, signing_secret)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReminderError(f"飞书消息发送失败：{type(exc).__name__}") from exc
    code = response_data.get("code", response_data.get("StatusCode", 0))
    if code not in (0, "0"):
        message = response_data.get("msg", response_data.get("StatusMessage", "unknown error"))
        raise ReminderError(f"飞书拒绝消息：{message}")


def emit_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as file:
            file.write(f"{name}={value}\n")


def print_dashboard(dashboards: list[ETFDashboard]) -> None:
    print(json.dumps([asdict(item) for item in dashboards], ensure_ascii=False, indent=2, default=str))


def run(args: argparse.Namespace) -> int:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    signing_secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()
    if args.send_failure_only:
        if not webhook:
            print("未配置 FEISHU_WEBHOOK_URL，无法发送失败告警", file=sys.stderr)
            return 1
        send_feishu(build_failure_card(args.send_failure_only), webhook, signing_secret)
        return 0

    config_root = load_config(args.config)
    force_send = args.force_send or env_bool("DCA_FORCE_SEND")
    dry_run = args.dry_run or env_bool("DCA_DRY_RUN")
    require_current = env_bool("DCA_REQUIRE_CURRENT_TRADE_DATE", True)
    as_of_text = args.as_of_date or os.getenv("DCA_AS_OF_DATE", "").strip()
    as_of = (
        parse_date(as_of_text, "as_of_date")
        if as_of_text
        else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    )
    if not force_send and not is_exchange_trade_day(as_of):
        print("今日不是交易日，不发送周期提醒")
        emit_github_output("state_changed", "false")
        return 0

    dashboards: list[ETFDashboard] = []
    normalized_configs: list[dict[str, Any]] = []
    for raw in config_root["etfs"]:
        config = normalize_etf_config(raw, config_root)
        normalized_configs.append(config)
        history_days = max(config["drawdown_lookback_days"], config["bias_lookback_days"])
        history_start = min(config["cycle_anchor_date"], as_of - timedelta(days=history_days * 2 + 60))
        bars = load_etf_bars(
            config["symbol"],
            history_start,
            as_of,
            config["price_adjustment"],
        )
        validate_market_freshness(bars, as_of, require_current)
        dashboard = calculate_dashboard(bars, config, force_send)
        dashboards.append(dashboard)
        print(
            f"{dashboard.symbol} 行情={dashboard.signal_date} "
            f"周期索引={dashboard.trading_day_index} 到期={dashboard.cycle_due}"
        )

    print_dashboard(dashboards)
    send_items = dashboards if force_send else [item for item in dashboards if item.cycle_due]
    if not send_items:
        print("今日没有到期的周期策略，不发送消息")
        emit_github_output("state_changed", "false")
        return 0

    digest = config_digest({"etfs": normalized_configs})
    message_key = dashboard_message_key(send_items, digest)
    state = load_state(args.state)
    already_sent = (
        state.get("last_message_key") == message_key
        or message_key in state.get("sent_message_keys", [])
    )
    if already_sent and not args.allow_duplicate:
        print("检测到相同信号已经发送，本次跳过")
        emit_github_output("state_changed", "false")
        return 0

    card = build_feishu_card(send_items, forced=force_send)
    if dry_run:
        print("DRY RUN：已生成飞书交互式卡片，但未发送且未更新幂等状态")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        emit_github_output("state_changed", "false")
        return 0
    if not webhook:
        raise ReminderError("未配置 FEISHU_WEBHOOK_URL GitHub Secret")

    send_feishu(card, webhook, signing_secret)
    save_state(args.state, message_key)
    emit_github_output("state_changed", "true")
    print("飞书策略看板发送成功，幂等状态已更新")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETF 定投周期策略飞书提醒")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--as-of-date", default="", help="仅用于回放/测试，格式 YYYY-MM-DD")
    parser.add_argument("--force-send", action="store_true", help="忽略周期是否到期")
    parser.add_argument("--dry-run", action="store_true", help="生成结果但不发送、不写状态")
    parser.add_argument("--allow-duplicate", action="store_true", help="允许重复发送相同信号")
    parser.add_argument("--send-failure-only", default="", help="只发送失败告警卡片")
    return parser


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except ReminderError as exc:
        print(f"DCA reminder failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"DCA reminder failed unexpectedly: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

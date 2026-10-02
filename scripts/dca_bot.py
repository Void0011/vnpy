"""Deterministic Chinese commands, owner authorization and durable bot work."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from collections.abc import Callable
import copy
import hashlib
import html
import re
import shlex
from typing import Any
from zoneinfo import ZoneInfo

from scripts import dca_reminder as reminder
from scripts.dca_bot_store import BotStore, encode


HELP = """定投助手 · 支持私聊和群内 @机器人
产品列表 / 查看 159792 / 状态 / 修改记录
推送 159792 / 推送全部
修改 159792 基础金额=5000 窗口=120 VA增长=4000
新增ETF 代码 名称=基金正式名称 基础金额=2000 周期=20 锚点=2026-10-09
暂停 159792 / 恢复 159792 / 删除 159792
重建计划 159792 周期=20 锚点=2026-10-09
绑定推送（将定时消息发送到当前会话）
确认 变更码 / 取消 变更码

修改金额和窗口从下一期生效；重建计划会从新锚点重新模拟。
所有配置变更都先预览再确认，有效期 10 分钟。
仅生成策略提醒，不连接券商、不下单。"""

FIELDS = {
    "名称": "name", "基础金额": "base_amount", "金额": "base_amount",
    "VA增长": "target_growth", "价值增长": "target_growth", "每期增长": "target_growth",
    "回撤窗口": "drawdown_lookback_days", "均线窗口": "bias_lookback_days",
    "周期": "cycle_days", "锚点": "cycle_anchor_date", "起算日": "cycle_anchor_date",
}
MUTABLE = {"base_amount", "target_growth", "drawdown_lookback_days", "bias_lookback_days"}


def text_message(text: str) -> dict[str, Any]:
    return {"msg_type": "text", "content": {"text": text}}


def confirmation(summary: str, token: str) -> dict[str, Any]:
    safe = html.escape(summary).replace("@", "＠")
    return {"msg_type": "interactive", "card": {
        "schema": "2.0", "header": {"template": "blue", "title": {"tag": "plain_text", "content": "确认配置变更"}},
        "body": {"elements": [
            {"tag": "markdown", "content": safe},
            {"tag": "column_set", "columns": [
                {"tag": "column", "width": "auto", "elements": [
                    {"tag": "button", "type": button_type,
                     "text": {"tag": "plain_text", "content": label},
                     "behaviors": [{"type": "callback", "value": {"command": f"{label} {token}"}}]}
                ]} for label, button_type in [("确认", "primary"), ("取消", "default")]
            ]},
            {"tag": "markdown", "content": f"有效期 10 分钟。也可回复：确认 {token} / 取消 {token}", "text_size": "notation"},
        ]},
    }}


def parse_fields(source: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        parts = shlex.split(source.replace("＝", "="))
    except ValueError as exc:
        raise reminder.ReminderError("参数引号不完整，请发送“帮助”查看格式。") from exc
    for part in parts:
        if "=" not in part:
            raise reminder.ReminderError(f"无法识别参数“{part}”，请使用 基础金额=5000 这样的格式。")
        key, value = part.split("=", 1)
        if key == "窗口":
            if "drawdown_lookback_days" in result or "bias_lookback_days" in result:
                raise reminder.ReminderError("同一参数不能重复设置")
            result.update(drawdown_lookback_days=value, bias_lookback_days=value)
        elif key in FIELDS and FIELDS[key] not in result:
            result[FIELDS[key]] = value
        else:
            raise reminder.ReminderError(f"不支持或重复的参数：{key}")
    return result


def clean_config(raw: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    config = reminder.normalize_etf_config(raw, root, use_env=False)
    if config["value_averaging_mode"] != "simulated":
        raise reminder.ReminderError("对话机器人当前仅支持模拟持仓")
    if not config["symbol"].startswith(("15", "16", "51", "52", "56", "58")):
        raise reminder.ReminderError("当前只支持境内场内 ETF；该代码不在支持的代码范围内。")
    if len(config["name"]) > 80 or any(ord(char) < 32 for char in config["name"]):
        raise reminder.ReminderError("产品名称应为不超过 80 字的单行文字")
    if config["cycle_days"] > 250 or max(config["drawdown_lookback_days"], config["bias_lookback_days"]) > 1000:
        raise reminder.ReminderError("周期范围为 1–250 个交易日，参考窗口范围为 1–1000 日")
    for value in (config["base_amount"], config["target_growth"]):
        if value > 10_000_000:
            raise reminder.ReminderError("每期金额不能超过 1000 万元")
    config["cycle_anchor_date"] = config["cycle_anchor_date"].isoformat()
    config["enabled"] = raw.get("enabled", True)
    return config


class BotService:
    def __init__(self, store: BotStore, owners: set[str],
                 market: Callable[..., list[reminder.PriceBar]] = reminder.load_etf_bars,
                 clock: Callable[[], datetime] | None = None,
                 trade_day: Callable[[date], bool] = reminder.is_exchange_trade_day,
                 scheduler_enabled: bool = False) -> None:
        self.store, self.owners, self.market = store, owners, market
        self.clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self.trade_day = trade_day
        self.scheduler_enabled = scheduler_enabled

    def receive(self, event_id: str, owner: str, chat: str, command: str) -> bool:
        if not event_id or not chat or not owner or len(command) > 2000:
            return False
        if owner not in self.owners and command.strip() != "身份":
            return False
        return self.store.enqueue(event_id, owner, chat, command.strip())

    def config(self, root: dict[str, Any], symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = next((item for item in root["etfs"] if item["symbol"] == symbol), None)
        if raw is None:
            raise reminder.ReminderError(f"尚未配置 {symbol}。发送“产品列表”查看已配置产品。")
        return raw, reminder.normalize_etf_config(raw, root, use_env=False)

    def bars(self, config: dict[str, Any], as_of: date) -> list[reminder.PriceBar]:
        windows = [config["drawdown_lookback_days"], config["bias_lookback_days"]]
        for change in config.get("parameter_history", []):
            windows.extend(change.get(key, 1) for key in ("drawdown_lookback_days", "bias_lookback_days"))
        start = min(config["cycle_anchor_date"], as_of - timedelta(days=max(windows) * 2 + 60))
        bars = self.market(config["symbol"], start, as_of, config["price_adjustment"])
        if not bars or any(bar.trade_date > as_of for bar in bars):
            raise reminder.ReminderError("行情为空或包含未来日期")
        bars = sorted(bars, key=lambda bar: bar.trade_date)
        expected = as_of
        for _ in range(30):
            if self.trade_day(expected):
                break
            expected -= timedelta(days=1)
        else:
            raise reminder.ReminderError("无法确定最近交易日")
        if bars[-1].trade_date != expected:
            raise reminder.ReminderError(f"行情尚未更新：最新 {bars[-1].trade_date}，预期 {expected}")
        return bars

    def query_date(self) -> date:
        now = self.clock()
        # The bot can be queried before the close. Never imply an intraday bar is final.
        return now.date() if now.hour >= 17 else now.date() - timedelta(days=1)

    def handle(self, owner: str, chat: str, text: str, event_id: str) -> list[dict[str, Any]]:
        if text == "身份":
            return [text_message(f"你的应用内 open_id：{owner}\n当前会话：{chat}\n"
                                 "请将你本人的 open_id 配置到服务器 FEISHU_ALLOWED_OPEN_IDS。")]
        if owner not in self.owners:
            return []
        if text in {"帮助", "help", "菜单", "你好"}:
            return [text_message(HELP)]
        revision, root = self.store.read()
        if text in {"产品列表", "查看所有产品", "查看配置"}:
            lines = [f"配置版本 {revision} · {len(root['etfs'])}/10 个产品"]
            for raw in root["etfs"]:
                item = reminder.normalize_etf_config(raw, root, use_env=False)
                lines.append(f"{item['symbol']} {item['name']} · {'启用' if raw.get('enabled', True) else '暂停'} · "
                             f"每 {item['cycle_days']} 交易日 · 起算 {item['cycle_anchor_date']}")
            return [text_message("\n".join(lines))]
        if text == "状态":
            inbox, outbox = self.store.queue_stats()
            return [text_message(f"定时开关：{'开启' if self.scheduler_enabled else '关闭（部署后切换）'}\n"
                                 f"推送会话：{root.get('push_chat', '尚未绑定')}\n"
                                 f"待处理请求 {inbox}，待发送消息 {outbox}\n配置版本 {revision}")]
        if text == "修改记录":
            rows = self.store.history()
            return [text_message("\n\n".join(f"版本 {r['revision']} · {r['at']}\n{r['summary']}" for r in rows) or "暂无修改记录")]
        match = re.fullmatch(r"(确认|取消)\s+([a-f0-9]{12})", text)
        if match:
            return [text_message(self.store.confirm(match[2], owner, chat, cancel=match[1] == "取消"))]
        match = re.fullmatch(r"(?:查看|查看配置)\s*(\d{6})", text)
        if match:
            raw, item = self.config(root, match[1])
            return [text_message(f"{item['name']}（{item['symbol']}）\n初始基础金额 {item['base_amount']:g} 元 · "
                                 f"初始 VA 每期增长 {item['target_growth']:g} 元\n初始回撤窗口 {item['drawdown_lookback_days']} · "
                                 f"均线窗口 {item['bias_lookback_days']}\n周期 {item['cycle_days']} 个交易日 · "
                                 f"起算日 {item['cycle_anchor_date']}\n持仓：模拟持仓\n"
                                 f"参数变更历史：{encode(raw.get('parameter_history', []))}")]
        match = re.fullmatch(r"(?:推送|推送看板)\s*(全部|\d{6})?", text)
        if match:
            symbols = [item["symbol"] for item in root["etfs"] if item.get("enabled", True)] if match[1] in {None, "全部"} else [match[1]]
            responses = []
            for symbol in symbols:
                try:
                    _, config = self.config(root, symbol)
                    bars = self.bars(config, self.query_date())
                    dashboard = reminder.calculate_dashboard(bars, config, force_send=True)
                    responses.append(reminder.build_feishu_card([dashboard], forced=True))
                except reminder.ReminderError as exc:
                    responses.append(text_message(f"{symbol} 暂未生成看板：{exc}"))
            return responses or [text_message("没有启用的产品。")]

        updated = copy.deepcopy(root)
        if text == "绑定推送":
            updated["push_chat"] = chat
            summary = "定时推送会话将设为当前会话。该操作不改变部署层的定时开关。"
        else:
            match = re.fullmatch(r"(新增ETF|新增|修改|暂停|恢复|删除|重建计划)\s*(\d{6})(?:\s+(.*))?", text, re.IGNORECASE)
            if not match:
                # A narrow natural-language form has an unambiguous meaning; never execute arbitrary text.
                natural = re.fullmatch(r"(?:请)?把\s*(\d{6})\s*的?(基础金额|金额|窗口|VA增长)(?:改成|改为|设为)\s*(\d+(?:\.\d+)?)\s*元?(?:[，,]?\s*从下期生效)?", text)
                if natural:
                    return self.handle(owner, chat, f"修改 {natural[1]} {natural[2]}={natural[3]}", event_id)
                return [text_message("暂时无法确定你的意思，未修改任何参数。\n" + HELP)]
            action, symbol, tail = match[1], match[2], match[3] or ""
            fields = parse_fields(tail)
            if action.lower() in {"新增etf", "新增"}:
                if len(updated["etfs"]) >= 10 or any(item["symbol"] == symbol for item in updated["etfs"]):
                    raise reminder.ReminderError("产品已存在或已达到 10 个产品的上限")
                if not fields.get("name") or "cycle_anchor_date" not in fields:
                    raise reminder.ReminderError("新增需要明确 名称=基金名称 锚点=YYYY-MM-DD，避免沿用旧产品的模拟起点。")
                new = clean_config({"symbol": symbol, **fields, "value_averaging_mode": "simulated"}, updated)
                # Confirm that this is a supported product with available prices, even for a future anchor.
                validation = reminder.normalize_etf_config(new, updated, use_env=False)
                self.bars(validation, self.query_date())
                updated["etfs"].append(new)
                summary = f"新增 {new['name']}（{symbol}）\n基础金额 {new['base_amount']:g} 元；VA 增长 {new['target_growth']:g} 元\n"
                summary += f"周期 {new['cycle_days']} 个交易日；起算日 {new['cycle_anchor_date']}；模拟持仓。"
            else:
                raw, config = self.config(updated, symbol)
                if action in {"暂停", "恢复", "删除"}:
                    if fields:
                        raise reminder.ReminderError("暂停、恢复和删除无需附加参数")
                    if action == "删除":
                        updated["etfs"].remove(raw)
                    else:
                        raw["enabled"] = action == "恢复"
                    summary = f"{action} {config['name']}（{symbol}）的提醒。"
                    if action in {"暂停", "恢复"}:
                        summary += "\n历史模拟仍按原策略计划计算。"
                elif action == "重建计划":
                    if set(fields) - {"cycle_days", "cycle_anchor_date"} or "cycle_anchor_date" not in fields:
                        raise reminder.ReminderError("重建计划需要 锚点=YYYY-MM-DD，可选 周期=20")
                    bars = self.bars(config, self.query_date())
                    index = (reminder.cycle_context(bars, config["cycle_anchor_date"], config["cycle_days"])[0]
                             if bars[-1].trade_date >= config["cycle_anchor_date"] else -1)
                    current = reminder.config_at_index(config, index)
                    current.pop("parameter_history", None)
                    current.update(fields, position_volume=0, completed_periods=0)
                    new = clean_config(current, updated)
                    new["enabled"] = raw.get("enabled", True)
                    updated["etfs"][updated["etfs"].index(raw)] = new
                    summary = f"重建 {symbol} 的模拟计划：起算日 {new['cycle_anchor_date']}，周期 {new['cycle_days']}。\n"
                    summary += "原模拟持仓与待生效参数将重置，从新锚点重新模拟。"
                else:
                    if not fields or set(fields) - MUTABLE:
                        raise reminder.ReminderError("修改支持基础金额、VA增长、窗口、回撤窗口、均线窗口。周期或锚点请用“重建计划”。")
                    bars = self.bars(config, self.query_date())
                    index = (reminder.cycle_context(bars, config["cycle_anchor_date"], config["cycle_days"])[0]
                             if bars[-1].trade_date >= config["cycle_anchor_date"] else -1)
                    effective = (index // config["cycle_days"] + 1) * config["cycle_days"]
                    current = reminder.config_at_index(config, effective)
                    checked = clean_config({**current, **fields}, updated)
                    change = {key: checked[key] for key in fields}
                    if any(change.get(key, 0) > 10_000_000 for key in {"base_amount", "target_growth"}):
                        raise reminder.ReminderError("金额超出支持范围")
                    raw.setdefault("parameter_history", []).append({"effective_index": effective, **change})
                    labels = {value: key for key, value in FIELDS.items()}
                    summary = f"修改 {symbol}，从下一期（周期索引 {effective}）生效：\n" + "\n".join(
                        f"{labels[key]}：{current[key]} → {value}" for key, value in change.items())
                    summary += "\n此前的模拟成交和累计目标保持原口径。"
        token = hashlib.sha256(f"{owner}:{chat}:{event_id}".encode()).hexdigest()[:12]
        self.store.propose(token, owner, chat, revision, updated, summary)
        return [confirmation(summary, token)]

    def process_one(self) -> bool:
        event = self.store.next_event()
        if event is None:
            return False
        try:
            responses = self.handle(event["owner"], event["chat"], event["command"], event["id"])
        except reminder.ReminderError as exc:
            responses = [text_message(str(exc))]
        except Exception:
            if event["attempts"] < 2:
                self.store.defer_event(event["id"])
                return True
            responses = [text_message("处理失败，未能完成本次请求。请查看配置或修改记录后重试。")]
        self.store.complete_event(event["id"], event["chat"], responses)
        return True

    def scheduled_check(self) -> None:
        if not self.scheduler_enabled:
            return
        day = reminder.latest_scheduled_date(self.clock())
        if self.store.schedule_done(day.isoformat()):
            return
        _, root = self.store.read()
        chat = root.get("push_chat")
        if not chat:
            return
        if not self.trade_day(day):
            self.store.finish_schedule(day.isoformat())
            return
        failed = False
        for raw in root["etfs"]:
            if not raw.get("enabled", True):
                continue
            symbol = raw["symbol"]
            try:
                config = reminder.normalize_etf_config(raw, root, use_env=False)
                if day < config["cycle_anchor_date"]:
                    continue
                dashboard = reminder.calculate_dashboard(self.bars(config, day), config)
                if dashboard.cycle_due:
                    key = reminder.dashboard_message_key([dashboard], reminder.config_digest({"etfs": [config]}))
                    self.store.queue(f"schedule:{chat}:{key}", chat, reminder.build_feishu_card([dashboard]))
            except Exception:
                failed = True
                self.store.queue(f"failure:{day}:{symbol}", chat, text_message(
                    f"{symbol} 在 {day} 的定时检查失败，将自动重试；其他产品继续检查。"))
        if not failed:
            self.store.finish_schedule(day.isoformat())

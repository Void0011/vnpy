"""Feishu application bot using the official SDK's outbound WebSocket connection.

Run: python -m scripts.feishu_bot
No inbound HTTP endpoint, brokerage connection or language-model credential is used.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import date
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from scripts import dca_reminder as reminder
from scripts.dca_bot import BotService, clean_config
from scripts.dca_bot_store import BotStore, encode

LOGGER = logging.getLogger("dca.bot")


def data_request(request: dict[str, Any]) -> Any:
    """Bound third-party data-library calls so an outage cannot hang the bot."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.feishu_bot", "--data-request", encode(request)],
            capture_output=True, text=True, encoding="utf-8", timeout=90, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise reminder.ReminderError("行情或交易日历服务暂时不可用，请稍后重试。") from exc


def bounded_market(symbol: str, start: date, end: date, adjustment: str) -> list[reminder.PriceBar]:
    rows = data_request({"kind": "bars", "symbol": symbol, "start": start.isoformat(),
                         "end": end.isoformat(), "adjustment": adjustment})
    return [reminder.PriceBar(date.fromisoformat(row[0]), float(row[1]), float(row[2])) for row in rows]


class ExchangeCalendar:
    def __init__(self) -> None:
        self.loaded_day = ""
        self.days: set[date] = set()

    def is_trade_day(self, day: date) -> bool:
        today = date.today().isoformat()
        if self.loaded_day != today:
            self.days = {date.fromisoformat(value) for value in data_request({"kind": "calendar"})}
            self.loaded_day = today
        if not self.days or day > max(self.days) or day < min(self.days):
            raise reminder.ReminderError("交易日历不覆盖请求日期")
        return day in self.days


def produce_data(request: dict[str, Any]) -> None:
    if request["kind"] == "calendar":
        import akshare as ak
        frame = ak.tool_trade_date_hist_sina()
        column = reminder.find_column(frame.columns, ("trade_date", "日期", "date"))
        if column is None:
            raise reminder.ReminderError("交易日历缺少日期字段")
        data = [str(value)[:10] for value in frame[column]]
    elif request["kind"] == "bars":
        with redirect_stdout(sys.stderr):
            bars = reminder.load_etf_bars(request["symbol"], date.fromisoformat(request["start"]),
                                          date.fromisoformat(request["end"]), request["adjustment"])
        data = [(bar.trade_date.isoformat(), bar.close, bar.high) for bar in bars]
    else:
        raise reminder.ReminderError("不支持的数据请求")
    print(encode(data))


class FeishuTransport:
    def __init__(self, app_id: str, app_secret: str) -> None:
        import lark_oapi as lark
        self.client = lark.Client.builder().app_id(app_id).app_secret(app_secret).timeout(20).log_level(lark.LogLevel.ERROR).build()

    def bot_open_id(self) -> str:
        import lark_oapi as lark
        request = lark.BaseRequest.builder().http_method(lark.HttpMethod.GET).uri("/open-apis/bot/v3/info") \
            .token_types({lark.AccessTokenType.TENANT}).build()
        response = self.client.request(request)
        body = json.loads(response.raw.content)
        if body.get("code") != 0 or not body.get("bot", {}).get("open_id"):
            raise reminder.ReminderError("无法获取机器人身份，请检查 App ID、App Secret 和应用发布状态")
        return str(body["bot"]["open_id"])

    def send(self, chat: str, payload: dict[str, Any], key: str) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        content = payload["card"] if payload["msg_type"] == "interactive" else payload["content"]
        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder().receive_id(chat).msg_type(payload["msg_type"])
            .content(encode(content)).uuid(str(uuid.uuid5(uuid.NAMESPACE_URL, key))).build(),
        ).build()
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise reminder.ReminderError(f"飞书发送失败（错误码 {response.code}），请检查应用权限和会话可见范围")


def message_from_event(data: dict[str, Any], bot_open_id: str) -> tuple[str, str, str, str] | None:
    event = data.get("event") or {}
    message, sender = event.get("message") or {}, event.get("sender") or {}
    if message.get("message_type") != "text" or sender.get("sender_type") != "user":
        return None
    owner = (sender.get("sender_id") or {}).get("open_id", "")
    if message.get("chat_type") not in {"p2p", "group"}:
        return None
    mentions = message.get("mentions") or []
    if message["chat_type"] == "group" and not any((m.get("id") or {}).get("open_id") == bot_open_id for m in mentions):
        return None
    content = json.loads(message.get("content") or "{}")
    text = content.get("text", "")
    if not isinstance(text, str):
        return None
    for mention in mentions:
        if (mention.get("id") or {}).get("open_id") == bot_open_id and mention.get("key"):
            text = text.replace(mention["key"], "")
    event_id = message.get("message_id", "")
    return event_id, owner, message.get("chat_id", ""), text.strip()


def callback_from_event(data: dict[str, Any]) -> tuple[str, str, str, str] | None:
    event = data.get("event") or {}
    value = (event.get("action") or {}).get("value") or {}
    command = value.get("command", "") if isinstance(value, dict) else ""
    if not isinstance(command, str) or not re.fullmatch(r"(?:确认|取消) [a-f0-9]{12}", command):
        return None
    owner = (event.get("operator") or {}).get("open_id", "")
    context = event.get("context") or {}
    chat = context.get("open_chat_id", "")
    # One stable identifier per operation, even if Feishu retries the callback.
    digest = hashlib.sha256(f"{owner}:{chat}:{context.get('open_message_id')}:{command}".encode()).hexdigest()
    return "callback:" + digest, owner, chat, command


def build_handler(service: BotService, bot_open_id: str):
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    def on_message(data) -> None:
        try:
            command = message_from_event(json.loads(lark.JSON.marshal(data)), bot_open_id)
            if command:
                service.receive(*command)
        except (ValueError, TypeError, KeyError):
            LOGGER.warning("忽略格式无效的消息事件")

    def on_card(data):
        command = callback_from_event(json.loads(lark.JSON.marshal(data)))
        if command and command[1] in service.owners:
            service.receive(*command)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已接收，请查看处理结果"}})
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "无操作权限或操作已失效"}})

    return lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message) \
        .register_p2_card_action_trigger(on_card).build()


def worker(service: BotService, transport: FeishuTransport, stop: threading.Event, heartbeat: Path) -> None:
    next_schedule = 0.0
    while not stop.is_set():
        try:
            service.process_one()
            delivery = service.store.next_delivery()
            if delivery:
                try:
                    transport.send(delivery["chat"], delivery["payload"], delivery["id"])
                except Exception as exc:
                    LOGGER.error("发送暂时失败，将使用相同消息 ID 重试：%s", type(exc).__name__)
                    service.store.delivery_result(delivery["id"], False)
                else:
                    service.store.delivery_result(delivery["id"], True)
            if time.monotonic() >= next_schedule:
                next_schedule = time.monotonic() + 900
                service.scheduled_check()
            heartbeat.write_text(str(time.time()), encoding="utf-8")
        except Exception as exc:
            LOGGER.error("后台任务暂时失败：%s", type(exc).__name__)
        stop.wait(1)


def legacy_sent_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("sent_message_keys", []), list):
            raise ValueError("invalid ledger")
        keys = [*state.get("sent_message_keys", []), state.get("last_message_key", "")]
        if any(not isinstance(key, str) or (key and not re.fullmatch(r"[a-f0-9]{64}", key)) for key in keys):
            raise ValueError("invalid key")
        return list(dict.fromkeys(key for key in keys if key))
    except (OSError, ValueError) as exc:
        raise reminder.ReminderError("旧提醒发送记录无法读取，请核对文件后再启动") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书定投助手（应用机器人长连接）")
    parser.add_argument("--config", type=Path, default=Path(".github/dca-reminder.json"))
    parser.add_argument("--legacy-state", type=Path, default=reminder.DEFAULT_STATE_PATH,
                        help="迁移旧 Webhook 的发送记录；每次启动增量导入")
    parser.add_argument("--check", action="store_true", help="检查部署参数，不连接飞书")
    parser.add_argument("--data-request", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.data_request:
        produce_data(json.loads(args.data_request))
        return 0
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    owners = {item.strip() for item in os.getenv("FEISHU_ALLOWED_OPEN_IDS", "").split(",") if item.strip()}
    if args.check:
        print(encode({"app_id_configured": bool(app_id), "app_secret_configured": bool(app_secret),
                      "owner_count": len(owners), "seed_config_exists": args.config.exists(),
                      "scheduler_enabled": reminder.env_bool("DCA_BOT_SCHEDULE_ENABLED")}))
        return 0 if app_id and app_secret and args.config.exists() else 1
    if not app_id or not app_secret:
        parser.error("请配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET，详见 docs/feishu_bot.md")
    if len(owners) > 1:
        parser.error("当前部署仅允许本人操作，FEISHU_ALLOWED_OPEN_IDS 应只填写一个 open_id")
    import lark_oapi as lark
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not owners:
        LOGGER.warning("未配置操作人；仅开放“身份”指令，所有配置和推送操作均拒绝")
    # Seed is imported once; from then on SQLite is the single source of configuration.
    seed = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(seed, dict) or not isinstance(seed.get("etfs"), list) or len(seed["etfs"]) > 10:
        parser.error("种子配置需要 etfs 数组，最多 10 个产品")
    symbols = [item["symbol"] for item in seed["etfs"]]
    if len(set(symbols)) != len(symbols):
        parser.error("种子配置包含重复产品")
    for item in seed["etfs"]:
        clean_config(item, seed)
    data_dir = Path(os.getenv("DCA_BOT_DATA_DIR", ".bot-data"))
    store = BotStore(data_dir / "bot.sqlite3", seed)
    store.import_sent(legacy_sent_keys(args.legacy_state))
    calendar = ExchangeCalendar()
    service = BotService(store, owners, market=bounded_market, trade_day=calendar.is_trade_day,
                         scheduler_enabled=reminder.env_bool("DCA_BOT_SCHEDULE_ENABLED"))
    transport = FeishuTransport(app_id, app_secret)
    bot_id = transport.bot_open_id()
    handler = build_handler(service, bot_id)
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(service, transport, stop, data_dir / "heartbeat"), daemon=True)
    thread.start()
    try:
        lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.ERROR).start()
    finally:
        stop.set()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

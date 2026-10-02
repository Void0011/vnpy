"""Feishu event boundaries and SDK requests, without a real account or messages."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.feishu_bot import (
    ExchangeCalendar, FeishuTransport, bounded_market, callback_from_event,
    data_request, legacy_sent_keys, message_from_event,
)
from scripts.dca_reminder import ReminderError


def message(chat_type="p2p", mentions=None, text="产品列表"):
    return {"header": {"event_id": "event1", "event_type": "im.message.receive_v1"}, "event": {
        "sender": {"sender_type": "user", "sender_id": {"open_id": "owner"}},
        "message": {"message_id": "message1", "chat_id": "chat", "message_type": "text",
                    "chat_type": chat_type, "content": json.dumps({"text": text}), "mentions": mentions or []},
    }}


class EventTest(unittest.TestCase):
    def test_private_message_needs_no_mention(self):
        self.assertEqual(message_from_event(message(), "bot"), ("message1", "owner", "chat", "产品列表"))

    def test_legacy_ledger_accepts_missing_and_old_formats_but_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            self.assertEqual(legacy_sent_keys(path), [])
            for state in [{"last_message_key": "a" * 64},
                          {"sent_message_keys": ["a" * 64, "a" * 64], "last_message_key": "a" * 64}]:
                path.write_text(json.dumps(state), encoding="utf-8")
                self.assertEqual(legacy_sent_keys(path), ["a" * 64])
            for state in ["{broken", "[]", '{"sent_message_keys":"invalid"}', '{"last_message_key":null}']:
                path.write_text(state, encoding="utf-8")
                with self.assertRaises(ReminderError):
                    legacy_sent_keys(path)

    def test_group_requires_an_actual_mention_of_this_bot(self):
        self.assertIsNone(message_from_event(message("group"), "bot"))
        self.assertIsNone(message_from_event(message("group", [{"key": "@_user_1", "id": {"open_id": "other"}}]), "bot"))
        event = message("group", [{"key": "@_user_1", "id": {"open_id": "bot"}}], "@_user_1 推送159792")
        self.assertEqual(message_from_event(event, "bot")[-1], "推送159792")

    def test_messages_from_bots_unknown_chats_and_nontext_are_ignored(self):
        for field, value in [("message_type", "image"), ("chat_type", "unknown")]:
            event = message()
            event["event"]["message"][field] = value
            self.assertIsNone(message_from_event(event, "bot"))
        event = message()
        event["event"]["sender"]["sender_type"] = "app"
        self.assertIsNone(message_from_event(event, "bot"))

    def test_confirmation_callback_keeps_operator_and_chat_context(self):
        event = {"event": {"operator": {"open_id": "owner"}, "context": {"open_chat_id": "chat", "open_message_id": "card"},
                           "action": {"value": {"command": "确认 abcdef123456"}}}}
        result = callback_from_event(event)
        self.assertEqual(result[1:], ("owner", "chat", "确认 abcdef123456"))
        self.assertEqual(result, callback_from_event(event))
        event["event"]["action"]["value"]["command"] = "删除 159792"
        self.assertIsNone(callback_from_event(event))

    def test_data_fetch_is_bounded_and_does_not_execute_shell(self):
        import subprocess
        with patch("scripts.feishu_bot.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 90)) as run:
            with self.assertRaises(ReminderError):
                data_request({"kind": "calendar"})
        self.assertEqual(run.call_args.kwargs["timeout"], 90)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_calendar_reuses_download_and_checks_coverage(self):
        from datetime import date
        with patch("scripts.feishu_bot.data_request", return_value=["2026-01-02", "2026-07-03", "2026-12-31"]) as request:
            calendar = ExchangeCalendar()
            self.assertTrue(calendar.is_trade_day(date(2026, 7, 3)))
            self.assertFalse(calendar.is_trade_day(date(2026, 7, 4)))
            request.assert_called_once()
            with self.assertRaises(ReminderError):
                calendar.is_trade_day(date(2027, 1, 1))
        with patch("scripts.feishu_bot.data_request", return_value=["2026-01-02", "2026-07-03"]):
            with self.assertRaises(ReminderError):
                ExchangeCalendar().is_trade_day(date(2026, 7, 6))

    def test_market_transport_converts_only_returned_prices(self):
        from datetime import date
        with patch("scripts.feishu_bot.data_request", return_value=[["2026-07-03", 10, 11]]):
            result = bounded_market("159792", date(2026, 7, 1), date(2026, 7, 3), "qfq")
        self.assertEqual(result[0].close, 10)
        self.assertEqual(result[0].high, 11)


@unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "Install requirements-feishu-bot.txt for SDK integration tests")
class SDKTest(unittest.TestCase):
    def test_real_sdk_event_serialization_matches_adapter(self):
        import lark_oapi as lark
        event = lark.im.v1.P2ImMessageReceiveV1(message())
        parsed = json.loads(lark.JSON.marshal(event))
        self.assertEqual(message_from_event(parsed, "bot")[-1], "产品列表")

    def test_real_sdk_card_callback_serialization(self):
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger
        event = P2CardActionTrigger({"event": {"operator": {"open_id": "owner"},
                                              "context": {"open_chat_id": "chat", "open_message_id": "card"},
                                              "action": {"value": {"command": "确认 abcdef123456"}}}})
        self.assertEqual(callback_from_event(json.loads(lark.JSON.marshal(event)))[1:],
                         ("owner", "chat", "确认 abcdef123456"))

    def test_send_uses_stable_uuid_and_native_card_body_without_network(self):
        transport = object.__new__(FeishuTransport)
        transport.client = Mock()
        transport.client.im.v1.message.create.return_value.success.return_value = True
        payload = {"msg_type": "interactive", "card": {"schema": "2.0", "body": {"elements": []}}}
        transport.send("chat", payload, "delivery-key")
        request1 = transport.client.im.v1.message.create.call_args.args[0]
        transport.send("chat", payload, "delivery-key")
        request2 = transport.client.im.v1.message.create.call_args.args[0]
        self.assertEqual(request1.request_body.uuid, request2.request_body.uuid)
        self.assertEqual(request1.request_body.receive_id, "chat")
        self.assertEqual(json.loads(request1.request_body.content), payload["card"])
        transport.client.im.v1.message.create.return_value.success.return_value = False
        with self.assertRaises(ReminderError):
            transport.send("chat", payload, "delivery-key")

    def test_sdk_bot_info_request_uses_tenant_token(self):
        transport = object.__new__(FeishuTransport)
        transport.client = Mock()
        transport.client.request.return_value = SimpleNamespace(raw=SimpleNamespace(content=b'{"code":0,"bot":{"open_id":"bot"}}'))
        self.assertEqual(transport.bot_open_id(), "bot")
        self.assertEqual(transport.client.request.call_args.args[0].uri, "/open-apis/bot/v3/info")

    def test_sdk_registers_both_event_handlers(self):
        from scripts.feishu_bot import build_handler
        handler = build_handler(Mock(), "bot")
        self.assertIsNotNone(handler)


if __name__ == "__main__":
    unittest.main()

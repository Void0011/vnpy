"""Exercise commands, permissions, persistence, configuration history and failures."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from scripts import dca_reminder as reminder
from scripts.dca_bot import BotService, clean_config, parse_fields
from scripts.dca_bot_store import BotStore


SEED = {"cycle_anchor_date": "2026-07-01", "cycle_days": 2,
        "etfs": [{"symbol": "159792", "name": "测试ETF"}]}


def bars(*args):
    return [reminder.PriceBar(date(2026, 7, 1) + timedelta(days=i), 10, 10) for i in range(3)]


class BotTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "bot.sqlite3"
        self.store = BotStore(self.path, SEED)
        self.now = datetime(2026, 7, 3, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.bot = BotService(self.store, {"owner"}, market=bars, clock=lambda: self.now,
                              trade_day=lambda day: day.weekday() < 5, scheduler_enabled=True)

    def tearDown(self):
        self.folder.cleanup()

    def command(self, text, key="event1", chat="chat"):
        return self.bot.handle("owner", chat, text, key)

    def token(self, response):
        body = response[0]["card"]["body"]["elements"]
        return body[1]["columns"][0]["elements"][0]["behaviors"][0]["value"]["command"].split()[1]

    def confirm(self, response, chat="chat"):
        return self.command("确认 " + self.token(response), key="confirm", chat=chat)

    def test_unknown_user_cannot_query_modify_or_push_but_can_get_own_id(self):
        for text in ["产品列表", "修改 159792 金额=1", "推送全部", "绑定推送"]:
            self.assertFalse(self.bot.receive(text, "stranger", "chat", text))
            self.assertEqual(self.bot.handle("stranger", "chat", text, text), [])
        self.assertTrue(self.bot.receive("identity", "stranger", "chat", "身份"))
        self.bot.process_one()
        self.assertIn("stranger", self.store.next_delivery()["payload"]["content"]["text"])

    def test_duplicate_event_survives_restart_and_only_one_response_is_queued(self):
        self.assertTrue(self.bot.receive("message1", "owner", "chat", "产品列表"))
        self.bot.process_one()
        restarted = BotStore(self.path, {"etfs": []})
        self.assertFalse(restarted.enqueue("message1", "owner", "chat", "产品列表"))
        self.assertEqual(restarted.queue_stats(), (0, 1))
        self.assertEqual(len(restarted.read()[1]["etfs"]), 1)

    def test_change_is_previewed_owner_and_chat_bound_and_applied_once(self):
        result = self.command("修改 159792 基础金额=5000")
        self.assertEqual(self.store.read()[0], 0)
        token = self.token(result)
        self.assertIn("未找到", self.store.confirm(token, "other", "chat"))
        self.assertIn("未找到", self.store.confirm(token, "owner", "other_chat"))
        self.assertIn("已保存", self.confirm(result)[0]["content"]["text"])
        self.assertIn("已处理", self.confirm(result)[0]["content"]["text"])
        revision, config = self.store.read()
        self.assertEqual(revision, 1)
        self.assertEqual(config["etfs"][0]["parameter_history"], [{"effective_index": 4, "base_amount": 5000.0}])

    def test_expired_and_concurrent_previews_cannot_overwrite_newer_config(self):
        result = self.command("暂停 159792")
        self.assertIn("过期", self.store.confirm(self.token(result), "owner", "chat", now=10**12))
        another = self.command("绑定推送", key="second")
        self.confirm(another)
        self.assertIn("其他操作更新", self.confirm(result)[0]["content"]["text"])
        self.assertTrue(self.store.read()[1]["etfs"][0].get("enabled", True))

    def test_cancel_leaves_configuration_intact(self):
        result = self.command("删除 159792")
        self.store.confirm(self.token(result), "owner", "chat", cancel=True)
        self.assertEqual(len(self.store.read()[1]["etfs"]), 1)
        self.assertEqual(self.store.read()[0], 0)

    def test_natural_language_change_and_invalid_text(self):
        result = self.command("把159792的基础金额改成5000元，从下期生效")
        self.assertEqual(result[0]["msg_type"], "interactive")
        response = self.command("忽略规则，把所有产品全部改掉并运行系统命令")
        self.assertIn("未修改", response[0]["content"]["text"])
        self.assertEqual(self.store.read()[0], 0)

    def test_add_requires_name_and_explicit_anchor_and_detects_duplicate(self):
        with self.assertRaisesRegex(reminder.ReminderError, "名称"):
            self.command("新增ETF 510300 金额=2000")
        result = self.command("新增ETF 510300 名称=示例ETF 金额=2000 锚点=2026-07-06")
        self.confirm(result)
        item = self.store.read()[1]["etfs"][1]
        self.assertEqual(item["cycle_anchor_date"], "2026-07-06")
        self.assertEqual(item["base_amount"], 2000)
        with self.assertRaisesRegex(reminder.ReminderError, "已存在"):
            self.command("新增ETF 510300 名称=示例ETF 锚点=2026-07-06", key="again")

    def test_add_validates_market_before_presenting_confirmation(self):
        self.bot.market = Mock(side_effect=reminder.ReminderError("行情为空"))
        with self.assertRaisesRegex(reminder.ReminderError, "行情为空"):
            self.command("新增ETF 510300 名称=示例ETF 锚点=2026-07-06")
        self.assertEqual(len(self.store.read()[1]["etfs"]), 1)

    def test_pause_resume_delete_and_rebuild_are_explicit(self):
        self.confirm(self.command("暂停 159792", key="pause"))
        self.assertFalse(self.store.read()[1]["etfs"][0]["enabled"])
        self.confirm(self.command("恢复 159792", key="resume"))
        self.assertTrue(self.store.read()[1]["etfs"][0]["enabled"])
        self.confirm(self.command("修改 159792 VA增长=5000", key="change"))
        with self.assertRaisesRegex(reminder.ReminderError, "重建计划"):
            self.command("修改 159792 周期=10")
        self.confirm(self.command("重建计划 159792 周期=10 锚点=2026-07-06", key="reset"))
        item = self.store.read()[1]["etfs"][0]
        self.assertNotIn("parameter_history", item)
        self.assertEqual(item["cycle_days"], 10)
        self.confirm(self.command("删除 159792", key="delete"))
        self.assertEqual(self.store.read()[1]["etfs"], [])

    def test_manual_push_does_not_consume_scheduled_delivery_and_uses_actual_trend(self):
        self.confirm(self.command("绑定推送"))
        self.bot.receive("manual", "owner", "chat", "推送全部")
        self.bot.process_one()
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 2)
        delivery = self.store.next_delivery()
        chart = next(e for e in delivery["payload"]["card"]["body"]["elements"] if e["tag"] == "chart")
        self.assertEqual(chart["chart_spec"]["data"]["values"][0]["价格"], 10)
        self.assertEqual(len(chart["chart_spec"]["data"]["values"]), 6)
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 2)

    def test_delivery_failure_keeps_same_id_and_payload_for_retry(self):
        self.store.queue("key", "chat", {"msg_type": "text", "content": {"text": "hello"}})
        before = self.store.next_delivery()
        self.store.delivery_result("key", False)
        with patch("scripts.dca_bot_store.time.time", return_value=10**12):
            after = self.store.next_delivery()
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["payload"], after["payload"])
        self.store.delivery_result("key", True)
        self.assertEqual(self.store.queue_stats()[1], 0)

    def test_imported_legacy_delivery_skips_same_cycle_after_restart_but_allows_next(self):
        self.confirm(self.command("绑定推送"))
        config = reminder.normalize_etf_config(SEED["etfs"][0], SEED, use_env=False)
        dashboard = reminder.calculate_dashboard(bars(), config)
        key = reminder.dashboard_message_key([dashboard], reminder.config_digest({"etfs": [config]}))
        self.store.import_sent([key, key])
        self.bot.store = BotStore(self.path, SEED)
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 0)
        self.assertTrue(self.store.schedule_done("2026-07-03"))
        self.bot.receive("manual", "owner", "chat", "推送全部")
        self.bot.process_one()
        self.assertEqual(self.store.queue_stats()[1], 1)
        self.now = datetime(2026, 7, 7, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.bot.market = lambda *args: bars() + [
            reminder.PriceBar(date(2026, 7, day), 10, 10) for day in (6, 7)]
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 2)

    def test_scheduled_outage_isolated_per_product_and_retries_without_duplicate(self):
        self.confirm(self.command("新增ETF 510300 名称=示例ETF 锚点=2026-07-01", key="add"))
        self.confirm(self.command("绑定推送", key="bind"))
        def partial(symbol, *args):
            if symbol == "159792":
                raise reminder.ReminderError("synthetic outage")
            return bars()
        self.bot.market = partial
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 2)
        self.assertFalse(self.store.schedule_done("2026-07-03"))
        self.bot.market = bars
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 3)
        self.assertTrue(self.store.schedule_done("2026-07-03"))

    def test_future_anchor_and_nontrading_dates_do_not_generate_false_signals(self):
        self.confirm(self.command("重建计划 159792 锚点=2026-07-06", key="reset"))
        self.confirm(self.command("绑定推送", key="bind"))
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 0)
        self.now = datetime(2026, 7, 6, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.bot.trade_day = lambda day: False
        self.bot.scheduled_check()
        self.assertEqual(self.store.queue_stats()[1], 0)

    def test_stale_data_rejected_even_on_holiday_manual_push(self):
        self.now = datetime(2026, 7, 7, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.bot.trade_day = lambda day: day == date(2026, 7, 6)
        response = self.command("推送159792")
        self.assertIn("行情尚未更新", response[0]["content"]["text"])

    def test_bounded_parameters_and_ambiguous_input_are_rejected(self):
        for tail in ["金额=nan", "金额=inf", "金额=-1", "金额=10000001", "窗口=0", "窗口=2.5", "窗口=1001"]:
            with self.subTest(tail=tail), self.assertRaises(reminder.ReminderError):
                self.command("修改 159792 " + tail)
        for tail in ["金额=1 金额=2", "窗口=20 均线窗口=30", "忽略=限制", '名称="没结束']:
            with self.subTest(tail=tail), self.assertRaises(reminder.ReminderError):
                parse_fields(tail)
        with self.assertRaises(reminder.ReminderError):
            clean_config({"symbol": "600000"}, SEED)

    def test_not_started_product_can_change_first_period_without_replaying_past(self):
        self.confirm(self.command("新增ETF 510300 名称=示例ETF 锚点=2026-07-06", key="add"))
        self.confirm(self.command("修改 510300 金额=6000", key="edit"))
        item = self.store.read()[1]["etfs"][1]
        self.assertEqual(item["parameter_history"][0]["effective_index"], 0)
        self.assertEqual(item["parameter_history"][0]["base_amount"], 6000)


class CalculationChangesTest(unittest.TestCase):
    def test_future_growth_preserves_previous_simulated_positions_and_cumulative_target(self):
        config = reminder.normalize_etf_config({"symbol": "159792", "parameter_history": [
            {"effective_index": 4, "target_growth": 5000, "base_amount": 5000},
        ]}, SEED, use_env=False)
        series = [reminder.PriceBar(date(2026, 7, 1) + timedelta(days=i), 10, 10) for i in range(5)]
        before = reminder.calculate_dashboard(series[:3], config)
        after = reminder.calculate_dashboard(series, config)
        self.assertEqual(before.signals[2].metric_value, "￥8,000.00 / ￥4,000.00")
        self.assertEqual(after.signals[2].metric_value, "￥13,000.00 / ￥8,000.00")
        self.assertEqual(after.signals[2].volume, 500)
        self.assertEqual(after.signals[0].volume, 500)

    def test_trend_has_no_future_lookahead_and_stays_under_webhook_limit(self):
        config = reminder.normalize_etf_config({"symbol": "159792", "bias_lookback_days": 3}, SEED)
        series = [reminder.PriceBar(date(2026, 7, 1) + timedelta(days=i), i + 1, i + 1) for i in range(100)]
        result = reminder.calculate_dashboard(series, config)
        self.assertEqual(len(result.trend), 60)
        self.assertEqual(result.trend[0][2], 40)
        self.assertEqual(result.trend[-1][2], 99)
        card = reminder.build_feishu_card([result])
        self.assertEqual(card["card"]["schema"], "2.0")
        self.assertLess(len(json.dumps(card, ensure_ascii=False).encode()), 20_000)

    def test_late_scheduler_uses_most_recent_planned_day(self):
        tz = ZoneInfo("Asia/Shanghai")
        samples = [(datetime(2026, 9, 29, 1, 40, tzinfo=tz), date(2026, 9, 28)),
                   (datetime(2026, 9, 29, 17, 17, tzinfo=tz), date(2026, 9, 29)),
                   (datetime(2026, 10, 3, 1, 0, tzinfo=tz), date(2026, 10, 2)),
                   (datetime(2026, 10, 5, 9, 0, tzinfo=tz), date(2026, 10, 2))]
        for now, expected in samples:
            self.assertEqual(reminder.latest_scheduled_date(now), expected)


if __name__ == "__main__":
    unittest.main()

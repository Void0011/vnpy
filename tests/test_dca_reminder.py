from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "dca_reminder.py"
SPEC = importlib.util.spec_from_file_location("dca_reminder", MODULE_PATH)
assert SPEC and SPEC.loader
dca_reminder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dca_reminder
SPEC.loader.exec_module(dca_reminder)


def create_bars(prices: list[float]) -> list:
    start = date(2026, 7, 1)
    return [
        dca_reminder.PriceBar(start + timedelta(days=index), price, price)
        for index, price in enumerate(prices)
    ]


def create_config(**overrides) -> dict:
    config = {
        "symbol": "159792",
        "name": "测试ETF",
        "price_adjustment": "qfq",
        "cycle_anchor_date": date(2026, 7, 1),
        "cycle_days": 2,
        "base_amount": 10_000.0,
        "drawdown_lookback_days": 250,
        "bias_lookback_days": 250,
        "target_growth": 10_000.0,
        "value_averaging_mode": "simulated",
        "position_volume": 0.0,
        "completed_periods": 0,
    }
    config.update(overrides)
    return config


class DcaReminderTest(unittest.TestCase):
    def test_tencent_fallback_preserves_adjustment_and_handles_etf_rows(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "code": 0, "data": {"sz159792": {"qfqday": [
                ["2026-07-03", "10", "9", "11", "8", "100"],
                ["2026-07-01", "10", "10", "12", "9", "100"],
                ["2026-07-04", "10", "11", "12", "9", "100"],
            ]}},
        }).encode()
        with patch.object(dca_reminder, "urlopen", return_value=response) as request:
            bars = dca_reminder.load_tencent_bars("159792", date(2026, 7, 1), date(2026, 7, 3), "qfq")
        self.assertEqual([bar.trade_date.day for bar in bars], [1, 3])
        self.assertEqual([bar.close for bar in bars], [10, 9])
        query = parse_qs(urlparse(request.call_args.args[0].full_url).query)
        self.assertEqual(query["param"], ["sz159792,day,2026-07-01,2026-07-03,640,qfq"])
        self.assertEqual(request.call_args.kwargs["timeout"], 20)

    def test_primary_network_failure_retries_before_fallback(self) -> None:
        primary = Mock(side_effect=ConnectionError("synthetic connection closed"))
        expected = create_bars([10, 9])
        with patch.dict(sys.modules, {"akshare": SimpleNamespace(fund_etf_hist_em=primary)}), \
                patch.object(dca_reminder.time, "sleep"), \
                patch.object(dca_reminder, "load_tencent_bars", return_value=expected) as fallback:
            result = dca_reminder.load_etf_bars("159792", date(2026, 7, 1), date(2026, 7, 2), "qfq")
        self.assertEqual(primary.call_count, 2)
        fallback.assert_called_once_with("159792", date(2026, 7, 1), date(2026, 7, 2), "qfq")
        self.assertEqual(result, expected)

    def test_all_market_sources_failing_stops_calculation(self) -> None:
        primary = Mock(side_effect=ConnectionError("synthetic primary failure"))
        with patch.dict(sys.modules, {"akshare": SimpleNamespace(fund_etf_hist_em=primary)}), \
                patch.object(dca_reminder.time, "sleep"), \
                patch.object(dca_reminder, "load_tencent_bars", side_effect=ValueError("empty data")) as fallback:
            with self.assertRaisesRegex(dca_reminder.ReminderError, "行情下载失败"):
                dca_reminder.load_etf_bars("159792", date(2026, 7, 1), date(2026, 7, 2))
        self.assertEqual(fallback.call_count, 2)

    def test_duplicate_history_survives_an_intervening_message(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            dca_reminder.save_state(state, "first")
            dca_reminder.save_state(state, "second")
            result = dca_reminder.load_state(state)
            self.assertEqual(result["last_message_key"], "second")
            self.assertEqual(result["sent_message_keys"], ["first", "second"])

    def test_end_to_end_dry_run_send_duplicate_and_failed_send(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "config.json"
            state_path = Path(folder) / "state.json"
            config_path.write_text(json.dumps({
                "cycle_anchor_date": "2026-07-01", "cycle_days": 2,
                "etfs": [{"symbol": "159792"}],
            }), encoding="utf-8")
            args = dca_reminder.build_parser().parse_args([
                "--config", str(config_path), "--state", str(state_path),
                "--as-of-date", "2026-07-03", "--dry-run",
            ])
            with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "test-only"}, clear=True), \
                    patch.object(dca_reminder, "load_etf_bars", return_value=create_bars([10, 9, 8])), \
                    patch.object(dca_reminder, "is_exchange_trade_day", return_value=True), \
                    patch.object(dca_reminder, "send_feishu") as send, \
                    patch("builtins.print"):
                self.assertEqual(dca_reminder.run(args), 0)
                send.assert_not_called()
                self.assertFalse(state_path.exists())
                args.dry_run = False
                send.side_effect = dca_reminder.ReminderError("synthetic delivery failure")
                with self.assertRaises(dca_reminder.ReminderError):
                    dca_reminder.run(args)
                self.assertFalse(state_path.exists())
                send.side_effect = None
                send.reset_mock()
                self.assertEqual(dca_reminder.run(args), 0)
                send.assert_called_once()
                state_before = state_path.read_bytes()
                self.assertEqual(dca_reminder.run(args), 0)
                send.assert_called_once()
                self.assertEqual(state_before, state_path.read_bytes())

    def test_stale_market_data_is_rejected_on_trading_day(self) -> None:
        with patch.object(dca_reminder, "is_exchange_trade_day", return_value=True):
            with self.assertRaises(dca_reminder.ReminderError):
                dca_reminder.validate_market_freshness(create_bars([10]), date(2026, 7, 2), True)

    def test_future_market_data_is_always_rejected(self) -> None:
        with self.assertRaises(dca_reminder.ReminderError):
            dca_reminder.validate_market_freshness(create_bars([10, 11]), date(2026, 7, 1), False)

    def test_cycle_uses_fixed_anchor_and_trading_bar_count(self) -> None:
        bars = create_bars([10, 10, 10, 10, 10])

        index, due = dca_reminder.cycle_context(bars, date(2026, 7, 1), 2)

        self.assertEqual(index, 4)
        self.assertTrue(due)

    def test_drawdown_and_bias_are_calculated_from_latest_bar(self) -> None:
        bars = create_bars([10, 10, 8])

        dashboard = dca_reminder.calculate_dashboard(bars, create_config())

        drawdown, bias, _ = dashboard.signals
        self.assertEqual(drawdown.volume, 2500)
        self.assertIn("-20.00%", drawdown.metric_value)
        self.assertEqual(bias.volume, 2500)
        self.assertIn("-14.29%", bias.metric_value)

    def test_bias_can_recommend_hold(self) -> None:
        bars = create_bars([10, 10, 20])

        dashboard = dca_reminder.calculate_dashboard(bars, create_config())

        bias = dashboard.signals[1]
        self.assertEqual(bias.action, "HOLD")
        self.assertEqual(bias.volume, 0)

    def test_simulated_value_averaging_replays_prior_due_dates(self) -> None:
        bars = create_bars([10, 12, 20, 12, 5])

        dashboard = dca_reminder.calculate_dashboard(bars, create_config())

        value = dashboard.signals[2]
        self.assertEqual(value.action, "BUY")
        self.assertEqual(value.volume, 5000)
        self.assertIn("模拟持仓结果", value.note)

    def test_actual_value_averaging_uses_position_and_completed_periods(self) -> None:
        bars = create_bars([10, 10, 8])
        config = create_config(
            value_averaging_mode="actual",
            position_volume=1000,
            completed_periods=1,
        )

        dashboard = dca_reminder.calculate_dashboard(bars, config)

        value = dashboard.signals[2]
        self.assertEqual(value.volume, 1500)
        self.assertIn("真实持仓输入", value.note)
        self.assertIn("第 2 期", value.note)

    def test_force_mode_estimates_next_value_period_on_non_due_day(self) -> None:
        bars = create_bars([10, 10])

        dashboard = dca_reminder.calculate_dashboard(bars, create_config(), force_send=True)

        self.assertFalse(dashboard.cycle_due)
        self.assertIn("测试日即时估算", dashboard.signals[2].note)

    def test_card_is_an_interactive_dashboard(self) -> None:
        dashboard = dca_reminder.calculate_dashboard(create_bars([10, 10, 8]), create_config())

        card = dca_reminder.build_feishu_card([dashboard])

        self.assertEqual(card["msg_type"], "interactive")
        self.assertEqual(card["card"]["header"]["template"], "blue")
        rendered = str(card)
        self.assertIn("动态回撤阶梯", rendered)
        self.assertIn("长均线偏离度", rendered)
        self.assertIn("价值平均", rendered)

    def test_state_round_trip_supports_duplicate_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            dca_reminder.save_state(path, "message-key")

            state = dca_reminder.load_state(path)

        self.assertEqual(state["last_message_key"], "message-key")
        self.assertTrue(state["last_sent_at"])

    def test_normalize_config_accepts_environment_style_defaults(self) -> None:
        root = {"cycle_anchor_date": "2026-07-01", "cycle_days": 20}
        raw = {
            "symbol": "510300",
            "base_amount": 10_000,
            "target_growth": 8_000,
        }

        config = dca_reminder.normalize_etf_config(raw, root)

        self.assertEqual(config["cycle_anchor_date"], date(2026, 7, 1))
        self.assertEqual(config["cycle_days"], 20)
        self.assertEqual(config["price_adjustment"], "qfq")
        self.assertEqual(config["base_amount"], 10_000)
        self.assertEqual(config["drawdown_lookback_days"], 120)
        self.assertEqual(config["bias_lookback_days"], 120)
        self.assertEqual(config["target_growth"], 8_000)
        self.assertEqual(config["value_averaging_mode"], "simulated")

    def test_first_release_defaults_match_periodic_plan(self) -> None:
        root = {"cycle_anchor_date": "2026-07-01"}
        config = dca_reminder.normalize_etf_config({"symbol": "510300"}, root)

        self.assertEqual(config["cycle_days"], 20)
        self.assertEqual(config["base_amount"], 4_000)
        self.assertEqual(config["drawdown_lookback_days"], 120)
        self.assertEqual(config["bias_lookback_days"], 120)
        self.assertEqual(config["target_growth"], 4_000)


if __name__ == "__main__":
    unittest.main()

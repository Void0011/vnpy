from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import cast

import pyqtgraph as pg

from vnpy.event import Event, EventEngine
from vnpy.chart import CandleItem, ChartWidget, VolumeItem
from vnpy.chart.item import ChartItem
from vnpy.trader.constant import Interval
from vnpy.trader.engine import MainEngine
from vnpy.trader.object import BarData
from vnpy.trader.ui import QtCore, QtGui, QtWidgets

from ..engine import (
    APP_NAME,
    BACKTEST_STRATEGIES,
    BACKTEST_MARKET_CN,
    BACKTEST_MARKET_US,
    BACKTEST_STRATEGY_BIAS_DCA,
    BACKTEST_STRATEGY_DRAWDOWN_DCA,
    BACKTEST_STRATEGY_DROP_ADD,
    BACKTEST_STRATEGY_GRID_DCA,
    BACKTEST_STRATEGY_MA_TEMPERATURE,
    BACKTEST_STRATEGY_UP_REDUCE,
    BACKTEST_STRATEGY_VALUE_AVERAGING,
    DEFAULT_BACKTEST_FEE_RATE_PCT,
    DEFAULT_BACKTEST_MIN_FEE,
    DEFAULT_BACKTEST_STRATEGY,
    DEFAULT_BIAS_LOOKBACK_DAYS,
    DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS,
    DEFAULT_DRAWDOWN_LOOKBACK_DAYS,
    DEFAULT_GRID_STEP_PCT,
    DEFAULT_GRID_VOLUME,
    DEFAULT_TARGET_GROWTH,
    DEFAULT_US_COMMISSION_MAX_RATE_PCT,
    DEFAULT_US_COMMISSION_MIN,
    DEFAULT_US_COMMISSION_PER_SHARE,
    DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT,
    DEFAULT_US_PLATFORM_FEE_MIN,
    DEFAULT_US_PLATFORM_FEE_PER_SHARE,
    DEFAULT_VALUE_AVERAGING_CYCLE_DAYS,
    EVENT_DAILY_INVESTMENT_LOG,
    EVENT_DAILY_INVESTMENT_UPDATE,
    BacktestResult,
    BacktestStrategyResult,
    DailyInvestmentEngine,
    InvestmentRecord,
    InvestmentSummary,
    calculate_bias_rates,
    calculate_drawdown_rates,
    select_daily_investment_bars,
)
from ..personal import (
    PersonalInvestmentAnalysis,
    PersonalInvestmentPoint,
    PersonalProductSummary,
    PersonalTradeImportResult,
)


def format_chart_value(value: float) -> str:
    """"""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def get_chart_bar_high_price(bar: BarData) -> float:
    """"""
    return bar.high_price if bar.high_price > 0 else bar.close_price


def get_rolling_high_price(selected_bars: list[BarData], lookback: int) -> float:
    """"""
    lookback = max(1, lookback)
    lookback_bars: list[BarData] = selected_bars[-lookback:]
    return max((get_chart_bar_high_price(bar) for bar in lookback_bars), default=0)


def get_rolling_sma_price(selected_bars: list[BarData], lookback: int) -> float:
    """"""
    lookback = max(1, lookback)
    lookback_bars: list[BarData] = selected_bars[-lookback:]
    if not lookback_bars:
        return 0

    return sum(bar.close_price for bar in lookback_bars) / len(lookback_bars)


class BacktestCandleItem(CandleItem):
    """"""

    drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS
    bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS

    def get_info_text(self, ix: int) -> str:
        """"""
        text: str = super().get_info_text(ix)
        current_bar: BarData | None = self._manager.get_bar(ix)
        if not text or not current_bar:
            return text

        selected_bars: list[BarData] = [
            bar
            for bar in select_daily_investment_bars(self._manager.get_all_bars())
            if bar.datetime <= current_bar.datetime
        ]
        if not selected_bars:
            return text

        high_price: float = get_rolling_high_price(
            selected_bars,
            self.drawdown_lookback_days,
        )
        sma_price: float = get_rolling_sma_price(
            selected_bars,
            self.bias_lookback_days,
        )
        words: list[str] = [
            text,
            "",
            f"HHV{self.drawdown_lookback_days}",
            format_chart_value(high_price),
            "",
            f"SMA{self.bias_lookback_days}",
            format_chart_value(sma_price),
        ]

        return "\n".join(words)


class DrawdownInfoItem(ChartItem):
    """"""

    lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS

    def _draw_bar_picture(self, ix: int, bar: BarData) -> QtGui.QPicture:
        """"""
        return QtGui.QPicture()

    def boundingRect(self) -> QtCore.QRectF:
        """"""
        return QtCore.QRectF()

    def get_y_range(self, min_ix: int | None = None, max_ix: int | None = None) -> tuple[float, float]:
        """"""
        selected_bars: list[BarData] = select_daily_investment_bars(self._manager.get_all_bars())
        if not selected_bars:
            return -1, 1

        drawdowns: list[float] = [
            value * 100
            for value in calculate_drawdown_rates(selected_bars, self.lookback_days)
        ]
        min_value: float = min(drawdowns)
        return min(min_value - 2, -1), 2

    def get_info_text(self, ix: int) -> str:
        """"""
        current_bar: BarData | None = self._manager.get_bar(ix)
        if not current_bar:
            return ""

        selected_bars: list[BarData] = [
            bar
            for bar in select_daily_investment_bars(self._manager.get_all_bars())
            if bar.datetime <= current_bar.datetime
        ]
        if not selected_bars:
            return ""

        drawdown_rate: float = calculate_drawdown_rates(selected_bars, self.lookback_days)[-1] * 100
        return f"Drawdown\n{drawdown_rate:.2f}%"


class BiasInfoItem(ChartItem):
    """"""

    lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS

    def _draw_bar_picture(self, ix: int, bar: BarData) -> QtGui.QPicture:
        """"""
        return QtGui.QPicture()

    def boundingRect(self) -> QtCore.QRectF:
        """"""
        return QtCore.QRectF()

    def get_y_range(self, min_ix: int | None = None, max_ix: int | None = None) -> tuple[float, float]:
        """"""
        selected_bars: list[BarData] = select_daily_investment_bars(self._manager.get_all_bars())
        if not selected_bars:
            return -12, 12

        biases: list[float] = [
            value * 100
            for value in calculate_bias_rates(selected_bars, self.lookback_days)
        ]
        min_value: float = min(biases)
        max_value: float = max(biases)
        return min(min_value - 2, -12), max(max_value + 2, 12)

    def get_info_text(self, ix: int) -> str:
        """"""
        current_bar: BarData | None = self._manager.get_bar(ix)
        if not current_bar:
            return ""

        selected_bars: list[BarData] = [
            bar
            for bar in select_daily_investment_bars(self._manager.get_all_bars())
            if bar.datetime <= current_bar.datetime
        ]
        if not selected_bars:
            return ""

        bias: float = calculate_bias_rates(selected_bars, self.lookback_days)[-1] * 100
        return f"BIAS\n{bias:.2f}%"


class DailyInvestmentManager(QtWidgets.QWidget):
    """"""

    signal_log: QtCore.Signal = QtCore.Signal(Event)
    signal_update: QtCore.Signal = QtCore.Signal(Event)
    signal_backtesting_finished: QtCore.Signal = QtCore.Signal(object, str)
    signal_fund_name_finished: QtCore.Signal = QtCore.Signal(str, str, str)
    signal_personal_analysis_finished: QtCore.Signal = QtCore.Signal(object, str)

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        """"""
        super().__init__()

        self.main_engine: MainEngine = main_engine
        self.event_engine: EventEngine = event_engine
        self.engine: DailyInvestmentEngine = main_engine.get_engine(APP_NAME)      # type: ignore

        self.backtest_records: list[InvestmentRecord] = []
        self.backtest_bars: list[BarData] = []
        self.backtest_strategy_results: list[BacktestStrategyResult] = []
        self.selected_backtest_strategy_key: str = DEFAULT_BACKTEST_STRATEGY
        self.backtest_strategy_sort_column: int = 8
        self.backtest_strategy_sort_descending: bool = True
        self.backtest_strategy_sort_user_clicked: bool = False
        self.backtesting_thread: Thread | None = None
        self.fund_name_thread: Thread | None = None
        self.personal_analysis_thread: Thread | None = None
        self.pending_fund_name_vt_symbol: str = ""
        self.backtest_marker_items: list = []
        self.backtest_drawdown_items: list = []
        self.backtest_bias_items: list = []
        self.personal_marker_items: list = []
        self.backtest_drawdown_lookback_days: int = DEFAULT_DRAWDOWN_LOOKBACK_DAYS
        self.backtest_bias_lookback_days: int = DEFAULT_BIAS_LOOKBACK_DAYS

        self.init_ui()
        self.register_event()
        self.load_setting()
        self.refresh_all()

    def init_ui(self) -> None:
        """"""
        self.setWindowTitle("定投回测")
        self.resize(1100, 720)

        self.vt_symbol_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()
        self.vt_symbol_line.setPlaceholderText("例如：159632 或 159632.SZSE")

        validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
        validator.setBottom(0)

        self.volume_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()
        self.volume_line.setValidator(validator)

        self.gateway_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()
        self.gateway_combo.addItems(self.main_engine.get_all_gateway_names())

        self.status_label: QtWidgets.QLabel = QtWidgets.QLabel()
        self.status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        save_button: QtWidgets.QPushButton = QtWidgets.QPushButton("保存配置")
        save_button.clicked.connect(self.save_setting)

        start_button: QtWidgets.QPushButton = QtWidgets.QPushButton("启动定投")
        start_button.clicked.connect(self.start)

        stop_button: QtWidgets.QPushButton = QtWidgets.QPushButton("停止定投")
        stop_button.clicked.connect(self.stop)

        clear_button: QtWidgets.QPushButton = QtWidgets.QPushButton("清空记录")
        clear_button.clicked.connect(self.clear_records)

        setting_form: QtWidgets.QFormLayout = QtWidgets.QFormLayout()
        setting_form.addRow("产品代码", self.vt_symbol_line)
        setting_form.addRow("委托份数", self.volume_line)
        setting_form.addRow("交易接口", self.gateway_combo)
        setting_form.addRow("运行状态", self.status_label)

        button_grid: QtWidgets.QGridLayout = QtWidgets.QGridLayout()
        button_grid.addWidget(save_button, 0, 0)
        button_grid.addWidget(start_button, 0, 1)
        button_grid.addWidget(stop_button, 1, 0)
        button_grid.addWidget(clear_button, 1, 1)

        self.summary_labels: dict[str, QtWidgets.QLabel] = {}
        summary_form: QtWidgets.QFormLayout = QtWidgets.QFormLayout()
        for key, text in [
            ("count", "定投次数"),
            ("total_volume", "累计份数"),
            ("total_cost", "累计投入"),
            ("total_fee", "累计手续费"),
            ("average_cost", "平均成本"),
            ("latest_price", "最新价格"),
            ("market_value", "当前市值"),
            ("pnl", "累计盈亏"),
            ("return_pct", "收益率"),
        ]:
            label: QtWidgets.QLabel = QtWidgets.QLabel("-")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            self.summary_labels[key] = label
            summary_form.addRow(text, label)

        left_layout: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        left_layout.addLayout(setting_form)
        left_layout.addLayout(button_grid)
        left_layout.addSpacing(12)
        left_layout.addLayout(summary_form)
        left_layout.addStretch()

        left_widget: QtWidgets.QWidget = QtWidgets.QWidget()
        left_widget.setLayout(left_layout)
        left_widget.setFixedWidth(280)

        self.record_table: QtWidgets.QTableWidget = self.create_table()

        self.backtest_vt_symbol_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()
        self.backtest_vt_symbol_line.setPlaceholderText("默认使用实时配置的产品代码")
        self.backtest_vt_symbol_line.editingFinished.connect(self.resolve_backtest_symbol)

        self.backtest_volume_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()
        self.backtest_volume_line.setValidator(validator)
        self.backtest_volume_line.setPlaceholderText("默认使用实时配置的委托份数")

        end_dt: datetime = datetime.now()
        start_dt: datetime = end_dt - timedelta(days=365)

        self.start_date_edit: QtWidgets.QDateEdit = QtWidgets.QDateEdit(
            QtCore.QDate(start_dt.year, start_dt.month, start_dt.day)
        )
        self.end_date_edit: QtWidgets.QDateEdit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())

        self.interval_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()
        self.interval_combo.addItems([Interval.MINUTE.value, Interval.DAILY.value])

        self.backtest_market_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()
        self.backtest_market_combo.addItem("A股市场", BACKTEST_MARKET_CN)
        self.backtest_market_combo.addItem("美股市场", BACKTEST_MARKET_US)
        self.backtest_market_combo.currentIndexChanged.connect(self.update_backtest_market_ui)

        self.backtest_strategy_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()
        for strategy in BACKTEST_STRATEGIES:
            self.backtest_strategy_combo.addItem(strategy.name, strategy.key)

        pct_validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
        pct_validator.setBottom(0)
        pct_validator.setTop(500)
        self.backtest_adjustment_pct_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit("50")
        self.backtest_adjustment_pct_line.setValidator(pct_validator)
        self.backtest_adjustment_pct_line.setPlaceholderText("用于加投/少投，例如 50")

        amount_validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
        amount_validator.setBottom(0)
        self.backtest_base_amount_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()
        self.backtest_base_amount_line.setValidator(amount_validator)
        self.backtest_base_amount_line.setPlaceholderText("留空则按 委托份数×首日价格 推算")

        period_validator: QtGui.QIntValidator = QtGui.QIntValidator()
        period_validator.setBottom(1)
        period_validator.setTop(365)
        self.backtest_drawdown_bias_cycle_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            str(DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS)
        )
        self.backtest_drawdown_bias_cycle_line.setValidator(period_validator)

        lookback_validator: QtGui.QIntValidator = QtGui.QIntValidator()
        lookback_validator.setBottom(1)
        lookback_validator.setTop(5000)
        self.backtest_drawdown_lookback_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            str(DEFAULT_DRAWDOWN_LOOKBACK_DAYS)
        )
        self.backtest_drawdown_lookback_line.setValidator(lookback_validator)
        self.backtest_drawdown_lookback_line.setPlaceholderText("例如 250")

        self.backtest_bias_lookback_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            str(DEFAULT_BIAS_LOOKBACK_DAYS)
        )
        self.backtest_bias_lookback_line.setValidator(lookback_validator)
        self.backtest_bias_lookback_line.setPlaceholderText("例如 250")

        self.backtest_value_averaging_cycle_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            str(DEFAULT_VALUE_AVERAGING_CYCLE_DAYS)
        )
        self.backtest_value_averaging_cycle_line.setValidator(period_validator)

        self.backtest_target_growth_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_TARGET_GROWTH:g}"
        )
        self.backtest_target_growth_line.setValidator(amount_validator)

        fee_rate_validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
        fee_rate_validator.setBottom(0)
        fee_rate_validator.setTop(100)
        fee_rate_validator.setDecimals(6)
        self.backtest_fee_rate_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_BACKTEST_FEE_RATE_PCT:g}"
        )
        self.backtest_fee_rate_line.setValidator(fee_rate_validator)
        self.backtest_fee_rate_line.setPlaceholderText("双向按成交额计费，例如 0.03")

        self.backtest_min_fee_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_BACKTEST_MIN_FEE:g}"
        )
        self.backtest_min_fee_line.setValidator(amount_validator)
        self.backtest_min_fee_line.setPlaceholderText("单笔最低手续费，例如 5")

        self.us_commission_per_share_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_COMMISSION_PER_SHARE:g}"
        )
        self.us_commission_per_share_line.setValidator(amount_validator)
        self.us_commission_min_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_COMMISSION_MIN:g}"
        )
        self.us_commission_min_line.setValidator(amount_validator)
        self.us_commission_max_rate_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_COMMISSION_MAX_RATE_PCT:g}"
        )
        self.us_commission_max_rate_line.setValidator(fee_rate_validator)
        self.us_platform_fee_per_share_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_PLATFORM_FEE_PER_SHARE:g}"
        )
        self.us_platform_fee_per_share_line.setValidator(amount_validator)
        self.us_platform_fee_min_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_PLATFORM_FEE_MIN:g}"
        )
        self.us_platform_fee_min_line.setValidator(amount_validator)
        self.us_platform_fee_max_rate_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT:g}"
        )
        self.us_platform_fee_max_rate_line.setValidator(fee_rate_validator)

        grid_step_validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
        grid_step_validator.setBottom(0)
        grid_step_validator.setTop(99)
        self.backtest_grid_step_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_GRID_STEP_PCT:g}"
        )
        self.backtest_grid_step_line.setValidator(grid_step_validator)

        self.backtest_grid_volume_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit(
            f"{DEFAULT_GRID_VOLUME:g}"
        )
        self.backtest_grid_volume_line.setValidator(validator)

        self.backtest_marker_checkbox: QtWidgets.QCheckBox = QtWidgets.QCheckBox("显示交易标记(B/S/N)")
        self.backtest_marker_checkbox.setChecked(True)
        self.backtest_marker_checkbox.stateChanged.connect(self.refresh_backtest_trade_markers)

        self.backtest_button: QtWidgets.QPushButton = QtWidgets.QPushButton("运行回测")
        self.backtest_button.clicked.connect(self.run_backtesting)

        self.refresh_backtest_button: QtWidgets.QPushButton = QtWidgets.QPushButton("刷新数据")
        self.refresh_backtest_button.clicked.connect(
            lambda checked=False: self.run_backtesting(refresh=True)
        )

        self.backtest_symbol_status_label: QtWidgets.QLabel = QtWidgets.QLabel("-")
        self.backtest_symbol_status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.backtest_data_status_label: QtWidgets.QLabel = QtWidgets.QLabel("-")
        self.backtest_data_status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.backtest_summary_label: QtWidgets.QLabel = QtWidgets.QLabel("-")
        self.backtest_summary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        product_group: QtWidgets.QGroupBox = QtWidgets.QGroupBox("产品相关信息配置")
        product_form: QtWidgets.QGridLayout = QtWidgets.QGridLayout()
        product_form.addWidget(QtWidgets.QLabel("市场类型"), 0, 0)
        product_form.addWidget(self.backtest_market_combo, 0, 1)
        product_form.addWidget(QtWidgets.QLabel("产品代码"), 0, 2)
        product_form.addWidget(self.backtest_vt_symbol_line, 0, 3)
        product_form.addWidget(QtWidgets.QLabel("K线周期"), 1, 0)
        product_form.addWidget(self.interval_combo, 1, 1)
        product_form.addWidget(self.backtest_symbol_status_label, 2, 0, 1, 4)
        product_group.setLayout(product_form)

        trade_group: QtWidgets.QGroupBox = QtWidgets.QGroupBox("交易相关信息配置")
        trade_form: QtWidgets.QGridLayout = QtWidgets.QGridLayout()
        trade_form.addWidget(QtWidgets.QLabel("开始日期"), 0, 0)
        trade_form.addWidget(self.start_date_edit, 0, 1)
        trade_form.addWidget(QtWidgets.QLabel("结束日期"), 0, 2)
        trade_form.addWidget(self.end_date_edit, 0, 3)
        self.cn_fee_rate_label: QtWidgets.QLabel = QtWidgets.QLabel("A股手续费率%")
        self.cn_min_fee_label: QtWidgets.QLabel = QtWidgets.QLabel("A股最低手续费")
        trade_form.addWidget(self.cn_fee_rate_label, 1, 0)
        trade_form.addWidget(self.backtest_fee_rate_line, 1, 1)
        trade_form.addWidget(self.cn_min_fee_label, 1, 2)
        trade_form.addWidget(self.backtest_min_fee_line, 1, 3)
        self.us_commission_per_share_label: QtWidgets.QLabel = QtWidgets.QLabel("美股佣金/股")
        self.us_commission_min_label: QtWidgets.QLabel = QtWidgets.QLabel("美股佣金最低")
        self.us_commission_max_rate_label: QtWidgets.QLabel = QtWidgets.QLabel("美股佣金封顶%")
        self.us_platform_fee_per_share_label: QtWidgets.QLabel = QtWidgets.QLabel("美股平台费/股")
        self.us_platform_fee_min_label: QtWidgets.QLabel = QtWidgets.QLabel("美股平台费最低")
        self.us_platform_fee_max_rate_label: QtWidgets.QLabel = QtWidgets.QLabel("美股平台费封顶%")
        trade_form.addWidget(self.us_commission_per_share_label, 2, 0)
        trade_form.addWidget(self.us_commission_per_share_line, 2, 1)
        trade_form.addWidget(self.us_commission_min_label, 2, 2)
        trade_form.addWidget(self.us_commission_min_line, 2, 3)
        trade_form.addWidget(self.us_commission_max_rate_label, 3, 0)
        trade_form.addWidget(self.us_commission_max_rate_line, 3, 1)
        trade_form.addWidget(self.us_platform_fee_per_share_label, 3, 2)
        trade_form.addWidget(self.us_platform_fee_per_share_line, 3, 3)
        trade_form.addWidget(self.us_platform_fee_min_label, 4, 0)
        trade_form.addWidget(self.us_platform_fee_min_line, 4, 1)
        trade_form.addWidget(self.us_platform_fee_max_rate_label, 4, 2)
        trade_form.addWidget(self.us_platform_fee_max_rate_line, 4, 3)
        trade_group.setLayout(trade_form)

        strategy_group: QtWidgets.QGroupBox = QtWidgets.QGroupBox("策略相关信息配置")
        strategy_form: QtWidgets.QFormLayout = QtWidgets.QFormLayout()
        strategy_form.addRow("回测策略", self.backtest_strategy_combo)
        self.backtest_strategy_desc_label: QtWidgets.QLabel = QtWidgets.QLabel()
        self.backtest_strategy_desc_label.setWordWrap(True)
        self.backtest_strategy_desc_label.setStyleSheet("color:#90A4AE")
        strategy_form.addRow("策略说明", self.backtest_strategy_desc_label)
        strategy_form.addRow("基础委托份数", self.backtest_volume_line)

        self.backtest_strategy_param_rows: dict[str, tuple[QtWidgets.QLabel, QtWidgets.QWidget]] = {}
        self.add_backtest_strategy_param_row(strategy_form, "adjustment_pct", "调仓比例%", self.backtest_adjustment_pct_line)
        self.add_backtest_strategy_param_row(strategy_form, "base_amount", "定投基础金额", self.backtest_base_amount_line)
        self.add_backtest_strategy_param_row(strategy_form, "drawdown_bias_cycle", "回撤/偏离周期(交易日)", self.backtest_drawdown_bias_cycle_line)
        self.add_backtest_strategy_param_row(strategy_form, "drawdown_lookback", "回撤高点参考日数", self.backtest_drawdown_lookback_line)
        self.add_backtest_strategy_param_row(strategy_form, "bias_lookback", "均线参考日数", self.backtest_bias_lookback_line)
        self.add_backtest_strategy_param_row(strategy_form, "target_growth", "月度市值增长目标", self.backtest_target_growth_line)
        self.add_backtest_strategy_param_row(strategy_form, "value_averaging_cycle", "价值平均周期(交易日)", self.backtest_value_averaging_cycle_line)
        self.add_backtest_strategy_param_row(strategy_form, "grid_step_pct", "网格间距%", self.backtest_grid_step_line)
        self.add_backtest_strategy_param_row(strategy_form, "grid_volume", "网格份数", self.backtest_grid_volume_line)

        backtest_button_layout: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        backtest_button_layout.addStretch()
        backtest_button_layout.addWidget(self.refresh_backtest_button)
        backtest_button_layout.addWidget(self.backtest_button)
        strategy_form.addRow(backtest_button_layout)
        strategy_group.setLayout(strategy_form)

        backtest_config_widget: QtWidgets.QWidget = QtWidgets.QWidget()
        backtest_config_layout: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        backtest_config_layout.addWidget(product_group)
        backtest_config_layout.addWidget(trade_group)
        backtest_config_layout.addWidget(strategy_group)
        backtest_config_layout.addWidget(self.backtest_data_status_label)
        backtest_config_layout.addStretch()
        backtest_config_widget.setLayout(backtest_config_layout)

        self.backtest_strategy_combo.currentIndexChanged.connect(
            self.update_backtest_strategy_parameter_visibility
        )

        self.backtest_table: QtWidgets.QTableWidget = self.create_table()
        self.backtest_strategy_table: QtWidgets.QTableWidget = self.create_backtest_strategy_table()
        self.backtest_trade_dialog: QtWidgets.QDialog = self.create_backtest_popup_dialog(
            "每笔交易信息",
            self.backtest_table,
        )
        self.backtest_strategy_dialog: QtWidgets.QDialog = self.create_backtest_popup_dialog(
            "策略收益对比表格",
            self.backtest_strategy_table,
        )
        self.backtest_chart: ChartWidget = ChartWidget()
        self.backtest_chart.add_plot("candle", hide_x_axis=True)
        self.backtest_chart.add_plot("volume", maximum_height=160)
        self.backtest_chart.add_plot("drawdown", maximum_height=120)
        self.backtest_chart.add_plot("bias", maximum_height=120)
        self.backtest_chart.add_item(BacktestCandleItem, "candle", "candle")
        self.backtest_chart.add_item(VolumeItem, "volume", "volume")
        self.backtest_chart.add_item(cast(type[ChartItem], DrawdownInfoItem), "drawdown_info", "drawdown")
        self.backtest_chart.add_item(cast(type[ChartItem], BiasInfoItem), "bias_info", "bias")
        self.backtest_drawdown_plot: pg.PlotItem = self.backtest_chart.get_plot("drawdown")
        self.backtest_drawdown_plot.setLabel("right", "回撤率", units="%")
        self.backtest_drawdown_plot.hide()
        self.backtest_bias_plot: pg.PlotItem = self.backtest_chart.get_plot("bias")
        self.backtest_bias_plot.setLabel("right", "乖离率", units="%")
        self.backtest_bias_plot.hide()
        self.backtest_chart.add_cursor()
        self.update_backtest_indicator_legend()

        backtest_display_widget: QtWidgets.QWidget = QtWidgets.QWidget()
        backtest_display_layout: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        display_option_layout: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        display_option_layout.addWidget(self.backtest_marker_checkbox)
        trade_info_button: QtWidgets.QPushButton = QtWidgets.QPushButton("每笔交易信息")
        trade_info_button.clicked.connect(self.toggle_backtest_trade_dialog)
        strategy_info_button: QtWidgets.QPushButton = QtWidgets.QPushButton("策略收益对比表格")
        strategy_info_button.clicked.connect(self.toggle_backtest_strategy_dialog)
        display_option_layout.addWidget(trade_info_button)
        display_option_layout.addWidget(strategy_info_button)
        display_option_layout.addStretch()
        backtest_display_layout.addWidget(self.backtest_summary_label)
        backtest_display_layout.addLayout(display_option_layout)
        backtest_display_layout.addWidget(self.backtest_chart, 1)
        backtest_display_widget.setLayout(backtest_display_layout)
        self.backtest_display_widget: QtWidgets.QWidget = backtest_display_widget

        self.personal_product_combo: QtWidgets.QComboBox = QtWidgets.QComboBox()

        import_folder_button: QtWidgets.QPushButton = QtWidgets.QPushButton("导入my_trade_data")
        import_folder_button.clicked.connect(self.import_personal_trade_folder)

        import_file_button: QtWidgets.QPushButton = QtWidgets.QPushButton("选择文件导入")
        import_file_button.clicked.connect(self.import_personal_trade_files)

        self.personal_analyze_button: QtWidgets.QPushButton = QtWidgets.QPushButton("分析产品")
        self.personal_analyze_button.clicked.connect(self.analyze_selected_personal_product)

        self.personal_status_label: QtWidgets.QLabel = QtWidgets.QLabel("-")
        self.personal_status_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.personal_summary_label: QtWidgets.QLabel = QtWidgets.QLabel("-")
        self.personal_summary_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self.personal_summary_label.setWordWrap(True)

        personal_form: QtWidgets.QGridLayout = QtWidgets.QGridLayout()
        personal_form.addWidget(QtWidgets.QLabel("交易产品"), 0, 0)
        personal_form.addWidget(self.personal_product_combo, 0, 1)
        personal_form.addWidget(import_folder_button, 0, 2)
        personal_form.addWidget(import_file_button, 0, 3)
        personal_form.addWidget(self.personal_analyze_button, 1, 3)
        personal_form.addWidget(self.personal_status_label, 2, 0, 1, 4)
        personal_form.addWidget(self.personal_summary_label, 3, 0, 1, 4)

        self.personal_product_table: QtWidgets.QTableWidget = self.create_personal_product_table()
        self.personal_point_table: QtWidgets.QTableWidget = self.create_personal_point_table()
        self.personal_return_table = QtWidgets.QTableWidget(0, 8)
        self.personal_return_table.setHorizontalHeaderLabels([
            "变化排名", "日期", "对比日期", "收盘价", "累计收益率", "变化(百分点)", "累计收益", "当日交易笔数",
        ])
        self.personal_return_table.setEditTriggers(QtWidgets.QTableWidget.EditTrigger.NoEditTriggers)
        self.personal_return_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.personal_return_table.setToolTip(
            "按相邻可用交易日日线的累计简单收益率之差排名，包含追加投入和手续费的影响。"
            "首日不排名；上涨或下跌不足3天时只显示实际数量。R+/R-标注收益率变化日，并不代表当天有买卖。"
        )
        personal_rank_tabs = QtWidgets.QTabWidget()
        personal_rank_tabs.addTab(self.personal_point_table, "买入贡献排名")
        personal_rank_tabs.addTab(self.personal_return_table, "每日收益率变化")

        self.personal_chart: ChartWidget = ChartWidget()
        self.personal_chart.add_plot("candle", hide_x_axis=True)
        self.personal_chart.add_plot("volume", maximum_height=160)
        self.personal_chart.add_item(CandleItem, "candle", "candle")
        self.personal_chart.add_item(VolumeItem, "volume", "volume")
        self.personal_chart.add_cursor()

        personal_table_splitter: QtWidgets.QSplitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        personal_table_splitter.addWidget(self.personal_product_table)
        personal_table_splitter.addWidget(personal_rank_tabs)
        personal_table_splitter.setStretchFactor(0, 2)
        personal_table_splitter.setStretchFactor(1, 3)

        personal_splitter: QtWidgets.QSplitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        personal_splitter.addWidget(self.personal_chart)
        personal_splitter.addWidget(personal_table_splitter)
        personal_splitter.setStretchFactor(0, 3)
        personal_splitter.setStretchFactor(1, 2)

        personal_widget: QtWidgets.QWidget = QtWidgets.QWidget()
        personal_layout: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        personal_layout.addLayout(personal_form)
        personal_layout.addWidget(personal_splitter)
        personal_widget.setLayout(personal_layout)

        self.log_monitor: QtWidgets.QTextEdit = QtWidgets.QTextEdit()
        self.log_monitor.setReadOnly(True)

        self.tabs: QtWidgets.QTabWidget = QtWidgets.QTabWidget()
        self.tabs.addTab(self.record_table, "定投记录")
        self.tabs.addTab(backtest_config_widget, "回测信息配置")
        self.tabs.addTab(backtest_display_widget, "收益回测显示")
        self.tabs.addTab(personal_widget, "个人收益")
        self.tabs.addTab(self.log_monitor, "日志")

        hbox: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        hbox.addWidget(left_widget)
        hbox.addWidget(self.tabs)
        self.setLayout(hbox)
        self.update_backtest_market_ui()
        self.update_backtest_strategy_parameter_visibility()

    def add_backtest_strategy_param_row(
        self,
        form: QtWidgets.QFormLayout,
        key: str,
        label_text: str,
        widget: QtWidgets.QWidget,
    ) -> None:
        """"""
        label: QtWidgets.QLabel = QtWidgets.QLabel(label_text)
        form.addRow(label, widget)
        self.backtest_strategy_param_rows[key] = (label, widget)

    def get_selected_backtest_market_type(self) -> str:
        """"""
        return self.backtest_market_combo.currentData() or BACKTEST_MARKET_CN

    def update_backtest_market_ui(self, *_args: object) -> None:
        """"""
        market_type: str = self.get_selected_backtest_market_type()
        is_us_market: bool = market_type == BACKTEST_MARKET_US

        for widget in (
            self.cn_fee_rate_label,
            self.backtest_fee_rate_line,
            self.cn_min_fee_label,
            self.backtest_min_fee_line,
        ):
            widget.setVisible(not is_us_market)

        for widget in (
            self.us_commission_per_share_label,
            self.us_commission_per_share_line,
            self.us_commission_min_label,
            self.us_commission_min_line,
            self.us_commission_max_rate_label,
            self.us_commission_max_rate_line,
            self.us_platform_fee_per_share_label,
            self.us_platform_fee_per_share_line,
            self.us_platform_fee_min_label,
            self.us_platform_fee_min_line,
            self.us_platform_fee_max_rate_label,
            self.us_platform_fee_max_rate_line,
        ):
            widget.setVisible(is_us_market)

        if is_us_market:
            self.backtest_vt_symbol_line.setPlaceholderText("例如：AAPL 或 AAPL.SMART")
        else:
            self.backtest_vt_symbol_line.setPlaceholderText("默认使用实时配置的产品代码")

        self.resolve_backtest_symbol()

    def update_backtest_strategy_parameter_visibility(self, *_args: object) -> None:
        """"""
        strategy_key: str = self.backtest_strategy_combo.currentData() or DEFAULT_BACKTEST_STRATEGY
        visible_keys: set[str] = set()

        if strategy_key in {
            BACKTEST_STRATEGY_DROP_ADD,
            BACKTEST_STRATEGY_UP_REDUCE,
            BACKTEST_STRATEGY_MA_TEMPERATURE,
        }:
            visible_keys.add("adjustment_pct")
        elif strategy_key == BACKTEST_STRATEGY_DRAWDOWN_DCA:
            visible_keys.update({"base_amount", "drawdown_bias_cycle", "drawdown_lookback"})
        elif strategy_key == BACKTEST_STRATEGY_BIAS_DCA:
            visible_keys.update({"base_amount", "drawdown_bias_cycle", "bias_lookback"})
        elif strategy_key == BACKTEST_STRATEGY_VALUE_AVERAGING:
            visible_keys.update({"target_growth", "value_averaging_cycle"})
        elif strategy_key == BACKTEST_STRATEGY_GRID_DCA:
            visible_keys.update({"grid_step_pct", "grid_volume"})

        for key, (label, widget) in self.backtest_strategy_param_rows.items():
            visible: bool = key in visible_keys
            label.setVisible(visible)
            widget.setVisible(visible)

        description: str = ""
        for strategy in BACKTEST_STRATEGIES:
            if strategy.key == strategy_key:
                description = strategy.description
                break

        self.backtest_strategy_desc_label.setText(description or "当前策略暂无额外说明。")

    def create_backtest_popup_dialog(
        self,
        title: str,
        table: QtWidgets.QTableWidget,
    ) -> QtWidgets.QDialog:
        """"""
        dialog: QtWidgets.QDialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(980, 560)
        layout: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        layout.addWidget(table)
        dialog.setLayout(layout)
        return dialog

    def toggle_backtest_trade_dialog(self) -> None:
        """"""
        self.toggle_backtest_dialog(self.backtest_trade_dialog)

    def toggle_backtest_strategy_dialog(self) -> None:
        """"""
        self.toggle_backtest_dialog(self.backtest_strategy_dialog)

    def toggle_backtest_dialog(self, dialog: QtWidgets.QDialog) -> None:
        """"""
        if dialog.isVisible():
            dialog.hide()
            return

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def update_backtest_indicator_legend(self) -> None:
        """"""
        BacktestCandleItem.drawdown_lookback_days = self.backtest_drawdown_lookback_days
        BacktestCandleItem.bias_lookback_days = self.backtest_bias_lookback_days
        DrawdownInfoItem.lookback_days = self.backtest_drawdown_lookback_days
        BiasInfoItem.lookback_days = self.backtest_bias_lookback_days

    def create_table(self) -> QtWidgets.QTableWidget:
        """"""
        table: QtWidgets.QTableWidget = QtWidgets.QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(
            ["时间", "产品代码", "价格", "份数", "成交额", "手续费", "成交号"]
        )
        table.setEditTriggers(table.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().sectionClicked.connect(self.sort_backtest_strategy_table)
        table.itemSelectionChanged.connect(self.process_backtest_strategy_selection)
        return table

    def create_backtest_strategy_table(self) -> QtWidgets.QTableWidget:
        """"""
        table: QtWidgets.QTableWidget = QtWidgets.QTableWidget()
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels([
            "策略",
            "交易次数",
            "累计份数",
            "累计投入",
            "手续费",
            "平均成本",
            "当前市值",
            "盈亏",
            "收益率",
        ])
        table.setEditTriggers(table.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        return table

    def create_personal_product_table(self) -> QtWidgets.QTableWidget:
        """"""
        table: QtWidgets.QTableWidget = QtWidgets.QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "产品代码",
            "名称",
            "起始时间",
            "交易笔数",
            "买入份数",
            "持有份数",
            "累计买入",
            "卖出回款",
        ])
        table.setEditTriggers(table.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        return table

    def create_personal_point_table(self) -> QtWidgets.QTableWidget:
        """"""
        table: QtWidgets.QTableWidget = QtWidgets.QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "类型",
            "时间",
            "价格",
            "份数",
            "投入",
            "当前价",
            "该笔盈亏",
            "当前收益贡献(百分点)",
        ])
        table.setEditTriggers(table.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        return table

    def register_event(self) -> None:
        """"""
        self.signal_log.connect(self.process_log_event)
        self.signal_update.connect(self.process_update_event)
        self.signal_backtesting_finished.connect(self.process_backtesting_finished)
        self.signal_fund_name_finished.connect(self.process_fund_name_finished)
        self.signal_personal_analysis_finished.connect(self.process_personal_analysis_finished)

        self.event_engine.register(EVENT_DAILY_INVESTMENT_LOG, self.signal_log.emit)
        self.event_engine.register(EVENT_DAILY_INVESTMENT_UPDATE, self.signal_update.emit)

    def process_log_event(self, event: Event) -> None:
        """"""
        self.write_log(event.data)

    def process_update_event(self, event: Event) -> None:
        """"""
        self.refresh_all()

    def write_log(self, msg: str) -> None:
        """"""
        timestamp: str = datetime.now().strftime("%H:%M:%S")
        self.log_monitor.append(f"{timestamp}\t{msg}")

    def load_setting(self) -> None:
        """"""
        setting = self.engine.setting
        self.vt_symbol_line.setText(setting.vt_symbol)
        self.volume_line.setText(str(setting.volume) if setting.volume else "")

        if setting.gateway_name:
            index: int = self.gateway_combo.findText(setting.gateway_name)
            if index >= 0:
                self.gateway_combo.setCurrentIndex(index)

        self.backtest_vt_symbol_line.setText(setting.vt_symbol)
        self.backtest_volume_line.setText(str(setting.volume) if setting.volume else "")
        if setting.vt_symbol:
            self.update_backtest_symbol_status(setting.vt_symbol)

    def save_setting(self) -> bool:
        """"""
        vt_symbol: str = self.vt_symbol_line.text().strip()
        volume_text: str = self.volume_line.text().strip()
        gateway_name: str = self.gateway_combo.currentText()

        if not vt_symbol:
            QtWidgets.QMessageBox.critical(self, "配置失败", "请输入产品代码")
            return False

        if not volume_text:
            QtWidgets.QMessageBox.critical(self, "配置失败", "请输入委托份数")
            return False

        try:
            resolved_vt_symbol: str = self.engine.update_setting(
                vt_symbol,
                float(volume_text),
                gateway_name,
                self.engine.setting.enabled,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "配置失败", str(exc))
            return False

        self.vt_symbol_line.setText(resolved_vt_symbol)
        self.backtest_vt_symbol_line.setText(resolved_vt_symbol)
        normalized_volume: str = f"{self.engine.setting.volume:g}"
        self.volume_line.setText(normalized_volume)
        self.backtest_volume_line.setText(normalized_volume)
        self.update_backtest_symbol_status(resolved_vt_symbol)
        return True

    def start(self) -> None:
        """"""
        if not self.save_setting():
            return

        self.engine.start()

    def stop(self) -> None:
        """"""
        self.engine.stop()

    def clear_records(self) -> None:
        """"""
        result = QtWidgets.QMessageBox.question(
            self,
            "清空记录",
            "确认清空所有定投成交记录？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )

        if result == QtWidgets.QMessageBox.StandardButton.Yes:
            self.engine.clear_records()

    def refresh_all(self) -> None:
        """"""
        setting = self.engine.setting
        self.status_label.setText("运行中" if setting.enabled else "已停止")
        self.refresh_summary(self.engine.get_summary())
        self.refresh_table(self.record_table, self.engine.get_records())
        self.refresh_personal_products()

    def refresh_summary(self, summary: InvestmentSummary) -> None:
        """"""
        values: dict[str, str] = {
            "count": str(summary.count),
            "total_volume": f"{summary.total_volume:g}",
            "total_cost": f"{summary.total_cost:,.2f}",
            "total_fee": f"{summary.total_fee:,.2f}",
            "average_cost": f"{summary.average_cost:,.4f}",
            "latest_price": f"{summary.latest_price:,.4f}",
            "market_value": f"{summary.market_value:,.2f}",
            "pnl": f"{summary.pnl:,.2f}",
            "return_pct": f"{summary.return_pct:,.2f}%",
        }

        for key, value in values.items():
            self.summary_labels[key].setText(value)

    def refresh_table(
        self,
        table: QtWidgets.QTableWidget,
        records: list[InvestmentRecord]
    ) -> None:
        """"""
        table.setRowCount(0)

        for record in reversed(records):
            row: int = table.rowCount()
            table.insertRow(row)

            values: list[str] = [
                record.datetime,
                record.vt_symbol,
                f"{record.price:,.4f}",
                f"{record.volume:g}",
                f"{record.turnover:,.2f}",
                f"{record.fee:,.2f}",
                record.tradeid,
            ]

            for column, value in enumerate(values):
                item: QtWidgets.QTableWidgetItem = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)

    def refresh_backtest_strategy_table(
        self,
        selected_strategy_key: str,
        results: list[BacktestStrategyResult],
    ) -> None:
        """"""
        self.selected_backtest_strategy_key = selected_strategy_key
        self.backtest_strategy_results = list(results)
        self.backtest_strategy_table.setRowCount(0)
        self.backtest_strategy_table.blockSignals(True)

        for result in self.get_sorted_backtest_strategy_results():
            row: int = self.backtest_strategy_table.rowCount()
            self.backtest_strategy_table.insertRow(row)

            summary: InvestmentSummary = result.summary
            values: list[tuple[str, object]] = [
                (result.strategy_name, result.strategy_name),
                (str(summary.count), summary.count),
                (f"{summary.total_volume:g}", summary.total_volume),
                (f"{summary.total_cost:,.2f}", summary.total_cost),
                (f"{summary.total_fee:,.2f}", summary.total_fee),
                (f"{summary.average_cost:,.4f}", summary.average_cost),
                (f"{summary.market_value:,.2f}", summary.market_value),
                (f"{summary.pnl:,.2f}", summary.pnl),
                (f"{summary.return_pct:,.2f}%", summary.return_pct),
            ]

            for column, (text, sort_value) in enumerate(values):
                item: QtWidgets.QTableWidgetItem = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, sort_value)
                item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, result.strategy_key)
                if result.strategy_key == selected_strategy_key:
                    item.setBackground(QtGui.QColor("#2D4F67"))
                self.backtest_strategy_table.setItem(row, column, item)

            if result.strategy_key == selected_strategy_key:
                self.backtest_strategy_table.selectRow(row)

        self.backtest_strategy_table.blockSignals(False)

    def get_sorted_backtest_strategy_results(self) -> list[BacktestStrategyResult]:
        """"""
        return sorted(
            self.backtest_strategy_results,
            key=lambda result: self.get_backtest_strategy_sort_value(
                result,
                self.backtest_strategy_sort_column,
            ),
            reverse=self.backtest_strategy_sort_descending,
        )

    def get_backtest_strategy_sort_value(
        self,
        result: BacktestStrategyResult,
        column: int,
    ) -> str | float:
        """"""
        summary: InvestmentSummary = result.summary
        values: list[str | float] = [
            result.strategy_name,
            summary.count,
            summary.total_volume,
            summary.total_cost,
            summary.total_fee,
            summary.average_cost,
            summary.market_value,
            summary.pnl,
            summary.return_pct,
        ]
        return values[column]

    def sort_backtest_strategy_table(self, column: int) -> None:
        """"""
        if column == self.backtest_strategy_sort_column and self.backtest_strategy_sort_user_clicked:
            self.backtest_strategy_sort_descending = not self.backtest_strategy_sort_descending
        else:
            self.backtest_strategy_sort_column = column
            self.backtest_strategy_sort_descending = column != 0
            self.backtest_strategy_sort_user_clicked = True

        self.refresh_backtest_strategy_table(
            self.selected_backtest_strategy_key,
            self.backtest_strategy_results,
        )

    def process_backtest_strategy_selection(self) -> None:
        """"""
        items: list[QtWidgets.QTableWidgetItem] = self.backtest_strategy_table.selectedItems()
        if not items:
            return

        strategy_key: str = items[0].data(QtCore.Qt.ItemDataRole.UserRole + 1)
        result: BacktestStrategyResult | None = self.find_backtest_strategy_result(strategy_key)
        if not result:
            return

        self.apply_backtest_strategy_result(result)
        self.update_backtest_strategy_row_highlights()

    def find_backtest_strategy_result(self, strategy_key: str) -> BacktestStrategyResult | None:
        """"""
        for result in self.backtest_strategy_results:
            if result.strategy_key == strategy_key:
                return result

        return None

    def apply_backtest_strategy_result(self, result: BacktestStrategyResult) -> None:
        """"""
        self.selected_backtest_strategy_key = result.strategy_key
        self.backtest_records = result.records
        self.refresh_table(self.backtest_table, result.records)
        self.backtest_summary_label.setText(
            f"策略：{result.strategy_name}，回测次数：{result.summary.count}，累计投入：{result.summary.total_cost:,.2f}，当前市值：{result.summary.market_value:,.2f}，盈亏：{result.summary.pnl:,.2f}，收益率：{result.summary.return_pct:,.2f}%"
        )
        self.refresh_backtest_trade_markers()
        self.refresh_backtest_drawdown_line()
        self.refresh_backtest_bias_line()

    def update_backtest_strategy_row_highlights(self) -> None:
        """"""
        selected_color: QtGui.QColor = QtGui.QColor("#2D4F67")
        empty_color: QtGui.QColor = QtGui.QColor()

        for row in range(self.backtest_strategy_table.rowCount()):
            key_item: QtWidgets.QTableWidgetItem | None = self.backtest_strategy_table.item(row, 0)
            strategy_key: str = key_item.data(QtCore.Qt.ItemDataRole.UserRole + 1) if key_item else ""
            for column in range(self.backtest_strategy_table.columnCount()):
                item: QtWidgets.QTableWidgetItem | None = self.backtest_strategy_table.item(row, column)
                if not item:
                    continue
                item.setBackground(selected_color if strategy_key == self.selected_backtest_strategy_key else empty_color)

    def refresh_personal_products(self) -> None:
        """"""
        summaries: list[PersonalProductSummary] = self.engine.get_personal_product_summaries()
        current_vt_symbol: str = self.personal_product_combo.currentData() or ""

        self.personal_product_combo.blockSignals(True)
        self.personal_product_combo.clear()
        for summary in summaries:
            label: str = f"{summary.vt_symbol} {summary.name}".strip()
            self.personal_product_combo.addItem(label, summary.vt_symbol)
        self.personal_product_combo.blockSignals(False)

        if current_vt_symbol:
            index: int = self.personal_product_combo.findData(current_vt_symbol)
            if index >= 0:
                self.personal_product_combo.setCurrentIndex(index)

        self.personal_product_table.setRowCount(0)
        for summary in summaries:
            row: int = self.personal_product_table.rowCount()
            self.personal_product_table.insertRow(row)
            values: list[str] = [
                summary.vt_symbol,
                summary.name,
                summary.first_datetime,
                str(summary.trade_count),
                f"{summary.buy_volume:g}",
                f"{summary.position_volume:g}",
                f"{summary.buy_cash:,.2f}",
                f"{summary.sell_cash:,.2f}",
            ]
            for column, value in enumerate(values):
                item: QtWidgets.QTableWidgetItem = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.personal_product_table.setItem(row, column, item)

    def import_personal_trade_folder(self) -> None:
        """"""
        folder_path: Path = Path.cwd().joinpath("my_trade_data")
        try:
            results: list[PersonalTradeImportResult] = self.engine.import_personal_trades_from_folder(folder_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导入失败", str(exc))
            self.write_log(f"个人流水导入失败：{exc}")
            return

        self.process_personal_import_results(results)

    def import_personal_trade_files(self) -> None:
        """"""
        default_path: str = str(Path.cwd().joinpath("my_trade_data"))
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "选择交易流水文件",
            default_path,
            "Trade Files (*.xls *.xlsx *.csv *.txt)",
        )
        if not filenames:
            return

        results: list[PersonalTradeImportResult] = []
        for filename in filenames:
            try:
                results.append(self.engine.import_personal_trades_from_file(filename))
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "导入失败", f"{filename}\n{exc}")
                self.write_log(f"个人流水导入失败：{filename}，{exc}")
                return

        self.process_personal_import_results(results)

    def process_personal_import_results(self, results: list[PersonalTradeImportResult]) -> None:
        """"""
        parsed_count: int = sum(result.parsed_count for result in results)
        added_count: int = sum(result.added_count for result in results)
        updated_count: int = sum(result.updated_count for result in results)
        removed_count: int = sum(result.removed_count for result in results)
        skipped_count: int = sum(result.skipped_count for result in results)
        self.personal_status_label.setStyleSheet("color:#4CAF50")
        self.personal_status_label.setText(
            f"导入完成：解析{parsed_count}条，新增{added_count}条，"
            f"更新{updated_count}条，移除{removed_count}条，跳过重复{skipped_count}条"
        )
        self.refresh_personal_products()

        if self.personal_product_combo.count():
            self.analyze_selected_personal_product()

    def analyze_selected_personal_product(self, refresh: bool = False) -> None:
        """"""
        vt_symbol: str = self.personal_product_combo.currentData() or ""
        if not vt_symbol:
            QtWidgets.QMessageBox.information(self, "个人收益", "请先导入交易流水", QtWidgets.QMessageBox.StandardButton.Ok)
            return

        if self.personal_analysis_thread and self.personal_analysis_thread.is_alive():
            return

        self.personal_analyze_button.setEnabled(False)
        self.personal_status_label.setStyleSheet("color:#4FC3F7")
        self.personal_status_label.setText(f"正在分析个人定投收益：{vt_symbol} ...")
        self.personal_analysis_thread = Thread(
            target=self.personal_analysis_worker,
            args=(vt_symbol, refresh),
            daemon=True,
        )
        self.personal_analysis_thread.start()

    def personal_analysis_worker(self, vt_symbol: str, refresh: bool) -> None:
        """"""
        try:
            analysis: PersonalInvestmentAnalysis = self.engine.analyze_personal_investment(vt_symbol, refresh)
        except Exception as exc:
            self.signal_personal_analysis_finished.emit(None, str(exc))
            return

        self.signal_personal_analysis_finished.emit(analysis, "")

    def process_personal_analysis_finished(self, result: object, error: str) -> None:
        """"""
        self.personal_analysis_thread = None
        self.personal_analyze_button.setEnabled(True)

        if error:
            self.personal_status_label.setStyleSheet("color:#FF6B6B")
            self.personal_status_label.setText(f"分析失败：{error}")
            self.write_log(f"个人收益分析失败：{error}")
            return

        analysis: PersonalInvestmentAnalysis = cast(PersonalInvestmentAnalysis, result)
        self.refresh_personal_analysis(analysis)

    def refresh_personal_analysis(self, analysis: PersonalInvestmentAnalysis) -> None:
        """"""
        self.personal_status_label.setStyleSheet("color:#4CAF50")
        self.personal_status_label.setText(f"分析完成：{analysis.vt_symbol} {analysis.name}")
        self.personal_summary_label.setText(
            f"起始：{analysis.start_datetime}，交易{analysis.trade_count}笔，累计买入：{analysis.buy_cash:,.2f}，持有份数：{analysis.position_volume:g}，"
            f"当前市值：{analysis.market_value:,.2f}，累计收益：{analysis.pnl:,.2f}，收益率：{analysis.return_pct:,.2f}%"
        )
        self.refresh_personal_point_table(analysis)
        self.refresh_personal_return_table(analysis)
        self.refresh_personal_chart(analysis)

    def refresh_personal_return_table(self, analysis: PersonalInvestmentAnalysis) -> None:
        """Show actual positive/negative return changes independently of buy rankings."""
        self.personal_return_table.setRowCount(0)
        rows = (
            [("下降", point) for point in analysis.worst_return_days]
            + [("上升", point) for point in analysis.best_return_days]
        )
        for rank, point in rows:
            row = self.personal_return_table.rowCount()
            self.personal_return_table.insertRow(row)
            values = [
                rank, point.date, point.previous_date or "—", f"{point.close_price:.4f}",
                f"{point.return_pct:.2f}%", f"{point.change_pct_points:+.2f}",
                f"{point.pnl:,.2f}", str(point.trade_count),
            ]
            for column, value in enumerate(values):
                self.personal_return_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        if not analysis.daily_returns:
            self.personal_status_label.setText(
                self.personal_status_label.text() + "；缺少有效日线，无法计算每日收益率变化"
            )

    def refresh_personal_point_table(self, analysis: PersonalInvestmentAnalysis) -> None:
        """"""
        self.personal_point_table.setRowCount(0)
        rows: list[tuple[str, PersonalInvestmentPoint]] = (
            [("贡献最低", point) for point in analysis.worst_points]
            + [("贡献最高", point) for point in analysis.best_points]
        )
        for row_type, point in rows:
            row: int = self.personal_point_table.rowCount()
            self.personal_point_table.insertRow(row)
            values: list[str] = [
                row_type,
                point.datetime,
                f"{point.price:,.4f}",
                f"{point.volume:g}",
                f"{point.turnover:,.2f}",
                f"{point.latest_price:,.4f}",
                f"{point.pnl:,.2f}",
                f"{point.contribution_pct:,.2f}%",
            ]
            for column, value in enumerate(values):
                item: QtWidgets.QTableWidgetItem = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.personal_point_table.setItem(row, column, item)

    def refresh_personal_chart(self, analysis: PersonalInvestmentAnalysis) -> None:
        """"""
        candle_plot: pg.PlotItem = self.personal_chart.get_plot("candle")
        for item in self.personal_marker_items:
            candle_plot.removeItem(item)
        self.personal_marker_items.clear()
        self.personal_chart.clear_all()

        if not analysis.bars:
            return

        self.personal_chart.update_history(analysis.bars)
        date_ix_map: dict[date, int] = {
            bar.datetime.date(): ix
            for ix, bar in enumerate(analysis.bars)
        }
        bar_by_date: dict[date, BarData] = {
            bar.datetime.date(): bar
            for bar in analysis.bars
        }

        scatter_data: list[dict] = []
        point_rows: list[tuple[str, PersonalInvestmentPoint]] = (
            [("差", point) for point in analysis.worst_points]
            + [("优", point) for point in analysis.best_points]
        )
        for label, point in point_rows:
            point_date: date = datetime.fromisoformat(point.datetime).date()
            ix: int | None = date_ix_map.get(point_date)
            bar: BarData | None = bar_by_date.get(point_date)
            if ix is None or bar is None:
                continue

            if label == "差":
                color: str = "#FF5252"
                symbol: str = "t"
                y_value: float = bar.high_price
                text_anchor: tuple[float, float] = (0.5, 1.2)
            else:
                color = "#FFD54F"
                symbol = "t1"
                y_value = bar.low_price
                text_anchor = (0.5, -0.2)

            scatter_data.append({
                "pos": (ix, y_value),
                "size": 14,
                "pen": pg.mkPen(color),
                "brush": pg.mkBrush(color),
                "symbol": symbol,
            })
            text_item: pg.TextItem = pg.TextItem(label, color=color, anchor=text_anchor)
            text_item.setPos(ix, y_value)
            self.personal_marker_items.append(text_item)
            candle_plot.addItem(text_item)

        if scatter_data:
            scatter_item: pg.ScatterPlotItem = pg.ScatterPlotItem(scatter_data)
            self.personal_marker_items.append(scatter_item)
            candle_plot.addItem(scatter_item)

        for label, points, color in [
            ("R-", analysis.worst_return_days, "#FF80AB"),
            ("R+", analysis.best_return_days, "#40C4FF"),
        ]:
            for daily_point in points:
                ix = date_ix_map.get(date.fromisoformat(daily_point.date))
                if ix is None:
                    continue
                marker = pg.TextItem(label, color=color, anchor=(0.5, 0.5))
                marker.setPos(ix, daily_point.close_price)
                marker.setToolTip(
                    f"{daily_point.previous_date} → {daily_point.date}：收益率变化 {daily_point.change_pct_points:+.2f} 个百分点"
                )
                self.personal_marker_items.append(marker)
                candle_plot.addItem(marker)

    def set_backtest_symbol_status(
        self,
        vt_symbol: str,
        fund_name: str | None = None,
        querying: bool = False,
    ) -> None:
        """"""
        description: str = self.engine.describe_vt_symbol(vt_symbol, fund_name)
        query_text: str = "产品名称" if self.get_selected_backtest_market_type() == BACKTEST_MARKET_US else "基金名称"
        suffix: str = f"，正在查询{query_text}..." if querying else ""
        self.backtest_symbol_status_label.setStyleSheet("color:#4FC3F7")
        self.backtest_symbol_status_label.setText(f"已识别：{description}{suffix}")

    def update_backtest_symbol_status(self, vt_symbol: str, query_name: bool = True) -> None:
        """"""
        fund_name: str = self.engine.get_cached_fund_name(vt_symbol)
        self.set_backtest_symbol_status(vt_symbol, fund_name or None, query_name and not fund_name)

        if query_name and not fund_name:
            self.start_fund_name_query(vt_symbol)

    def start_fund_name_query(self, vt_symbol: str) -> None:
        """"""
        if (
            self.pending_fund_name_vt_symbol == vt_symbol
            and self.fund_name_thread
            and self.fund_name_thread.is_alive()
        ):
            return

        self.pending_fund_name_vt_symbol = vt_symbol
        self.fund_name_thread = Thread(
            target=self.query_fund_name_worker,
            args=(vt_symbol, self.get_selected_backtest_market_type()),
            daemon=True,
        )
        self.fund_name_thread.start()

    def query_fund_name_worker(self, vt_symbol: str, market_type: str) -> None:
        """"""
        try:
            fund_name: str = self.engine.query_backtest_product_name(vt_symbol, market_type)
        except Exception as exc:
            self.signal_fund_name_finished.emit(vt_symbol, "", str(exc))
            return

        self.signal_fund_name_finished.emit(vt_symbol, fund_name, "")

    def process_fund_name_finished(self, vt_symbol: str, fund_name: str, error: str) -> None:
        """"""
        if self.pending_fund_name_vt_symbol == vt_symbol:
            self.fund_name_thread = None

        if self.backtest_vt_symbol_line.text().strip() != vt_symbol:
            return

        if error:
            self.write_log(f"产品名称查询失败：{vt_symbol}，{error}")
            self.set_backtest_symbol_status(vt_symbol)
            return

        if fund_name:
            self.set_backtest_symbol_status(vt_symbol, fund_name)
        else:
            self.set_backtest_symbol_status(vt_symbol)

    def resolve_backtest_symbol(self, show_message: bool = False) -> str:
        """"""
        if self.get_selected_backtest_market_type() == BACKTEST_MARKET_US:
            vt_symbol: str = self.backtest_vt_symbol_line.text().strip()
        else:
            vt_symbol = self.backtest_vt_symbol_line.text().strip() or self.vt_symbol_line.text().strip()
        if not vt_symbol:
            self.backtest_symbol_status_label.setStyleSheet("")
            self.backtest_symbol_status_label.setText("-")
            return ""

        try:
            resolved_vt_symbol: str = self.engine.resolve_backtest_vt_symbol(
                vt_symbol,
                self.get_selected_backtest_market_type(),
            )
        except Exception as exc:
            self.backtest_symbol_status_label.setStyleSheet("color:#FF6B6B")
            self.backtest_symbol_status_label.setText(f"无法识别代码：{exc}")
            if show_message:
                QtWidgets.QMessageBox.critical(self, "回测失败", str(exc))
            return ""

        self.backtest_vt_symbol_line.setText(resolved_vt_symbol)
        self.update_backtest_symbol_status(resolved_vt_symbol)
        return resolved_vt_symbol

    def run_backtesting(self, refresh: bool = False) -> None:
        """"""
        volume_text: str = self.backtest_volume_line.text().strip() or self.volume_line.text().strip()

        if self.backtesting_thread and self.backtesting_thread.is_alive():
            return

        market_type: str = self.get_selected_backtest_market_type()
        if (
            not self.backtest_vt_symbol_line.text().strip()
            and (market_type == BACKTEST_MARKET_US or not self.vt_symbol_line.text().strip())
        ):
            QtWidgets.QMessageBox.critical(self, "回测失败", "请输入产品代码")
            return

        if not volume_text:
            QtWidgets.QMessageBox.critical(self, "回测失败", "请输入委托份数")
            return

        resolved_vt_symbol: str = self.resolve_backtest_symbol(show_message=True)
        if not resolved_vt_symbol:
            return

        start: datetime = cast(datetime, self.start_date_edit.dateTime().toPython())
        end: datetime = cast(datetime, self.end_date_edit.dateTime().toPython())
        end = end.replace(hour=23, minute=59, second=59)
        interval: Interval = Interval(self.interval_combo.currentText())
        volume: float = float(volume_text)
        strategy_key: str = self.backtest_strategy_combo.currentData() or DEFAULT_BACKTEST_STRATEGY
        adjustment_pct_text: str = self.backtest_adjustment_pct_line.text().strip()
        adjustment_pct: float = float(adjustment_pct_text) if adjustment_pct_text else 0
        base_amount_text: str = self.backtest_base_amount_line.text().strip()
        base_amount: float = float(base_amount_text) if base_amount_text else 0
        drawdown_bias_cycle_text: str = self.backtest_drawdown_bias_cycle_line.text().strip()
        drawdown_bias_cycle_days: int = (
            int(drawdown_bias_cycle_text)
            if drawdown_bias_cycle_text
            else DEFAULT_DRAWDOWN_BIAS_CYCLE_DAYS
        )
        drawdown_lookback_text: str = self.backtest_drawdown_lookback_line.text().strip()
        drawdown_lookback_days: int = (
            int(drawdown_lookback_text)
            if drawdown_lookback_text
            else DEFAULT_DRAWDOWN_LOOKBACK_DAYS
        )
        bias_lookback_text: str = self.backtest_bias_lookback_line.text().strip()
        bias_lookback_days: int = (
            int(bias_lookback_text)
            if bias_lookback_text
            else DEFAULT_BIAS_LOOKBACK_DAYS
        )
        value_averaging_cycle_text: str = self.backtest_value_averaging_cycle_line.text().strip()
        value_averaging_cycle_days: int = (
            int(value_averaging_cycle_text)
            if value_averaging_cycle_text
            else DEFAULT_VALUE_AVERAGING_CYCLE_DAYS
        )
        target_growth_text: str = self.backtest_target_growth_line.text().strip()
        target_growth: float = (
            float(target_growth_text)
            if target_growth_text
            else DEFAULT_TARGET_GROWTH
        )
        fee_rate_text: str = self.backtest_fee_rate_line.text().strip()
        backtest_fee_rate_pct: float = (
            float(fee_rate_text)
            if fee_rate_text
            else DEFAULT_BACKTEST_FEE_RATE_PCT
        )
        min_fee_text: str = self.backtest_min_fee_line.text().strip()
        backtest_min_fee: float = (
            float(min_fee_text)
            if min_fee_text
            else DEFAULT_BACKTEST_MIN_FEE
        )
        us_commission_per_share_text: str = self.us_commission_per_share_line.text().strip()
        us_commission_per_share: float = (
            float(us_commission_per_share_text)
            if us_commission_per_share_text
            else DEFAULT_US_COMMISSION_PER_SHARE
        )
        us_commission_min_text: str = self.us_commission_min_line.text().strip()
        us_commission_min: float = (
            float(us_commission_min_text)
            if us_commission_min_text
            else DEFAULT_US_COMMISSION_MIN
        )
        us_commission_max_rate_text: str = self.us_commission_max_rate_line.text().strip()
        us_commission_max_rate_pct: float = (
            float(us_commission_max_rate_text)
            if us_commission_max_rate_text
            else DEFAULT_US_COMMISSION_MAX_RATE_PCT
        )
        us_platform_fee_per_share_text: str = self.us_platform_fee_per_share_line.text().strip()
        us_platform_fee_per_share: float = (
            float(us_platform_fee_per_share_text)
            if us_platform_fee_per_share_text
            else DEFAULT_US_PLATFORM_FEE_PER_SHARE
        )
        us_platform_fee_min_text: str = self.us_platform_fee_min_line.text().strip()
        us_platform_fee_min: float = (
            float(us_platform_fee_min_text)
            if us_platform_fee_min_text
            else DEFAULT_US_PLATFORM_FEE_MIN
        )
        us_platform_fee_max_rate_text: str = self.us_platform_fee_max_rate_line.text().strip()
        us_platform_fee_max_rate_pct: float = (
            float(us_platform_fee_max_rate_text)
            if us_platform_fee_max_rate_text
            else DEFAULT_US_PLATFORM_FEE_MAX_RATE_PCT
        )
        grid_step_text: str = self.backtest_grid_step_line.text().strip()
        grid_step_pct: float = float(grid_step_text) if grid_step_text else DEFAULT_GRID_STEP_PCT
        grid_volume_text: str = self.backtest_grid_volume_line.text().strip()
        grid_volume: float = float(grid_volume_text) if grid_volume_text else DEFAULT_GRID_VOLUME
        self.backtest_drawdown_lookback_days = max(1, drawdown_lookback_days)
        self.backtest_bias_lookback_days = max(1, bias_lookback_days)
        self.update_backtest_indicator_legend()

        self.set_backtesting_running(True)
        action: str = "正在刷新历史行情" if refresh else "正在加载历史行情"
        self.backtest_data_status_label.setStyleSheet("color:#4FC3F7")
        self.backtest_data_status_label.setText(f"{action}：{resolved_vt_symbol} ...")
        self.write_log(f"{action}：{resolved_vt_symbol}")

        self.backtesting_thread = Thread(
            target=self.run_backtesting_worker,
            args=(
                resolved_vt_symbol,
                volume,
                start,
                end,
                interval,
                refresh,
                market_type,
                strategy_key,
                adjustment_pct,
                base_amount,
                drawdown_bias_cycle_days,
                drawdown_lookback_days,
                bias_lookback_days,
                value_averaging_cycle_days,
                target_growth,
                backtest_fee_rate_pct,
                backtest_min_fee,
                us_commission_per_share,
                us_commission_min,
                us_commission_max_rate_pct,
                us_platform_fee_per_share,
                us_platform_fee_min,
                us_platform_fee_max_rate_pct,
                grid_step_pct,
                grid_volume,
            ),
            daemon=True,
        )
        self.backtesting_thread.start()

    def run_backtesting_worker(
        self,
        vt_symbol: str,
        volume: float,
        start: datetime,
        end: datetime,
        interval: Interval,
        refresh: bool,
        market_type: str,
        strategy_key: str,
        adjustment_pct: float,
        base_amount: float,
        drawdown_bias_cycle_days: int,
        drawdown_lookback_days: int,
        bias_lookback_days: int,
        value_averaging_cycle_days: int,
        target_growth: float,
        backtest_fee_rate_pct: float,
        backtest_min_fee: float,
        us_commission_per_share: float,
        us_commission_min: float,
        us_commission_max_rate_pct: float,
        us_platform_fee_per_share: float,
        us_platform_fee_min: float,
        us_platform_fee_max_rate_pct: float,
        grid_step_pct: float,
        grid_volume: float,
    ) -> None:
        """"""
        try:
            result: BacktestResult = self.engine.run_resolved_backtesting(
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
                drawdown_bias_cycle_days=drawdown_bias_cycle_days,
                drawdown_lookback_days=drawdown_lookback_days,
                bias_lookback_days=bias_lookback_days,
                value_averaging_cycle_days=value_averaging_cycle_days,
                grid_step_pct=grid_step_pct,
                grid_volume=grid_volume,
                target_growth=target_growth,
                backtest_fee_rate_pct=backtest_fee_rate_pct,
                backtest_min_fee=backtest_min_fee,
                us_commission_per_share=us_commission_per_share,
                us_commission_min=us_commission_min,
                us_commission_max_rate_pct=us_commission_max_rate_pct,
                us_platform_fee_per_share=us_platform_fee_per_share,
                us_platform_fee_min=us_platform_fee_min,
                us_platform_fee_max_rate_pct=us_platform_fee_max_rate_pct,
            )
        except Exception as exc:
            self.signal_backtesting_finished.emit(None, str(exc))
            return

        self.signal_backtesting_finished.emit(result, "")

    def process_backtesting_finished(self, result: object, error: str) -> None:
        """"""
        self.backtesting_thread = None
        self.set_backtesting_running(False)

        if error:
            self.backtest_data_status_label.setStyleSheet("color:#FF6B6B")
            self.backtest_data_status_label.setText(f"行情加载失败：{error}")
            QtWidgets.QMessageBox.critical(self, "回测失败", error)
            self.write_log(f"回测失败：{error}")
            return

        backtest_result: BacktestResult = cast(BacktestResult, result)
        self.backtest_bars = backtest_result.bars
        self.backtest_strategy_results = backtest_result.strategy_results or []
        self.selected_backtest_strategy_key = backtest_result.strategy_key
        self.backtest_records = backtest_result.records
        self.backtest_drawdown_lookback_days = backtest_result.drawdown_lookback_days
        self.backtest_bias_lookback_days = backtest_result.bias_lookback_days
        self.update_backtest_indicator_legend()
        self.refresh_table(self.backtest_table, backtest_result.records)
        self.refresh_backtest_strategy_table(
            backtest_result.strategy_key,
            self.backtest_strategy_results,
        )
        self.refresh_backtest_chart(backtest_result.bars)
        self.backtest_vt_symbol_line.setText(backtest_result.vt_symbol)
        self.update_backtest_symbol_status(backtest_result.vt_symbol)

        source_text: str = "缓存行情" if backtest_result.from_cache else "新加载行情"
        if backtest_result.bars:
            first_dt: str = backtest_result.bars[0].datetime.strftime("%Y-%m-%d")
            last_dt: str = backtest_result.bars[-1].datetime.strftime("%Y-%m-%d")
            self.backtest_data_status_label.setStyleSheet("color:#4CAF50")
            self.backtest_data_status_label.setText(
                f"已使用{source_text}：{first_dt} 至 {last_dt}，共 {len(backtest_result.bars)} 根K线"
            )
        else:
            self.backtest_data_status_label.setStyleSheet("color:#FFB74D")
            self.backtest_data_status_label.setText(
                f"代码格式有效，但未获取到历史行情（{source_text}）"
            )

        self.backtest_summary_label.setText(
            f"策略：{backtest_result.strategy_name}，回测次数：{backtest_result.summary.count}，累计投入：{backtest_result.summary.total_cost:,.2f}，手续费：{backtest_result.summary.total_fee:,.2f}，"
            f"当前市值：{backtest_result.summary.market_value:,.2f}，盈亏：{backtest_result.summary.pnl:,.2f}，收益率：{backtest_result.summary.return_pct:,.2f}%"
        )

        self.write_log(
            f"回测完成：{backtest_result.vt_symbol} {backtest_result.strategy_name}，"
            f"记录 {backtest_result.summary.count} 笔"
        )
        self.tabs.setCurrentWidget(self.backtest_display_widget)

    def set_backtesting_running(self, running: bool) -> None:
        """"""
        self.backtest_button.setEnabled(not running)
        self.refresh_backtest_button.setEnabled(not running)
        self.backtest_market_combo.setEnabled(not running)
        self.backtest_strategy_combo.setEnabled(not running)
        self.backtest_adjustment_pct_line.setEnabled(not running)
        self.backtest_base_amount_line.setEnabled(not running)
        self.backtest_drawdown_bias_cycle_line.setEnabled(not running)
        self.backtest_drawdown_lookback_line.setEnabled(not running)
        self.backtest_bias_lookback_line.setEnabled(not running)
        self.backtest_value_averaging_cycle_line.setEnabled(not running)
        self.backtest_target_growth_line.setEnabled(not running)
        self.backtest_fee_rate_line.setEnabled(not running)
        self.backtest_min_fee_line.setEnabled(not running)
        self.us_commission_per_share_line.setEnabled(not running)
        self.us_commission_min_line.setEnabled(not running)
        self.us_commission_max_rate_line.setEnabled(not running)
        self.us_platform_fee_per_share_line.setEnabled(not running)
        self.us_platform_fee_min_line.setEnabled(not running)
        self.us_platform_fee_max_rate_line.setEnabled(not running)
        self.backtest_grid_step_line.setEnabled(not running)
        self.backtest_grid_volume_line.setEnabled(not running)

    def refresh_backtest_chart(self, bars: list[BarData]) -> None:
        """"""
        self.clear_backtest_trade_markers()
        self.clear_backtest_drawdown_line()
        self.clear_backtest_bias_line()
        self.backtest_chart.clear_all()

        if bars:
            self.backtest_chart.update_history(bars)
            self.refresh_backtest_trade_markers()
            self.refresh_backtest_drawdown_line()
            self.refresh_backtest_bias_line()

    def clear_backtest_drawdown_line(self) -> None:
        """"""
        if self.backtest_drawdown_plot:
            for item in self.backtest_drawdown_items:
                self.backtest_drawdown_plot.removeItem(item)
        self.backtest_drawdown_items.clear()

    def refresh_backtest_drawdown_line(self) -> None:
        """"""
        self.clear_backtest_drawdown_line()

        if self.selected_backtest_strategy_key != BACKTEST_STRATEGY_DRAWDOWN_DCA:
            self.backtest_drawdown_plot.hide()
            return

        if not self.backtest_bars:
            self.backtest_drawdown_plot.hide()
            return

        selected_bars: list[BarData] = select_daily_investment_bars(self.backtest_bars)
        if not selected_bars:
            self.backtest_drawdown_plot.hide()
            return

        self.backtest_drawdown_plot.show()

        dt_ix_map: dict[datetime, int] = {
            bar.datetime.replace(microsecond=0): ix
            for ix, bar in enumerate(self.backtest_bars)
        }
        date_ix_map: dict[object, int] = {
            bar.datetime.date(): ix
            for ix, bar in enumerate(self.backtest_bars)
        }

        x_values: list[int] = []
        y_values: list[float] = []
        drawdown_rates: list[float] = calculate_drawdown_rates(
            selected_bars,
            self.backtest_drawdown_lookback_days,
        )
        for bar, drawdown_rate in zip(selected_bars, drawdown_rates, strict=True):
            bar_dt: datetime = bar.datetime.replace(microsecond=0)
            ix: int | None = dt_ix_map.get(bar_dt)
            if ix is None:
                ix = date_ix_map.get(bar_dt.date())
            if ix is None:
                continue

            x_values.append(ix)
            y_values.append(drawdown_rate * 100)

        if not x_values:
            self.backtest_drawdown_plot.hide()
            return

        line_item: pg.PlotDataItem = pg.PlotDataItem(
            x_values,
            y_values,
            pen=pg.mkPen("#FFB74D", width=2),
        )
        self.backtest_drawdown_plot.addItem(line_item)
        self.backtest_drawdown_items.append(line_item)

        for threshold in (-5, -15, -25):
            threshold_item: pg.InfiniteLine = pg.InfiniteLine(
                pos=threshold,
                angle=0,
                pen=pg.mkPen("#78909C", width=1, style=QtCore.Qt.PenStyle.DashLine),
            )
            self.backtest_drawdown_plot.addItem(threshold_item)
            self.backtest_drawdown_items.append(threshold_item)

        latest_label: pg.TextItem = pg.TextItem(
            f"回撤率 {y_values[-1]:.2f}%",
            color="#FFB74D",
            anchor=(1, 1),
        )
        latest_label.setPos(x_values[-1], y_values[-1])
        self.backtest_drawdown_plot.addItem(latest_label)
        self.backtest_drawdown_items.append(latest_label)

        min_y: float = min(min(y_values), -1)
        self.backtest_drawdown_plot.setLimits(yMin=min_y - 2, yMax=2)
        self.backtest_drawdown_plot.setYRange(min_y - 2, 2, padding=0)

    def clear_backtest_bias_line(self) -> None:
        """"""
        if self.backtest_bias_plot:
            for item in self.backtest_bias_items:
                self.backtest_bias_plot.removeItem(item)
        self.backtest_bias_items.clear()

    def refresh_backtest_bias_line(self) -> None:
        """"""
        self.clear_backtest_bias_line()

        if self.selected_backtest_strategy_key != BACKTEST_STRATEGY_BIAS_DCA:
            self.backtest_bias_plot.hide()
            return

        if not self.backtest_bars:
            self.backtest_bias_plot.hide()
            return

        selected_bars: list[BarData] = select_daily_investment_bars(self.backtest_bars)
        if not selected_bars:
            self.backtest_bias_plot.hide()
            return

        self.backtest_bias_plot.show()

        dt_ix_map: dict[datetime, int] = {
            bar.datetime.replace(microsecond=0): ix
            for ix, bar in enumerate(self.backtest_bars)
        }
        date_ix_map: dict[object, int] = {
            bar.datetime.date(): ix
            for ix, bar in enumerate(self.backtest_bars)
        }

        x_values: list[int] = []
        y_values: list[float] = []
        bias_rates: list[float] = calculate_bias_rates(
            selected_bars,
            self.backtest_bias_lookback_days,
        )
        for bar, bias_rate in zip(selected_bars, bias_rates, strict=True):
            bar_dt: datetime = bar.datetime.replace(microsecond=0)
            ix: int | None = dt_ix_map.get(bar_dt)
            if ix is None:
                ix = date_ix_map.get(bar_dt.date())
            if ix is None:
                continue

            x_values.append(ix)
            y_values.append(bias_rate * 100)

        if not x_values:
            self.backtest_bias_plot.hide()
            return

        line_item: pg.PlotDataItem = pg.PlotDataItem(
            x_values,
            y_values,
            pen=pg.mkPen("#64B5F6", width=2),
        )
        self.backtest_bias_plot.addItem(line_item)
        self.backtest_bias_items.append(line_item)

        for threshold in (-15, -5, 5, 15):
            threshold_item: pg.InfiniteLine = pg.InfiniteLine(
                pos=threshold,
                angle=0,
                pen=pg.mkPen("#78909C", width=1, style=QtCore.Qt.PenStyle.DashLine),
            )
            self.backtest_bias_plot.addItem(threshold_item)
            self.backtest_bias_items.append(threshold_item)

        zero_item: pg.InfiniteLine = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen("#546E7A", width=1),
        )
        self.backtest_bias_plot.addItem(zero_item)
        self.backtest_bias_items.append(zero_item)

        latest_label: pg.TextItem = pg.TextItem(
            f"乖离率 {y_values[-1]:.2f}%",
            color="#64B5F6",
            anchor=(1, 1),
        )
        latest_label.setPos(x_values[-1], y_values[-1])
        self.backtest_bias_plot.addItem(latest_label)
        self.backtest_bias_items.append(latest_label)

        min_y: float = min(min(y_values), -12)
        max_y: float = max(max(y_values), 12)
        self.backtest_bias_plot.setLimits(yMin=min_y - 2, yMax=max_y + 2)
        self.backtest_bias_plot.setYRange(min_y - 2, max_y + 2, padding=0)

    def clear_backtest_trade_markers(self) -> None:
        """"""
        candle_plot: pg.PlotItem = self.backtest_chart.get_plot("candle")
        if candle_plot:
            for item in self.backtest_marker_items:
                candle_plot.removeItem(item)
        self.backtest_marker_items.clear()

    def refresh_backtest_trade_markers(self, *_args: object) -> None:
        """"""
        self.clear_backtest_trade_markers()
        if not self.backtest_marker_checkbox.isChecked():
            return

        if not self.backtest_bars or not self.backtest_records:
            return

        candle_plot: pg.PlotItem = self.backtest_chart.get_plot("candle")
        if not candle_plot:
            return

        dt_ix_map: dict[datetime, int] = {
            bar.datetime.replace(microsecond=0): ix
            for ix, bar in enumerate(self.backtest_bars)
        }
        date_ix_map: dict[object, int] = {
            bar.datetime.date(): ix
            for ix, bar in enumerate(self.backtest_bars)
        }
        bar_by_ix: dict[int, BarData] = {
            ix: bar
            for ix, bar in enumerate(self.backtest_bars)
        }

        scatter_data: list[dict] = []
        marker_font: QtGui.QFont = QtGui.QFont()
        marker_font.setPixelSize(8)
        marker_font.setBold(True)
        for record in self.backtest_records:
            try:
                record_dt: datetime = datetime.fromisoformat(record.datetime).replace(microsecond=0)
            except ValueError:
                continue

            ix: int | None = dt_ix_map.get(record_dt)
            if ix is None:
                ix = date_ix_map.get(record_dt.date())

            bar: BarData | None = bar_by_ix.get(ix) if ix is not None else None
            if ix is None or bar is None:
                continue

            if record.volume > 0:
                label: str = "B"
                color: str = "#4CAF50"
                symbol: str = "t1"
                y_value: float = bar.low_price
                text_anchor: tuple[float, float] = (0.5, -0.15)
            elif record.volume < 0:
                label = "S"
                color = "#FF5252"
                symbol = "t"
                y_value = bar.high_price
                text_anchor = (0.5, 1.15)
            else:
                label = "N"
                color = "#FFB300"
                symbol = "o"
                y_value = bar.high_price
                text_anchor = (0.5, 1.15)

            if y_value <= 0:
                y_value = bar.close_price

            scatter_data.append({
                "pos": (ix, y_value),
                "size": 7 if record.volume else 5,
                "pen": pg.mkPen(color),
                "brush": pg.mkBrush(color),
                "symbol": symbol,
            })
            text_item: pg.TextItem = pg.TextItem(label, color=color, anchor=text_anchor)
            text_item.setFont(marker_font)
            text_item.setPos(ix, y_value)
            self.backtest_marker_items.append(text_item)
            candle_plot.addItem(text_item)

        if scatter_data:
            scatter_item: pg.ScatterPlotItem = pg.ScatterPlotItem(scatter_data)
            self.backtest_marker_items.append(scatter_item)
            candle_plot.addItem(scatter_item)

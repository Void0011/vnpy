"""Smoke the real Qt result view with synthetic records, without starting trading."""
import os
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vnpy.dca.engine import InvestmentSetting, InvestmentSummary  # noqa: E402
from vnpy.dca.personal import calculate_personal_investment_analysis  # noqa: E402
from vnpy.dca.ui.widget import DailyInvestmentManager  # noqa: E402
from vnpy.event import EventEngine  # noqa: E402
from vnpy.trader.ui import QtGui, QtWidgets  # noqa: E402
from test_personal_returns import bars, trade  # noqa: E402


def test_personal_return_tables_and_chart_render(tmp_path) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists():
        font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QtGui.QFont(families[0], 9))
    engine = SimpleNamespace(
        setting=InvestmentSetting(),
        get_summary=lambda: InvestmentSummary(),
        get_records=lambda: [],
        get_personal_product_summaries=lambda: [],
    )
    main_engine = SimpleNamespace(get_engine=lambda name: engine, get_all_gateway_names=lambda: [])
    widget = DailyInvestmentManager(main_engine, EventEngine())
    analysis = calculate_personal_investment_analysis(
        "159792.SZSE", [trade(1, 10, 100, -1000)], bars([10, 11, 9, 12, 10]),
    )
    widget.refresh_personal_analysis(analysis)
    widget.tabs.setCurrentIndex(3)
    widget.personal_return_table.parentWidget().parentWidget().setCurrentIndex(1)
    widget.show()
    app.processEvents()
    assert widget.windowTitle() == "定投回测"
    assert widget.personal_point_table.rowCount() == 2
    assert widget.personal_return_table.rowCount() == 4
    assert len(widget.personal_marker_items) >= 4
    widget.personal_chart._cursor.update_info()
    assert widget.grab().save(str(tmp_path / "personal-returns.png"))
    widget.close()

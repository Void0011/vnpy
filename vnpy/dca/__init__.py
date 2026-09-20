from pathlib import Path

from vnpy.trader.app import BaseApp

from .engine import APP_NAME, DailyInvestmentEngine


__all__ = [
    "APP_NAME",
    "DailyInvestmentApp",
    "DailyInvestmentEngine",
]


class DailyInvestmentApp(BaseApp):
    """"""

    app_name: str = APP_NAME
    app_module: str = __module__
    app_path: Path = Path(__file__).parent
    display_name: str = "定投回测"
    engine_class: type[DailyInvestmentEngine] = DailyInvestmentEngine
    widget_name: str = "DailyInvestmentManager"
    icon_name: str = ""

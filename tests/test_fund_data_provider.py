from datetime import date

from src.services.fund_data_provider import FundDataProvider
from src.schemas.fund_report_schema import FundNavRow


def _assert_close(a, b, tol=1e-4):
    assert abs(a - b) < tol


def test_stats_compute_mdd_and_return():
    rows = [
        FundNavRow(date=date(2024, 1, 1), unit_nav=1.0, daily_growth=0.0),
        FundNavRow(date=date(2024, 1, 2), unit_nav=1.1, daily_growth=0.1),
        FundNavRow(date=date(2024, 1, 3), unit_nav=0.99, daily_growth=-0.1),
        FundNavRow(date=date(2024, 1, 4), unit_nav=1.2, daily_growth=0.212),
    ]
    stats = FundDataProvider.stats_from_nav(rows)
    _assert_close(stats["interval_return"], 0.2)      # 1.2/1.0 - 1
    _assert_close(stats["max_drawdown"], 0.10)        # 1.1 -> 0.99
    _assert_close(stats["current_drawdown"], 0.0)     # 1.2 为历史新高


def test_parse_nav_dataframe_column_map():
    import pandas as pd
    df = pd.DataFrame({
        "净值日期": ["2024-01-01", "2024-01-02"],
        "单位净值": [1.0, 1.1],
        "日增长率": [0.0, 0.1],
    })
    parsed = FundDataProvider._normalize_nav_df(df)
    assert len(parsed) == 2
    assert parsed[0].unit_nav == 1.0

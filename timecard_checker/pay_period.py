"""
Pay period boundaries for comparing First Due vs Executime.

Use inclusive calendar dates (same day-grain as WorkDate after normalization).
Change PAY_PERIOD_START / PAY_PERIOD_END when you run a new two-week audit.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

# Inclusive calendar days for this audit (e.g. 03/16/2026–03/29/2026).
PAY_PERIOD_START = date(2026, 3, 16)  # 03/16
PAY_PERIOD_END = date(2026, 3, 29)  # 03/29


def filter_to_pay_period(
    df: pd.DataFrame,
    column: str = "WorkDate",
    start: date = PAY_PERIOD_START,
    end: date = PAY_PERIOD_END,
) -> pd.DataFrame:
    """
    Keep rows whose WorkDate falls on or between start and end (inclusive).

    Drops First Due spillover outside the period, and clips Executime rows
    if the export ever includes extra calendar days.
    """
    if df.empty:
        return df.copy()

    as_date = pd.to_datetime(df[column], errors="coerce").dt.normalize().dt.date
    mask = (as_date >= start) & (as_date <= end)
    return df.loc[mask].copy()

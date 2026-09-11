from datetime import datetime, date

from .loaders import load_executime_csv, load_firstdue_csv
from .normalizers import (
    normalize_executime,
    normalize_firstdue,
    expand_firstdue_to_calendar_days,
    aggregate_firstdue_calendar_days,
    aggregate_executime_daily,
)
from .compare import compare_firstdue_vs_executime
from .pay_period import filter_to_pay_period
from .signatures import build_signature_review


EXCLUDED_EMPLOYEES = {
    "SWANSON, PAUL",
    "SANDHOLDT, PATRICK",
    "HOUCK, REBEKAH",
    "HUNTER, RACHEL",
    "KENUI, THERESA",
}


def parse_browser_date(date_text: str) -> date:
    """
    Convert browser date input (YYYY-MM-DD) into a Python date object.
    """
    return datetime.strptime(date_text, "%Y-%m-%d").date()


def run_web_pipeline(
    executime_file,
    firstdue_file,
    pay_period_start_text: str,
    pay_period_end_text: str,
    include_signatures: bool = False,
):
    """
    Run the full comparison pipeline for the web app.

    Inputs:
    - executime_file: uploaded CSV file object
    - firstdue_file: uploaded CSV file object
    - pay_period_start_text: browser date string like '2026-03-16'
    - pay_period_end_text: browser date string like '2026-03-29'
    - include_signatures: when True, include rank-based signature review

    Returns:
    - comparison DataFrame
    """

    pay_period_start = parse_browser_date(pay_period_start_text)
    pay_period_end = parse_browser_date(pay_period_end_text)

    # Load raw CSVs.
    executime_raw = load_executime_csv(executime_file)
    firstdue_raw = load_firstdue_csv(firstdue_file)

    # Normalize + aggregate executime.
    executime_normalized = normalize_executime(executime_raw)
    executime_aggregated = aggregate_executime_daily(executime_normalized)

    # Normalize + expand + aggregate First Due.
    firstdue_normalized = normalize_firstdue(firstdue_raw)
    firstdue_expanded = expand_firstdue_to_calendar_days(firstdue_raw)
    firstdue_aggregated = aggregate_firstdue_calendar_days(firstdue_expanded)

    # Filter both to browser-selected pay period.
    firstdue_for_compare = filter_to_pay_period(
        firstdue_aggregated,
        start=pay_period_start,
        end=pay_period_end,
    )

    executime_for_compare = filter_to_pay_period(
        executime_aggregated,
        start=pay_period_start,
        end=pay_period_end,
    )

    # Exclude selected personnel.
    firstdue_for_compare = firstdue_for_compare[
        ~firstdue_for_compare["EmpKey"].isin(EXCLUDED_EMPLOYEES)
    ].copy()

    executime_for_compare = executime_for_compare[
        ~executime_for_compare["EmpKey"].isin(EXCLUDED_EMPLOYEES)
    ].copy()

    signature_review = None
    if include_signatures:
        signature_review = build_signature_review(
            firstdue_for_compare,
            executime_for_compare,
        )

    comparison = compare_firstdue_vs_executime(
        firstdue_for_compare,
        executime_for_compare,
        signature_review_df=signature_review,
    )

    return comparison

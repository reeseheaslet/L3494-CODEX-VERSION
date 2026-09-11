from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import pandas as pd
from flask import Blueprint, redirect, render_template, request, session, url_for

from .pipeline import run_web_pipeline


MAX_CSV_BYTES = 5 * 1024 * 1024
EXECUTIME_COLUMNS = {
    "startTime", "timeEntryTypeLabel", "totalTime", "approvals", "employeeLabel"
}
FIRSTDUE_COLUMNS = {
    "Start at (local datetime)", "End at (local datetime)", "Duration (hours)",
    "Last Name", "First Name", "Personnel Rank", "Activity Type Shortcode",
    "Activity Subtype Name",
}


class TimecardInputError(ValueError):
    """A safe validation error that may be shown to the member."""


def _read_csv_upload(upload, label: str, required_columns: set[str]) -> BytesIO:
    if upload is None or not upload.filename:
        raise TimecardInputError(f"Please choose the {label} CSV file.")
    if "." not in upload.filename or upload.filename.rsplit(".", 1)[1].lower() != "csv":
        raise TimecardInputError(f"The {label} upload must be a .csv file.")

    contents = upload.stream.read(MAX_CSV_BYTES + 1)
    if not contents:
        raise TimecardInputError(f"The {label} CSV is empty.")
    if len(contents) > MAX_CSV_BYTES:
        raise TimecardInputError(f"The {label} CSV is larger than 5 MB.")

    try:
        columns = set(pd.read_csv(BytesIO(contents), nrows=0).columns)
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        raise TimecardInputError(f"The {label} upload is not a readable CSV.") from None

    missing = sorted(required_columns - columns)
    if missing:
        raise TimecardInputError(
            f"The {label} CSV does not match the expected export format. "
            "Please export a fresh CSV and try again."
        )
    return BytesIO(contents)


def _parse_date(value: str) -> date:
    """Accept the ISO value from the picker and common US typed date formats."""
    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise TimecardInputError("Please enter a valid pay-period start and end date.")


def _validate_dates(start_text: str, end_text: str) -> tuple[str, str]:
    start = _parse_date(start_text)
    end = _parse_date(end_text)
    if end < start:
        raise TimecardInputError("The pay-period end date must be on or after the start date.")
    if (end - start).days > 31:
        raise TimecardInputError("The selected pay period cannot be longer than 32 days.")
    return start.isoformat(), end.isoformat()


def create_timecard_blueprint(member_access_check):
    bp = Blueprint("timecard", __name__, url_prefix="/members/tools/timecard")

    @bp.before_request
    def require_member():
        if not member_access_check():
            return redirect(url_for("login"))

    @bp.route("", methods=["GET", "POST"])
    def index():
        results_rows = []
        error = None
        submitted = request.method == "POST"
        pay_period_start = request.form.get("pay_period_start", "").strip()
        pay_period_end = request.form.get("pay_period_end", "").strip()

        if submitted:
            try:
                normalized_start, normalized_end = _validate_dates(
                    pay_period_start, pay_period_end
                )
                executime_file = _read_csv_upload(
                    request.files.get("executime_file"), "Executime", EXECUTIME_COLUMNS
                )
                firstdue_file = _read_csv_upload(
                    request.files.get("firstdue_file"), "First Due", FIRSTDUE_COLUMNS
                )
                result_df = run_web_pipeline(
                    executime_file,
                    firstdue_file,
                    normalized_start,
                    normalized_end,
                    include_signatures=True,
                )
                review_df = result_df[result_df["NeedsReview"]].copy()
                results_rows = review_df.to_dict(orient="records") if not review_df.empty else []
            except TimecardInputError as exc:
                error = str(exc)
            except (KeyError, TypeError, ValueError, pd.errors.ParserError):
                error = "We could not process those exports. Please export fresh CSV files and try again."
            except Exception:
                # Deliberately avoid logging uploaded payroll data or raw parser details.
                error = "The comparison could not be completed. Please verify the files and try again."

        return render_template(
            "member_timecard.html",
            results_rows=results_rows,
            error=error,
            submitted=submitted,
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end,
            username=session.get("username", ""),
            role=session.get("role", "member"),
        )

    return bp

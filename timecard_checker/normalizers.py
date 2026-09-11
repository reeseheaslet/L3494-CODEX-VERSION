import pandas as pd
from .utils import build_emp_key
from .employee_crosswalk import canonicalize_employee_name

def parse_approvals(approval_text: str) -> dict:
    """
    Convert executime approvals text into boolean signature flags.

    Expected tags:
    1 = Employee
    2 = Supervisor
    3 = Div Head
    4 = Dept Head
    """
    if pd.isna(approval_text):
        approval_text = ""

    approval_text = str(approval_text)

    return {
        "HasEmpSig": "1=" in approval_text,
        "HasSupSig": "2=" in approval_text,
        "HasDivHeadSig": "3=" in approval_text,
        "HasDeptHeadSig": "4=" in approval_text,
    }


def map_executime_type(raw_type_label: str) -> str:
    """
    Map executime raw type labels into normalized categories.

    For now:
    - keep core payroll buckets explicit
    - roll recognized non-core leave types into OTHER_NONWORK
    - keep truly unknown values visible as UNKNOWN
    """
    if pd.isna(raw_type_label):
        return "UNKNOWN"

    raw_type_label = str(raw_type_label).strip()

    mapping = {
        "01 (Reg Hours)": "REG",
        "62 (Fire OT)": "OT",

        "05 (Vacation)": "VAC",
        "VS (Vacation Shift Trade Fire)": "VAC",

        "06 (Sick)": "SICK",
        "SS (Sick Shift Trade Fire)": "SICK",
        "53 (TPT Sick)": "SICK",

        "15 (Injury Leave Safety)": "INJURY",


        "20 (Act Capt Regular)": "AC",
        "21 (Act Capt OT)": "ACOT",

        "50 (Act BC Regular)": "ABC",
        "51 (Act BC OT)": "ABCOT",

        "57 (Bereavement Leave)": "OTHER_NONWORK",
        "23 (Parental Leave)": "OTHER_NONWORK",
    }

    return mapping.get(raw_type_label, "UNKNOWN")













def normalize_executime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert raw executime rows into a small normalized schema.

    Version 1 choices:
    - WorkDate comes from startTime
    - Hours comes from totalTime
    - RawTypeLabel comes from timeEntryTypeLabel
    - Signature booleans come from approvals
    """
    working_df = df.copy()

    working_df["startTime"] = pd.to_datetime(
        working_df["startTime"],
        format="%b %d, %Y %I:%M %p",
        errors="coerce"
    )
    working_df["WorkDate"] = working_df["startTime"].dt.date

    working_df["RawTypeLabel"] = working_df["timeEntryTypeLabel"]
    working_df["NormalizedType"] = working_df["RawTypeLabel"].apply(map_executime_type)
    working_df["Hours"] = working_df["totalTime"]

    approval_flags = working_df["approvals"].apply(parse_approvals).apply(pd.Series)
    working_df = pd.concat([working_df, approval_flags], axis=1)

    working_df["EmployeeNameCanonical"] = (
        working_df["employeeLabel"]
        .astype(str)
        .str.strip()
        .str.upper()
        .apply(canonicalize_employee_name)
    )

    working_df["EmpKey"] = working_df["EmployeeNameCanonical"].apply(build_emp_key)

    if "employeeId" not in working_df.columns:
        working_df["employeeId"] = pd.NA

    normalized_df = working_df[
        [
            "EmployeeNameCanonical",
            "EmpKey",
            "employeeId",
            "WorkDate",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ].copy()

    normalized_df = normalized_df.rename(
        columns={
            "EmployeeNameCanonical": "EmployeeName",
            "employeeId": "EmployeeId",
        }
    )

    normalized_df["SourceSystem"] = "executime"

    normalized_df = normalized_df[
        [
            "SourceSystem",
            "EmployeeName",
            "EmpKey",
            "EmployeeId",
            "WorkDate",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ]

    return normalized_df




def aggregate_executime_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate normalized executime rows into one row per:

    - EmployeeName
    - WorkDate
    - NormalizedType

    This makes executime comparable to aggregated First Due daily rows.
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "SourceSystem",
                "EmployeeName",
                "EmpKey",
                "EmployeeId",
                "WorkDate",
                "PersonnelRank",
                "RawTypeLabel",
                "NormalizedType",
                "Hours",
                "HasEmpSig",
                "HasSupSig",
                "HasDivHeadSig",
                "HasDeptHeadSig",
            ]
        )

    aggregated_df = (
        df.groupby(
            ["EmployeeName", "WorkDate", "NormalizedType"],
            dropna=False,
            as_index=False
        )
        .agg(
            SourceSystem=("SourceSystem", "first"),
            EmpKey=("EmpKey", "first"),
            EmployeeId=("EmployeeId", "first"),
            RawTypeLabel=("RawTypeLabel", combine_distinct_labels),
            Hours=("Hours", "sum"),
            HasEmpSig=("HasEmpSig", "max"),
            HasSupSig=("HasSupSig", "max"),
            HasDivHeadSig=("HasDivHeadSig", "max"),
            HasDeptHeadSig=("HasDeptHeadSig", "max"),
        )
    )

    aggregated_df = aggregated_df[
        [
            "SourceSystem",
            "EmployeeName",
            "EmpKey",
            "EmployeeId",
            "WorkDate",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ].copy()

    aggregated_df["Hours"] = pd.to_numeric(aggregated_df["Hours"], errors="coerce")

    return aggregated_df







def map_firstdue_type(raw_type_label: str) -> str | None:
    """
    Map First Due raw activity codes into normalized categories.

    Temporary debugging choice:
    - blank values remain visible as BLANK
    - TRADEON is dropped entirely
    """

    if pd.isna(raw_type_label):
        return "UNKNOWN"

    raw_type_label = str(raw_type_label).strip().upper()

    if raw_type_label == "TRADEON":
        return None

    # In this export, blank type labels represent TRADEOFF rows
    if raw_type_label == "":
        raw_type_label = "TRADEOFF"

    mapping = {
        "REG": "REG",
        "TRADEOFF": "REG",

        "OT": "OT",
        "OT | NORMAL": "OT",
        "FORCEDOT | NORMAL": "OT",
        "OT | EMERGENCY RECALL": "OT",

        "SICK": "SICK",

        "VAC": "VAC",
        "FF VAC": "VAC",
        "CPT VAC": "VAC",
        "BC VAC": "VAC",

        "AC": "AC",
        "ABC": "ABC",
        "OT | ACTING CAPTAIN": "ACOT",
        "OT | ACTING BATTALION CHIEF": "ABCOT",

        "JURY": "OTHER_NONWORK",
        "BRV": "OTHER_NONWORK",
        "PARENT": "OTHER_NONWORK",
    }

    if raw_type_label in mapping:
        return mapping[raw_type_label]

    if "INJ" in raw_type_label or "INJURY" in raw_type_label:
        return "INJURY"

    return "UNKNOWN"




def build_firstdue_raw_type_label(activity_type: str, activity_subtype: str) -> str:
    """
    Preserve raw First Due classification fields in one audit-friendly label.
    """
    type_part = "" if pd.isna(activity_type) else str(activity_type).strip()
    subtype_part = "" if pd.isna(activity_subtype) else str(activity_subtype).strip()

    if type_part and subtype_part:
        return f"{type_part} | {subtype_part}"
    if type_part:
        return type_part
    if subtype_part:
        return subtype_part
    return ""

def prepare_firstdue_working_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare raw First Due rows for downstream normalization / expansion.

    This keeps the original interval datetimes available while also building
    the shared normalized fields we want later.
    """
    working_df = df.copy()

    working_df["Start at (local datetime)"] = pd.to_datetime(
        working_df["Start at (local datetime)"],
        errors="coerce"
    )

    working_df["End at (local datetime)"] = pd.to_datetime(
        working_df["End at (local datetime)"],
        errors="coerce"
    )

    working_df["Hours"] = pd.to_numeric(
        working_df["Duration (hours)"],
        errors="coerce"
    )

    working_df["EmployeeName"] = (
        working_df["Last Name"].fillna("").astype(str).str.strip().str.upper()
        + ", "
        + working_df["First Name"].fillna("").astype(str).str.strip().str.upper()
    )

    working_df["EmployeeName"] = working_df["EmployeeName"].apply(canonicalize_employee_name)

    working_df["EmployeeId"] = pd.NA
    working_df["PersonnelRank"] = working_df.get("Personnel Rank", pd.Series(pd.NA, index=working_df.index))

    working_df["RawTypeLabel"] = working_df.apply(
        lambda row: build_firstdue_raw_type_label(
            row["Activity Type Shortcode"],
            row["Activity Subtype Name"]
        ),
        axis=1
    )

    working_df["NormalizedType"] = working_df["RawTypeLabel"].apply(map_firstdue_type)
    working_df = working_df[working_df["NormalizedType"].notna()].copy()

    working_df["HasEmpSig"] = pd.NA
    working_df["HasSupSig"] = pd.NA
    working_df["HasDivHeadSig"] = pd.NA
    working_df["HasDeptHeadSig"] = pd.NA
    working_df["SourceSystem"] = "First Due"

    working_df["EmpKey"] = working_df["EmployeeName"].apply(build_emp_key)

    return working_df

def normalize_firstdue(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert raw First Due rows into the same normalized schema used by executime.

    First-pass choices:
    - WorkDate comes from Start at (local datetime)
    - Hours comes from Duration (hours)
    - RawTypeLabel combines Activity Type + Activity Subtype
    - Signature fields are unknown in this CSV, so they stay as pd.NA
    """
    working_df = prepare_firstdue_working_df(df)

    working_df["WorkDate"] = working_df["Start at (local datetime)"].dt.date

    normalized_df = working_df[
        [
            "SourceSystem",
            "EmployeeName",
            "EmpKey",
            "EmployeeId",
            "WorkDate",
            "PersonnelRank",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ].copy()

    return normalized_df







from datetime import datetime, timedelta


def split_firstdue_row_to_days(row: pd.Series) -> list[dict]:
    """
    Split one prepared First Due row into one or more calendar-day rows.

    Input row must already have:
    - parsed Start at (local datetime)
    - parsed End at (local datetime)
    - normalized metadata fields
    """
    start = row["Start at (local datetime)"]
    end = row["End at (local datetime)"]

    if pd.isna(start) or pd.isna(end):
        return []

    if end <= start:
        return []

    results = []
    current_start = start

    while current_start < end:
        next_midnight = datetime.combine(
            current_start.date() + timedelta(days=1),
            datetime.min.time()
        )

        current_end = min(next_midnight, end)
        hours = (current_end - current_start).total_seconds() / 3600

        new_row = row.copy()
        new_row["WorkDate"] = current_start.date()
        new_row["Hours"] = hours

        results.append(new_row.to_dict())

        current_start = current_end

    return results


def expand_firstdue_to_calendar_days(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expand raw First Due interval rows into calendar-day rows.

    This function:
    1. prepares the raw First Due dataframe
    2. splits each interval across calendar days
    3. returns the shared normalized schema
    """
    working_df = prepare_firstdue_working_df(df)

    expanded_rows = []

    for _, row in working_df.iterrows():
        split_rows = split_firstdue_row_to_days(row)
        expanded_rows.extend(split_rows)

    expanded_df = pd.DataFrame(expanded_rows)

    if expanded_df.empty:
        return pd.DataFrame(
            columns=[
                "SourceSystem",
                "EmployeeName",
                "EmpKey",
                "EmployeeId",
                "WorkDate",
                "PersonnelRank",
                "RawTypeLabel",
                "NormalizedType",
                "Hours",
                "HasEmpSig",
                "HasSupSig",
                "HasDivHeadSig",
                "HasDeptHeadSig",
            ]
        )

    expanded_df = expanded_df[
        [
            "SourceSystem",
            "EmployeeName",
            "EmpKey",
            "EmployeeId",
            "WorkDate",
            "PersonnelRank",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ].copy()

    return expanded_df


def combine_distinct_labels(series: pd.Series) -> str:
    """
    Join distinct nonblank raw labels in a stable, readable way.
    """
    values = []

    for value in series:
        if pd.isna(value):
            continue

        value = str(value).strip()
        if not value:
            continue

        if value not in values:
            values.append(value)

    return " ; ".join(values)


def aggregate_firstdue_calendar_days(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate expanded First Due calendar-day rows into one row per:

    - EmployeeName
    - WorkDate
    - NormalizedType

    This is the final First Due daily shape before comparison.
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "SourceSystem",
                "EmployeeName",
                "EmpKey",
                "EmployeeId",
                "WorkDate",
                "PersonnelRank",
                "RawTypeLabel",
                "NormalizedType",
                "Hours",
                "HasEmpSig",
                "HasSupSig",
                "HasDivHeadSig",
                "HasDeptHeadSig",
            ]
        )

    aggregated_df = (
        df.groupby(
            ["EmployeeName", "WorkDate", "NormalizedType"],
            dropna=False,
            as_index=False
        )
        .agg(
            SourceSystem=("SourceSystem", "first"),
            EmpKey=("EmpKey", "first"),
            EmployeeId=("EmployeeId", "first"),
            PersonnelRank=("PersonnelRank", "first"),
            RawTypeLabel=("RawTypeLabel", combine_distinct_labels),
            Hours=("Hours", "sum"),
            HasEmpSig=("HasEmpSig", "first"),
            HasSupSig=("HasSupSig", "first"),
            HasDivHeadSig=("HasDivHeadSig", "first"),
            HasDeptHeadSig=("HasDeptHeadSig", "first"),
        )
    )

    aggregated_df = aggregated_df[
        [
            "SourceSystem",
            "EmployeeName",
            "EmpKey",
            "EmployeeId",
            "WorkDate",
            "PersonnelRank",
            "RawTypeLabel",
            "NormalizedType",
            "Hours",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
        ]
    ].copy()

    aggregated_df["Hours"] = pd.to_numeric(aggregated_df["Hours"], errors="coerce")

    return aggregated_df

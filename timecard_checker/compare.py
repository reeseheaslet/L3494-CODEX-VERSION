import pandas as pd


def compare_firstdue_vs_executime(
    firstdue_df: pd.DataFrame,
    executime_df: pd.DataFrame,
    signature_review_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Compare First Due vs executime on:
    - EmpKey
    - WorkDate
    - NormalizedType

    If signature_review_df is supplied, append rank/signature review columns.
    """

    fd = firstdue_df.copy()
    et = executime_df.copy()

    fd = fd.rename(columns={"Hours": "FD_Hours"})
    et = et.rename(columns={"Hours": "ET_Hours"})

    # Keep only needed columns for the hours comparison.
    fd = fd[["EmpKey", "WorkDate", "NormalizedType", "FD_Hours"]]
    et = et[["EmpKey", "WorkDate", "NormalizedType", "ET_Hours"]]

    merged = pd.merge(
        fd,
        et,
        on=["EmpKey", "WorkDate", "NormalizedType"],
        how="outer",
    )

    # Fill missing with 0 for comparison.
    merged["FD_Hours"] = merged["FD_Hours"].fillna(0)
    merged["ET_Hours"] = merged["ET_Hours"].fillna(0)

    merged["HourDiff"] = merged["FD_Hours"] - merged["ET_Hours"]

    # Tolerance set to 0.15 hours because First Due rounds in 0.5-hour increments.
    # This suppresses tiny float/rounding noise while still catching meaningful mismatches.
    merged["IsMismatch"] = merged["HourDiff"].abs() > 0.15

    if signature_review_df is not None:
        sig = signature_review_df.copy()
        sig_cols = [
            "EmpKey",
            "WorkDate",
            "PersonnelRank",
            "RankGroup",
            "HasEmpSig",
            "HasSupSig",
            "HasDivHeadSig",
            "HasDeptHeadSig",
            "SignatureIssueLevel",
            "HasSignatureIssue",
            "SignatureIssueReason",
        ]
        sig = sig[[col for col in sig_cols if col in sig.columns]]

        merged = pd.merge(
            merged,
            sig,
            on=["EmpKey", "WorkDate"],
            how="left",
        )

        merged["HasSignatureIssue"] = merged["HasSignatureIssue"].fillna(False).astype(bool)
        merged["SignatureIssueLevel"] = merged["SignatureIssueLevel"].fillna("OK")
        merged["SignatureIssueReason"] = merged["SignatureIssueReason"].fillna("")
        merged["NeedsReview"] = merged["IsMismatch"] | merged["HasSignatureIssue"]
        merged["ReviewReason"] = merged.apply(_build_review_reason, axis=1)
    else:
        merged["NeedsReview"] = merged["IsMismatch"]
        merged["ReviewReason"] = merged["IsMismatch"].map(
            {True: "Hours mismatch", False: ""}
        )

    merged = merged.sort_values(
        by=["EmpKey", "WorkDate", "NormalizedType"]
    ).reset_index(drop=True)

    return merged


def _build_review_reason(row: pd.Series) -> str:
    reasons: list[str] = []

    if bool(row.get("IsMismatch", False)):
        reasons.append("Hours mismatch")

    signature_reason = str(row.get("SignatureIssueReason", "")).strip()
    if signature_reason:
        reasons.append(signature_reason)

    return "; ".join(reasons)

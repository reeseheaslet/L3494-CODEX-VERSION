from __future__ import annotations

import pandas as pd


def classify_rank(rank_text: str | None) -> str:
    """
    Convert a raw First Due Personnel Rank value into the signature-rule group.

    Groups:
    - BATTALION_CHIEF: requires Dept Head signature only
    - CAPTAIN: requires employee OR supervisor signature, plus Div Head
    - FIREFIGHTER: default group; requires employee, supervisor, and Div Head

    Defaulting unknown ranks to FIREFIGHTER-style rules is intentional because
    it is safer to flag for review than to silently ignore a missing approval.
    """
    if pd.isna(rank_text):
        return "FIREFIGHTER"

    rank = str(rank_text).strip().upper()

    if "BATTALION" in rank or rank in {"BC", "B/C"}:
        return "BATTALION_CHIEF"

    if "CAPTAIN" in rank or rank in {"CPT", "CAPT"}:
        return "CAPTAIN"

    return "FIREFIGHTER"


def build_rank_lookup(firstdue_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one rank row per employee/day from First Due data.

    First Due has the Personnel Rank field. executime has the signatures.
    We combine them later using EmpKey + WorkDate.
    """
    if firstdue_df.empty:
        return pd.DataFrame(columns=["EmpKey", "WorkDate", "PersonnelRank", "RankGroup"])

    required = {"EmpKey", "WorkDate", "PersonnelRank"}
    missing = required - set(firstdue_df.columns)
    if missing:
        raise ValueError(f"First Due data is missing required rank columns: {sorted(missing)}")

    rank_lookup = (
        firstdue_df.dropna(subset=["EmpKey", "WorkDate"])
        .groupby(["EmpKey", "WorkDate"], as_index=False, dropna=False)
        .agg(PersonnelRank=("PersonnelRank", "first"))
    )
    rank_lookup["RankGroup"] = rank_lookup["PersonnelRank"].apply(classify_rank)
    return rank_lookup


def build_executime_signature_lookup(executime_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one signature row per employee/day from normalized/aggregated executime data.

    If an employee has any row that contains a given signature for that date,
    that signature counts as present for the day.
    """
    if executime_df.empty:
        return pd.DataFrame(
            columns=[
                "EmpKey",
                "WorkDate",
                "HasEmpSig",
                "HasSupSig",
                "HasDivHeadSig",
                "HasDeptHeadSig",
            ]
        )

    required = {
        "EmpKey",
        "WorkDate",
        "HasEmpSig",
        "HasSupSig",
        "HasDivHeadSig",
        "HasDeptHeadSig",
    }
    missing = required - set(executime_df.columns)
    if missing:
        raise ValueError(f"executime data is missing required signature columns: {sorted(missing)}")

    sig = (
        executime_df.dropna(subset=["EmpKey", "WorkDate"])
        .groupby(["EmpKey", "WorkDate"], as_index=False, dropna=False)
        .agg(
            HasEmpSig=("HasEmpSig", "max"),
            HasSupSig=("HasSupSig", "max"),
            HasDivHeadSig=("HasDivHeadSig", "max"),
            HasDeptHeadSig=("HasDeptHeadSig", "max"),
        )
    )

    for col in ["HasEmpSig", "HasSupSig", "HasDivHeadSig", "HasDeptHeadSig"]:
        sig[col] = sig[col].fillna(False).astype(bool)

    return sig


def evaluate_signature_row(row: pd.Series) -> pd.Series:
    """
    Apply rank-specific signature rules.

    Importance:
    - HARD: missing final required approval
    - SOFT: missing lower-level signature expectation
    - OK: no signature issue
    """
    rank_group = row.get("RankGroup", "FIREFIGHTER")

    has_emp = bool(row.get("HasEmpSig", False))
    has_sup = bool(row.get("HasSupSig", False))
    has_div = bool(row.get("HasDivHeadSig", False))
    has_dept = bool(row.get("HasDeptHeadSig", False))

    soft_reasons: list[str] = []
    hard_reasons: list[str] = []

    if rank_group == "BATTALION_CHIEF":
        if not has_dept:
            hard_reasons.append("Missing Dept Head signature")

    elif rank_group == "CAPTAIN":
        if not has_emp and not has_sup:
            soft_reasons.append("Missing employee or supervisor signature")
        if not has_div:
            hard_reasons.append("Missing Div Head signature")

    else:  # FIREFIGHTER/default
        if not has_emp:
            soft_reasons.append("Missing employee signature")
        if not has_sup:
            soft_reasons.append("Missing supervisor signature")
        if not has_div:
            hard_reasons.append("Missing Div Head signature")

    if hard_reasons:
        issue_level = "HARD"
    elif soft_reasons:
        issue_level = "SOFT"
    else:
        issue_level = "OK"

    all_reasons = hard_reasons + soft_reasons

    return pd.Series(
        {
            "SignatureIssueLevel": issue_level,
            "HasSignatureIssue": issue_level != "OK",
            "SignatureIssueReason": "; ".join(all_reasons),
        }
    )


def build_signature_review(firstdue_df: pd.DataFrame, executime_df: pd.DataFrame) -> pd.DataFrame:
    """
    Return one row per employee/day with rank, signature flags, and issue level.
    """
    rank_lookup = build_rank_lookup(firstdue_df)
    sig_lookup = build_executime_signature_lookup(executime_df)

    review = pd.merge(rank_lookup, sig_lookup, on=["EmpKey", "WorkDate"], how="outer")

    review["PersonnelRank"] = review["PersonnelRank"].fillna("UNKNOWN")
    review["RankGroup"] = review["RankGroup"].fillna("FIREFIGHTER")

    for col in ["HasEmpSig", "HasSupSig", "HasDivHeadSig", "HasDeptHeadSig"]:
        review[col] = review[col].fillna(False).astype(bool)

    issue_columns = review.apply(evaluate_signature_row, axis=1)
    review = pd.concat([review, issue_columns], axis=1)

    return review.sort_values(["EmpKey", "WorkDate"]).reset_index(drop=True)

from io import BytesIO

import pandas as pd
from flask import Flask, session
from flask_wtf.csrf import CSRFProtect

from timecard_checker.blueprint import create_timecard_blueprint
from timecard_checker.pipeline import run_web_pipeline


def _executime_csv(hours=8):
    return (
        "startTime,timeEntryTypeLabel,totalTime,approvals,employeeLabel,employeeId\n"
        f'"Aug 01, 2026 08:00 AM",01 (Reg Hours),{hours},1=x;2=x;3=x,"MEMBER, TEST",1\n'
    ).encode()


def _firstdue_csv(hours=8):
    return (
        "Start at (local datetime),End at (local datetime),Duration (hours),Last Name,First Name,Personnel Rank,Activity Type Shortcode,Activity Subtype Name\n"
        f"2026-08-01 08:00:00,2026-08-01 16:00:00,{hours},Member,Test,Firefighter,REG,\n"
    ).encode()


def test_pipeline_matching_synthetic_exports_have_no_review_items():
    result = run_web_pipeline(
        BytesIO(_executime_csv()), BytesIO(_firstdue_csv()),
        "2026-08-01", "2026-08-14", include_signatures=True,
    )
    assert not result["NeedsReview"].any()


def _app():
    app = Flask(__name__, template_folder="../templates")
    app.secret_key = "test"
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    CSRFProtect(app)
    app.add_url_rule("/login", "login", lambda: "login")
    app.add_url_rule("/members", "members", lambda: "members")
    app.register_blueprint(create_timecard_blueprint(lambda: session.get("approved") is True))
    return app


def test_route_requires_member_access():
    response = _app().test_client().get("/members/tools/timecard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_route_rejects_wrong_export_schema_without_details():
    client = _app().test_client()
    with client.session_transaction() as member_session:
        member_session["approved"] = True
    response = client.post(
        "/members/tools/timecard",
        data={
            "pay_period_start": "2026-08-01",
            "pay_period_end": "2026-08-14",
            "executime_file": (BytesIO(b"wrong,column\n1,2\n"), "executime.csv"),
            "firstdue_file": (BytesIO(_firstdue_csv()), "firstdue.csv"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"does not match the expected export format" in response.data


def test_route_processes_uploads_in_memory():
    client = _app().test_client()
    with client.session_transaction() as member_session:
        member_session["approved"] = True
    response = client.post(
        "/members/tools/timecard",
        data={
            "pay_period_start": "2026-08-01",
            "pay_period_end": "2026-08-14",
            "executime_file": (BytesIO(_executime_csv()), "executime.csv"),
            "firstdue_file": (BytesIO(_firstdue_csv()), "firstdue.csv"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"No issues found" in response.data


def test_route_accepts_manually_typed_us_dates(monkeypatch):
    received_dates = []

    def fake_pipeline(*args, **kwargs):
        received_dates.extend(args[2:4])
        return pd.DataFrame({"NeedsReview": []})

    monkeypatch.setattr("timecard_checker.blueprint.run_web_pipeline", fake_pipeline)
    client = _app().test_client()
    with client.session_transaction() as member_session:
        member_session["approved"] = True
    response = client.post(
        "/members/tools/timecard",
        data={
            "pay_period_start": "8/1/2026",
            "pay_period_end": "8/14/26",
            "executime_file": (BytesIO(_executime_csv()), "executime.csv"),
            "firstdue_file": (BytesIO(_firstdue_csv()), "firstdue.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"No issues found" in response.data
    assert received_dates == ["2026-08-01", "2026-08-14"]


def test_route_renders_typeable_date_fields_with_calendar_pickers():
    client = _app().test_client()
    with client.session_transaction() as member_session:
        member_session["approved"] = True
    html = client.get("/members/tools/timecard").get_data(as_text=True)

    assert 'type="text" id="pay_period_start"' in html
    assert 'data-date-picker-for="pay_period_start"' in html
    assert "target.value = parts[1] + '/' + parts[2] + '/' + parts[0]" in html


def test_route_renders_hard_issue_filter_and_row_attributes(monkeypatch):
    review_items = pd.DataFrame([
        {
            "Employee": "Soft Signature",
            "NeedsReview": True,
            "IsMismatch": False,
            "HasSignatureIssue": True,
            "SignatureIssueLevel": "SOFT",
        },
        {
            "Employee": "Hard Signature",
            "NeedsReview": True,
            "IsMismatch": False,
            "HasSignatureIssue": True,
            "SignatureIssueLevel": "HARD",
        },
        {
            "Employee": "Hours Mismatch",
            "NeedsReview": True,
            "IsMismatch": True,
            "HasSignatureIssue": False,
            "SignatureIssueLevel": "NONE",
        },
    ])
    monkeypatch.setattr(
        "timecard_checker.blueprint.run_web_pipeline",
        lambda *args, **kwargs: review_items,
    )

    client = _app().test_client()
    with client.session_transaction() as member_session:
        member_session["approved"] = True
    response = client.post(
        "/members/tools/timecard",
        data={
            "pay_period_start": "2026-08-01",
            "pay_period_end": "2026-08-14",
            "executime_file": (BytesIO(_executime_csv()), "executime.csv"),
            "firstdue_file": (BytesIO(_firstdue_csv()), "firstdue.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-timecard-view="all" aria-pressed="true"' in html
    assert 'data-timecard-view="hours" aria-pressed="false"' in html
    assert 'data-timecard-view="hard" aria-pressed="false"' in html
    assert "setTimecardView(button.dataset.timecardView)" in html
    assert "row.hidden = hidden" in html
    assert "results.querySelectorAll('#timecard-results-table tbody tr')" in html
    assert 'data-mismatch="False" data-signature="True" data-siglevel="SOFT"' in html
    assert 'data-mismatch="False" data-signature="True" data-siglevel="HARD"' in html
    assert 'data-mismatch="True" data-signature="False" data-siglevel="NONE"' in html

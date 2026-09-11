from io import BytesIO

import app as union_app


def test_storage_warning_threshold(tmp_path):
    (tmp_path / "tracked.bin").write_bytes(b"x" * 80)
    status = union_app.get_storage_status(tmp_path, quota_bytes=100, warning_percent=80)
    assert status["percent"] == 80
    assert status["warning"] is True


def test_storage_below_threshold_does_not_warn(tmp_path):
    (tmp_path / "tracked.bin").write_bytes(b"x" * 79)
    status = union_app.get_storage_status(tmp_path, quota_bytes=100, warning_percent=80)
    assert status["warning"] is False


def test_documents_page_has_no_external_url_option(monkeypatch):
    monkeypatch.setitem(union_app.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(union_app, "require_member_access", lambda: True)
    monkeypatch.setattr(union_app, "get_db", lambda: _FakeDb([]))
    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username="Member", role="member", status="active")
    response = client.get("/members/documents")
    assert response.status_code == 200
    assert b"External URL" not in response.data
    assert b'name="url"' not in response.data
    assert b'name="file"' in response.data
    assert b"Union Meeting Minutes" in response.data
    assert b'/members/documents/union-meeting-minutes' in response.data


def test_union_meeting_minutes_page_is_member_only_and_lists_minutes(monkeypatch):
    monkeypatch.setitem(union_app.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(union_app, "require_member_access", lambda: True)
    minutes = [{
        "id": 10,
        "title": "August 25, 2026 General Membership Meeting Minutes",
        "description": "General Membership Meeting minutes from August 25, 2026.",
        "url": "/static/documents/union-meeting-minutes/august-25-2026-general-membership-meeting-minutes.pdf",
        "added_by": None,
        "created_at": "2026-08-25 11:40:00",
    }]
    monkeypatch.setattr(union_app, "get_db", lambda: _FakeDb(minutes))
    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username="Member", role="member", status="active")

    response = client.get("/members/documents/union-meeting-minutes")

    assert response.status_code == 200
    assert b"August 25, 2026 General Membership Meeting Minutes" in response.data
    assert b"august-25-2026-general-membership-meeting-minutes.pdf" in response.data


def test_legacy_document_url_remains_viewable(monkeypatch):
    monkeypatch.setitem(union_app.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(union_app, "require_member_access", lambda: True)
    legacy = [{
        "id": 9, "title": "Legacy resource", "description": None,
        "url": "https://example.com/legacy", "added_by": 2,
        "created_at": "2026-01-01 00:00:00",
    }]
    monkeypatch.setattr(union_app, "get_db", lambda: _FakeDb(legacy))
    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username="Member", role="member", status="active")
    response = client.get("/members/documents")
    assert response.status_code == 200
    assert b"https://example.com/legacy" in response.data


def test_document_add_rejects_url_only_submission(monkeypatch):
    monkeypatch.setitem(union_app.app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setattr(union_app, "require_member_access", lambda: True)
    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username="Member", role="member", status="active")
    response = client.post(
        "/members/documents/add",
        data={"title": "Legacy-style link", "url": "https://example.com"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as member_session:
        assert any("Please upload a file." in message for _, message in member_session["_flashes"])


class _FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args, **kwargs):
        return _FakeRows(self.rows)

    def close(self):
        pass

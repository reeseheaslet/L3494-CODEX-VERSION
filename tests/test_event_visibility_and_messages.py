import sqlite3
from pathlib import Path

import app as union_app


def _database_factory(path):
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    return connect


def _build_event_database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE members (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            profile_photo TEXT
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            event_type TEXT,
            signup_enabled INTEGER DEFAULT 0,
            visibility TEXT NOT NULL,
            supplies TEXT
        );
        CREATE TABLE event_signups (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            note TEXT
        );
        CREATE TABLE event_declines (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL
        );
        CREATE TABLE event_messages (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.execute("INSERT INTO members VALUES (1, 'Test Member', 'member', NULL)")
    db.execute("""
        INSERT INTO events
        VALUES (1, 'Visible Event', 'Details', 'Station 31', '2026-09-11',
                '10:00', 'member', 1, 'member_portal', NULL)
    """)
    db.execute("""
        INSERT INTO events
        VALUES (2, 'Family Event', 'Family details', 'Park', '2026-09-12',
                '11:00', 'member', 0, 'family_section', NULL)
    """)
    db.execute("INSERT INTO event_messages (event_id, author_id, content) VALUES (1, 1, 'Canonical schema works')")
    db.commit()
    db.close()


def test_event_detail_uses_canonical_message_schema_and_visibility(tmp_path, monkeypatch):
    database = tmp_path / 'events.db'
    _build_event_database(database)
    monkeypatch.setattr(union_app, 'get_db', _database_factory(database))

    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username='Test Member', role='member', status='active')

    visible = client.get('/members/events/1')
    assert visible.status_code == 200
    assert b'Canonical schema works' in visible.data

    family_only = client.get('/members/events/2', follow_redirects=False)
    assert family_only.status_code == 302
    assert family_only.headers['Location'].endswith('/members/events')


def test_event_detail_supports_legacy_message_schema(tmp_path, monkeypatch):
    database = tmp_path / 'legacy-events.db'
    db = sqlite3.connect(database)
    db.executescript("""
        CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, location TEXT,
            event_date TEXT NOT NULL, event_time TEXT, event_type TEXT, signup_enabled INTEGER,
            visibility TEXT NOT NULL, supplies TEXT
        );
        CREATE TABLE event_signups (id INTEGER PRIMARY KEY, event_id INTEGER, member_id INTEGER, note TEXT);
        CREATE TABLE event_declines (id INTEGER PRIMARY KEY, event_id INTEGER, member_id INTEGER);
        CREATE TABLE event_messages (
            id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
            message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.execute("INSERT INTO members VALUES (1, 'Legacy Member', 'member')")
    db.execute("INSERT INTO events VALUES (1, 'Academy Graduation', 'Details', 'Station 31', '2026-09-11', '10:00', 'member', 1, 'member_portal', NULL)")
    db.execute("INSERT INTO event_messages (event_id, member_id, message) VALUES (1, 1, 'Legacy message works')")
    db.commit()
    db.close()
    monkeypatch.setattr(union_app, 'get_db', _database_factory(database))

    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(user_id=1, username='Legacy Member', role='member', status='active')

    response = client.get('/members/events/1')
    assert response.status_code == 200
    assert b'Legacy message works' in response.data


def test_family_account_cannot_open_member_event_routes():
    client = union_app.app.test_client()
    with client.session_transaction() as family_session:
        family_session.update(user_id=9, username='Family User', role='family', status='active')

    for path in ('/members/events', '/members/chat', '/members/discussions', '/members/meetings/create'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers['Location'].endswith('/login')


def test_event_forms_use_visibility_without_redundant_type_dropdown():
    root = Path(__file__).resolve().parents[1]
    member_form = (root / 'templates' / 'member_create_event.html').read_text()
    admin_form = (root / 'templates' / 'admin.html').read_text()

    assert 'name="event_type"' not in member_form
    assert 'name="event_type"' not in admin_form
    assert 'visibility_public_homepage' in member_form
    assert 'visibility_member_portal' in member_form
    assert 'visibility_family_section' in member_form

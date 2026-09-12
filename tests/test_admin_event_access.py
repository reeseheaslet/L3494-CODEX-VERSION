import sqlite3

import pytest

import app as union_app


def _database_factory(path):
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    return connect


def _build_admin_database(path, role):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE members (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            linked_member_id INTEGER,
            profile_photo TEXT,
            rank TEXT,
            shift TEXT,
            years_of_service INTEGER,
            badge_number TEXT
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
            created_by INTEGER
        );
        CREATE TABLE event_signups (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            note TEXT
        );
        CREATE TABLE store_orders (
            id INTEGER PRIMARY KEY,
            item_name TEXT,
            size TEXT,
            quantity INTEGER,
            price REAL,
            customer_name TEXT,
            customer_email TEXT,
            payment_method TEXT,
            order_status TEXT,
            created_at TEXT
        );
        CREATE TABLE bulletin_posts (
            id INTEGER PRIMARY KEY,
            title TEXT,
            content TEXT,
            pinned INTEGER,
            created_at TEXT,
            author_id INTEGER
        );
        CREATE TABLE family_photos (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE contact_messages (
            id INTEGER PRIMARY KEY,
            name TEXT,
            email TEXT,
            subject TEXT,
            message TEXT,
            status TEXT,
            viewed_at TEXT,
            created_at TEXT
        );
        CREATE TABLE general_meetings (
            id INTEGER PRIMARY KEY,
            meeting_date TEXT,
            meeting_time TEXT,
            location TEXT
        );
    """)
    db.execute(
        "INSERT INTO members (id, name, email, role, status) VALUES (1, 'Promoted User', 'admin@example.test', ?, 'active')",
        (role,),
    )
    db.execute(
        "INSERT INTO members (id, name, email, role, status) VALUES (2, 'Event Author', 'author@example.test', 'member', 'active')"
    )
    db.execute("""
        INSERT INTO events
            (id, title, description, location, event_date, event_time, event_type,
             signup_enabled, visibility, created_by)
        VALUES
            (10, 'Original Event', 'Original details', 'Station 31', '2026-10-01',
             '10:00', 'member', 0, 'member_portal', 2)
    """)
    db.commit()
    db.close()


def _logged_in_client(database, monkeypatch, session_role='member'):
    monkeypatch.setitem(union_app.app.config, 'WTF_CSRF_ENABLED', False)
    monkeypatch.setattr(union_app, 'get_db', _database_factory(database))
    client = union_app.app.test_client()
    with client.session_transaction() as member_session:
        member_session.update(
            user_id=1,
            username='Promoted User',
            role=session_role,
            member_role=session_role,
            status='active',
            user_type='member',
        )
    return client


@pytest.mark.parametrize('database_role', ['admin', 'super_admin'])
def test_promoted_admin_roles_immediately_see_dashboard(tmp_path, monkeypatch, database_role):
    database = tmp_path / f'{database_role}.db'
    _build_admin_database(database, database_role)
    client = _logged_in_client(database, monkeypatch, session_role='member')

    response = client.get('/admin')

    assert response.status_code == 200
    assert b'Admin Dashboard' in response.data
    assert b'Original Event' in response.data
    with client.session_transaction() as member_session:
        assert member_session['role'] == database_role
        assert member_session['member_role'] == database_role


def test_current_role_does_not_modify_session(tmp_path, monkeypatch):
    database = tmp_path / 'current-admin.db'
    _build_admin_database(database, 'admin')
    monkeypatch.setattr(union_app, 'get_db', _database_factory(database))

    with union_app.app.test_request_context('/members/profile'):
        union_app.session.update(
            user_id=1,
            role='admin',
            member_role='admin',
            user_type='member',
        )
        union_app.session.modified = False

        union_app.refresh_authenticated_member_role()

        assert union_app.session.modified is False


@pytest.mark.parametrize('database_role', ['admin', 'super_admin'])
def test_admin_roles_see_dashboard_link_in_member_app(tmp_path, monkeypatch, database_role):
    database = tmp_path / f'navigation-{database_role}.db'
    _build_admin_database(database, database_role)
    client = _logged_in_client(database, monkeypatch, session_role='member')

    response = client.get('/members/profile')

    assert response.status_code == 200
    assert b'title="Admin Dashboard"' in response.data
    assert b'href="/admin"' in response.data


@pytest.mark.parametrize('database_role', ['admin', 'super_admin'])
def test_admin_roles_can_edit_events_created_by_someone_else(tmp_path, monkeypatch, database_role):
    database = tmp_path / f'edit-{database_role}.db'
    _build_admin_database(database, database_role)
    client = _logged_in_client(database, monkeypatch, session_role='member')

    response = client.post('/admin/events/edit', data={
        'event_id': '10',
        'title': f'Edited by {database_role}',
        'description': 'Updated details',
        'location': 'Station 32',
        'event_date': '2026-10-02',
        'event_time': '11:00',
        'visibility_member_portal': '1',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin?tab=events')
    db = _database_factory(database)()
    event = db.execute('SELECT * FROM events WHERE id = 10').fetchone()
    db.close()
    assert event['title'] == f'Edited by {database_role}'
    assert event['created_by'] == 2


def test_ordinary_member_cannot_open_dashboard_or_edit_events(tmp_path, monkeypatch):
    database = tmp_path / 'member.db'
    _build_admin_database(database, 'member')
    client = _logged_in_client(database, monkeypatch)

    dashboard = client.get('/admin', follow_redirects=False)
    edit = client.post('/admin/events/edit', data={
        'event_id': '10',
        'title': 'Unauthorized edit',
        'description': 'Should not save',
        'location': 'Nowhere',
        'event_date': '2026-10-03',
    }, follow_redirects=False)

    assert dashboard.status_code == 302
    assert dashboard.headers['Location'].endswith('/members')
    assert edit.status_code == 302
    assert edit.headers['Location'].endswith('/members')
    db = _database_factory(database)()
    event = db.execute('SELECT title, location FROM events WHERE id = 10').fetchone()
    db.close()
    assert event['title'] == 'Original Event'
    assert event['location'] == 'Station 31'

    profile = client.get('/members/profile')
    assert profile.status_code == 200
    assert b'title="Admin Dashboard"' not in profile.data

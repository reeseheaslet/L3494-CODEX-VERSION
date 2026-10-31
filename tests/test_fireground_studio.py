import sqlite3
from pathlib import Path

import pytest

import app as union_app


def _database_factory(path):
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    return connect


def _build_members_database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE members (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL
        );
        INSERT INTO members (id, name, role, status)
        VALUES
            (1, 'Test Member', 'member', 'active'),
            (2, 'Test Family', 'family', 'active');
    """)
    db.commit()
    db.close()


@pytest.fixture
def fireground_client(tmp_path, monkeypatch):
    database = tmp_path / 'fireground.db'
    _build_members_database(database)
    monkeypatch.setattr(union_app, 'get_db', _database_factory(database))
    return union_app.app.test_client()


def _login(client, user_id, role):
    with client.session_transaction() as member_session:
        member_session.update(
            user_id=user_id,
            username='Test User',
            role=role,
            member_role=role,
            status='active',
            user_type='family' if role == 'family' else 'member',
        )


def test_fireground_studio_requires_member_login(fireground_client):
    response = fireground_client.get('/members/fireground-studio/', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')


def test_fireground_studio_has_no_public_static_bypass(fireground_client):
    response = fireground_client.get('/static/fireground-studio/index.html')

    assert response.status_code == 404


def test_fireground_studio_rejects_family_accounts(fireground_client):
    _login(fireground_client, 2, 'family')

    response = fireground_client.get('/members/fireground-studio/', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/family')


def test_member_can_open_studio_and_reviewed_assets(fireground_client):
    _login(fireground_client, 1, 'member')

    entry = fireground_client.get('/members/fireground-studio/')

    assert entry.status_code == 200
    assert b'FIREGROUND <strong>STUDIO</strong>' in entry.data
    assert b'href="/members"' in entry.data
    for filename in union_app.FIREGROUND_STUDIO_FILES - {'index.html'}:
        response = fireground_client.get(f'/members/fireground-studio/{filename}')
        assert response.status_code == 200, filename
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']


@pytest.mark.parametrize('filename', [
    'index.html',
    'server.cjs',
    'qa-review/desktop.png',
    '../app.py',
])
def test_member_cannot_fetch_unreviewed_files(fireground_client, filename):
    _login(fireground_client, 1, 'member')

    response = fireground_client.get(f'/members/fireground-studio/{filename}')

    assert response.status_code == 404


def test_deployed_runtime_is_exactly_the_allowlisted_package():
    runtime = Path(union_app.FIREGROUND_STUDIO_FOLDER)
    deployed_files = {
        str(path.relative_to(runtime)).replace('\\', '/')
        for path in runtime.rglob('*')
        if path.is_file()
    }

    assert deployed_files == union_app.FIREGROUND_STUDIO_FILES


def test_member_portal_links_to_fireground_studio():
    template = Path(union_app.app.root_path, 'templates', 'members.html').read_text()

    assert 'href="/members/fireground-studio/"' in template
    assert '<strong>Fireground Studio</strong>' in template

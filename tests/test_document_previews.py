import hashlib

import pytest

import app as union_app


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Db:
    def __init__(self, docs):
        self.docs = docs

    def execute(self, query, params=()):
        if 'member_documents' not in query:
            return Rows([])
        return Rows([d for d in self.docs if not params or d['id'] == params[0]])

    def close(self):
        pass


@pytest.fixture
def setup_preview(tmp_path, monkeypatch):
    source = tmp_path / 'original.docx'
    source.write_bytes(b'original')
    for name, content in [('preview.pdf', b'pdf'), ('page.png', b'png'), ('text.txt', b'Readable <text>')]:
        (tmp_path / name).write_bytes(content)
    url = '/static/documents/original.docx'
    preview = {'source': str(source), 'sha256': hashlib.sha256(b'original').hexdigest(),
               'pdf': 'preview.pdf', 'pages': ['page.png'], 'text': 'text.txt'}
    docs = [dict(id=3, title='Test document', description=None, url=url, added_by=1,
                 created_at='2026-01-01 00:00:00')]
    monkeypatch.setattr(union_app, 'DOCUMENT_PREVIEWS', {url: preview})
    monkeypatch.setattr(union_app, 'DOCUMENT_PREVIEW_FOLDER', str(tmp_path))
    monkeypatch.setattr(union_app, 'get_db', lambda: Db(docs))
    monkeypatch.setitem(union_app.app.config, 'WTF_CSRF_ENABLED', False)
    return union_app.app.test_client(), docs, source, tmp_path


def member(client, role='member'):
    with client.session_transaction() as session:
        session.update(user_id=1, role=role, username='Test')


def test_member_only_preview_and_assets(setup_preview):
    client, _, _, _ = setup_preview
    assert client.get('/members/documents/3/view').status_code == 302
    assert client.get('/members/documents/3/preview/page.png').status_code == 302
    member(client, 'family')
    assert client.get('/members/documents/3/view').status_code == 302
    assert client.get('/members/documents/3/preview/page.png').status_code == 302
    member(client)
    page = client.get('/members/documents/3/view')
    assert page.status_code == 200
    assert b'View document' in client.get('/members/documents').data
    for label in (b'Download PDF', b'Download editable Word original', b'Read text version',
                  b'Readable &lt;text&gt;', b'Page 1 of 1', b'\xe2\x86\x90 Documents', b'Member Home'):
        assert label in page.data
    assert b'<iframe' not in page.data
    assert page.headers['Cache-Control'] == 'private, no-store'
    asset = client.get('/members/documents/3/preview/page.png')
    assert asset.status_code == 200 and asset.headers['Cache-Control'] == 'private, no-store'
    assert client.get('/members/documents/3/preview/preview.pdf').status_code == 200
    for path in ('text.txt', 'other.png', '%2e%2e%2fother.png'):
        assert client.get('/members/documents/3/preview/' + path).status_code == 404
    assert client.get('/members/documents/999/view').status_code == 404


def test_source_change_and_missing_assets_disable_preview(setup_preview):
    client, _, source, tmp_path = setup_preview
    member(client)
    source.write_bytes(b'changed')
    assert b'View document' not in client.get('/members/documents').data
    assert b'In-site preview is not available' in client.get('/members/documents/3/view').data
    assert client.get('/members/documents/3/preview/page.png').status_code == 404
    source.write_bytes(b'original')
    (tmp_path / 'page.png').unlink()
    assert client.get('/members/documents/3/preview/preview.pdf').status_code == 404
    assert b'In-site preview is not available' in client.get('/members/documents/3/view').data


def test_unknown_and_external_documents_remain_original_links(setup_preview):
    client, docs, _, _ = setup_preview
    member(client)
    for url in ('/static/documents/new.docx', 'https://example.com/file.docx', '/static/documents/file.pdf'):
        docs[0]['url'] = url
        listing = client.get('/members/documents')
        assert url.encode() in listing.data
        assert b'View document' not in listing.data
        assert b'In-site preview is not available' in client.get('/members/documents/3/view').data
        assert client.get('/members/documents/3/preview/page.png').status_code == 404

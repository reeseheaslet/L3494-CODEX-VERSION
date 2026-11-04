from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATE = (ROOT / "templates" / "base.html").read_text()


def test_portal_brand_and_mode_links_are_home_labeled_and_contextual():
    family_brand = (
        "<a href=\"{% if request.path.startswith('/family') %}/family{% else %}/members{% endif %}\" "
        "class=\"app-brand\" aria-label=\"Local 3494 {% if request.path.startswith('/family') %}Family Home{% else %}Member Home{% endif %}\">"
    )
    assert family_brand in BASE_TEMPLATE
    assert 'class="mode-switch__item {% if request.path.startswith(\'/members\') %}is-active{% endif %}">🏠 Member Home' in BASE_TEMPLATE
    assert 'class="mode-switch__item is-muted">🏠 Member Home' in BASE_TEMPLATE
    assert 'class="mode-switch__item {% if request.path.startswith(\'/family\') %}is-active{% endif %}">🏠 Family Home' in BASE_TEMPLATE


def test_portal_mobile_home_navigation_is_preserved():
    assert 'class="app-bottom-nav" aria-label="Member navigation"' in BASE_TEMPLATE
    assert 'class="app-bottom-nav" aria-label="Family navigation"' in BASE_TEMPLATE
    assert '<span>Home</span>' in BASE_TEMPLATE


def test_ios_notification_guidance_matches_the_required_home_screen_flow():
    for portal in ("member", "family"):
        template = (ROOT / "templates" / f"{portal}_get_app.html").read_text()

        steps = (
            "Open <strong>Local 3494</strong> from your home screen.",
            "Return to <strong>Get the App — Notifications</strong>.",
            "Tap the red <strong>Enable Notifications</strong> button.",
            "When iOS asks, tap <strong>Allow</strong>.",
        )
        positions = [template.index(step) for step in steps]
        assert positions == sorted(positions)
        assert 'onclick="requestNotificationPermission()"' in template
        assert "Enable Notifications" in template


def test_notification_permission_requires_explicit_click_and_keeps_granted_refresh():
    assert "setTimeout(requestNotificationPermission, 3000);" not in BASE_TEMPLATE
    assert "Notification.permission === 'granted'" in BASE_TEMPLATE
    for portal in ("member", "family"):
        template = (ROOT / "templates" / f"{portal}_get_app.html").read_text()
        assert 'onclick="requestNotificationPermission()"' in template


def test_return_navigation_renders_context_and_role():
    import app as union_app
    from flask import session

    cases = [
        ('/members/events/42', 'member', '/members/events', 'Events', '/members', 'Member Home'),
        ('/members/documents/union-meeting-minutes', 'member', '/members/documents', 'Documents', '/members', 'Member Home'),
        ('/members/events', 'member', None, None, '/members', 'Member Home'),
        ('/family/discussions/general', 'family', '/family/discussions', 'Discussions', '/family', 'Family Home'),
        ('/family/get-the-app', 'family', None, None, '/family', 'Family Home'),
    ]
    for path, role, parent, label, home, home_label in cases:
        with union_app.app.test_request_context(path):
            session.update(user_id=1, role=role, username='Test')
            html = union_app.app.jinja_env.get_template('base.html').render()
            bar = html.split('<nav class="portal-return-nav', 1)[1].split('</nav>', 1)[0]
            assert f'href="{home}">{home_label}</a>' in bar
            if parent:
                assert f'href="{parent}">← {label}</a>' in bar
            else:
                assert bar.count('<a ') == 1
            if role == 'family':
                assert 'href="/members"' not in bar

    with union_app.app.test_request_context('/members'):
        session.update(user_id=1, role='member', username='Test')
        assert 'portal-return-nav' not in union_app.app.jinja_env.get_template('base.html').render()

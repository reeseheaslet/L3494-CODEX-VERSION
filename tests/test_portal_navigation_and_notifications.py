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

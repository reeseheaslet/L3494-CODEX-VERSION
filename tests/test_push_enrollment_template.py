from pathlib import Path


def test_push_enrollment_waits_for_service_worker_and_checks_register_response():
    template = Path(__file__).resolve().parents[1] / "templates" / "base.html"
    html = template.read_text()

    assert "async function registerPushToken()" in html
    assert "const swReg = await navigator.serviceWorker.ready;" in html
    assert "serviceWorkerRegistration: swReg" in html
    assert "const payload = await response.json().catch(() => ({}));" in html
    assert "if (!response.ok || !payload.success)" in html
    assert "await registerPushToken();" in html
    assert "retryPushTokenRegistration('Notification setup retry failed:');" in html
    assert "retryPushTokenRegistration('Token refresh retry failed:');" in html

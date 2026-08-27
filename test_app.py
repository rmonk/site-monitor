import os
import shutil

try:
    import pytest
except ImportError:
    pytest = None
from pathlib import Path
from fastapi.testclient import TestClient

# Use temporary DB for tests
os.environ["DATA_DIR"] = "/tmp/site_monitor_test_data"
os.environ["INITIAL_ADMIN_USER"] = "testadmin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "testsecret123"

from app.main import app
from app.database import init_db, get_db, get_setting

client = TestClient(app)


def setup_module(module):
    """Initializes the temporary test database environment."""
    test_dir = Path("/tmp/site_monitor_test_data")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)
    init_db()


def test_initial_setup():
    """Verifies default database settings and initial admin user creation."""
    assert get_setting("auth_mode") == "readonly_public"
    assert get_setting("default_capture_screenshots") == "true"
    assert get_setting("theme_mode") == "light"
    assert get_setting("theme_color_preset") == "default"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = 'testadmin'")
        user = cursor.fetchone()
        assert user is not None


def test_public_dashboard_access():
    """Verifies unauthenticated visitors can view public status dashboard."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Site Monitor" in response.text


def test_login_flow():
    """Verifies authentication validation for valid and invalid credentials."""
    # Bad credentials
    res = client.post(
        "/login", data={"username": "testadmin", "password": "wrongpassword"}
    )
    assert res.status_code == 200
    assert "Invalid username or password" in res.text

    # Good credentials
    res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "session_token" in res.cookies


def test_monitor_crud_operations():
    """Verifies complete monitor lifecycle: creation, viewing, check, API and deletion."""
    # Login first
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Create monitor
    create_res = client.post(
        "/monitors/new",
        data={
            "name": "HTTPBin Status",
            "url": "https://httpbin.org/status/200",
            "check_interval": "30",
            "timeout": "5",
            "regex_pattern": "",
            "failure_threshold": "2",
            "repeat_alerts": "1",
            "repeat_interval_minutes": "15",
            "is_active": "1",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert create_res.status_code == 303

    # Check database
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE name = 'HTTPBin Status'")
        monitor = cursor.fetchone()
        assert monitor is not None
        assert monitor["check_interval"] == 30
        assert monitor["failure_threshold"] == 2
        assert monitor["repeat_alerts"] == 1
        assert monitor["repeat_interval_minutes"] == 15
        monitor_id = monitor["id"]

    # 2. View monitor detail
    detail_res = client.get(f"/monitors/{monitor_id}")
    assert detail_res.status_code == 200
    assert "HTTPBin Status" in detail_res.text

    # 3. Trigger check now
    check_res = client.post(
        f"/monitors/{monitor_id}/check",
        cookies={"session_token": session_token},
        follow_redirects=True,
    )
    assert check_res.status_code == 200

    # 4. Check API status
    api_res = client.get("/api/status")
    assert api_res.status_code == 200
    data = api_res.json()
    assert len(data["monitors"]) == 1
    assert data["monitors"][0]["name"] == "HTTPBin Status"

    # 5. Edit monitor
    edit_res = client.post(
        f"/monitors/{monitor_id}/edit",
        data={
            "name": "HTTPBin Status Updated",
            "url": "https://httpbin.org/status/200",
            "check_interval": "60",
            "timeout": "10",
            "regex_pattern": ".*",
            "failure_threshold": "1",
            "repeat_alerts": "default",
            "repeat_interval_minutes": "",
            "is_active": "1",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert edit_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        m_updated = cursor.fetchone()
        assert m_updated["name"] == "HTTPBin Status Updated"
        assert m_updated["repeat_alerts"] is None

    # 6. Delete monitor
    del_res = client.post(
        f"/monitors/{monitor_id}/delete",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert del_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM monitors WHERE id = ?", (monitor_id,)
        )
        assert cursor.fetchone()["count"] == 0


def test_screenshot_route():
    """Verifies that requesting a nonexistent screenshot returns 404 and invalid formats return 400."""
    # Test valid filename format for non-existent file -> 404
    res_404 = client.get("/screenshots/monitor_99999_success.png")
    assert res_404.status_code == 404

    # Test invalid filename format -> 400
    res_400 = client.get("/screenshots/nonexistent.png")
    assert res_400.status_code == 400


def test_screenshot_settings():
    """Verifies updating global screenshot defaults and monitor screenshot override settings."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Verify global default setting update
    client.post(
        "/settings/alerts-default",
        data={
            "default_repeat_alerts": "true",
            "default_repeat_interval_minutes": "60",
            "default_capture_screenshots": "false",
        },
        cookies={"session_token": session_token},
    )
    assert get_setting("default_capture_screenshots") == "false"

    client.post(
        "/settings/alerts-default",
        data={
            "default_repeat_alerts": "true",
            "default_repeat_interval_minutes": "60",
            "default_capture_screenshots": "true",
        },
        cookies={"session_token": session_token},
    )
    assert get_setting("default_capture_screenshots") == "true"

    # 2. Test monitor creation with screenshot setting override (0 = disabled)
    create_res = client.post(
        "/monitors/new",
        data={
            "name": "Screenshot Disabled Host",
            "url": "https://example.com",
            "check_interval": "60",
            "timeout": "10",
            "failure_threshold": "1",
            "capture_screenshots": "0",
            "is_active": "1",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert create_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE name = 'Screenshot Disabled Host'")
        m = cursor.fetchone()
        assert m is not None
        assert m["capture_screenshots"] == 0
        m_id = m["id"]

    # Delete test monitor
    client.post(f"/monitors/{m_id}/delete", cookies={"session_token": session_token})


def test_theme_settings():
    """Verifies theme customization, presets, and HTML dataset attribute rendering."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Update theme mode to dark with emerald preset
    res = client.post(
        "/settings/theme",
        data={
            "theme_mode": "dark",
            "theme_color_preset": "emerald",
            "theme_custom_primary": "#0d6efd",
            "theme_custom_bg": "#f8f9fa",
            "theme_custom_card": "#ffffff",
            "theme_custom_text": "#212529",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert get_setting("theme_mode") == "dark"
    assert get_setting("theme_color_preset") == "emerald"

    # 2. Update theme to custom colors
    res_custom = client.post(
        "/settings/theme",
        data={
            "theme_mode": "system",
            "theme_color_preset": "custom",
            "theme_custom_primary": "#123456",
            "theme_custom_bg": "#abcdef",
            "theme_custom_card": "#ffffff",
            "theme_custom_text": "#111111",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_custom.status_code == 303
    assert get_setting("theme_mode") == "system"
    assert get_setting("theme_color_preset") == "custom"
    assert get_setting("theme_custom_primary") == "#123456"
    assert get_setting("theme_custom_bg") == "#abcdef"

    # 3. Check dashboard renders HTML with data attributes
    dash_res = client.get("/")
    assert dash_res.status_code == 200
    assert 'data-theme-mode="system"' in dash_res.text
    assert 'data-theme-preset="custom"' in dash_res.text
    assert "#123456" in dash_res.text

    # Reset theme back to light default
    client.post(
        "/settings/theme",
        data={"theme_mode": "light", "theme_color_preset": "default"},
        cookies={"session_token": session_token},
    )


def test_require_login_mode():
    """Verifies that require_login mode blocks anonymous dashboard access."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # Switch to require_login
    client.post(
        "/settings/auth",
        data={"auth_mode": "require_login"},
        cookies={"session_token": session_token},
    )

    # Unauthenticated request to dashboard should redirect to login
    # Create fresh testclient with empty cookies
    unauth_client = TestClient(app)
    anon_res = unauth_client.get("/", follow_redirects=False)
    assert anon_res.status_code == 303
    assert "/login" in anon_res.headers["location"]


def test_healthz_endpoint():
    """Verifies the health check /healthz endpoint returns healthy diagnostics."""
    # 1. Healthz endpoint should return 200 and healthy status
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] is True
    assert data["worker_alive"] is True
    assert "heartbeat_age_seconds" in data


def test_heartbeat_settings():
    """Verifies Dead Man's Switch external heartbeat settings update."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # Save heartbeat settings
    res = client.post(
        "/settings/heartbeat",
        data={
            "heartbeat_ping_url": "https://hc-ping.com/12345-test",
            "heartbeat_ping_interval_minutes": "30",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res.status_code == 303

    assert get_setting("heartbeat_ping_url") == "https://hc-ping.com/12345-test"
    assert get_setting("heartbeat_ping_interval_minutes") == "30"


def test_pushover_test_suite_routes():
    """Verifies Pushover configuration and the 3-action test suite endpoints."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Update Pushover settings with priority 2
    res_save = client.post(
        "/settings/pushover",
        data={
            "pushover_enabled": "true",
            "pushover_api_token": "token123",
            "pushover_user_key": "user456",
            "pushover_priority_down": "2",
            "pushover_emergency_retry": "90",
            "pushover_emergency_expire": "1800",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_save.status_code == 303
    assert get_setting("pushover_priority_down") == "2"
    assert get_setting("pushover_emergency_retry") == "90"
    assert get_setting("pushover_emergency_expire") == "1800"

    # 2. Test normal test route
    res_normal = client.post(
        "/settings/pushover/test/normal",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_normal.status_code == 303
    assert "Pushover+normal+test" in res_normal.headers["location"]

    # 3. Test alert test route
    res_alert = client.post(
        "/settings/pushover/test/alert",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_alert.status_code == 303
    assert "Pushover+alert+test" in res_alert.headers["location"]

    # 4. Test recovery test route
    res_rec = client.post(
        "/settings/pushover/test/recovery",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_rec.status_code == 303
    assert "Pushover+recovery+test" in res_rec.headers["location"]

    # Reset
    client.post(
        "/settings/pushover",
        data={
            "pushover_enabled": "false",
            "pushover_api_token": "",
            "pushover_user_key": "",
        },
        cookies={"session_token": session_token},
    )


def test_receipt_sync_and_api_routes():
    """Verifies manual receipt synchronization endpoint and receipt status JSON API."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Create a monitor
    create_res = client.post(
        "/monitors/new",
        data={
            "name": "Sync Test Host",
            "url": "https://example-sync.com",
            "check_interval": "60",
            "timeout": "10",
            "failure_threshold": "1",
            "capture_screenshots": "0",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert create_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM monitors WHERE name = 'Sync Test Host'")
        m_row = cursor.fetchone()
        m_id = m_row["id"]
        cursor.execute(
            "INSERT INTO alert_state (monitor_id, is_currently_down, consecutive_failures, active_receipts) "
            "VALUES (?, 1, 1, 'rcpt_sync_test') "
            "ON CONFLICT(monitor_id) DO UPDATE SET is_currently_down = 1, active_receipts = 'rcpt_sync_test'",
            (m_id,),
        )

    # 2. Test sync-receipt POST route
    sync_res = client.post(
        f"/monitors/{m_id}/sync-receipt",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert sync_res.status_code == 303
    assert f"/monitors/{m_id}" in sync_res.headers["location"]

    # 3. Test receipt JSON API route
    api_res = client.get(
        f"/api/monitors/{m_id}/receipt", cookies={"session_token": session_token}
    )
    assert api_res.status_code == 200
    data = api_res.json()
    assert data["monitor_id"] == m_id
    assert data["is_currently_down"] == 1
    assert data["active_receipts"] == "rcpt_sync_test"


def test_api_status_endpoint():
    """Verifies the /api/status endpoint returns valid JSON monitoring status summary."""
    api_res = client.get("/api/status")
    assert api_res.status_code == 200
    data = api_res.json()
    assert data["status"] == "ok"
    assert "monitors" in data
    assert isinstance(data["monitors"], list)


def test_monitor_detail_screenshots_visibility():
    """Verifies that the Request Screenshots section is omitted when screenshots are disabled."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Create a monitor with screenshots explicitly DISABLED (capture_screenshots=0)
    client.post(
        "/monitors/new",
        data={
            "name": "No Screenshot Monitor",
            "url": "https://example-noscreen.com",
            "check_interval": "60",
            "timeout": "10",
            "failure_threshold": "1",
            "capture_screenshots": "0",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM monitors WHERE name = 'No Screenshot Monitor'")
        m_id = cursor.fetchone()["id"]

    res = client.get(f"/monitors/{m_id}")
    assert res.status_code == 200
    assert "Request Screenshots" not in res.text

    # 2. Update monitor to explicitly ENABLE screenshots (capture_screenshots=1)
    client.post(
        f"/monitors/{m_id}/edit",
        data={
            "name": "No Screenshot Monitor",
            "url": "https://example-noscreen.com",
            "check_interval": "60",
            "timeout": "10",
            "failure_threshold": "1",
            "capture_screenshots": "1",
            "is_active": "1",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )

    res_enabled = client.get(f"/monitors/{m_id}")
    assert res_enabled.status_code == 200
    assert "Request Screenshots" in res_enabled.text


def test_time_display_settings_and_preferences():
    """Verifies service default timestamp setting, per-user cookie toggle, and HTML attributes."""
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]

    # 1. Default should be 'utc'
    assert get_setting("default_time_display") == "utc"

    # 2. Update service default to 'local' via settings form
    res_set = client.post(
        "/settings/theme",
        data={
            "theme_mode": "light",
            "theme_color_preset": "default",
            "default_time_display": "local",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert res_set.status_code == 303
    assert get_setting("default_time_display") == "local"

    # 3. Check page without cookies reflects service default
    res_dash = client.get("/")
    assert res_dash.status_code == 200
    assert 'data-time-display="local"' in res_dash.text
    assert "timeDisplayDropdown" in res_dash.text

    # 4. User preference toggle via /settings/time-display cookie route
    res_toggle = client.post(
        "/settings/time-display",
        data={"time_display": "utc"},
        follow_redirects=False,
    )
    assert res_toggle.status_code == 303
    assert "time_display=utc" in res_toggle.headers.get("set-cookie", "")

    # 5. User with cookie set to 'utc' overrides service default 'local'
    res_override = client.get("/", cookies={"time_display": "utc"})
    assert res_override.status_code == 200
    assert 'data-time-display="utc"' in res_override.text

    # Reset service default back to 'utc'
    client.post(
        "/settings/theme",
        data={
            "theme_mode": "light",
            "theme_color_preset": "default",
            "default_time_display": "utc",
        },
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert get_setting("default_time_display") == "utc"


def test_security_sanitization():
    """Verifies open redirect prevention, path injection protection, and safe error handling."""
    from app.main import resolve_internal_redirect

    # 1. Test resolve_internal_redirect validator against open redirect vectors
    assert resolve_internal_redirect("/settings") == "/settings"
    assert resolve_internal_redirect("/monitors/1") == "/monitors/1"
    assert resolve_internal_redirect("/monitors/42?msg=test") == "/monitors/42"
    assert resolve_internal_redirect("https://attacker.com/evil", default="/") == "/"
    assert (
        resolve_internal_redirect("https://attacker.com", default="/default")
        == "/default"
    )
    assert resolve_internal_redirect("//attacker.com", default="/") == "/"
    assert resolve_internal_redirect(r"/\attacker.com", default="/") == "/"
    assert resolve_internal_redirect(None, default="/") == "/"

    # 2. Test screenshot path traversal protections
    res_traversal = client.get("/screenshots/../../etc/passwd")
    assert res_traversal.status_code in (400, 404)

    res_invalid_name = client.get("/screenshots/arbitrary_file.txt")
    assert res_invalid_name.status_code == 400


def test_passkey_auth_and_management():
    """Verifies Passkey options generation, registration, settings listing, authentication flow, and deletion."""
    from app.database import (
        get_user_by_username,
        get_user_passkeys,
        save_passkey,
        get_passkey_by_credential_id,
        update_passkey_usage,
        delete_passkey,
    )
    from app.auth import (
        generate_passkey_registration_options,
        generate_passkey_authentication_options,
        store_challenge,
        pop_challenge,
    )

    # 1. Login as admin
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies["session_token"]
    user = get_user_by_username("testadmin")
    assert user is not None

    # 2. Test registration options endpoint
    # Unauthenticated should fail (clear cookies in client jar)
    client.cookies.clear()
    unauth_reg = client.post("/api/passkeys/register/options")
    assert unauth_reg.status_code == 401

    # Authenticated should succeed
    auth_reg = client.post(
        "/api/passkeys/register/options",
        cookies={"session_token": session_token},
    )
    assert auth_reg.status_code == 200
    reg_data = auth_reg.json()
    assert "options" in reg_data
    assert "challenge_id" in reg_data
    assert reg_data["options"]["rp"]["name"] == "Site Monitor"

    # 3. Test Passkey DB persistence
    mock_cred_id = "test_cred_id_abc123"
    mock_pub_key = "test_pub_key_xyz789"
    pk_id = save_passkey(
        user_id=user["id"],
        credential_id=mock_cred_id,
        public_key=mock_pub_key,
        sign_count=0,
        name="MacBook Touch ID",
        aaguid="00000000-0000-0000-0000-000000000000",
    )
    assert pk_id is not None

    # Retrieve passkeys
    user_pks = get_user_passkeys(user["id"])
    assert len(user_pks) >= 1
    assert any(pk["credential_id"] == mock_cred_id for pk in user_pks)

    found_pk = get_passkey_by_credential_id(mock_cred_id)
    assert found_pk is not None
    assert found_pk["name"] == "MacBook Touch ID"
    assert found_pk["sign_count"] == 0

    # Update sign count
    update_passkey_usage(mock_cred_id, 5)
    updated_pk = get_passkey_by_credential_id(mock_cred_id)
    assert updated_pk["sign_count"] == 5
    assert updated_pk["last_used_at"] is not None

    # 4. Test Settings page renders Passkeys section
    settings_res = client.get("/settings", cookies={"session_token": session_token})
    assert settings_res.status_code == 200
    assert (
        "Passkeys &amp; Biometrics" in settings_res.text
        or "Passkeys & Biometrics" in settings_res.text
    )
    assert "MacBook Touch ID" in settings_res.text

    # 5. Test Login page renders Passkey button
    login_page_res = client.get("/login")
    assert login_page_res.status_code == 200
    assert "Sign in with Passkey" in login_page_res.text

    # 6. Test Authentication Options endpoint
    auth_opt_res = client.post("/api/auth/passkey/options", json={})
    assert auth_opt_res.status_code == 200
    auth_opt_data = auth_opt_res.json()
    assert "options" in auth_opt_data
    assert "challenge_id" in auth_opt_data

    # User-specific authentication options
    auth_opt_user_res = client.post(
        "/api/auth/passkey/options", json={"username": "testadmin"}
    )
    assert auth_opt_user_res.status_code == 200
    auth_opt_user_data = auth_opt_user_res.json()
    assert "options" in auth_opt_user_data
    assert "challenge_id" in auth_opt_user_data

    # 7. Test Passkey Verify endpoint error handling
    bad_verify = client.post(
        "/api/auth/passkey/verify",
        json={"response": {}, "challenge_id": "invalid_challenge"},
    )
    assert bad_verify.status_code == 400

    # 8. Test Secure Cookie generation on HTTPS requests
    https_login = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert https_login.status_code == 303
    set_cookie_header = https_login.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie_header

    # 9. Test Passkey deletion
    del_res = client.post(
        f"/settings/passkeys/{pk_id}/delete",
        cookies={"session_token": session_token},
        follow_redirects=False,
    )
    assert del_res.status_code == 303
    assert get_passkey_by_credential_id(mock_cred_id) is None


def test_uptime_calculation_and_periods():
    """Verifies that uptime percentage calculates accurately over time windows and is not stuck at 100%."""
    from datetime import datetime, timezone, timedelta
    from app.database import get_uptime_statistics, canonicalize_period

    # 1. Login as admin
    login_res = client.post(
        "/login",
        data={"username": "testadmin", "password": "testsecret123"},
        follow_redirects=False,
    )
    session_token = login_res.cookies.get("session_token")

    # 2. Create a monitor for uptime test
    create_res = client.post(
        "/monitors/new",
        data={
            "name": "Historical Uptime Host",
            "url": "https://example-uptime-test.org",
            "check_interval": "60",
            "timeout": "5",
            "failure_threshold": "1",
            "capture_screenshots": "0",
        },
        cookies={"session_token": session_token} if session_token else {},
        follow_redirects=False,
    )
    assert create_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM monitors WHERE name = 'Historical Uptime Host'")
        m_id = cursor.fetchone()["id"]

        # Clear existing check_history to test exact counts
        cursor.execute("DELETE FROM check_history")

        now = datetime.now(timezone.utc)

        # 3 checks within last 30 minutes (2 UP, 1 DOWN) -> 1h window uptime = 66.7%
        ts_10m = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        ts_20m = (now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
        ts_30m = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 1, 200, ?)",
            (m_id, ts_10m),
        )
        cursor.execute(
            "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 1, 200, ?)",
            (m_id, ts_20m),
        )
        cursor.execute(
            "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 0, 500, ?)",
            (m_id, ts_30m),
        )

        # 1 check 12 hours ago (1 UP) -> 24h window: 3 UP, 1 DOWN out of 4 = 75.0%
        ts_12h = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 1, 200, ?)",
            (m_id, ts_12h),
        )

        # 1 check 3 days ago (1 UP) -> 7d window: 4 UP, 1 DOWN out of 5 = 80.0%
        ts_3d = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 1, 200, ?)",
            (m_id, ts_3d),
        )

        # 5 checks 15 days ago (5 UP) -> 30d / all window: 9 UP, 1 DOWN out of 10 = 90.0%
        ts_15d = (now - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
        for _ in range(5):
            cursor.execute(
                "INSERT INTO check_history (monitor_id, is_up, status_code, timestamp) VALUES (?, 1, 200, ?)",
                (m_id, ts_15d),
            )

    # 3. Test python calculation helper directly across periods
    pct_1h, stats_1h = get_uptime_statistics("1h")
    assert pct_1h == 66.7
    assert stats_1h[m_id]["total_checks"] == 3
    assert stats_1h[m_id]["up_checks"] == 2
    assert stats_1h[m_id]["uptime_pct"] == 66.7

    pct_24h, stats_24h = get_uptime_statistics("24h")
    assert pct_24h == 75.0
    assert stats_24h[m_id]["total_checks"] == 4
    assert stats_24h[m_id]["uptime_pct"] == 75.0

    pct_7d, stats_7d = get_uptime_statistics("7d")
    assert pct_7d == 80.0
    assert stats_7d[m_id]["total_checks"] == 5

    pct_30d, stats_30d = get_uptime_statistics("30d")
    assert pct_30d == 90.0
    assert stats_30d[m_id]["total_checks"] == 10

    pct_all, stats_all = get_uptime_statistics("all")
    assert pct_all == 90.0

    # 4. Test Dashboard HTTP endpoint with period queries
    dash_1h = client.get(
        "/?period=1h",
        cookies={"session_token": session_token} if session_token else {},
    )
    assert dash_1h.status_code == 200
    assert "66.7%" in dash_1h.text
    assert "1 Hour" in dash_1h.text
    assert "Uptime Time Window" in dash_1h.text
    assert "Uptime (1 Hour)" in dash_1h.text

    dash_24h = client.get(
        "/?period=24h",
        cookies={"session_token": session_token} if session_token else {},
    )
    assert dash_24h.status_code == 200
    assert "75.0%" in dash_24h.text
    assert "Uptime (1 Day)" in dash_24h.text

    dash_7d = client.get(
        "/?period=7d",
        cookies={"session_token": session_token} if session_token else {},
    )
    assert dash_7d.status_code == 200
    assert "80.0%" in dash_7d.text

    dash_30d = client.get(
        "/?period=30d",
        cookies={"session_token": session_token} if session_token else {},
    )
    assert dash_30d.status_code == 200
    assert "90.0%" in dash_30d.text

    # 5. Test JSON status API with period parameter
    api_res = client.get(
        "/api/status?period=1h",
        cookies={"session_token": session_token} if session_token else {},
    )
    assert api_res.status_code == 200
    api_data = api_res.json()
    assert api_data["overall_uptime_pct"] == 66.7
    assert api_data["period"] == "1h"
    assert any(
        m["name"] == "Historical Uptime Host" and m["uptime_pct"] == 66.7
        for m in api_data["monitors"]
    )


if __name__ == "__main__":
    setup_module(None)
    test_initial_setup()
    test_public_dashboard_access()
    test_login_flow()
    test_monitor_crud_operations()
    test_screenshot_route()
    test_screenshot_settings()
    test_theme_settings()
    test_require_login_mode()
    test_healthz_endpoint()
    test_heartbeat_settings()
    test_pushover_test_suite_routes()
    test_receipt_sync_and_api_routes()
    test_api_status_endpoint()
    test_monitor_detail_screenshots_visibility()
    test_time_display_settings_and_preferences()
    test_security_sanitization()
    test_passkey_auth_and_management()
    test_uptime_calculation_and_periods()
    print("ALL test_app.py tests passed successfully!")

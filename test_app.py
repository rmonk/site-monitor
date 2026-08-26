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
    test_dir = Path("/tmp/site_monitor_test_data")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)
    init_db()


def test_initial_setup():
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
    response = client.get("/")
    assert response.status_code == 200
    assert "Site Monitor" in response.text


def test_login_flow():
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
    # Test non-existent file
    res_404 = client.get("/screenshots/nonexistent.png")
    assert res_404.status_code == 404


def test_screenshot_settings():
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
    # 1. Healthz endpoint should return 200 and healthy status
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] is True
    assert data["worker_alive"] is True
    assert "heartbeat_age_seconds" in data


def test_heartbeat_settings():
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
            "UPDATE alert_state SET is_currently_down = 1, active_receipts = 'rcpt_sync_test' WHERE monitor_id = ?",
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
    api_res = client.get(f"/api/monitors/{m_id}/receipt")
    assert api_res.status_code == 200
    data = api_res.json()
    assert data["monitor_id"] == m_id
    assert data["is_currently_down"] == 1
    assert data["active_receipts"] == "rcpt_sync_test"


def test_api_status_endpoint():
    api_res = client.get("/api/status")
    assert api_res.status_code == 200
    data = api_res.json()
    assert data["status"] == "ok"
    assert "monitors" in data
    assert isinstance(data["monitors"], list)


def test_monitor_detail_screenshots_visibility():
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


if __name__ == "__main__":
    setup_module(None)
    test_initial_setup()
    test_public_dashboard_access()
    test_login_flow()
    test_monitor_lifecycle()
    test_check_now_flow()
    test_settings_update()
    test_require_login_mode()
    test_healthz_endpoint()
    test_heartbeat_settings()
    test_pushover_test_suite_routes()
    test_receipt_sync_and_api_routes()
    test_api_status_endpoint()
    test_monitor_detail_screenshots_visibility()
    print("ALL test_app.py tests passed successfully!")

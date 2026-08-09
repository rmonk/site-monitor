import os
import shutil
import pytest
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
    res = client.post("/login", data={"username": "testadmin", "password": "wrongpassword"})
    assert res.status_code == 200
    assert "Invalid username or password" in res.text

    # Good credentials
    res = client.post("/login", data={"username": "testadmin", "password": "testsecret123"}, follow_redirects=False)
    assert res.status_code == 303
    assert "session_token" in res.cookies

def test_monitor_crud_operations():
    # Login first
    login_res = client.post("/login", data={"username": "testadmin", "password": "testsecret123"}, follow_redirects=False)
    session_token = login_res.cookies["session_token"]

    # 1. Create monitor
    create_res = client.post("/monitors/new", data={
        "name": "HTTPBin Status",
        "url": "https://httpbin.org/status/200",
        "check_interval": "30",
        "timeout": "5",
        "regex_pattern": "",
        "failure_threshold": "2",
        "repeat_alerts": "1",
        "repeat_interval_minutes": "15",
        "is_active": "1"
    }, cookies={"session_token": session_token}, follow_redirects=False)
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
    check_res = client.post(f"/monitors/{monitor_id}/check", cookies={"session_token": session_token}, follow_redirects=True)
    assert check_res.status_code == 200

    # 4. Check API status
    api_res = client.get("/api/status")
    assert api_res.status_code == 200
    data = api_res.json()
    assert len(data["monitors"]) == 1
    assert data["monitors"][0]["name"] == "HTTPBin Status"

    # 5. Edit monitor
    edit_res = client.post(f"/monitors/{monitor_id}/edit", data={
        "name": "HTTPBin Status Updated",
        "url": "https://httpbin.org/status/200",
        "check_interval": "60",
        "timeout": "10",
        "regex_pattern": ".*",
        "failure_threshold": "1",
        "repeat_alerts": "default",
        "repeat_interval_minutes": "",
        "is_active": "1"
    }, cookies={"session_token": session_token}, follow_redirects=False)
    assert edit_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        m_updated = cursor.fetchone()
        assert m_updated["name"] == "HTTPBin Status Updated"
        assert m_updated["repeat_alerts"] is None

    # 6. Delete monitor
    del_res = client.post(f"/monitors/{monitor_id}/delete", cookies={"session_token": session_token}, follow_redirects=False)
    assert del_res.status_code == 303

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM monitors WHERE id = ?", (monitor_id,))
        assert cursor.fetchone()["count"] == 0

def test_require_login_mode():
    login_res = client.post("/login", data={"username": "testadmin", "password": "testsecret123"}, follow_redirects=False)
    session_token = login_res.cookies["session_token"]

    # Switch to require_login
    client.post("/settings/auth", data={"auth_mode": "require_login"}, cookies={"session_token": session_token})

    # Unauthenticated request to dashboard should redirect to login
    # Create fresh testclient with empty cookies
    unauth_client = TestClient(app)
    anon_res = unauth_client.get("/", follow_redirects=False)
    assert anon_res.status_code == 303
    assert "/login" in anon_res.headers["location"]

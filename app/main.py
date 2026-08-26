import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException, status, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, HOST, PORT
from app.database import (
    init_db,
    get_db,
    get_setting,
    set_setting,
    get_all_settings,
    get_setting_bool,
    get_setting_int,
    format_utc_timestamp,
)
from app.auth import (
    hash_password,
    verify_password,
    create_session,
    remove_session,
    get_current_user,
    is_authenticated,
    check_access,
)
from app.alerts import (
    send_test_alert,
    send_normal_test_alert,
    send_down_test_alert,
    send_recovery_test_alert,
    close_alert_client,
)
from app.monitor import (
    check_monitor,
    monitoring_worker_loop,
    watchdog_worker_loop,
    get_worker_heartbeat,
    sync_monitor_receipt_status,
)
from app.screenshots import get_screenshots_dir
from fastapi.responses import FileResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    init_db()
    task_holder: Dict[str, Any] = {}
    task_holder["worker_task"] = asyncio.create_task(monitoring_worker_loop())
    task_holder["watchdog_task"] = asyncio.create_task(
        watchdog_worker_loop(task_holder)
    )
    yield
    # Shutdown logic
    if task_holder.get("worker_task"):
        task_holder["worker_task"].cancel()
    if task_holder.get("watchdog_task"):
        task_holder["watchdog_task"].cancel()
    await close_alert_client()


app = FastAPI(title="Site Monitor", docs_url=None, redoc_url=None, lifespan=lifespan)

# Mount Static and Templates
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def hex_to_rgb(hex_str: str) -> str:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 6:
        try:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return f"{r}, {g}, {b}"
        except ValueError:
            pass
    return "13, 110, 253"


def get_template_context(
    request: Request, active_page: str = "", msg: str = "", msg_type: str = "info"
) -> dict:
    user = get_current_user(request)
    auth_mode = get_setting("auth_mode", "readonly_public")

    # Theme settings
    theme_mode = get_setting("theme_mode", "light")
    theme_color_preset = get_setting("theme_color_preset", "default")
    theme_custom_primary = get_setting("theme_custom_primary", "#0d6efd")
    theme_custom_bg = get_setting("theme_custom_bg", "#f8f9fa")
    theme_custom_card = get_setting("theme_custom_card", "#ffffff")
    theme_custom_text = get_setting("theme_custom_text", "#212529")

    # Query param messages
    if not msg and "msg" in request.query_params:
        msg = request.query_params.get("msg")
        msg_type = request.query_params.get("type", "info")

    return {
        "user": user,
        "auth_mode": auth_mode,
        "active_page": active_page,
        "msg": msg,
        "msg_type": msg_type,
        "current_year": datetime.now().year,
        "theme_mode": theme_mode,
        "theme_color_preset": theme_color_preset,
        "theme_custom_primary": theme_custom_primary,
        "theme_custom_primary_rgb": hex_to_rgb(theme_custom_primary),
        "theme_custom_bg": theme_custom_bg,
        "theme_custom_card": theme_custom_card,
        "theme_custom_text": theme_custom_text,
    }


# --- Routes ---


@app.get("/healthz")
async def healthz():
    """
    Service healthcheck endpoint for Docker / monitoring orchestrators.
    Returns HTTP 200 if healthy, HTTP 503 if worker is stalled or database unreachable.
    """
    db_ok = False
    active_monitors_count = 0
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM monitors WHERE is_active = 1")
            row = cursor.fetchone()
            active_monitors_count = row["count"] if row else 0
            db_ok = True
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": False, "error": str(e)},
        )

    heartbeat = get_worker_heartbeat()
    heartbeat_age = round(time.time() - heartbeat, 1)

    # If active monitors exist, worker heartbeat should not exceed 180s
    worker_ok = True
    if active_monitors_count > 0 and heartbeat_age > 180.0:
        worker_ok = False

    if not worker_ok:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": True,
                "worker_alive": False,
                "heartbeat_age_seconds": heartbeat_age,
                "active_monitors": active_monitors_count,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "healthy",
            "database": True,
            "worker_alive": True,
            "heartbeat_age_seconds": heartbeat_age,
            "active_monitors": active_monitors_count,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    auth_mode = get_setting("auth_mode", "readonly_public")
    user = get_current_user(request)

    if auth_mode == "require_login" and not user:
        return RedirectResponse(
            url="/login?msg=Please+login+to+access+dashboard&type=warning",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors ORDER BY name ASC")
        monitors_raw = [dict(m) for m in cursor.fetchall()]

        # Bulk fetch all alert states
        cursor.execute("SELECT * FROM alert_state")
        alert_states = {row["monitor_id"]: dict(row) for row in cursor.fetchall()}

        # Bulk fetch latest check history for each monitor in a single query
        cursor.execute("""
            SELECT ch.monitor_id, ch.is_up, ch.response_time_ms, ch.timestamp
            FROM check_history ch
            INNER JOIN (
                SELECT monitor_id, MAX(id) AS max_id
                FROM check_history
                GROUP BY monitor_id
            ) latest ON ch.id = latest.max_id
        """)
        latest_checks = {row["monitor_id"]: dict(row) for row in cursor.fetchall()}

    monitors = []
    up_count = 0
    down_count = 0

    for m_dict in monitors_raw:
        m_id = m_dict["id"]
        latest_check = latest_checks.get(m_id)
        a_state = alert_states.get(m_id)

        if a_state and a_state["is_currently_down"] == 1:
            m_dict["last_is_up"] = 0
            down_count += 1
        elif latest_check:
            m_dict["last_is_up"] = latest_check["is_up"]
            if latest_check["is_up"] == 1:
                up_count += 1
            else:
                down_count += 1
        else:
            m_dict["last_is_up"] = None

        m_dict["last_response_time_ms"] = (
            latest_check["response_time_ms"] if latest_check else None
        )
        m_dict["last_check_time"] = latest_check["timestamp"] if latest_check else None

        # Pushover Receipt & Acknowledgment details
        m_dict["is_currently_down"] = a_state["is_currently_down"] if a_state else 0
        m_dict["active_receipts"] = (
            a_state["active_receipts"]
            if a_state and a_state.get("active_receipts")
            else ""
        )
        m_dict["receipt_acknowledged"] = (
            a_state["receipt_acknowledged"] if a_state else 0
        )
        m_dict["receipt_acknowledged_by"] = (
            a_state["receipt_acknowledged_by"] if a_state else ""
        )
        m_dict["receipt_acknowledged_device"] = (
            a_state["receipt_acknowledged_device"] if a_state else ""
        )
        m_dict["receipt_acknowledged_at_formatted"] = format_utc_timestamp(
            a_state.get("receipt_acknowledged_at") if a_state else 0
        )

        monitors.append(m_dict)

    total_count = len(monitors)
    uptime_pct = round((up_count / total_count * 100), 1) if total_count > 0 else 100.0

    default_repeat = get_setting_bool("default_repeat_alerts", True)
    default_repeat_interval = get_setting("default_repeat_interval_minutes", "60")

    ctx = get_template_context(request, active_page="dashboard")
    ctx.update(
        {
            "monitors": monitors,
            "total_count": total_count,
            "up_count": up_count,
            "down_count": down_count,
            "uptime_pct": uptime_pct,
            "default_repeat_alerts": default_repeat,
            "default_repeat_interval": default_repeat_interval,
        }
    )
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    """Serves captured monitor screenshot PNG images."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = get_screenshots_dir() / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return FileResponse(filepath, media_type="image/png")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    ctx = get_template_context(request, active_page="login")
    return templates.TemplateResponse(request, "login.html", ctx)


@app.post("/login")
async def login_post(request: Request):
    form_data = await request.form()
    username = str(form_data.get("username", "")).strip()
    password = str(form_data.get("password", ""))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user_row = cursor.fetchone()

    if user_row and verify_password(password, user_row["password_hash"]):
        session_token = create_session(username)
        response = RedirectResponse(
            url="/?msg=Successfully+logged+in&type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(
            key="session_token", value=session_token, httponly=True, max_age=86400 * 30
        )
        return response

    ctx = get_template_context(
        request,
        active_page="login",
        msg="Invalid username or password",
        msg_type="danger",
    )
    return templates.TemplateResponse(request, "login.html", ctx)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        remove_session(token)
    response = RedirectResponse(
        url="/login?msg=Logged+out+successfully&type=info",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(key="session_token")
    return response


@app.get("/monitors/new", response_class=HTMLResponse)
async def monitor_new_page(request: Request):
    check_access(request, require_write=True)
    ctx = get_template_context(request, active_page="monitors_new")
    ctx["monitor"] = None
    return templates.TemplateResponse(request, "monitor_form.html", ctx)


@app.post("/monitors/new")
async def monitor_new_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()

    name = str(form_data.get("name", "")).strip()
    url = str(form_data.get("url", "")).strip()
    check_interval = int(form_data.get("check_interval", 60))
    timeout = int(form_data.get("timeout", 10))
    regex_pattern = str(form_data.get("regex_pattern", "")).strip() or None
    failure_threshold = int(form_data.get("failure_threshold", 1))

    repeat_val = form_data.get("repeat_alerts")
    repeat_alerts = 1 if repeat_val == "1" else (0 if repeat_val == "0" else None)

    rep_int_val = str(form_data.get("repeat_interval_minutes", "")).strip()
    repeat_interval_minutes = int(rep_int_val) if rep_int_val else None

    capture_val = form_data.get("capture_screenshots")
    capture_screenshots = (
        1 if capture_val == "1" else (0 if capture_val == "0" else None)
    )

    is_active = 1 if form_data.get("is_active") == "1" else 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO monitors (name, url, check_interval, timeout, regex_pattern, failure_threshold, repeat_alerts, repeat_interval_minutes, capture_screenshots, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                url,
                check_interval,
                timeout,
                regex_pattern,
                failure_threshold,
                repeat_alerts,
                repeat_interval_minutes,
                capture_screenshots,
                is_active,
            ),
        )

    return RedirectResponse(
        url="/?msg=Monitor+created+successfully&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/monitors/{monitor_id}", response_class=HTMLResponse)
async def monitor_detail(request: Request, monitor_id: int):
    """Renders the detailed status, response time graph, screenshots, and check history for a monitor."""
    auth_mode = get_setting("auth_mode", "readonly_public")
    user = get_current_user(request)

    if auth_mode == "require_login" and not user:
        return RedirectResponse(
            url="/login?msg=Please+login+first&type=warning",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        m_row = cursor.fetchone()
        if not m_row:
            raise HTTPException(status_code=404, detail="Monitor not found")

        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        a_state = cursor.fetchone()

        cursor.execute(
            """
            SELECT * FROM check_history WHERE monitor_id = ? ORDER BY id DESC LIMIT 50
        """,
            (monitor_id,),
        )
        history = [dict(r) for r in cursor.fetchall()]

    default_repeat = get_setting_bool("default_repeat_alerts", True)
    default_repeat_interval = get_setting("default_repeat_interval_minutes", "60")
    default_capture_screenshots = get_setting_bool("default_capture_screenshots", True)

    if m_row["capture_screenshots"] is not None:
        screenshots_enabled = bool(m_row["capture_screenshots"])
    else:
        screenshots_enabled = default_capture_screenshots

    a_dict = dict(a_state) if a_state else None
    if a_dict:
        a_dict["receipt_acknowledged_at_formatted"] = format_utc_timestamp(
            a_dict.get("receipt_acknowledged_at", 0)
        )

    ctx = get_template_context(request)
    ctx.update(
        {
            "monitor": dict(m_row),
            "alert_state": a_dict,
            "history": history,
            "default_repeat_alerts": default_repeat,
            "default_repeat_interval": default_repeat_interval,
            "default_capture_screenshots": default_capture_screenshots,
            "screenshots_enabled": screenshots_enabled,
        }
    )
    return templates.TemplateResponse(request, "monitor_detail.html", ctx)


@app.get("/monitors/{monitor_id}/edit", response_class=HTMLResponse)
async def monitor_edit_page(request: Request, monitor_id: int):
    check_access(request, require_write=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        m_row = cursor.fetchone()
        if not m_row:
            raise HTTPException(status_code=404, detail="Monitor not found")

    ctx = get_template_context(request)
    ctx["monitor"] = dict(m_row)
    return templates.TemplateResponse(request, "monitor_form.html", ctx)


@app.post("/monitors/{monitor_id}/edit")
async def monitor_edit_post(request: Request, monitor_id: int):
    check_access(request, require_write=True)
    form_data = await request.form()

    name = str(form_data.get("name", "")).strip()
    url = str(form_data.get("url", "")).strip()
    check_interval = int(form_data.get("check_interval", 60))
    timeout = int(form_data.get("timeout", 10))
    regex_pattern = str(form_data.get("regex_pattern", "")).strip() or None
    failure_threshold = int(form_data.get("failure_threshold", 1))

    repeat_val = form_data.get("repeat_alerts")
    repeat_alerts = 1 if repeat_val == "1" else (0 if repeat_val == "0" else None)

    rep_int_val = str(form_data.get("repeat_interval_minutes", "")).strip()
    repeat_interval_minutes = int(rep_int_val) if rep_int_val else None

    capture_val = form_data.get("capture_screenshots")
    capture_screenshots = (
        1 if capture_val == "1" else (0 if capture_val == "0" else None)
    )

    is_active = 1 if form_data.get("is_active") == "1" else 0

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE monitors
            SET name = ?, url = ?, check_interval = ?, timeout = ?, regex_pattern = ?,
                failure_threshold = ?, repeat_alerts = ?, repeat_interval_minutes = ?, capture_screenshots = ?, is_active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """,
            (
                name,
                url,
                check_interval,
                timeout,
                regex_pattern,
                failure_threshold,
                repeat_alerts,
                repeat_interval_minutes,
                capture_screenshots,
                is_active,
                monitor_id,
            ),
        )

    return RedirectResponse(
        url=f"/monitors/{monitor_id}?msg=Monitor+updated+successfully&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/monitors/{monitor_id}/delete")
async def monitor_delete(request: Request, monitor_id: int):
    check_access(request, require_write=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))

    return RedirectResponse(
        url="/?msg=Monitor+deleted&type=info", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/monitors/{monitor_id}/check")
async def monitor_check_now(request: Request, monitor_id: int):
    check_access(request, require_write=True)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        m_row = cursor.fetchone()
        if not m_row:
            raise HTTPException(status_code=404, detail="Monitor not found")

    result = await check_monitor(dict(m_row), is_manual=True)
    status_msg = (
        "UP" if result["is_up"] else f"DOWN ({result['error_message'] or 'Failed'})"
    )

    # Redirect back to referring page or monitor detail
    referer = request.headers.get("referer", f"/monitors/{monitor_id}")
    msg_type = "success" if result["is_up"] else "danger"
    return RedirectResponse(
        url=f"{referer}?msg=Check+completed:+{status_msg}&type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/monitors/{monitor_id}/sync-receipt")
async def monitor_sync_receipt_post(request: Request, monitor_id: int):
    check_access(request, require_write=False)
    status_res = await sync_monitor_receipt_status(monitor_id)
    referer = request.headers.get("referer", f"/monitors/{monitor_id}")
    clean_referer = referer.split("?")[0]

    if status_res:
        if status_res.get("acknowledged"):
            device = status_res.get("acknowledged_by_device") or "user device"
            user = status_res.get("acknowledged_by") or "user"
            msg = f"Receipt+synchronized:+Acknowledged+on+{device}+(User:+{user})"
            msg_type = "success"
        else:
            msg = "Receipt+synchronized:+Still+pending+acknowledgment"
            msg_type = "warning"
    else:
        msg = "No+active+receipt+found+or+Pushover+query+failed"
        msg_type = "info"

    return RedirectResponse(
        url=f"{clean_referer}?msg={msg}&type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/api/monitors/{monitor_id}/receipt")
async def monitor_receipt_api(request: Request, monitor_id: int):
    status_res = await sync_monitor_receipt_status(monitor_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alert_state WHERE monitor_id = ?", (monitor_id,))
        a_state = cursor.fetchone()

    if not a_state:
        raise HTTPException(status_code=404, detail="Alert state not found")

    ack_at_ts = a_state["receipt_acknowledged_at"]
    formatted_ts = (
        datetime.fromtimestamp(ack_at_ts, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        if (ack_at_ts and ack_at_ts > 0)
        else ""
    )

    return {
        "monitor_id": monitor_id,
        "is_currently_down": a_state["is_currently_down"],
        "active_receipts": a_state["active_receipts"],
        "receipt_acknowledged": bool(a_state["receipt_acknowledged"]),
        "receipt_acknowledged_at": a_state["receipt_acknowledged_at"],
        "receipt_acknowledged_at_formatted": formatted_ts,
        "receipt_acknowledged_by": a_state["receipt_acknowledged_by"],
        "receipt_acknowledged_device": a_state["receipt_acknowledged_device"],
        "receipt_last_checked": a_state["receipt_last_checked"],
        "live_status": status_res,
    }


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    check_access(request, require_write=True)
    all_settings = get_all_settings()
    ctx = get_template_context(request, active_page="settings")
    ctx["settings"] = all_settings
    return templates.TemplateResponse(request, "settings.html", ctx)


@app.post("/settings/auth")
async def settings_auth_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()
    auth_mode = str(form_data.get("auth_mode", "readonly_public"))
    if auth_mode in ("readonly_public", "require_login"):
        set_setting("auth_mode", auth_mode)
    return RedirectResponse(
        url="/settings?msg=Authentication+access+mode+updated&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/theme")
async def settings_theme_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()
    theme_mode = str(form_data.get("theme_mode", "light")).strip()
    theme_color_preset = str(form_data.get("theme_color_preset", "default")).strip()
    theme_custom_primary = str(form_data.get("theme_custom_primary", "#0d6efd")).strip()
    theme_custom_bg = str(form_data.get("theme_custom_bg", "#f8f9fa")).strip()
    theme_custom_card = str(form_data.get("theme_custom_card", "#ffffff")).strip()
    theme_custom_text = str(form_data.get("theme_custom_text", "#212529")).strip()

    if theme_mode in ("light", "dark", "system"):
        set_setting("theme_mode", theme_mode)
    if theme_color_preset in (
        "default",
        "emerald",
        "purple",
        "amber",
        "crimson",
        "slate",
        "custom",
    ):
        set_setting("theme_color_preset", theme_color_preset)

    if theme_custom_primary.startswith("#") and len(theme_custom_primary) in (4, 7):
        set_setting("theme_custom_primary", theme_custom_primary)
    if theme_custom_bg.startswith("#") and len(theme_custom_bg) in (4, 7):
        set_setting("theme_custom_bg", theme_custom_bg)
    if theme_custom_card.startswith("#") and len(theme_custom_card) in (4, 7):
        set_setting("theme_custom_card", theme_custom_card)
    if theme_custom_text.startswith("#") and len(theme_custom_text) in (4, 7):
        set_setting("theme_custom_text", theme_custom_text)

    # If request came from quick toggle, redirect to referer if present
    referer = request.headers.get("referer")
    target_url = "/settings?msg=Theme+settings+updated&type=success"
    if "quick_toggle" in form_data and referer:
        target_url = f"{referer}?msg=Theme+updated&type=success"

    return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


@app.post("/settings/alerts-default")
async def settings_alerts_default_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()
    default_repeat = (
        "true" if form_data.get("default_repeat_alerts") == "true" else "false"
    )
    repeat_interval = str(
        form_data.get("default_repeat_interval_minutes", "60")
    ).strip()
    default_screenshots = (
        "true" if form_data.get("default_capture_screenshots") == "true" else "false"
    )

    set_setting("default_repeat_alerts", default_repeat)
    set_setting("default_repeat_interval_minutes", repeat_interval)
    set_setting("default_capture_screenshots", default_screenshots)

    return RedirectResponse(
        url="/settings?msg=Global+monitoring+defaults+updated&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/heartbeat")
async def settings_heartbeat_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()
    ping_url = str(form_data.get("heartbeat_ping_url", "")).strip()
    ping_interval = str(form_data.get("heartbeat_ping_interval_minutes", "15")).strip()

    set_setting("heartbeat_ping_url", ping_url)
    set_setting("heartbeat_ping_interval_minutes", ping_interval)

    return RedirectResponse(
        url="/settings?msg=Heartbeat+configuration+saved&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/pushover")
async def settings_pushover_post(request: Request):
    check_access(request, require_write=True)
    form_data = await request.form()
    enabled = "true" if form_data.get("pushover_enabled") == "true" else "false"
    token = str(form_data.get("pushover_api_token", "")).strip()
    user_key = str(form_data.get("pushover_user_key", "")).strip()
    priority_down = str(form_data.get("pushover_priority_down", "2")).strip()
    retry_secs = str(form_data.get("pushover_emergency_retry", "60")).strip()
    expire_secs = str(form_data.get("pushover_emergency_expire", "3600")).strip()

    set_setting("pushover_enabled", enabled)
    set_setting("pushover_api_token", token)
    set_setting("pushover_user_key", user_key)
    set_setting("pushover_priority_down", priority_down)
    set_setting("pushover_emergency_retry", retry_secs)
    set_setting("pushover_emergency_expire", expire_secs)

    return RedirectResponse(
        url="/settings?msg=Pushover+configuration+saved&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/pushover/test/normal")
async def settings_pushover_test_normal_post(request: Request):
    check_access(request, require_write=True)
    success, msg = await send_normal_test_alert()
    msg_type = "success" if success else "danger"
    return RedirectResponse(
        url=f"/settings?msg=Pushover+normal+test:+{msg}&type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/pushover/test/alert")
async def settings_pushover_test_alert_post(request: Request):
    check_access(request, require_write=True)
    success, msg, receipt = await send_down_test_alert()
    msg_type = "success" if success else "danger"
    return RedirectResponse(
        url=f"/settings?msg=Pushover+alert+test:+{msg}&type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/pushover/test/recovery")
async def settings_pushover_test_recovery_post(request: Request):
    check_access(request, require_write=True)
    success, msg, cancelled_receipt = await send_recovery_test_alert()
    msg_type = "success" if success else "danger"
    return RedirectResponse(
        url=f"/settings?msg=Pushover+recovery+test:+{msg}&type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/pushover/test")
async def settings_pushover_test_post(request: Request):
    return await settings_pushover_test_normal_post(request)


@app.post("/settings/password")
async def settings_password_post(request: Request):
    username = check_access(request, require_write=True)
    form_data = await request.form()
    old_password = str(form_data.get("old_password", ""))
    new_password = str(form_data.get("new_password", ""))
    confirm_password = str(form_data.get("confirm_password", ""))

    if new_password != confirm_password:
        return RedirectResponse(
            url="/settings?msg=New+passwords+do+not+match&type=danger",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user_row = cursor.fetchone()

        if not user_row or not verify_password(old_password, user_row["password_hash"]):
            return RedirectResponse(
                url="/settings?msg=Incorrect+current+password&type=danger",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        new_hash = hash_password(new_password)
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (new_hash, username),
        )

    return RedirectResponse(
        url="/settings?msg=Password+updated+successfully&type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/api/status")
async def api_status(request: Request):
    """JSON API endpoint returning health and monitor status summary."""
    auth_mode = get_setting("auth_mode", "readonly_public")
    user = get_current_user(request)

    if auth_mode == "require_login" and not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitors")
        monitors_raw = [dict(m) for m in cursor.fetchall()]

        cursor.execute("SELECT * FROM alert_state")
        alert_states = {row["monitor_id"]: dict(row) for row in cursor.fetchall()}

        cursor.execute("""
            SELECT ch.monitor_id, ch.is_up, ch.status_code, ch.response_time_ms, ch.timestamp, ch.error_message
            FROM check_history ch
            INNER JOIN (
                SELECT monitor_id, MAX(id) AS max_id
                FROM check_history
                GROUP BY monitor_id
            ) latest ON ch.id = latest.max_id
        """)
        latest_checks = {row["monitor_id"]: dict(row) for row in cursor.fetchall()}

    monitors_status = []
    for m in monitors_raw:
        m_id = m["id"]
        last_check = latest_checks.get(m_id)
        a_state = alert_states.get(m_id)

        monitors_status.append(
            {
                "id": m_id,
                "name": m["name"],
                "url": m["url"],
                "is_active": bool(m["is_active"]),
                "is_down": bool(a_state["is_currently_down"]) if a_state else False,
                "consecutive_failures": (
                    a_state["consecutive_failures"] if a_state else 0
                ),
                "last_check": last_check,
            }
        )

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monitors": monitors_status,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)

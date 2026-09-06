# Site Monitor - Project TODO

- [x] **1. Git Setup & Repository Structure**
  - [x] Initialize git repository
  - [x] Create and checkout `devel` branch
  - [x] Create `.gitignore`

- [x] **2. Backend Core & Database (FastAPI + SQLite)**
  - [x] DB Schema setup (`users`, `settings`, `monitors`, `check_history`, `alert_state`)
  - [x] Migration / initial setup logic
  - [x] Initial admin user creation from environment variables (`INITIAL_ADMIN_USER`, `INITIAL_ADMIN_PASSWORD`)
  - [x] Password hashing & Session authentication middleware
  - [x] Auth mode enforcement (`readonly_public` vs `require_login`)

- [x] **3. Background Monitoring Scheduler & Alerting Engine**
  - [x] Async background task for periodic HTTP site checking
  - [x] URL status & response code checking
  - [x] Response body regex pattern matching
  - [x] Consecutive failure counter & threshold checking
  - [x] Pushover API alerting (Initial alert on threshold, Repeat alerts with default/per-host intervals, Recovery alerts)
  - [x] Store check history & prune old records

- [x] **4. Web UI & Endpoints**
  - [x] Clean UI with HTML/CSS/JS (Jinja2 templates + modern responsive design)
  - [x] Status Dashboard (Overview stats, host list, quick actions, filter)
  - [x] Monitor Add / Edit / Delete interface with failure threshold & repeat alert settings
  - [x] History & Detailed View for each monitor (response times, status logs)
  - [x] Settings Page (Pushover config, Auth mode toggle, Global repeat alert defaults, Admin credentials update, Test Pushover button)
  - [x] Immediate "Check Now" action endpoint

- [x] **5. Containerization & Testing**
  - [x] Create `Dockerfile` with volume mount for SQLite (`/data`)
  - [x] Test local container build with `podman`
  - [x] Run full test suite / automated endpoint & scheduler tests

- [x] **6. GitHub Actions Workflow**
  - [x] Add `.github/workflows/docker-publish.yml` to build and publish container image to GHCR on pushes to `main`

- [x] **7. Host Detail Enhancements (Uptime Graph & Screenshots)**
  - [x] Add Chart.js / uptime status timeline graph over time in host detail view (`/monitors/{id}`)
  - [x] Integrate Playwright / Headless browser to capture screenshots on checks
  - [x] Store & display screenshot of last successful request
  - [x] Store & display screenshot of failed request if site is currently down
  - [x] Serve screenshots via static/media endpoint or route (`/screenshots/...`)
  - [x] Update `Dockerfile` & `requirements.txt` to include necessary headless browser / playwright dependencies
  - [x] Update automated tests & run full test suite
  - [x] Merge to `main` and push to remote

- [x] **8. Configurable Screenshot Settings (Global & Per-Monitor)**
  - [x] Add `default_capture_screenshots` setting (default: `true`) in SQLite DB
  - [x] Add `capture_screenshots` column to `monitors` table (NULL = inherit default, 1 = enable, 0 = disable)
  - [x] Update background checker / monitor logic to honor per-monitor and global screenshot configuration
  - [x] Add Screenshot Capture toggle to Global Settings page
  - [x] Add Screenshot Capture radio options to Add/Edit Monitor forms
  - [x] Show screenshot configuration indicator on Host Details page
  - [x] Update unit tests & verify all tests pass
  - [x] Merge to `main` and push to remote

- [x] **9. Themeing Capabilities**
  - [x] Add theme settings in database & global context (`theme_mode`: light [default], dark, system; `theme_color_preset`: default, emerald, purple, amber, crimson, slate, custom; custom color variables: `custom_primary`, `custom_bg`, `custom_surface`, `custom_text`)
  - [x] Change default HTML theme from dark to light (`data-bs-theme="light"`)
  - [x] Support Light, Dark, System mode (using `prefers-color-scheme` media query in JS/CSS for system mode)
  - [x] Support preset complementary color schemes (Emerald, Purple, Amber, Crimson, Slate, etc.)
  - [x] Support custom color palette inputs in Settings (primary color, background, card surface, text color)
  - [x] Add Theme Customization section to Settings page (`/settings`) and quick theme toggle dropdown/selector in Navbar
  - [x] Save theme settings via `/settings/theme` endpoint & save to database (and client-side local storage for immediate persistence)
  - [x] Update CSS custom properties and dynamic styling in `base.html` / `style.css` / `app.js`
  - [x] Update automated tests in `test_app.py` & run full test suite
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **10. Worker Resilience & Process Reaping Fixes**
  - [x] Add `tini` as container entrypoint in `Dockerfile` to reap zombie processes from Chromium/Playwright
  - [x] Add container flags (`--disable-dev-shm-usage`, `--no-sandbox`, `--disable-gpu`, `--no-zygote`) to Playwright launcher
  - [x] Implement concurrency semaphore and strict 15s timeout guard in `app/screenshots.py`
  - [x] Add smart screenshot throttling in `app/monitor.py` to prevent redundant browser launches for healthy hosts
  - [x] Add per-task and batch timeout guards in `monitoring_worker_loop()`
  - [x] Refactor Pushover alerting to asynchronous `httpx.AsyncClient` in `app/alerts.py`
  - [x] Verify integration & resilience tests pass
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **11. Watchdog, Self-Healing, /healthz & Dead Man's Switch**
  - [x] Add heartbeat tracking (`get_worker_heartbeat()`, `update_worker_heartbeat()`) in `app/monitor.py`
  - [x] Implement independent `watchdog_worker_loop()` with emergency Pushover alerting and auto-task restart
  - [x] Implement `GET /healthz` endpoint with database and heartbeat freshness evaluation
  - [x] Add `HEALTHCHECK` directive in `Dockerfile` testing `/healthz` via `curl`
  - [x] Add Dead Man's Switch settings (`heartbeat_ping_url`, `heartbeat_ping_interval_minutes`) and Settings UI card
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **12. Pushover Receipt Tracking, Auto-Cancellation & Test Suite**
  - [x] Add receipt extraction and `cancel_pushover_receipt` in `app/alerts.py`
  - [x] Add `active_receipts` column in `alert_state` table with schema migration in `app/database.py`
  - [x] Store emergency receipt tokens when hosts go DOWN and auto-cancel all active receipts when host RECOVERS in `app/monitor.py`
  - [x] Add priority configuration (Priority 2 Emergency / Priority 1 / Priority 0) with retry and expire settings
  - [x] Add 3-action test suite in Settings UI (Normal Test, Alert Test, Recovery & Cancel Test) and endpoints in `app/main.py`
  - [x] Add automated unit tests in `test_app.py` for receipt cancellation and test endpoints
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **13. Pushover Receipt Acknowledgment Monitoring & Dashboard Sub-Row**
  - [x] Add `get_pushover_receipt_status` API query function in `app/alerts.py`
  - [x] Add `receipt_acknowledged`, `receipt_acknowledged_at`, `receipt_acknowledged_by`, `receipt_acknowledged_device` columns in `alert_state` table in `app/database.py`
  - [x] Implement periodic 60s background polling for DOWN monitors in `watchdog_worker_loop()` and `sync_monitor_receipt_status()` in `app/monitor.py`
  - [x] Add on-demand sync endpoint (`POST /monitors/{id}/sync-receipt`) and JSON API endpoint (`GET /api/monitors/{id}/receipt`) in `app/main.py`
  - [x] Add attached secondary acknowledgment row to dashboard table in `app/templates/dashboard.html` showing confirmation device, user, timestamp, or pending state
  - [x] Add Pushover acknowledgment card on Host Detail page in `app/templates/monitor_detail.html`
  - [x] Add automated unit and integration tests in `scratch/test_fixes.py` and `test_app.py`
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **14. Performance Optimization, DRY Simplification & Codebase Formatting**
  - [x] Eliminate N+1 query loops on `GET /` (dashboard) and `GET /api/status` using bulk dictionary fetches
  - [x] Add typed setting helpers (`get_setting_int`, `get_setting_bool`), timestamp formatters, and receipt list parsers in `app/database.py`
  - [x] Maintain shared pooled `httpx.AsyncClient` with connection limits in `app/alerts.py` and close during app lifespan teardown
  - [x] Consolidate multi-stage database transactions in `check_monitor` into a single atomic write transaction outside network call paths
  - [x] Format entire Python codebase using `black`
  - [x] Add comprehensive unit and integration tests covering optimizations
  - [x] Commit to `devel`, merge to `main`, and push to remote `origin`

- [x] **15. Hide Screenshots Section in Detail View When Disabled**
  - [x] Created dedicated branch `hide-disabled-screenshots`
  - [x] Calculate `screenshots_enabled` in `monitor_detail` route in `app/main.py` based on per-monitor setting or global default
  - [x] Wrap Screenshots card in `{% if screenshots_enabled %}` in `app/templates/monitor_detail.html`
  - [x] Add automated unit tests in `test_app.py` verifying screenshot section visibility when enabled vs disabled
  - [x] Format Python code with `black`
  - [x] Commit and push branch `hide-disabled-screenshots` to remote `origin`

- [x] **16. Per-User Local Time / UTC Timestamp Display Toggle & Service Default**
  - [x] Created dedicated branch `timezone-toggle` based on `main`
  - [x] Added `default_time_display` service setting (defaults to `utc`) configurable in `app/templates/settings.html` and saved in `POST /settings/theme`
  - [x] Added per-user time display toggle dropdown next to Theme Mode switcher in navbar (`app/templates/base.html`)
  - [x] Implemented instant client-side timezone formatting and Chart.js re-rendering in `app/static/app.js`
  - [x] Added `app-timestamp` and `data-utc` attributes across dashboard, monitor detail, screenshot captions, and history tables
  - [x] Added unit and integration tests verifying settings, cookie overrides, and template rendering
  - [x] Formatted Python code with `black`

- [x] **17. Build & Publish Container Images on Pull Requests to Main**
  - [x] Created dedicated branch `build-pr-containers` based on `main`
  - [x] Updated `.github/workflows/docker-publish.yml` to trigger on `pull_request` (`opened`, `synchronize`, `reopened`) targeting `main`
  - [x] Tag container images with the source branch name (`github.head_ref`) for pull requests from this repository
  - [x] Retained `latest` tag generation for pushes directly to `main`
  - [x] Ensured new commits to PR branches (`synchronize`) trigger container builds

- [x] **18. Passkey (WebAuthn) Authentication & Biometrics Management**
  - [x] Created dedicated branch `passkey-auth` based on `main`
  - [x] Integrated `webauthn>=2.1.0` dependency in `requirements.txt`
  - [x] Added SQLite `passkeys` table schema in `app/database.py` with foreign key cascaded user links
  - [x] Implemented WebAuthn registration and authentication options generation, challenge management with 5-minute TTL, and signature verification in `app/auth.py`
  - [x] Added Passkey API endpoints (`/api/passkeys/register/options`, `/api/passkeys/register/verify`, `/settings/passkeys/{id}/delete`, `/api/auth/passkey/options`, `/api/auth/passkey/verify`) in `app/main.py`
  - [x] Added "Passkeys & Biometrics" management card with device list and registration modal in `app/templates/settings.html`
  - [x] Added 1-click "Sign in with Passkey" button and error feedback in `app/templates/login.html`
  - [x] Implemented WebAuthn Base64URL encoding/decoding and browser authenticator handlers in `app/static/app.js`
  - [x] Added comprehensive automated tests in `test_app.py`
  - [x] Formatted Python code with `black`

- [x] **19. Historical Uptime Calculation, Time Window Selector & Individual Monitor Uptime Column**
  - [x] Created dedicated branch `uptime-filter-and-stats` based on `origin/main`
  - [x] Investigated and resolved the root cause of the overall uptime card displaying 100% (was measuring instantaneous operational count instead of historical checks)
  - [x] Added `get_uptime_statistics(period)` and `canonicalize_period` in `app/database.py` calculating overall and per-monitor check success rates over time windows
  - [x] Added time window selector (`1 Hour`, `1 Day`, `1 Week`, `1 Month`, `All Time`) on dashboard with active state highlighting and cookie preference persistence
  - [x] Added dedicated "Uptime (%)" column with color-coded health badges in the "Monitored Websites" dashboard table
  - [x] Updated `/api/status` endpoint to support period queries and include individual monitor uptime metrics
  - [x] Added automated unit and integration tests in `test_app.py`
  - [x] Formatted Python code with `black`













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

- [ ] **8. Configurable Screenshot Settings (Global & Per-Monitor)**
  - [ ] Add `default_capture_screenshots` setting (default: `true`) in SQLite DB
  - [ ] Add `capture_screenshots` column to `monitors` table (NULL = inherit default, 1 = enable, 0 = disable)
  - [ ] Update background checker / monitor logic to honor per-monitor and global screenshot configuration
  - [ ] Add Screenshot Capture toggle to Global Settings page
  - [ ] Add Screenshot Capture radio options to Add/Edit Monitor forms
  - [ ] Show screenshot configuration indicator on Host Details page
  - [ ] Update unit tests & verify all tests pass
  - [ ] Merge to `main` and push to remote

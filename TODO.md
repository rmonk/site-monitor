# Site Monitor - Project TODO

- [ ] **1. Git Setup & Repository Structure**
  - [x] Initialize git repository
  - [x] Create and checkout `devel` branch
  - [x] Create `.gitignore`

- [ ] **2. Backend Core & Database (FastAPI + SQLite)**
  - [ ] DB Schema setup (`users`, `settings`, `monitors`, `check_history`, `alert_state`)
  - [ ] Migration / initial setup logic
  - [ ] Initial admin user creation from environment variables (`INITIAL_ADMIN_USER`, `INITIAL_ADMIN_PASSWORD`)
  - [ ] Password hashing & Session authentication middleware
  - [ ] Auth mode enforcement (`readonly_public` vs `require_login`)

- [ ] **3. Background Monitoring Scheduler & Alerting Engine**
  - [ ] Async background task for periodic HTTP site checking
  - [ ] URL status & response code checking
  - [ ] Response body regex pattern matching
  - [ ] Consecutive failure counter & threshold checking
  - [ ] Pushover API alerting (Initial alert on threshold, Repeat alerts with default/per-host intervals, Recovery alerts)
  - [ ] Store check history & prune old records

- [ ] **4. Web UI & Endpoints**
  - [ ] Clean UI with HTML/CSS/JS (Jinja2 templates + modern responsive design)
  - [ ] Status Dashboard (Overview stats, host list, quick actions, filter)
  - [ ] Monitor Add / Edit / Delete interface with failure threshold & repeat alert settings
  - [ ] History & Detailed View for each monitor (response times, status logs)
  - [ ] Settings Page (Pushover config, Auth mode toggle, Global repeat alert defaults, Admin credentials update, Test Pushover button)
  - [ ] Immediate "Check Now" action endpoint

- [ ] **5. Containerization & Testing**
  - [ ] Create `Dockerfile` with volume mount for SQLite (`/data`)
  - [ ] Test local container build with `podman`
  - [ ] Run full test suite / automated endpoint & scheduler tests

- [ ] **6. GitHub Actions Workflow**
  - [ ] Add `.github/workflows/docker-publish.yml` to build and publish container image to GHCR on pushes to `main`

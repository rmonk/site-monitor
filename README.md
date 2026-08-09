# Site Monitor

A lightweight, containerized website monitoring application with a web UI and Pushover alert integration.

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED)
![License](https://img.shields.io/badge/License-MIT-green.svg)

---

## Features

- **Web Management Interface**:
  - Clean, responsive dashboard showing real-time website operational status, response times, and historical metrics.
  - Interactive monitor creation, configuration, editing, deletion, and manual "Check Now" execution.
  - Detailed check logs per host (HTTP status code, response latency, regex match status, timestamp, error descriptions).

- **Monitoring Capabilities**:
  - Configurable target URL and check interval (seconds).
  - Customizable request timeout (seconds).
  - **Regex Content Verification**: Optionally verify response body against regex pattern in addition to HTTP status codes (2xx/3xx).
  - **Visual Request Screenshots**: Captures screenshots of requests (last successful request and current failure request). Configurable globally (enabled by default) with per-host overrides (enable/disable/inherit default).

- **Flexible Alerting System (Pushover)**:
  - **Failure Thresholds**: Configure consecutive failure counts required before triggering an alert.
  - **Repeat Alerts**: Configurable re-alert behavior with default global settings and per-host overrides (enable/disable, custom interval in minutes).
  - **Recovery Notifications**: Automatic notification when a host recovers and returns to UP status.

- **Theming & Appearance Capabilities**:
  - **Modes**: Light mode (default), Dark mode, and System mode (automatically syncs with OS preference).
  - **Complementary Color Schemes**: Default Blue, Emerald / Teal, Purple / Violet, Amber / Warm, Crimson / Rose, and Slate / Dark.
  - **Fully Custom Color Palette**: Configure custom primary accent color, background tone, card surface tone, and text color.
  - **Quick Theme Switcher**: Instant mode toggle accessible directly from the top navigation bar.

- **Access Control Modes**:
  - **Read-Only Public Access**: Unauthenticated visitors can view the status dashboard, while administrative actions (add/edit/delete/settings) require login.
  - **Always Require Login**: Secures all routes behind admin authentication.
  - Configurable initial admin credentials via environment variables (`INITIAL_ADMIN_USER`, `INITIAL_ADMIN_PASSWORD`).

- **SQLite Database**:
  - Automated database initialization and migrations stored in a single file (`/data/site-monitor.db`).
  - Automatic history log pruning to maintain fast performance over time.

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8000` | Port for the web interface and API |
| `HOST` | `0.0.0.0` | Host address to bind the web server |
| `DATA_DIR` | `/data` | Directory path for SQLite database storage |
| `DB_PATH` | `/data/site-monitor.db` | Direct path to the SQLite database file |
| `INITIAL_ADMIN_USER` | `admin` | Username created on first startup if no users exist |
| `INITIAL_ADMIN_PASSWORD` | `admin123` | Password set for initial admin user |
| `SECRET_KEY` | *(Auto-generated)* | Key for signing session tokens |

---

## Quickstart

### Option 1: Docker Compose (Recommended)

Start the service using `docker-compose.yml`:

```yaml
version: "3.8"
services:
  site-monitor:
    image: ghcr.io/rmonk/site-monitor:latest
    container_name: site-monitor
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - INITIAL_ADMIN_USER=admin
      - INITIAL_ADMIN_PASSWORD=change_me_secret_password
      - SECRET_KEY=replace_with_a_long_random_secret_key
    volumes:
      - site_monitor_data:/data

volumes:
  site_monitor_data:
```

Run:
```bash
docker compose up -d
```

### Option 2: Docker / Podman CLI

```bash
docker run -d \
  --name site-monitor \
  -p 8000:8000 \
  -v site_monitor_data:/data \
  -e INITIAL_ADMIN_USER=admin \
  -e INITIAL_ADMIN_PASSWORD=MySecurePassword123! \
  ghcr.io/rmonk/site-monitor:latest
```

Access the Web UI at `http://localhost:8000`.

---

## Local Development

1. Create virtual environment & install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Run local dev server:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

3. Run automated test suite:
   ```bash
   pytest test_app.py -v
   ```

---

## Repository & Git Workflow

- Development is conducted on the `devel` branch.
- Pushes to `main` trigger the GitHub Actions workflow (`.github/workflows/docker-publish.yml`), which builds and publishes the container image to GitHub Container Registry (`ghcr.io`).

---

## License

Distributed under the [MIT License](LICENSE).


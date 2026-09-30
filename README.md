# Odoo 20 — Setup Guide

This repo contains the custom addons, Docker setup, and configuration for running
Odoo `20.0`. Two installation paths are supported:

| Path                                                            | Best for                          |
| --------------------------------------------------------------- | --------------------------------- |
| [Docker](#docker-installation)                                  | Local dev, staging, quick spin-up |
| [Direct (VM / bare-metal)](#direct-installation-vm--bare-metal) | Production on a Linux server      |

---

## Directory Structure

```
repo-root/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt          ← extra Python deps (Twilio, Stripe, etc.)
├── config/
│   └── odoo.conf.example     ← copy → odoo.conf and fill in secrets
├── odoo/                     ← Odoo 20 source (clone manually, gitignored)
├── enterprise/               ← enterprise addons (optional, gitignored)
└── addons/                   ← all custom modules live here
```

---

## Docker Installation

### Prerequisites

- [Docker](https://docs.docker.com/engine/install/) with the Compose plugin
- Git

### 1. Clone this repo

```bash
git clone <your-repo-url> odoo20
cd odoo20
```

### 2. Clone the Odoo 20 source

```bash
git clone --depth=1 --branch 20.0 https://github.com/odoo/odoo ./odoo
```

### 3. Clone enterprise addons (optional — requires Odoo partner access)

```bash
git clone --depth=1 --branch 20.0 https://github.com/odoo/enterprise ./enterprise
```

Leave `./enterprise` empty if you don't have access — it will still mount fine.

### 4. Create your odoo.conf

```bash
cp config/odoo.conf.example config/odoo.conf
# Edit config/odoo.conf — set admin_passwd and db_name at minimum
```

### 5. Build and start

```bash
docker compose build
docker compose up -d
```

The first build takes several minutes as it compiles all system and Python dependencies.

### 6. Initialize the database (first time only)

The database container starts empty — you must install the Odoo schema before the web UI works:

```bash
docker compose run --rm web --config=/etc/odoo/odoo.conf -d odoo20 -i base --stop-after-init
```

This takes 1–3 minutes. You'll see `Modules loaded.` when it's done.

### 7. Open Odoo

Go to [http://localhost:8069](http://localhost:8069) — you should see the login page.

> **Database manager:** [http://localhost:8069/web/database/manager](http://localhost:8069/web/database/manager)

### Day-to-day commands

```bash
docker compose restart web          # restart after config or addon changes
docker compose logs -f web          # tail logs
docker compose down                 # stop everything
```

---

## Direct Installation (VM / Bare-metal)

Use this path for production on a Linux server (Ubuntu 22.04 / 24.04 recommended).

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- `wkhtmltopdf` (for PDF reports)
- `npm` + `rtlcss` (for RTL support)
- `git`

### 1. Install system dependencies

```bash
sudo apt-get update && sudo apt-get install -y \
    build-essential libpq-dev postgresql-client \
    libxml2-dev libxslt1-dev libjpeg-dev libpng-dev libfreetype6-dev \
    libldap2-dev libsasl2-dev libssl-dev \
    fonts-dejavu-core fonts-font-awesome fonts-roboto-unhinted gsfonts \
    wkhtmltopdf npm git curl

sudo npm install -g rtlcss
```

### 2. Create the odoo system user

```bash
sudo useradd -ms /bin/bash odoo20
```

### 3. Clone the Odoo 20 source

```bash
sudo mkdir -p /opt/odoo20
sudo git clone --depth=1 --branch 20.0 https://github.com/odoo/odoo /opt/odoo20/odoo
sudo chown -R odoo20:odoo20 /opt/odoo20
```

### 4. Set up a Python virtual environment

```bash
sudo -u odoo20 python3 -m venv /opt/odoo20/venv
sudo -u odoo20 /opt/odoo20/venv/bin/pip install --upgrade pip
sudo -u odoo20 /opt/odoo20/venv/bin/pip install -r /opt/odoo20/odoo/requirements.txt
```

### 5. Install extra Python dependencies from this repo

```bash
sudo -u odoo20 /opt/odoo20/venv/bin/pip install -r /path/to/this-repo/requirements.txt
```

### 6. Clone this repo (custom addons)

```bash
sudo git clone <your-repo-url> /opt/odoo20/custom-addons
sudo chown -R odoo20:odoo20 /opt/odoo20/custom-addons
```

### 7. (Optional) Clone enterprise addons

```bash
sudo -u odoo20 git clone --depth=1 --branch 20.0 \
    https://github.com/odoo/enterprise /opt/odoo20/enterprise
```

### 8. Create the Odoo config file

```bash
sudo cp /opt/odoo20/custom-addons/config/odoo.conf.example /etc/odoo20.conf
sudo nano /etc/odoo20.conf
# fill in db credentials, admin_passwd, addons_path
```

Key `addons_path` for a direct install:

```ini
addons_path = /opt/odoo20/odoo/addons,/opt/odoo20/custom-addons/addons,/opt/odoo20/enterprise
```

### 9. Set up PostgreSQL

```bash
sudo -u postgres createuser -s odoo20
sudo -u postgres psql -c "ALTER USER odoo20 WITH PASSWORD 'yourpassword';"
```

### 10. Create a systemd service

```bash
sudo nano /etc/systemd/system/odoo20.service
```

Paste:

```ini
[Unit]
Description=Odoo 20
After=network.target postgresql.service

[Service]
User=odoo20
ExecStart=/opt/odoo20/venv/bin/python3 /opt/odoo20/odoo/odoo-bin \
    --config=/etc/odoo20.conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now odoo20
sudo systemctl status odoo20
```

### 11. Open Odoo

Go to `http://<your-server-ip>:8069` and create your first database.

### Day-to-day commands

```bash
sudo systemctl restart odoo20                        # restart after Python/model changes

# Upgrade a module (no restart needed after)
sudo -u odoo20 /opt/odoo20/venv/bin/python3 /opt/odoo20/odoo/odoo-bin \
    -c /etc/odoo20.conf -d <db_name> -u <module_name> --stop-after-init

sudo journalctl -u odoo20 -f                         # tail logs
```

---

## Custom Addons

Place modules inside `./addons/`. Each module must have `__init__.py` and `__manifest__.py`:

```
addons/
└── my_module/
    ├── __init__.py
    ├── __manifest__.py
    └── ...
```

After adding a module, update the app list from **Settings → Apps → Update Apps List**
(or use `-u <module_name>` on the CLI).

---

## Configuration Reference

Key options in `config/odoo.conf.example`:

| Option         | Description                                          |
| -------------- | ---------------------------------------------------- |
| `addons_path`  | Comma-separated list of addon directories            |
| `admin_passwd` | Master password for database management              |
| `db_name`      | Default database name                                |
| `dbfilter`     | Regex filter — restrict which DBs are accessible     |
| `list_db`      | Set `False` in production to hide the DB list        |
| `proxy_mode`   | Set `True` when behind nginx/reverse proxy           |
| `workers`      | `0` = single-threaded (dev); set `4`+ for production |

---

## Useful Commands

```bash
# View logs
docker compose logs -f web

# Restart web only (picks up odoo.conf changes)
docker compose restart web

# Rebuild image after odoo source changes
docker compose build web
docker compose up -d

# Stop everything
docker compose down

# Stop and delete volumes (destroys database)
docker compose down -v
```

---

## n8n Setup (AI Workflow Orchestration)

n8n is included in `docker-compose.yml` and handles AI intent routing and execution
via the Odoo API endpoints.

### 1. Start the stack

n8n starts automatically with `docker compose up -d`. It runs on port **5678**.

### 2. Open n8n

Go to [http://localhost:5678](http://localhost:5678) and create your owner account on first launch.

> Default basic-auth credentials (set in `docker-compose.yml`):
> **User:** `admin` / **Password:** `dojo-n8n-dev`

### 3. Set the API key in Odoo

n8n authenticates to Odoo via `X-Api-Key` header. Set the key in Odoo:

1. Log in to Odoo at [http://localhost:8069](http://localhost:8069)
2. Go to **Settings → Technical → Parameters → System Parameters**
3. Create or update the key `ai_assistant.api_key` with a secret value (e.g. `dojo-dev-key-2026`)

### 4. Configure n8n credentials

In n8n, create an **HTTP Header Auth** credential:

| Field        | Value                            |
| ------------ | -------------------------------- |
| Name         | `Odoo AI API`                    |
| Header Name  | `X-Api-Key`                      |
| Header Value | _(same value you set in step 3)_ |

### 5. Odoo API endpoints available to n8n

All endpoints accept JSON bodies and return JSON responses.

| Method | Endpoint              | Auth    | Description                                                                                                                               |
| ------ | --------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/v1/ai/health`   | None    | Health check — returns `{"status":"ok", "agent_count": 9}`                                                                                |
| `POST` | `/api/v1/ai/discover` | API Key | Vector similarity routing — takes `{"text":"...","role":"instructor"}`, returns matched agent + scored intents                            |
| `POST` | `/api/v1/ai/execute`  | API Key | Intent execution — three modes: full parse (`text`), direct execute (`intent_type` + `resolved_data`), or confirm pending (`session_key`) |

**Important:** From inside n8n's Docker container, Odoo is reachable at `http://web:8069` (not `localhost`). The environment variable `ODOO_BASE_URL` is pre-set to this value.

### 6. Example: test the connection

From your host machine:

```bash
# Health check (no auth needed)
curl http://localhost:8070/api/v1/ai/health

# Discover intent routing
curl -X POST http://localhost:8070/api/v1/ai/discover \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: dojo-dev-key-2026" \
  -d '{"text": "check in Jordan", "role": "instructor"}'

# Direct execute
curl -X POST http://localhost:8070/api/v1/ai/execute \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: dojo-dev-key-2026" \
  -d '{"intent_type": "schedule_today", "resolved_data": {}, "role": "instructor"}'
```

### 7. Building a workflow in n8n

A typical AI workflow follows this pattern:

1. **Webhook trigger** — receives user text input
2. **HTTP Request → Discover** — `POST http://web:8069/api/v1/ai/discover` with the user's text; returns the matched agent and intents
3. **OpenAI node** — sends the user text + system prompt + filtered intents to GPT for structured JSON parsing
4. **HTTP Request → Execute** — `POST http://web:8069/api/v1/ai/execute` with the parsed `intent_type` and `resolved_data`
5. **Response** — return the execution result to the caller

### n8n day-to-day commands

```bash
docker compose restart n8n          # restart n8n
docker compose logs -f n8n          # tail n8n logs
```

---

## Switching to Cloud SQL

To replace the local `db` container with Cloud SQL:

1. Add the Cloud SQL Auth Proxy as a service in `docker-compose.yml`:

```yaml
cloudsql-proxy:
  image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:latest
  command: --address 0.0.0.0 <PROJECT>:<REGION>:<INSTANCE>
  volumes:
    - ./service-account.json:/config/service-account.json:ro
  environment:
    - GOOGLE_APPLICATION_CREDENTIALS=/config/service-account.json
```

2. Update `odoo.conf`:

```ini
db_host = cloudsql-proxy
db_user = <cloud-sql-user>
db_password = <cloud-sql-password>
```

3. Remove the `db` service and `odoo-db-data-19-2` volume from `docker-compose.yml`.

---

## Troubleshooting

These are the most common issues when setting up for the first time.

### `Name or service not known` / can't connect to database

`db_host` is missing from `config/odoo.conf`. Make sure these lines are present:

```ini
db_host = db
db_port = 5432
db_user = odoo
db_password = odoo
```

The hostname `db` matches the service name in `docker-compose.yml`. Without it, Odoo tries to connect via a Unix socket which doesn't exist inside the container.

### `relation "ir_module_module" does not exist`

The database exists but has never been initialized. Run the one-time init command (Step 6 above):

```bash
docker compose run --rm web --config=/etc/odoo/odoo.conf -d odoo20 -i base --stop-after-init
```

### `localhost:8069` loads nothing / connection refused from host

Odoo's default in 20.0 is to bind to `127.0.0.1` inside the container, which blocks Docker's port forwarding. Add this to `config/odoo.conf`:

```ini
http_interface = 0.0.0.0
```

Then restart: `docker compose restart web`

### `PermissionError: /var/lib/odoo/sessions`

The data volume was created with root ownership. Fix it:

```bash
docker exec -u root odoo-web-1 chown -R odoo:odoo /var/lib/odoo
docker compose restart web
```

### `no such directory '/mnt/custom-addons'`

The `addons_path` in `odoo.conf` doesn't match the volume mounts in `docker-compose.yml`. The correct paths for this setup are:

```ini
addons_path = /mnt/extra-addons,/mnt/enterprise-addons
```

### `configparser.ParsingError` / config not loading

The `odoo.conf` file has corrupt line endings (embedded newlines from copy-paste). Rewrite it cleanly from the terminal — do not paste multi-line values in a text editor:

```bash
cat > config/odoo.conf << 'EOF'
[options]
addons_path = /mnt/extra-addons,/mnt/enterprise-addons
data_dir = /var/lib/odoo
admin_passwd = <your-hashed-password>
db_host = db
db_port = 5432
db_user = odoo
db_password = odoo
db_name = odoo20
dbfilter = ^odoo20$
list_db = True
http_interface = 0.0.0.0
EOF
```

### `COPY ./odoo` fails during build

The `Dockerfile` copies the Odoo source from `./odoo/` which is gitignored. Clone it first (Step 2 above):

```bash
git clone --depth=1 --branch 20.0 https://github.com/odoo/odoo ./odoo
docker compose build --no-cache
```

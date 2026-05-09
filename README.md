# LIST

LIST is a lightweight SOC investigation platform built with FastAPI and Dash. It supports case tracking, alert staging, entity relationships, attachments, reports, backups, and optional BIAS-powered ATT&CK gap analysis.

## Quick Start

Requirements:

- Python 3.9+

Run the launcher:

```bash
python start.py
```

The launcher will:

1. install Python dependencies
2. create the database and default admin account on first run
3. start the API and GUI
4. open `http://localhost:8050`

Default local login:

- username: `admin`
- password: `admin123`

For anything beyond local testing, change `ADMIN_PASSWORD`, change `LIST_SECRET_KEY`, or delete the bootstrap admin account after creating real admin users.

## Manual Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Seed the database:

```bash
python seed.py
```

Start the API:

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Start the GUI:

```bash
python gui/app.py
```

## Sigma Ingest

LIST includes a helper script for forwarding Sigma-style detections into the
existing ingest pipeline.

Files:

- `scripts/sigma_ingest.py`
- `scripts/sample_sigma_detection.yml`

What it does:

- reads a JSON or YAML Sigma rule/detection export
- extracts ATT&CK tags such as `attack.t1059.001`
- maps Sigma level values into LIST severity values
- forwards normalized alerts to `POST /api/ingest/bulk`

Typical usage:

```bash
python scripts/sigma_ingest.py scripts/sample_sigma_detection.yml \
  --api-url http://localhost:8000 \
  --token YOUR_LIST_BEARER_TOKEN
```

Expected input fields:

- `title` or `rule.title`
- `id` or `rule.id`
- `level`
- `tags`
- optional host and network fields such as `host.name`, `source.ip`, and `destination.ip`

The raw detection payload is preserved under `raw_data` so analysts can review
the original Sigma content inside LIST after ingest.

## Configuration

Use environment variables directly or copy values from `.env.example` into your local shell setup.

| Variable | Default | Description |
|---|---|---|
| `LIST_API_PORT` | `8000` | API server port |
| `LIST_GUI_PORT` | `8050` | GUI port |
| `LIST_DATABASE_URL` | `sqlite:///./list.db` | Database URL |
| `LIST_SECRET_KEY` | generated at runtime if unset | JWT signing key |
| `ADMIN_USERNAME` | `admin` | Seeded admin username |
| `ADMIN_EMAIL` | `admin@list.local` | Seeded admin email |
| `ADMIN_PASSWORD` | `admin123` | Seeded admin password |
| `BIAS_PATH` | empty | Optional path to a BIAS checkout |
| `CALDERA_PATH` | empty | Optional path to a CALDERA checkout for ability lookup |
| `LIST_API_URL` | `http://localhost:8000` | API base URL used by helper scripts |

## BIAS Integration

LIST runs without BIAS if none is configured. To enable gap analysis:

- set `BIAS_PATH` to your BIAS repo root, or
- place a `BIAS/` or `bias/` directory beside this repo or inside it

If BIAS is unavailable, LIST still starts and `GET /health` reports `"bias_ready": false`.

## CALDERA Integration

LIST includes a local CALDERA stockpile so BIAS v2 bridge candidates can be enriched with adversarial actions in air-gapped environments.

By default, LIST indexes `stockpile/data/abilities` packaged with this repo. You can override that bundled data by setting `CALDERA_PATH` to a local CALDERA/stockpile checkout.

When available, LIST indexes ATT&CK-mapped abilities from local CALDERA ability YAMLs and shows matched actions for observables, bridge candidates, and multi-hop techniques in the BIAS view. If CALDERA ability data is unavailable, LIST still starts and `GET /health` reports `"caldera_ready": false`.

## Repository Layout

```text
list/
├── api/           FastAPI backend
├── gui/           Dash frontend
├── scripts/       Local helper scripts including Sigma ingest
├── attachments/   Runtime uploads (gitignored)
├── backups/       Runtime backups (gitignored)
├── reports/       Generated reports (gitignored)
├── config.py      Environment-backed settings
├── seed.py        Database/bootstrap script
├── start.py       One-command launcher
└── requirements.txt
```

## Core API Surface

Authentication:

- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/users`
- `GET /api/auth/me`

Cases:

- `GET /api/cases/`
- `POST /api/cases/`
- `GET /api/cases/{id}`
- `PATCH /api/cases/{id}`
- `POST /api/cases/{id}/observables`
- `POST /api/cases/{id}/bias-run`
- `GET /api/cases/{id}/notes`
- `POST /api/cases/{id}/notes`

Staging and ingestion:

- `GET /api/staging/`
- `GET /api/staging/{id}`
- `POST /api/staging/{id}/promote`
- `DELETE /api/staging/{id}`
- `POST /api/ingest/alert`
- `POST /api/ingest/bulk`

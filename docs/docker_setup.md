# Docker Setup — Design Notes

> Status: Draft — not yet implemented.

## Why

- Auto-restart on crash (no manual intervention)
- No need for `caffeinate -s &`
- Clean start/stop/logs via `docker compose`
- Same dev workflow — edit files in VS Code, restart with one command

## What's needed

### `Dockerfile`

- Base image: `python:3.12-slim`
- Copy source, install deps from `requirements.txt`
- `CMD ["python", "main.py"]` (exec form for proper signal handling)
- Don't bake in `.env`, `data/`, `logs/`, `history.json` — those are mounted

### `docker-compose.yml`

- Single service: `aiboss`
- `env_file: .env`
- Volume mounts for persistent state:
  - `./data:/app/data`
  - `./logs:/app/logs`
  - `./history.json:/app/history.json`
- `restart: unless-stopped`
- GCP auth: mount `~/.config/gcloud:/root/.config/gcloud:ro`

## Dev workflow with Docker

```bash
# Start (background)
docker compose up -d --build

# View logs
docker compose logs -f

# Restart after code changes (one command)
docker compose up -d --build

# Stop
docker compose down
```

## Open question

- `requirements.txt` lists `anthropic[vertex]` but code imports `google.genai` — verify which packages are actually needed before building the image.

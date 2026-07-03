# rmf2-map-server

Lightweight FastAPI service that stores and serves VDA5050 **LIF (Layout Interchange Format)** map data and robot configurations for the RMF2 dashboard.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/api/lif` | Return current LIF layout document |
| PUT | `/api/lif` | Replace layout (editor save) |
| POST | `/api/lif` | Submit a new layout file (file upload) |
| GET | `/api/robots` | Return robot configurations |
| PUT | `/api/robots` | Update robot configurations |

Interactive docs available at `http://localhost:8008/docs` once the server is running.

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8008
```

## Docker

```bash
# Build and run
docker compose up --build

# Run in background
docker compose up -d
```

The `data/` directory is mounted as a volume so layouts survive container restarts.

## Configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `DATA_DIR` | `./data` | Directory where LIF and robots JSON files are stored |
| `EXTRA_CORS_ORIGINS` | `""` | Comma-separated list of additional CORS origins |

## Demo data

`data/layout.lif.json` — a three-robot warehouse demo layout with:
- 10 nodes (start, waypoints, goals)
- 8 directed edges with TANGENTIAL orientation
- 6 stations mapped to start/goal nodes

`data/robots.json` — matching robot path configurations consumed by the 3D viewer.

Both files are overwritten in-place when the dashboard saves via PUT/POST, so back them up before connecting a live editor.

## Project structure

```
rmf2_map_server/
├── app/
│   └── main.py          # FastAPI application
├── data/
│   ├── layout.lif.json  # Persisted LIF layout (editable via API)
│   └── robots.json      # Robot path/config data
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

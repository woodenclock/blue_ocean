"""RMF2 Map Server — VDA5050 LIF layout and robot configuration REST API."""

import json
import logging
import os
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "vda_map": None,       # fetched once from VDA5050 master on startup
    "pending_map": None,   # latest map POSTed from the LIF editor UI
}

# ---------------------------------------------------------------------------
# Startup — fetch VDA5050 /map once
# ---------------------------------------------------------------------------

VDA5050_BASE = os.environ.get("VDA5050_BASE", "http://localhost:8000").rstrip("/")
# Scheduler demo API — used to reset MAPF coordination after a live map upload
# so the mapf-planner reloads its graph (a global MapfReset triggers a grid
# re-fetch). Best-effort: a failed reset is surfaced as a warning, not a 502.
SCHEDULER_BASE = os.environ.get("SCHEDULER_BASE", "http://scheduler:8089").rstrip("/")


def _fetch_vda_map() -> dict[str, Any] | None:
    url = f"{VDA5050_BASE}/map"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            logger.info("Fetched VDA5050 /map from %s", url)
            return data
    except urllib.error.URLError as exc:
        logger.warning("Could not reach VDA5050 /map at %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error fetching VDA5050 /map: %s", exc)
        return None


def _post_vda_lif_map(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{VDA5050_BASE}/map/lif"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            logger.info("Updated VDA5050 map via %s", url)
            return data
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"VDA5050 rejected the submitted map ({exc.code}): {detail}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach VDA5050 map update endpoint at {url}: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected error updating VDA5050 map at {url}: {exc}",
        ) from exc


def _trigger_planner_reset() -> str | None:
    """Nudge the mapf-planner to reload its graph for the freshly uploaded map.

    Uses the scheduler's ``/demo/mapf-all-idle`` — the lightweight MAPF reset:
    it clears stored plans and publishes a global ``MapfReset`` (which the
    planner rebuilds its graph on) WITHOUT stopping robots or wiping scheduler
    missions. The heavier ``/demo/demo-reset`` would disrupt other dashboard
    pages that track live robot/mission state.

    Best-effort: returns a human-readable warning string on failure, or None
    on success. The map has already been persisted and pushed to VDA5050, so a
    reset failure must not fail the /newmap request."""
    url = f"{SCHEDULER_BASE}/demo/mapf-all-idle"
    req = urllib.request.Request(
        url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        logger.info("Triggered planner reload via %s", url)
        return None
    except urllib.error.URLError as exc:
        logger.warning("Could not reach scheduler mapf-all-idle at %s: %s", url, exc)
        return (
            "Map pushed, but the planner reload could not be triggered "
            f"({exc}). Restart the stack or run POST /demo/mapf-all-idle so the "
            "planner picks up the new map."
        )
    except Exception as exc:
        logger.warning("Unexpected error triggering planner reload: %s", exc)
        return (
            f"Map pushed, but the planner reload failed ({exc}). Restart the "
            "stack or run POST /demo/mapf-all-idle so the planner picks up the new map."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _state["vda_map"] = _fetch_vda_map()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RMF2 Map Server",
    description="Serves and persists VDA5050 LIF map layouts and robot configurations.",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://localhost:5173",
    *[o.strip() for o in os.environ.get("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()],
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
LIF_FILE = DATA_DIR / "layout.lif.json"
ROBOTS_FILE = DATA_DIR / "robots.json"
MAP_IMAGE_FILE = DATA_DIR / "map.image.png"
MAP_IMAGE_META_FILE = DATA_DIR / "map.image.json"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Coords(BaseModel):
    x: float
    y: float
    z: float = 0.0


class RobotWaypoint(Coords):
    id: str | int
    label: str | None = None


class RobotConfig(BaseModel):
    id: str
    name: str | None = None
    color: str | None = None
    position: Coords | None = None
    target: Coords | None = None
    path: list[RobotWaypoint] | None = None
    startWaypointIndex: int = 0
    loop: bool = False
    speed: float = 1.0
    enabled: bool = True
    rotationZ: float = 0.0
    scale: float | dict[str, float] = 1.0
    coordinateSystem: Literal["navigation", "world"] = "world"


class RobotsPayload(BaseModel):
    coordinateSystem: Literal["navigation", "world"] = "world"
    robots: list[RobotConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Corrupt JSON in {path.name}: {exc}",
        ) from exc


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Health / meta
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"service": "rmf2-map-server", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# VDA5050 map — fetched once at startup
# ---------------------------------------------------------------------------


@app.get("/api/map", summary="Return the VDA5050 /map snapshot (fetched once at startup)")
def get_vda_map() -> Any:
    if _state["vda_map"] is None:
        raise HTTPException(
            status_code=503,
            detail=f"VDA5050 /map not available (could not reach {VDA5050_BASE}/map at startup)",
        )
    return _state["vda_map"]


# ---------------------------------------------------------------------------
# Map image — static PNG + ROS-style metadata served for the LIF editor
# ---------------------------------------------------------------------------


@app.get("/api/map/image/meta", summary="Return map image metadata (resolution, origin, size)")
def get_map_image_meta() -> Any:
    if not MAP_IMAGE_META_FILE.exists():
        raise HTTPException(status_code=404, detail="No map image metadata found")
    return read_json(MAP_IMAGE_META_FILE, {})


@app.get("/api/map/image", summary="Return the map image PNG")
def get_map_image():
    if not MAP_IMAGE_FILE.exists():
        raise HTTPException(status_code=404, detail="No map image found")
    return FileResponse(MAP_IMAGE_FILE, media_type="image/png")


# ---------------------------------------------------------------------------
# New map — LIF editor submission handoff to the VDA5050 master
# ---------------------------------------------------------------------------


@app.post("/newmap", summary="Accept an updated LIF map from the UI editor")
def post_new_map(payload: dict[str, Any]) -> Any:
    write_json(LIF_FILE, payload)
    _state["pending_map"] = payload
    vda_result = _post_vda_lif_map(payload)
    _state["vda_map"] = _fetch_vda_map()
    reset_warning = _trigger_planner_reset()

    nodes = vda_result.get("nodes")
    edges = vda_result.get("edges")
    counts = []
    if nodes is not None:
        counts.append(f"{nodes} nodes")
    if edges is not None:
        counts.append(f"{edges} edges")
    count_text = f" ({', '.join(counts)})" if counts else ""
    warning = vda_result.get("warning")
    message = f"New map saved and pushed to VDA5050{count_text}"
    if warning:
        message = f"{message}. {warning}"
    if reset_warning:
        message = f"{message}. {reset_warning}"
    else:
        message = f"{message}. Planner reset to pick up the new map."

    return {
        "status": "ok",
        "message": message,
        "vda5050": vda_result,
        "planner_reset": reset_warning is None,
    }


@app.get("/newmap", summary="Return the latest LIF map submitted via POST /newmap")
def get_new_map() -> Any:
    if _state["pending_map"] is None:
        raise HTTPException(status_code=404, detail="No map has been submitted yet")
    return _state["pending_map"]


# ---------------------------------------------------------------------------
# LIF layout — persisted to disk
# ---------------------------------------------------------------------------

_LIF_DEFAULT: dict[str, Any] = {
    "metaInformation": {
        "projectIdentification": "Demo Project",
        "creator": "rmf2-map-server",
        "lifVersion": "1.0.0",
    },
    "layouts": [],
}


@app.get("/api/lif", summary="Get the current LIF layout document")
def get_lif() -> Any:
    return read_json(LIF_FILE, _LIF_DEFAULT)


@app.put("/api/lif", summary="Replace the LIF layout document (editor save)")
def put_lif(payload: dict[str, Any]) -> Any:
    write_json(LIF_FILE, payload)
    return payload


@app.post("/api/lif", summary="Submit a new LIF layout document (file upload)")
def post_lif(payload: dict[str, Any]) -> Any:
    write_json(LIF_FILE, payload)
    return payload


# ---------------------------------------------------------------------------
# Robots configuration — persisted to disk
# ---------------------------------------------------------------------------

_ROBOTS_DEFAULT: dict[str, Any] = {
    "coordinateSystem": "world",
    "robots": [],
}


@app.get("/api/robots", response_model=RobotsPayload, summary="Get robot configurations")
def get_robots():
    payload = read_json(ROBOTS_FILE, _ROBOTS_DEFAULT)
    if isinstance(payload, list):
        return {"coordinateSystem": "world", "robots": payload}
    return payload


@app.put("/api/robots", response_model=RobotsPayload, summary="Update robot configurations")
def put_robots(payload: RobotsPayload):
    data = payload.model_dump(mode="json")
    write_json(ROBOTS_FILE, data)
    return data

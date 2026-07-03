#!/usr/bin/env python3
"""Example VDA5050 master webserver (FastAPI + Swagger).

The FMS-side counterpart to ``example_autoxing_client.py``. It drives the bound
``VDA5050Master`` over MQTT:

    Browser --HTTP--> this server --VDA5050Master.assign_order--> MQTT
            --> vda5050 adapter (example_autoxing_client.py) --> AutoXing L300

Plan intake endpoint:

  POST /plans/mapf   accepts a planned route from the MAPF planner, re-resolves
                     coordinates against the master topology map and dispatches
                     it as a VDA5050 Order over MQTT.

This server only validates / assigns / delivers; it does not plan routes.

Run with uv (from this folder). fastapi/uvicorn are declared in the PEP 723
block below, so uv provisions an isolated env on first run. The vda5050_core_py
binding is a colcon-built C extension, so source the ROS workspace first to put
it on PYTHONPATH (uv inherits PYTHONPATH; --python 3.10 matches the binding's
ABI)::

    source /opt/ros/humble/setup.bash
    source ../../install/setup.bash         # exports vda5050_core_py on PYTHONPATH
    uv run --python 3.10 example_master_webserver.py
    # then open http://127.0.0.1:8000/docs

Prerequisites for orders to actually publish: a running MQTT broker (mosquitto)
and a ready AGV (example_autoxing_client.py + robot). assign_order runs a
readiness pre-flight (ONLINE + AUTOMATIC + position-initialized); without a live
AGV the endpoint returns AGV_OFFLINE / AGV_NO_STATE_YET rather than publishing.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fastapi>=0.110",
#     "uvicorn>=0.29",
#     "websockets>=12",
#     "rich>=13",
#     "pika>=1.3",
#     "pyyaml>=6",
#     "paho-mqtt>=2.0",
# ]
# ///

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib
import json
import logging
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from types import ModuleType
from urllib.parse import quote, urlparse

import pika
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from rich.console import Console
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import vda5050_core_py as vda
from log_tap import LogTap

# Diagnostics on stderr, default style red (not a property of sys.stderr itself).
stderr_console = Console(stderr=True, style="bold red")
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def green(text: str) -> str:
    return f"{GREEN}{text}{RESET}"


def yellow(text: str) -> str:
    return f"{YELLOW}{text}{RESET}"

# ===== Configuration (matches example_autoxing_client.py) =====
BROKER = os.environ.get("MQTT_BROKER", "tcp://localhost:1883")
CLIENT_ID = "example_master"
INTERFACE = "uagv"
PROTOCOL_VERSION = "2.0.0"

# Robot IP/URL config for direct jack control (set in config/config.env or real env file).
# AutoXing: full base URL e.g. http://192.168.68.64:8090
AUTOXING_BASE_URL = os.environ.get("AUTOXING_BASE_URL", "").rstrip("/")
# Reeman: host or host:port (no scheme)
REEMAN_ROBOT_IP = os.environ.get("REEMAN_ROBOT_IP", "")
# Real-demo master map id (AutoXing "From Mapping 40" frame). Override via VDA5050_MAP_ID.
# Used only as a fallback; the canonical layout's `map.id` is authoritative.
MAP_ID = os.environ.get("VDA5050_MAP_ID", "From Mapping 40")

# This master is the single map/transform/robots authority for the stack: it
# loads the canonical files from maps/ and serves them over HTTP (/map,
# /map/grid, /transform, /robots). In docker these are mounted at /app/maps and
# the paths below are set explicitly per compose profile; the defaults resolve
# the repo's maps/ dir for local (non-docker) runs from this folder.
_MAPS_DIR = Path(__file__).resolve().parents[4] / "maps"

# Canonical LIF layout (nodes + metric x/y + edges). The master validates/dispatches
# against the metric coords; the planner fetches the grid view via /map/grid.
MASTER_MAP_FILE = os.environ.get(
    "MASTER_MAP_FILE",
    str(_MAPS_DIR / MAP_ID / "real.layout.lif.json"),
)

# Canonical robot config (identity + onboard maps + per-mode routes).
MASTER_ROBOTS_FILE = os.environ.get("MASTER_ROBOTS_FILE", str(_MAPS_DIR / "robots.yaml"))

# Canonical frame transforms (served verbatim over /transform).
MAP_TF_FILE = os.environ.get("MAP_TF_FILE", str(_MAPS_DIR / "map_transforms.yaml"))

# Optional master-frame occupancy map image (PNG) + its ROS map_server metadata
# (resolution + origin), served to the UI over /map/image and /map/image/meta so
# the visualiser can drape the real building map behind the topology nodes (the
# layout coords share this image's AutoXing master frame).
MASTER_MAP_IMAGE_FILE = os.environ.get("MASTER_MAP_IMAGE_FILE")  # PNG to serve
MASTER_MAP_IMAGE_META = os.environ.get("MASTER_MAP_IMAGE_META")  # ROS yaml (res/origin)

# AMQP back-channel: when a robot's order completes (nodeStates emptied,
# §6.6.1), the master emits a TaskComplete on the @RECEIVE@ fanout so the MAPF
# planner can drop the finished task from its joint-plan batch. Best-effort —
# the master never blocks on the broker being reachable.
AMQP_HOST = os.environ.get("AMQP_HOST", "amqp-broker")
AMQP_PORT = int(os.environ.get("AMQP_PORT", "5672"))
AMQP_EXCHANGE = "@RECEIVE@"

# Adapter-local recovery control plane. In docker-compose the robot_id is also
# the service name; override the template for deployments with different names.
ADAPTER_CONTROL_PORT = int(os.environ.get("ADAPTER_CONTROL_PORT", "8099"))
ADAPTER_CONTROL_URL_TEMPLATE = os.environ.get(
    "ADAPTER_CONTROL_URL_TEMPLATE",
    "http://{robot_id}:{port}",
)


def _resolve_path(path_str: str) -> Path:
    """Resolve a configured path: absolute as-is, else relative to this folder
    then to the repo maps/ dir (covers both docker and local runs)."""
    path = Path(path_str)
    if path.is_absolute():
        return path
    here = Path(__file__).parent / path
    return here if here.exists() else (_MAPS_DIR / path)


def _load_robots() -> list[dict]:
    """Canonical robot config from maps/robots.yaml (identity + routes)."""
    with _resolve_path(MASTER_ROBOTS_FILE).open() as f:
        return yaml.safe_load(f)["robots"]


def _lif_document_to_layout(data: dict) -> dict:
    """Parse LIF JSON into the structures the master serves.

    Returns metric ``nodes``/``edges`` in VDA5050-config shape (edges expanded
    bidirectionally so the C++ map loader and traversability validator behave
    exactly as with the old hand-written JSON), plus the undirected
    ``raw_edges`` / ``blocked_edges`` the planner's grid view needs.
    """
    map_path = _resolve_path(MASTER_MAP_FILE)
    # lif_loader.py lives in the maps/ root. Locally that's _MAPS_DIR; in docker
    # the maps tree is mounted elsewhere (e.g. /app/maps), so also try the dir
    # two levels up from the resolved map file (maps/<floor>/real.layout.lif.json).
    for cand in (_MAPS_DIR, map_path.parent.parent):
        if cand.exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    from lif_loader import lif_to_layout  # noqa: PLC0415

    return lif_to_layout(data)


def _load_layout() -> dict:
    """Load and parse the canonical LIF JSON file."""
    return _lif_document_to_layout(json.loads(_resolve_path(MASTER_MAP_FILE).read_text()))


def _layout_to_config_json(layout: dict) -> dict:
    """Canonical layout -> the JSON document master.load_map_from_config expects."""
    return {
        "map_info": {
            "map_id": layout["map_id"],
            "map_version": layout["version"],
            "map_status": "ENABLED",
        },
        "nodes": layout["nodes"],
        "edges": layout["edges"],
    }


def _load_layout_into_master(master: vda.VDA5050Master, layout: dict) -> None:
    """Load a parsed layout into the VDA5050 core map validator."""
    config_json = _layout_to_config_json(layout)
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="vda5050_map_", delete=False
    )
    try:
        json.dump(config_json, tmp)
        tmp.close()
        ok, errs = master.load_map_from_config(tmp.name)
    finally:
        os.unlink(tmp.name)
    if not ok:
        raise RuntimeError(json.dumps(errs, default=str))


def _apply_layout(layout: dict, *, master: vda.VDA5050Master) -> None:
    """Apply a parsed layout to VDA5050 core and all HTTP/WebSocket views."""
    _load_layout_into_master(master, layout)
    state.map_data = {
        "nodes": layout["nodes"],
        "edges": layout["edges"],
        "stations": layout.get("stations", []),
    }
    state.map_id = layout["map_id"]
    state.map_nodes = {n["node_id"]: n for n in layout["nodes"]}
    # Robot positions come from live /state — no config starts in /map/grid.
    state.grid = {
        "nodes": [n["node_id"] for n in layout["nodes"]],
        "edges": layout["raw_edges"],
        "blocked_edges": layout["blocked_edges"],
        # Station nodes (Rack_*, etc.) so the planner can keep them goal-only:
        # never routed into/through unless they are a robot's explicit goal.
        "stations": layout.get("stations", []),
    }
    state.map_revision += 1


def _persist_lif_document(payload: dict) -> Path:
    """Persist a submitted LIF document to the canonical master map file."""
    map_path = _resolve_path(MASTER_MAP_FILE)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = map_path.with_name(f".{map_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp_path, map_path)
    return map_path


def _lif_station_from_map_station(station: dict) -> dict:
    """Convert the master's /map station shape back to LIF station shape."""
    station_id = str(station.get("station_id") or station.get("station_name") or "")
    station_name = str(station.get("station_name") or station_id)
    return {
        "stationId": station_id,
        "interactionNodeIds": [
            str(node_id) for node_id in station.get("node_ids", [])
        ],
        "stationName": station_name,
        "stationPosition": {
            "x": float(station.get("x", 0.0)),
            "y": float(station.get("y", 0.0)),
            "theta": float(station.get("theta", 0.0)),
        },
    }


def _merge_active_rack_stations(payload: dict) -> dict:
    """Preserve live Rack_* stations when an editor submission omits them."""
    layouts = payload.get("layouts")
    if not isinstance(layouts, list) or not layouts:
        return payload
    layout = layouts[0]
    if not isinstance(layout, dict):
        return payload

    incoming_stations = layout.get("stations")
    if not isinstance(incoming_stations, list):
        incoming_stations = []
    has_racks = any(
        str(station.get("stationName", "")).startswith("Rack_")
        for station in incoming_stations
        if isinstance(station, dict)
    )
    if has_racks:
        return payload

    active_racks = [
        station
        for station in state.map_data.get("stations", [])
        if str(station.get("station_name", "")).startswith("Rack_")
    ]
    if not active_racks:
        return payload

    merged = copy.deepcopy(payload)
    merged["layouts"][0]["stations"] = [
        *incoming_stations,
        *[_lif_station_from_map_station(station) for station in active_racks],
    ]
    return merged


def _load_map_image() -> dict | None:
    """Resolve the optional master-frame map image + its ROS metadata.

    Returns ``{path, resolution, origin: [x, y, theta], width, height}`` (px
    width/height read straight from the PNG IHDR header — no image library) or
    ``None`` when no image is configured / files are missing. The UI overlays
    the PNG using resolution + origin to align it with the topology node coords.
    """
    if not MASTER_MAP_IMAGE_FILE or not MASTER_MAP_IMAGE_META:
        return None
    try:
        png = _resolve_path(MASTER_MAP_IMAGE_FILE)
        meta = yaml.safe_load(_resolve_path(MASTER_MAP_IMAGE_META).read_text())
        origin = [float(v) for v in meta["origin"]]
        with png.open("rb") as f:
            hdr = f.read(24)  # 8-byte signature + IHDR length/type + width/height
        width, height = struct.unpack(">II", hdr[16:24])
        return {
            "path": str(png),
            "resolution": float(meta["resolution"]),
            "origin": origin,
            "width": int(width),
            "height": int(height),
        }
    except (OSError, KeyError, ValueError, struct.error) as exc:
        print(f"Map image load failed ({MASTER_MAP_IMAGE_FILE}): {exc}", file=sys.stderr)
        return None


def _load_transform() -> dict:
    """Frame transforms (maps/map_transforms.yaml) as a JSON-able dict."""
    try:
        return yaml.safe_load(_resolve_path(MAP_TF_FILE).read_text()) or {}
    except OSError as exc:  # served best-effort; consumers handle empty
        print(f"Transform load failed ({MAP_TF_FILE}): {exc}", file=sys.stderr)
        return {}


ROBOTS = _load_robots()

# Single unified id: robots.yaml now carries one `robot_id` (was planner_id +
# fleet_id). Tolerate the old schema so a stale file still loads.
for _r in ROBOTS:
    _r["robot_id"] = _r.get("robot_id") or _r.get("planner_id") or _r.get("fleet_id")
    _r.setdefault("endpoint", "")

# (manufacturer, serial_number) -> robot_id, shared by /status, /ws/logs
# (LogTap) and other observability endpoints below.
_ROBOT_IDS = {
    (r["manufacturer"], r["serial_number"]): r["robot_id"] for r in ROBOTS
}

# Independent raw-MQTT tap for the /ws/logs debug feed (see log_tap.py) — kept
# separate from the VDA5050Master binding so every topic (not just the ones
# the C++ master cares about) is visible for debugging.
log_tap = LogTap(
    broker=BROKER,
    interface=INTERFACE,
    protocol_version=PROTOCOL_VERSION,
    client_id=CLIENT_ID,
    history_size=500,
    robot_lookup=_ROBOT_IDS,
)

# Robot REST endpoints are now the per-robot `endpoint` field in robots.yaml
# (single source of truth); the env vars remain ad-hoc overrides for the AutoXing
# jack poller / ad-hoc overrides only.
def _endpoint_for(manufacturer: str) -> str:
    return next(
        (r.get("endpoint", "") for r in ROBOTS
         if r.get("manufacturer") == manufacturer and r.get("endpoint")),
        "",
    )


if not AUTOXING_BASE_URL:
    AUTOXING_BASE_URL = _endpoint_for("Autoxing").rstrip("/")
if not REEMAN_ROBOT_IP:
    REEMAN_ROBOT_IP = _endpoint_for("Reeman")

_SPELLBOOK_IMPORT_LOCK = threading.Lock()

# AGVs onboarded at startup so their MQTT state is already tracked when the
# first MAPF plan arrives (onboarding only at plan time would race the
# adapter's next state publish and fail pre-flight with AGV_NO_STATE_YET).
ONBOARD_AGVS = [(r["manufacturer"], r["serial_number"]) for r in ROBOTS]

# ===== API models =====
class DepartureBlocker(BaseModel):
    """Hold rule: do not leave this waypoint until ``robot_id`` has progressed
    past ``required_progress`` along its own plan."""

    robot_id: str
    required_progress: float
    # MAPF plan version of the blocker robot's plan that produced this
    # dependency. Without this, a freshly posted plan can be released against
    # stale progress from the blocker robot's previous plan.
    plan_version: int | None = None


class PlanWaypoint(BaseModel):
    name: str
    x: float
    y: float
    # CBS timestep of this waypoint in its robot's plan — the value other
    # robots' departure_blockers (required_progress) compare against. Falls
    # back to the waypoint index when the planner doesn't send it.
    progress: float | None = None
    departure_blockers: list[DepartureBlocker] = Field(default_factory=list)
    # VDA5050 node actions to execute when the robot reaches this waypoint.
    # Each dict: action_type (str), action_id (str, auto-generated if absent),
    # blocking_type ("HARD"|"SOFT"|"NONE", default "HARD"), action_description (str, optional).
    actions: list[dict] = Field(default_factory=list)


class MapfPlanRequest(BaseModel):
    """A planned route for one robot, as produced by the MAPF planner.

    Carries everything the master needs to later build and dispatch a VDA5050
    order: identity (robot / manufacturer / serial), the plan metadata, and the
    full waypoint list with positions and departure blockers.
    """

    robot_id: str
    manufacturer: str
    serial_number: str
    order_id: str
    map_id: str = "gametl_demo"
    plan_version: int = 0
    task_id: str = ""
    waypoints: list[PlanWaypoint]


class MapfPlanResponse(BaseModel):
    accepted: bool
    robot_id: str
    order_id: str
    waypoints: int
    blockers: int
    dispatched: bool
    decision: str
    errors: list[str] = Field(default_factory=list)
    note: str


class MapfPlanResetResponse(BaseModel):
    cleared_robots: list[str]
    pending_cancelled: int
    had_active_dispatch: bool


class OrderStateClearResponse(BaseModel):
    robot_id: str
    reason: str
    pending_cancelled: int
    had_active_order: bool | None
    master_lifecycle_cleared: bool
    adapter_reset: dict = Field(default_factory=dict)
    force_fresh_order: bool


# ===== Master lifecycle =====
class _State:
    master: vda.VDA5050Master | None = None
    # Latest MAPF plan per robot_id, as received from the planner. Dispatched
    # as-is in planner-frame coordinates; the reeman adapter TFs its poses into
    # the AutoXing master frame (the reference) so real robots can follow these.
    mapf_plans: dict[str, MapfPlanRequest] = {}
    # Direct-navigate last_node_id override: robot_id -> {"node_id", "shadow"}.
    # direct-navigate bypasses VDA5050, so the order-driven last_node_id would
    # otherwise stay stale. This pins it to the snapped destination until the AGV
    # reports a last_node_id different from `shadow` (the value at override time),
    # i.e. until a real VDA5050 order advances the node. See POST /state/last-node.
    manual_last_node: dict[str, dict] = {}
    # Parsed topology map (nodes/edges), served to the visualiser over /ws/state.
    map_data: dict = {}
    map_revision: int = 0
    # map_id of the loaded topology map and node lookup by node_id. Order
    # coordinates are re-resolved against these, so MASTER_MAP_FILE is the
    # single source of master-frame coords (the planner stays grid-frame).
    map_id: str = ""
    map_nodes: dict[str, dict] = {}
    # Planner grid view (undirected edges + blocked_edges), served over
    # /map/grid so the MAPF planner stops reading its own LIF file. Robot
    # positions come from live /state, not config.
    grid: dict = {}
    # Frame transforms (maps/map_transforms.yaml), served over /transform.
    transform: dict = {}
    # Optional master-frame map image + ROS metadata (resolution/origin/size),
    # served over /map/image + /map/image/meta. None when no image configured.
    map_image: dict | None = None
    # Last dispatched VDA5050 order per robot_id:
    # {"order_id", "update_id", "released_until"}. Lets a replan for a
    # mid-order robot go out as a stitched update (same order_id, bumped
    # update id) instead of a new order_id (which the stitch guard rejects
    # while an order is in flight), and tracks how far the order's base has
    # been released for departure-blocker enforcement.
    dispatched_orders: dict[str, dict] = {}
    # robot_ids that must not stitch from live stale nodeStates on the next
    # idle MAPF dispatch. Set by demo recovery after reset or stitch rejection.
    mapf_recovery_boundaries: set[str] = set()


state = _State()

# Jack state cache — robot_id → "unknown"|"hold"|"jacking_up"|"jacking_down"|"up"|"down"|None.
# AutoXing reports live state via WS polling; Reeman is updated optimistically on command.
_jack_states: dict[str, str | None] = {}

# Battery cache — robot_id → {"battery_percent": float|None}.
# Polled via spellbook get_battery_state (robot REST/WS); last good value kept on fail.
_battery_cache: dict[str, dict] = {}
_spellbook_poll_lock = threading.Lock()


async def _poll_jack_once(base_url: str) -> str | None:
    """Subscribe to a robot's WS /jack_state and return the first state value.

    AutoXing and Reeman share the ``/ws/v2/topics`` + ``enable_topic`` protocol
    (see each spellbook's jack_state command). The robot may send an
    ``enabled_topics`` ack and other topic payloads before ``/jack_state``;
    ``state`` is on the jack message top level."""
    if not base_url:
        return None
    host_port = base_url
    for prefix in ("http://", "https://", "ws://", "wss://"):
        if host_port.startswith(prefix):
            host_port = host_port[len(prefix):]
            break
    host_port = host_port.rstrip("/")
    ws_uri = f"ws://{host_port}/ws/v2/topics"
    try:
        import websockets as _ws
        async with _ws.connect(ws_uri, open_timeout=2) as ws:
            await ws.send(json.dumps({"enable_topic": "/jack_state"}))
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                remaining = max(0.1, deadline - asyncio.get_event_loop().time())
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                data = json.loads(raw)
                if data.get("topic") != "/jack_state":
                    continue
                st = data.get("state")
                if st is None:
                    payload = data.get("data", {})
                    if isinstance(payload, dict):
                        st = payload.get("state")
                return st if isinstance(st, str) else None
    except Exception:
        return None
    return None


async def _poll_and_store_jack(robot_id: str, base_url: str) -> None:
    """Poll one robot's jack state and cache it (best-effort — keep last on fail)."""
    try:
        st = await _poll_jack_once(base_url)
        if st is not None:
            _jack_states[robot_id] = st
    except Exception:  # noqa: BLE001 — best-effort polling
        pass


async def _jack_state_watcher() -> None:
    """Background task: poll AutoXing jack state every 1 s into _jack_states."""
    while True:
        await asyncio.sleep(1.0)
        await asyncio.gather(*[
            _poll_and_store_jack(r["robot_id"], (r.get("endpoint") or "").strip())
            for r in ROBOTS
            if r.get("manufacturer") == "Autoxing"
            and r.get("robot_id")
            and (r.get("endpoint") or "").strip()
        ])


def _normalize_battery_percent(raw: dict, manufacturer: str) -> float | None:
    pct = raw.get("percentage")
    if pct is None:
        pct = raw.get("battery")
    if pct is None:
        return None
    try:
        val = float(pct)
    except (TypeError, ValueError):
        return None
    if manufacturer == "Autoxing" and val <= 1.0:
        val *= 100.0
    return max(0.0, min(100.0, val))


def _reeman_battery_raw(host_port: str) -> dict | None:
    """GET Reeman battery straight from its REST endpoint (from robots.yaml).

    A plain ``GET http://<endpoint>/reeman/base_encode`` — no spellbook import,
    no process env, no shared global state. The endpoint is passed in per call,
    so two robots can be polled without racing on an "active robot". Returns the
    parsed ``base_encode`` JSON (carries ``battery``) or None on any error.
    """
    url = f"http://{host_port}/reeman/base_encode"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — trusted robot host
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body.strip() else None
    except Exception:  # noqa: BLE001 — best-effort polling
        return None


def _poll_battery_once(robot: dict) -> dict | None:
    """Poll one robot's battery; return ``{"battery_percent": ...}`` or None.

    Reeman is a plain REST GET to the endpoint from robots.yaml — read directly
    from the master, no spellbook, no env, no shared global state. AutoXing
    battery arrives over a WS topic, so we still load the AutoXing ``ws_helper``;
    that import mutates process-global ``sys.path``/``sys.modules`` (the two
    spellbooks share bare module names), so it stays serialized under
    ``_spellbook_poll_lock``. Either way the robot's IP comes from ``host_port``,
    never from shared env.
    """
    base = (robot.get("endpoint") or "").strip()
    if not base:
        return None
    mfg = (robot.get("manufacturer") or "").strip()
    try:
        host_port = _endpoint_host_port(base)
    except HTTPException:
        return None

    if mfg == "Reeman":
        raw = _reeman_battery_raw(host_port)
    elif mfg == "Autoxing":
        with _spellbook_poll_lock:
            try:
                _activate_spellbook_for_robot(robot, base)
                ws_helper = importlib.import_module("ws_helper")
                msg = ws_helper.ws_get_topics(
                    host_port,
                    ["/battery_state"],
                    timeout=2,
                ).get("/battery_state")
                raw = msg if isinstance(msg, dict) else None
            except Exception:  # noqa: BLE001 — best-effort polling
                return None
    else:
        return None

    if not isinstance(raw, dict):
        return None
    return {
        "battery_percent": _normalize_battery_percent(raw, mfg),
    }


async def _poll_and_store_battery(robot: dict) -> None:
    """Poll one robot's battery and cache it (best-effort — keep last on fail)."""
    rid = robot.get("robot_id")
    if not rid:
        return
    try:
        result = await asyncio.to_thread(_poll_battery_once, robot)
        if result is not None:
            _battery_cache[rid] = result
    except Exception:  # noqa: BLE001 — best-effort polling
        pass


async def _battery_watcher() -> None:
    """Background task: poll every robot's battery every 3 s into _battery_cache."""
    while True:
        await asyncio.sleep(3.0)
        robots = [
            r for r in ROBOTS
            if r.get("robot_id") and (r.get("endpoint") or "").strip()
        ]
        for robot in robots:
            await _poll_and_store_battery(robot)


def _robot_jack_post(url: str) -> dict:
    """HTTP POST to a robot jack endpoint; returns {"success": bool, ...}."""
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"success": True, "raw": json.loads(body) if body.strip() else {}}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def _robot_cmd_request(
    url: str,
    method: str = "POST",
    body: dict | None = None,
    timeout: float = 5,
) -> dict:
    """HTTP request to a robot control endpoint (POST/PATCH/...); returns
    {"success": bool, ...}. Generalises _robot_jack_post for stop/cancel, which
    is a Reeman POST but an AutoXing PATCH with a JSON body."""
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"success": True, "raw": json.loads(raw) if raw.strip() else {}}
    except urllib.error.HTTPError as exc:
        return {"success": False, "status": exc.code, "message": str(exc)}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def _cancel_robot_move(robot: dict) -> dict:
    """Cancel a robot's current move via its REST control plane (no MAPF/VDA5050).

    Idempotent: with nothing to cancel (the robot has no active move → 404) the
    stop has already achieved its goal, so we report success. Shared by the
    ``/actions/stop`` endpoint and the MAPF supersede path (a new Apply order
    overriding an in-flight one — see ``receive_mapf_plan``)."""
    rid = robot["robot_id"]
    mfg = robot["manufacturer"]
    endpoint = (robot.get("endpoint") or "").strip()
    if mfg == "Autoxing" and endpoint:
        url = f"{endpoint.rstrip('/')}/chassis/moves/current"
        result = _robot_cmd_request(url, method="PATCH", body={"state": "cancelled"})
    elif mfg == "Reeman" and endpoint:
        base = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
        result = _robot_cmd_request(f"{base.rstrip('/')}/cmd/cancel_goal", method="POST")
    else:
        return {
            "robot_id": rid, "success": True,
            "note": f"No {mfg} endpoint configured — stop is a no-op.",
        }
    if not result.get("success") and result.get("status") == 404:
        result = {"success": True, "note": "No active move to cancel (already stopped)."}
    result["robot_id"] = rid
    return result


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Best-effort: the server still starts (and Swagger is demoable) even with
    # no broker — connect()/onboard just log failures.
    try:
        mqtt = vda.create_default_mqtt_client(BROKER, CLIENT_ID)
        master = vda.VDA5050Master.make(mqtt)
        master.connect()
        for manufacturer, serial in ONBOARD_AGVS:
            master.onboard_agv(manufacturer, serial)
        map_path = _resolve_path(MASTER_MAP_FILE)
        try:
            layout = _load_layout()
            _apply_layout(layout, master=master)
            state.transform = _load_transform()
            state.map_image = _load_map_image()
        except Exception as exc:  # noqa: BLE001 — keep the server up regardless
            print(f"Canonical map load failed ({map_path}): {exc}", file=sys.stderr)
        state.master = master
        print(f"Master connected={master.is_connected()}, map {map_path.name}, "
              f"onboarded {ONBOARD_AGVS}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — keep the webserver up regardless
        print(f"Master startup failed (server still up): {exc}", file=sys.stderr)
    log_tap.start()
    log_tap.attach_loop(asyncio.get_running_loop())
    watcher = asyncio.create_task(_release_watcher())
    jack_watcher = asyncio.create_task(_jack_state_watcher())
    battery_watcher = asyncio.create_task(_battery_watcher())
    yield
    watcher.cancel()
    jack_watcher.cancel()
    battery_watcher.cancel()
    log_tap.stop()
    if state.master is not None:
        try:
            state.master.disconnect()
        except Exception as exc:  # noqa: BLE001
            print(f"Master disconnect failed: {exc}", file=sys.stderr)


TAGS = [
    {"name": "2 · Status", "description": "Master / AGV observability."},
    {"name": "3 · MAPF Plans", "description": "Plan intake from the MAPF planner."},
    {"name": "4 · Jack Control", "description": "Direct jack up / down commands to robots."},
]

app = FastAPI(
    title="VDA5050 Example Master Webserver",
    description=__doc__,
    version="0.1.0",
    openapi_tags=TAGS,
    lifespan=lifespan,
)

# The UI (served from a different origin/port) fetches /map/image/meta directly
# from the master, which needs CORS. Permissive for this demo stack.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_master() -> vda.VDA5050Master:
    if state.master is None:
        raise HTTPException(503, "Master not initialized (startup failed).")
    return state.master


# ===== MAPF plan intake =====
def first_hold_index(req: MapfPlanRequest) -> int:
    """Index of the first waypoint with departure blockers (the first hold
    point), or the final index when the plan has none — i.e. how far the
    initial order's base may be released."""
    for i, waypoint in enumerate(req.waypoints):
        if waypoint.departure_blockers:
            return i
    return len(req.waypoints) - 1


def _plan_node_id(waypoint: PlanWaypoint) -> str:
    """The map node id a plan waypoint resolves to.

    Pickup/dropoff goal nodes — waypoints carrying node actions (the
    ``jackUp`` / ``jackDown`` that pick up or drop off the load) — are *snapped
    to the nearest topology node* instead of trusting the planner-supplied
    last-node id: a move-items goal that isn't itself a graph node (e.g. a rack
    / station point) still lands on a real map node, so the jack fires at the
    right place and the order passes the master's map-integrity validation.

    The snap uses the node's own master-frame coords when the name is already a
    known node (so it returns that same node — a frame-agnostic no-op that never
    relabels a valid goal), and only falls back to the planner coords for an
    off-graph goal name. Plain transit waypoints keep their id unchanged."""
    if not waypoint.actions:
        return waypoint.name
    node_def = state.map_nodes.get(waypoint.name)
    x = float(node_def["x"]) if node_def else waypoint.x
    y = float(node_def["y"]) if node_def else waypoint.y
    snapped = _snap_to_map_node(x, y)
    return snapped if snapped is not None else waypoint.name


def build_plan_order(
    req: MapfPlanRequest,
    order_id: str | None = None,
    order_update_id: int = 0,
    released_until: int | None = None,
    from_idx: int = 0,
    seq_base: int = 0,
) -> vda.Order:
    """MAPF plan waypoints[from_idx:] -> VDA5050 Order.

    Departure blockers are enforced via the VDA5050 base/horizon split:
    nodes/edges up to and including waypoint ``released_until`` are released
    (the base); everything after is the unreleased horizon the AGV must not
    enter. The release watcher later extends the base with stitched order
    updates (same order_id, bumped update id, ``from_idx`` = the hold
    waypoint) as blocking robots make progress. ``released_until=None``
    releases everything.

    ``seq_base`` offsets the VDA5050 sequence ids so they stay monotonic per
    order_id across stitched re-solves: waypoint ``i`` gets ``seq_base + 2*i``.
    A fresh order uses ``seq_base=0`` (the historical ``2*i`` numbering); a
    stitch sets it to the AGV's reported ``last_node_sequence_id`` so the
    robot's current node keeps the id it already reached and the adapter
    resumes after it instead of re-driving from the start.
    """
    nodes = []
    edges = []
    # The order_id this build dispatches under — used both for the Order below
    # and to derive DETERMINISTIC action ids (see the action loop). Must be the
    # resolved id (a stitch reuses the active order's id), not req.order_id.
    effective_order_id = order_id if order_id is not None else req.order_id
    # Resolve every waypoint to its map node id up front so the snapped goal id
    # (pickup/dropoff nodes are snapped to the nearest topology node — see
    # _plan_node_id) is used consistently for the node AND the edge into it.
    node_ids = [_plan_node_id(w) for w in req.waypoints]
    for i in range(from_idx, len(req.waypoints)):
        waypoint = req.waypoints[i]
        node_id = node_ids[i]
        if node_id != waypoint.name:
            print(
                f"Snap goal {waypoint.name!r} -> nearest node {node_id!r} "
                f"(pickup/dropoff actions {[a.get('action_type') for a in waypoint.actions]})",
                file=sys.stderr,
            )
        released = released_until is None or i <= released_until
        # Re-resolve coordinates by the (possibly snapped) node id against the
        # loaded topology map: the planner sends grid-frame coords, the master's
        # map carries the master-frame ones. Planner coords are only a fallback
        # for waypoints missing from the map.
        node_def = state.map_nodes.get(node_id)
        # Position-only orders for now: x/y + optional XY tolerance. Leave
        # theta and allowed_deviation_theta unset (null) — do not default to 0.
        pos = vda.NodePosition()
        pos.x = float(node_def["x"]) if node_def else waypoint.x
        pos.y = float(node_def["y"]) if node_def else waypoint.y
        if node_def and "allowed_deviation_xy" in node_def:
            pos.allowed_deviation_x_y = float(node_def["allowed_deviation_xy"])
        pos.map_id = state.map_id or req.map_id
        node = vda.Node()
        node.node_id = node_id
        node.sequence_id = seq_base + 2 * i
        node.released = released
        node.node_position = pos
        if waypoint.actions:
            built_actions = []
            for a_idx, a in enumerate(waypoint.actions):
                act = vda.Action()
                act.action_type = a["action_type"]
                # Deterministic action_id: a same-goal re-solve dispatched as a
                # stitch update reuses the same order_id, so keying the id on
                # (order_id, node, index, type) makes the rebuilt goal node
                # BYTE-IDENTICAL to the base's. A random uuid here instead makes
                # every stitch of an order whose released stitch node carries an
                # action (pickup/dropoff jack goals) fail §6.6.2 stitch-node
                # immutability — the "Stitch node content differs from base"
                # rejection. Still globally unique across orders (order_id
                # differs) per §6.6.2. A planner-supplied id, if any, still wins.
                act.action_id = a.get("action_id") or (
                    f"{effective_order_id}:{node_id}:a{a_idx}:{a['action_type']}"
                )
                bt = a.get("blocking_type", "HARD").upper()
                act.blocking_type = (
                    vda.BlockingType.HARD if bt == "HARD"
                    else vda.BlockingType.SOFT if bt == "SOFT"
                    else vda.BlockingType.NONE
                )
                if "action_description" in a:
                    act.action_description = a["action_description"]
                built_actions.append(act)
            node.actions = built_actions
        nodes.append(node)
        if i > from_idx:
            prev_id = node_ids[i - 1]
            edge = vda.Edge()
            edge.edge_id = f"edge_{prev_id}_{node_id}"
            edge.sequence_id = seq_base + 2 * i - 1
            edge.start_node_id = prev_id
            edge.end_node_id = node_id
            edge.released = released
            edges.append(edge)

    order = vda.Order()
    order.order_id = effective_order_id
    order.order_update_id = order_update_id
    order.header.version = PROTOCOL_VERSION
    order.header.manufacturer = req.manufacturer
    order.header.serial_number = req.serial_number
    order.nodes = nodes
    order.edges = edges
    return order


def _is_new_task(req: MapfPlanRequest, prev: dict | None) -> bool:
    """True when this plan belongs to a DIFFERENT task than the tracked order.

    The planner stamps each dispatch with the orchestrator's task URN
    (``task_id``); routine re-solves within one mission reuse it, while a new
    mission carries a new one. A new task must never be stitched onto the
    previous order — see ``_resolve_order_identity``. Only compares when both
    ids are known, so a legacy dispatch record without a task_id never
    misfires (falls back to the nodeStates heuristic)."""
    return bool(
        prev is not None
        and req.task_id
        and prev.get("task_id")
        and prev["task_id"] != req.task_id
    )


def _resolve_order_identity(req: MapfPlanRequest) -> tuple[str, int, int]:
    """Pick the (order_id, order_update_id, seq_base) a new plan dispatches under.

    A new order_id while the robot's previous order is still in flight is
    rejected by the master's stitch guard (concurrent orders, §6.6 — FMS must
    cancel first, and cancelOrder is not implemented adapter-side). So when the
    robot has an in-flight order, reuse THAT order_id with a bumped
    order_update_id: the core treats the new plan as an order update (stitched
    override) instead of rejecting it. Crucially, the active order_id is read
    from the AGV's own last State (its reported orderId while nodeStates is
    non-empty), not just our local ``dispatched_orders`` bookkeeping — so a new
    Apply still overrides the running order after a ``/plans/mapf/reset`` wiped
    that bookkeeping, or after a master restart. Once the AGV reports the order
    complete (nodeStates emptied, §6.6.1), a fresh order_id is dispatched as a
    normal new assignment.

    ``seq_base`` keeps VDA5050 sequence ids monotonic per order_id: a stitch
    anchors the new plan at the AGV's reported ``last_node_sequence_id`` (so its
    waypoint[0] — the robot's current node, since the planner re-fetches live
    pose — keeps the id it already reached and the adapter resumes after it). A
    fresh assignment starts at 0.
    """
    prev = state.dispatched_orders.get(req.robot_id)
    prev_order_id = prev["order_id"] if prev else None
    prev_update_id = prev["update_id"] if prev else -1

    master = state.master
    agv = master.get_agv(req.manufacturer, req.serial_number) if master else None
    last = agv.get_last_state() if agv else None
    driving = bool(getattr(last, "driving", False)) if last is not None else False
    agv_order_active = (
        last is not None and bool(last.order_id) and len(last.node_states) > 0
    )
    force_fresh = req.robot_id in state.mapf_recovery_boundaries

    # Start a NEW VDA order chain (fresh order_id) instead of stitching when:
    #  - demo recovery stopped the robot and cleared master/adapter state
    #    (force_fresh), or
    #  - this plan is a genuinely new task (new task_id) — its route is
    #    independent of the previous order, which may even have already
    #    completed with its nodeStates still lingering. Stitching successive
    #    tasks onto one order_id accumulates divergent routes until a stitch
    #    node can no longer stay byte-identical to the base (§6.6.2) and every
    #    update is rejected.
    # Gated on the robot being idle (not driving): receive_mapf_plan clears the
    # prior order's lifecycle in the same case so the fresh order_id is accepted
    # as a new base rather than rejected as concurrent. A mid-drive supersede
    # (driving) still stitches — cleanly aborting a moving robot's order needs
    # VDA5050 cancelOrder (not yet implemented adapter-side).
    if (force_fresh or _is_new_task(req, prev)) and last is not None and not driving:
        return req.order_id, 0, 0

    if agv_order_active:
        # Override the order the robot is actually running. Bump strictly past
        # both the robot-reported update_id and our locally tracked one.
        base_update = (
            max(prev_update_id, last.order_update_id)
            if last.order_id == prev_order_id
            else last.order_update_id
        )
        return last.order_id, base_update + 1, last.last_node_sequence_id

    # Robot idle / order complete / no State yet — fresh assignment.
    return req.order_id, 0, 0


@app.post(
    "/plans/mapf",
    response_model=MapfPlanResponse,
    tags=["3 · MAPF Plans"],
    summary="Receive a MAPF plan for one robot and dispatch it as a VDA5050 Order",
    description=(
        "General plan intake from the MAPF planner: one robot's full route as "
        "waypoints (name, x, y) plus departure blockers (waits behind another "
        "robot's progress). The master stores the latest plan per robot, then "
        "builds a VDA5050 `Order` and dispatches it via `assign_order` over "
        "MQTT.\n\n"
        "Coordinates are still raw planner-frame (map unification / TF reeman "
        "-> autoxing frame is pending), and departure blockers are enforced "
        "by releasing VDA5050 order updates only when blockers clear. "
        "With no live, ready AGV the readiness pre-flight yields "
        "`AGV_OFFLINE` / `AGV_NO_STATE_YET` (`dispatched: false`); the plan "
        "is still stored and the request still succeeds."
    ),
)
def receive_mapf_plan(req: MapfPlanRequest):
    # Detect a destination change before overwriting the stored plan: a new
    # Apply moves the robot to a different goal, whereas routine replans keep
    # the same goal and only re-route. Only the former should preempt the
    # robot's current move (see the supersede cancel below).
    prev_plan = state.mapf_plans.get(req.robot_id)
    new_goal = req.waypoints[-1].name if req.waypoints else None
    prev_goal = (
        prev_plan.waypoints[-1].name
        if prev_plan and prev_plan.waypoints
        else None
    )
    goal_changed = new_goal is not None and new_goal != prev_goal

    state.mapf_plans[req.robot_id] = req
    blockers = sum(len(w.departure_blockers) for w in req.waypoints)
    path = " -> ".join(w.name for w in req.waypoints)

    master = _require_master()
    if (req.manufacturer, req.serial_number) not in master.get_onboarded_agvs():
        master.onboard_agv(req.manufacturer, req.serial_number)

    # New goal (Apply / supersede): flush this robot's stale queued outbound
    # orders BEFORE dispatching the new one, so it doesn't pile up behind
    # outdated stitch updates that can no longer combine (mirrors stop/reset).
    # Routine same-goal replans keep their queue — blocker-gated releases
    # depend on it.
    if goal_changed:
        agv = master.get_agv(req.manufacturer, req.serial_number)
        if agv is not None:
            stale = agv.get_pending_order_count()
            if stale:
                agv.cancel_pending_orders()
                print(
                    f"New goal for {req.robot_id}: cleared {stale} stale queued "
                    f"order(s) before dispatch",
                    file=sys.stderr,
                )

    # A new task (distinct orchestrator task_id) on an idle robot must dispatch
    # as a FRESH order, not stitch onto the prior — often already completed —
    # order whose nodeStates still linger. Clear the prior order's master
    # lifecycle so the concurrent-order guard drops and the fresh order_id is
    # accepted as a new base. Mirrors _resolve_order_identity's new-task gate.
    prev_dispatch = state.dispatched_orders.get(req.robot_id)
    if _is_new_task(req, prev_dispatch):
        agv = master.get_agv(req.manufacturer, req.serial_number)
        last = agv.get_last_state() if agv is not None else None
        idle = last is not None and not bool(getattr(last, "driving", False))
        if (
            agv is not None
            and idle
            and hasattr(agv, "has_active_order")
            and agv.has_active_order()
        ):
            if hasattr(agv, "clear_order_lifecycle"):
                agv.clear_order_lifecycle()
            agv.cancel_pending_orders()
            print(
                f"New task {req.task_id} for {req.robot_id}: cleared prior order "
                f"lifecycle {prev_dispatch.get('order_id')!r} before fresh dispatch",
                file=sys.stderr,
            )

    order_id, order_update_id, seq_base = _resolve_order_identity(req)
    stitched = order_id != req.order_id
    released_until = first_hold_index(req)
    result = master.assign_order(
        req.manufacturer, req.serial_number,
        build_plan_order(
            req, order_id, order_update_id, released_until, seq_base=seq_base,
        ),
    )
    dispatched = bool(result)
    decision = result.decision.name
    errors = [e.error_type for e in result.errors]
    recovery = None
    if not dispatched and decision == "STITCH_REJECTED":
        recovery = _recover_stale_order_after_stitch_rejection(req, errors)

    # A stitch-queued update is still pending delivery, so it claims its
    # update id — the next replan must bump past it, not reuse it.
    if dispatched or decision == "STITCH_QUEUED":
        state.dispatched_orders[req.robot_id] = {
            "order_id": order_id,
            "update_id": order_update_id,
            "released_until": released_until,
            "seq_base": seq_base,
            "task_id": req.task_id,
        }
        if order_id == req.order_id:
            state.mapf_recovery_boundaries.discard(req.robot_id)

    # Supersede: a new Apply (different goal) that overrode an in-flight order
    # (stitched onto the active order_id). Preempt the robot's current move via
    # its REST control plane so it abandons the old path immediately and picks
    # up the override, instead of finishing the current leg first. Routine
    # same-goal replans are left untouched (no goal_changed → no preemption).
    if (dispatched or decision == "STITCH_QUEUED") and stitched and goal_changed:
        cfg = _robot_cfg(req.robot_id)
        if cfg is not None:
            cancel = _cancel_robot_move(cfg)
            print(
                f"Supersede {req.robot_id}: new goal {new_goal} overrides active "
                f"order {order_id}; cancel current move "
                f"→ {'OK' if cancel.get('success') else 'FAILED'}",
                file=sys.stderr,
            )

    print(
        f"MAPF plan v{req.plan_version} {req.order_id}: {req.robot_id} "
        f"({req.manufacturer}/{req.serial_number}) {path} "
        f"[{blockers} blocker(s)] — dispatched={dispatched} ({decision})"
        + (f" as update {order_update_id} of {order_id}" if stitched else ""),
        file=sys.stderr,
    )
    if not dispatched:
        note = f"Stored; dispatch refused by pre-flight: {decision}."
        if recovery is not None:
            note += " Idle stale-order recovery boundary recorded."
    elif stitched:
        note = (
            f"Robot mid-order: dispatched as update {order_update_id} of "
            f"active order {order_id} (stitch-guarded) instead of new order "
            f"{req.order_id}."
        )
    else:
        note = (
            "Dispatched over MQTT (planner-frame coords; TF reeman -> autoxing "
            "frame and blocker enforcement still pending)."
        )
    return MapfPlanResponse(
        accepted=True,
        robot_id=req.robot_id,
        order_id=order_id,
        waypoints=len(req.waypoints),
        blockers=blockers,
        dispatched=dispatched,
        decision=decision,
        errors=errors,
        note=note,
    )


@app.get(
    "/plans/mapf",
    tags=["3 · MAPF Plans"],
    summary="Latest stored MAPF plan per robot",
)
def list_mapf_plans():
    return {robot_id: plan.model_dump() for robot_id, plan in state.mapf_plans.items()}


@app.post(
    "/plans/mapf/reset",
    response_model=MapfPlanResetResponse,
    tags=["3 · MAPF Plans"],
    summary="Clear stored MAPF plans and dispatch bookkeeping",
    description=(
        "Drops all stored MAPF plans and per-robot dispatch records "
        "(``mapf_plans``, ``dispatched_orders``), and cancels each AGV's "
        "master-side outbound order queue. Does **not** send ``cancelOrder`` "
        "to adapters — in-flight robot motion may continue until the current "
        "order finishes or is stopped out-of-band."
    ),
)
def reset_mapf_plans():
    master = _require_master()
    cleared_robots = list(state.mapf_plans.keys())
    cleared_dispatch_robots = list(state.dispatched_orders.keys())
    had_active_dispatch = bool(state.dispatched_orders)
    pending_cancelled = 0
    state.mapf_plans.clear()
    state.dispatched_orders.clear()
    state.mapf_recovery_boundaries.update(cleared_robots)
    state.mapf_recovery_boundaries.update(cleared_dispatch_robots)
    for mfg, serial in master.get_onboarded_agvs():
        agv = master.get_agv(mfg, serial)
        if agv is None:
            continue
        robot_id = _ROBOT_IDS.get((mfg, serial))
        if robot_id:
            state.mapf_recovery_boundaries.add(robot_id)
        pending_cancelled += agv.get_pending_order_count()
        agv.cancel_pending_orders()
    print(
        f"MAPF reset: cleared plans for {cleared_robots or '(none)'}, "
        f"cancelled {pending_cancelled} pending outbound order(s), "
        f"had_active_dispatch={had_active_dispatch}",
        file=sys.stderr,
    )
    return MapfPlanResetResponse(
        cleared_robots=cleared_robots,
        pending_cancelled=pending_cancelled,
        had_active_dispatch=had_active_dispatch,
    )


def _robot_cfg(robot_id: str) -> dict | None:
    return next((r for r in ROBOTS if r["robot_id"] == robot_id), None)


def _adapter_control_base_url(robot: dict) -> str:
    return ADAPTER_CONTROL_URL_TEMPLATE.format(
        robot_id=quote(robot["robot_id"], safe=""),
        manufacturer=quote(robot["manufacturer"], safe=""),
        serial_number=quote(robot["serial_number"], safe=""),
        serial=quote(robot["serial_number"], safe=""),
        port=ADAPTER_CONTROL_PORT,
    ).rstrip("/")


def _post_adapter_order_reset(robot: dict) -> dict:
    if ADAPTER_CONTROL_PORT <= 0:
        return {"success": False, "skipped": True, "reason": "disabled"}

    try:
        url = f"{_adapter_control_base_url(robot)}/order-state/reset"
    except Exception as exc:
        return {
            "success": False,
            "error": type(exc).__name__,
            "detail": str(exc),
        }

    request = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as resp:
            payload = json.loads(resp.read() or b"{}")
            payload.setdefault("success", 200 <= resp.status < 300)
            payload["url"] = url
            return payload
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            "success": False,
            "url": url,
            "error": type(exc).__name__,
            "detail": str(exc),
        }


def _agv_has_idle_stale_order(agv) -> bool:
    if agv is None:
        return False
    last = agv.get_last_state()
    if last is None:
        return False
    pending = int(agv.get_pending_order_count() or 0)
    return (
        bool(getattr(last, "order_id", ""))
        and len(getattr(last, "node_states", []) or []) > 0
        and not bool(getattr(last, "driving", False))
        and pending == 0
    )


def _clear_order_state_for_robot(
    robot_id: str,
    *,
    reason: str,
    reset_adapter: bool = True,
) -> dict:
    robot = _robot_cfg(robot_id)
    if robot is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")

    master = _require_master()
    agv = master.get_agv(robot["manufacturer"], robot["serial_number"])
    pending_cancelled = 0
    had_active_order: bool | None = None
    master_lifecycle_cleared = False

    if agv is not None:
        pending_cancelled = int(agv.get_pending_order_count() or 0)
        agv.cancel_pending_orders()
        if hasattr(agv, "has_active_order"):
            had_active_order = bool(agv.has_active_order())
        if hasattr(agv, "clear_order_lifecycle"):
            agv.clear_order_lifecycle()
            master_lifecycle_cleared = True

    state.dispatched_orders.pop(robot_id, None)
    state.mapf_recovery_boundaries.add(robot_id)
    adapter_reset = (
        _post_adapter_order_reset(robot)
        if reset_adapter
        else {"success": False, "skipped": True, "reason": "not_requested"}
    )

    print(
        f"Order-state recovery ({reason}) for {robot_id}: "
        f"pending_cancelled={pending_cancelled}, "
        f"master_lifecycle_cleared={master_lifecycle_cleared}, "
        f"adapter_reset={'OK' if adapter_reset.get('success') else 'FAILED/SKIPPED'}; "
        "next MAPF dispatch will use a fresh order_id",
        file=sys.stderr,
    )
    return {
        "robot_id": robot_id,
        "reason": reason,
        "pending_cancelled": pending_cancelled,
        "had_active_order": had_active_order,
        "master_lifecycle_cleared": master_lifecycle_cleared,
        "adapter_reset": adapter_reset,
        "force_fresh_order": True,
    }


def _recover_stale_order_after_stitch_rejection(
    req: MapfPlanRequest,
    errors: list[str],
) -> dict | None:
    if "orderUpdateError" not in errors:
        return None
    robot = _robot_cfg(req.robot_id)
    if robot is None:
        return None
    master = state.master
    agv = (
        master.get_agv(robot["manufacturer"], robot["serial_number"])
        if master is not None
        else None
    )
    if not _agv_has_idle_stale_order(agv):
        return None
    try:
        return _clear_order_state_for_robot(
            req.robot_id,
            reason="stitch_rejected",
            reset_adapter=True,
        )
    except Exception as exc:  # pragma: no cover - defensive live recovery path
        print(
            f"Order-state recovery after stitch rejection failed for "
            f"{req.robot_id}: {exc}",
            file=sys.stderr,
        )
        return None


def _clear_mapf_state_for_robot(robot_id: str) -> tuple[int, bool]:
    """Clear stored plan/dispatch for one robot; cancel its pending orders."""
    master = _require_master()
    pending_cancelled = 0
    had_active_dispatch = (
        robot_id in state.dispatched_orders or robot_id in state.mapf_plans
    )
    plan = state.mapf_plans.pop(robot_id, None)
    state.dispatched_orders.pop(robot_id, None)
    state.mapf_recovery_boundaries.add(robot_id)
    cfg = _robot_cfg(robot_id)
    manufacturer = plan.manufacturer if plan else (cfg or {}).get("manufacturer")
    serial_number = plan.serial_number if plan else (cfg or {}).get("serial_number")
    if manufacturer and serial_number:
        agv = master.get_agv(manufacturer, serial_number)
        if agv is not None:
            pending_cancelled = agv.get_pending_order_count()
            agv.cancel_pending_orders()
    return pending_cancelled, had_active_dispatch


@app.post(
    "/plans/mapf/reset/{robot_id}",
    response_model=MapfPlanResetResponse,
    tags=["3 · MAPF Plans"],
    summary="Clear stored MAPF plan for one robot",
    description=(
        "Drops the stored MAPF plan and dispatch record for ``robot_id`` and "
        "cancels that AGV's master-side outbound order queue. Does **not** send "
        "``cancelOrder`` to adapters."
    ),
)
def reset_mapf_plan_for_robot(robot_id: str):
    pending_cancelled, had_active_dispatch = _clear_mapf_state_for_robot(robot_id)
    print(
        f"MAPF reset ({robot_id}): cancelled {pending_cancelled} pending "
        f"outbound order(s), had_active_dispatch={had_active_dispatch}",
        file=sys.stderr,
    )
    return MapfPlanResetResponse(
        cleared_robots=[robot_id],
        pending_cancelled=pending_cancelled,
        had_active_dispatch=had_active_dispatch,
    )


@app.post(
    "/actions/clear-order-state/{robot_id}",
    response_model=OrderStateClearResponse,
    tags=["4 · Jack Control"],
    summary="Clear stale VDA5050 order state after an idle demo reset",
    description=(
        "Recovery-only action for an already-stopped robot. Clears the "
        "master-side outbound queues and active order lifecycle, asks the "
        "adapter-local control plane to clear published nodeStates, and marks "
        "the next MAPF dispatch as a fresh order boundary. This is not full "
        "VDA5050 cancelOrder support."
    ),
)
def clear_order_state(robot_id: str, reason: str = "manual_recovery"):
    return _clear_order_state_for_robot(robot_id, reason=reason, reset_adapter=True)


# ===== Departure-blocker enforcement (release watcher) =====
def _robot_progress(robot_id: str, plan_version: int | None = None) -> float | None:
    """A robot's current progress along its own stored plan: the ``progress``
    of the plan waypoint matching its last reached node (None when it has no
    plan / no state / hasn't reached a plan node yet)."""
    plan = state.mapf_plans.get(robot_id)
    if plan is None or state.master is None:
        return None
    if plan_version is not None and plan.plan_version != plan_version:
        return None
    agv = state.master.get_agv(plan.manufacturer, plan.serial_number)
    last = agv.get_last_state() if agv else None
    if last is None or not last.last_node_id:
        return None
    seq_base = (state.dispatched_orders.get(robot_id) or {}).get("seq_base", 0)
    progress = None
    for i, waypoint in enumerate(plan.waypoints):
        # Match against the resolved (possibly snapped) node id — that's what
        # the AGV reports as last_node_id for a snapped pickup/dropoff goal.
        if _plan_node_id(waypoint) != last.last_node_id:
            continue
        # Disambiguate nodes a plan revisits (yield-and-return, e.g.
        # 4,3 -> 4,2 -> 4,3) by the VDA5050 sequence id, matching
        # _robot_at_plan_index. Without this, a robot parked at a start node
        # that its plan visits again later reads as having completed the whole
        # plan, so a dependent robot's departure blocker clears prematurely.
        if last.last_node_sequence_id != seq_base + 2 * i:
            continue
        p = waypoint.progress if waypoint.progress is not None else float(i)
        progress = p if progress is None else max(progress, p)
    return progress


def _log_release_wait(rec: dict, key: tuple, message: str) -> None:
    """Print a hold reason once per unchanged wait condition."""
    if rec.get("_last_wait_log") == key:
        return
    rec["_last_wait_log"] = key
    print(yellow(message), file=sys.stderr)


def _blockers_satisfied(
    blockers: list[DepartureBlocker],
    *,
    robot_id: str = "",
    hold_node: str = "",
    rec: dict | None = None,
) -> bool:
    for blocker in blockers:
        progress = _robot_progress(blocker.robot_id, blocker.plan_version)
        if progress is None or progress < blocker.required_progress:
            if rec is not None:
                _log_release_wait(
                    rec,
                    (
                        "blocker",
                        robot_id,
                        hold_node,
                        blocker.robot_id,
                        blocker.plan_version,
                        blocker.required_progress,
                        progress,
                    ),
                    "MAPF HOLD_BLOCKER "
                    f"{robot_id or '?'} at {hold_node or '?'}: waiting for "
                    f"{blocker.robot_id} plan_v={blocker.plan_version} "
                    f"progress>={blocker.required_progress} "
                    f"(current={progress})",
                )
            return False
    return True


def _robot_at_plan_index(
    robot_id: str, plan: MapfPlanRequest, idx: int, rec: dict,
) -> bool:
    """True when the held robot has reached the plan waypoint it will stitch from."""
    master = state.master
    agv = master.get_agv(plan.manufacturer, plan.serial_number) if master else None
    last = agv.get_last_state() if agv else None
    if last is None:
        _log_release_wait(
            rec,
            ("self", robot_id, idx, "no_state"),
            f"MAPF HOLD_SELF {robot_id}: waiting for state before release",
        )
        return False

    expected_node = _plan_node_id(plan.waypoints[idx])
    expected_seq = rec.get("seq_base", 0) + 2 * idx
    arrived = (
        last.last_node_id == expected_node
        and last.last_node_sequence_id == expected_seq
    )
    if not arrived:
        _log_release_wait(
            rec,
            (
                "self",
                robot_id,
                idx,
                expected_node,
                expected_seq,
                last.last_node_id,
                last.last_node_sequence_id,
            ),
            "MAPF HOLD_SELF "
            f"{robot_id}: blockers clear, waiting for arrival at "
            f"{expected_node} seq={expected_seq} "
            f"(last={last.last_node_id!r} seq={last.last_node_sequence_id})",
        )
    return arrived


def _advance_releases() -> None:
    """Extend each held order's released base once its hold waypoint's
    departure blockers are satisfied, via a stitched order update (same
    order_id, bumped update id, starting at the hold waypoint per §6.6.2)."""
    master = state.master
    if master is None:
        return
    for robot_id, rec in list(state.dispatched_orders.items()):
        plan = state.mapf_plans.get(robot_id)
        if plan is None:
            continue
        last_idx = len(plan.waypoints) - 1
        hold_idx = rec["released_until"]
        if hold_idx >= last_idx:
            continue  # fully released
        hold_node = plan.waypoints[hold_idx].name
        if not _blockers_satisfied(
            plan.waypoints[hold_idx].departure_blockers,
            robot_id=robot_id,
            hold_node=hold_node,
            rec=rec,
        ):
            continue
        if not _robot_at_plan_index(robot_id, plan, hold_idx, rec):
            continue
        next_hold = next(
            (
                i
                for i in range(hold_idx + 1, last_idx + 1)
                if plan.waypoints[i].departure_blockers
            ),
            last_idx,
        )
        update_id = rec["update_id"] + 1
        order = build_plan_order(
            plan, rec["order_id"], update_id,
            released_until=next_hold, from_idx=hold_idx,
            seq_base=rec.get("seq_base", 0),
        )
        queued = master.publish_order(plan.manufacturer, plan.serial_number, order)
        if not queued:
            _log_release_wait(
                rec,
                ("publish_rejected", robot_id, hold_idx, update_id),
                "MAPF RELEASE_RETRY "
                f"{robot_id}: outbound queue rejected update {update_id} "
                f"of {rec['order_id']}",
            )
            continue
        rec["update_id"] = update_id
        rec["released_until"] = next_hold
        rec.pop("_last_wait_log", None)
        print(
            green(
                f"MAPF RELEASE {robot_id}: blockers at "
                f"{plan.waypoints[hold_idx].name} cleared; released through "
                f"{plan.waypoints[next_hold].name} as update {update_id} "
                f"of {rec['order_id']} (queued={queued})"
            ),
            file=sys.stderr,
        )


def _publish_task_complete(robot_id: str, task_id: str) -> None:
    """Emit a TaskComplete to the @RECEIVE@ fanout for the MAPF planner.

    Best-effort and self-contained (connect → publish → close), mirroring the
    scheduler's publisher: completions are infrequent, so a short-lived
    connection avoids holding a pika channel inside the asyncio event loop.
    """
    message = {"type": "TaskComplete", "robot_id": robot_id, "task_id": task_id}
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=AMQP_HOST,
                port=AMQP_PORT,
                # Bound the connect: this runs in the asyncio watcher loop, so a
                # hung broker must not stall the release watcher / /ws/state feed.
                socket_timeout=2.0,
                blocked_connection_timeout=2.0,
            )
        )
    except (pika.exceptions.AMQPError, OSError) as exc:
        print(
            f"TaskComplete for {robot_id} not sent (AMQP {AMQP_HOST}:{AMQP_PORT} "
            f"unreachable): {exc}",
            file=sys.stderr,
        )
        return
    try:
        channel = connection.channel()
        channel.exchange_declare(
            exchange=AMQP_EXCHANGE, exchange_type="fanout", durable=True
        )
        channel.basic_publish(
            exchange=AMQP_EXCHANGE, routing_key="", body=json.dumps(message)
        )
        print(
            f"TaskComplete sent: {robot_id} task={task_id or '<none>'}",
            file=sys.stderr,
        )
    finally:
        connection.close()


def _emit_completions() -> None:
    """Emit one TaskComplete per robot whose dispatched order just finished.

    Completion mirrors the stitch guard's test (``_resolve_order_identity``):
    the AGV's last state reports the dispatched ``order_id`` with an empty
    ``node_states`` (§6.6.1). The dispatched-order record is popped on the
    transition so the signal fires exactly once; a later replan for the same
    robot creates a fresh record (and a fresh order_id).
    """
    master = state.master
    if master is None:
        return
    for robot_id, rec in list(state.dispatched_orders.items()):
        plan = state.mapf_plans.get(robot_id)
        if plan is None:
            continue
        agv = master.get_agv(plan.manufacturer, plan.serial_number)
        last = agv.get_last_state() if agv else None
        completed = (
            last is not None
            and last.order_id == rec["order_id"]
            and len(last.node_states) == 0
        )
        if not completed:
            continue
        state.dispatched_orders.pop(robot_id, None)
        _publish_task_complete(robot_id, plan.task_id)


async def _release_watcher() -> None:
    while True:
        await asyncio.sleep(0.3)
        try:
            _advance_releases()
            _emit_completions()
        except Exception as exc:  # noqa: BLE001 — keep the watcher alive
            print(f"Release watcher error: {exc}", file=sys.stderr)


# ===== Status =====
def _jack_state_from_action_states(action_states) -> str | None:
    """Derive human-readable jack state from the most recent jack action state."""
    for s in reversed(list(action_states)):
        if s.action_type == "jackUp":
            if s.action_status.name in ("RUNNING", "INITIALIZING"):
                return "jacking_up"
            if s.action_status.name == "FINISHED":
                return "up"
        elif s.action_type == "jackDown":
            if s.action_status.name in ("RUNNING", "INITIALIZING"):
                return "jacking_down"
            if s.action_status.name == "FINISHED":
                return "down"
    return None


def _resolve_jack_state(
    robot_id: str,
    manufacturer: str,
    action_states,
) -> str | None:
    polled = _jack_states.get(robot_id)
    from_actions = _jack_state_from_action_states(action_states)

    # AutoXing: live WS poll is authoritative — passthrough hold/jacking_* as-is.
    if manufacturer == "Autoxing" and polled is not None:
        return polled

    return from_actions if from_actions is not None else polled


def _snap_to_map_node(x: float, y: float) -> str | None:
    """node_id of the nearest topology node to (x, y) in the master frame, or
    None when no map is loaded."""
    nodes = state.map_nodes
    if not nodes:
        return None
    return min(
        nodes.values(),
        key=lambda n: (float(n["x"]) - x) ** 2 + (float(n["y"]) - y) ** 2,
    )["node_id"]


def _effective_last_node(robot_id: str, reported: str | None) -> str | None:
    """``last_node_id`` with the direct-navigate override applied.

    The override (POST /state/last-node) wins until the AGV reports a
    last_node_id different from the one it had when the override was set — i.e.
    until a real VDA5050 order advances the node — at which point it is dropped."""
    ov = state.manual_last_node.get(robot_id)
    if ov is None:
        return reported
    if (reported or "") != ov["shadow"]:
        state.manual_last_node.pop(robot_id, None)
        return reported
    return ov["node_id"]


def _agv_state_snapshot() -> dict:
    """Live pose snapshot of every onboarded AGV, for /state and /ws/state."""
    master = _require_master()
    agvs = []
    for mfg, serial in master.get_onboarded_agvs():
        agv = master.get_agv(mfg, serial)
        last = agv.get_last_state() if agv else None
        pose = last.agv_position if last is not None else None
        has_pose = bool(pose is not None and pose.position_initialized)
        robot_id = _ROBOT_IDS.get((mfg, serial), f"{mfg}/{serial}")
        vda_action_states = list(last.action_states) if last is not None else []
        agvs.append({
            "robot_id": robot_id,
            "manufacturer": mfg,
            "serial_number": serial,
            "connection_status": agv.get_connection_status().name if agv else None,
            "last_node_id": _effective_last_node(
                robot_id, last.last_node_id if last is not None else None
            ),
            # Authoritative VDA5050 motion flag — the adapter sets it False once
            # the robot has stopped at the final node. Consumers (e.g. the
            # scheduler mission runner) gate "arrived & settled" actions like
            # jack on this so they don't fire while the robot is still moving.
            # None when we have no state yet (treat as "still moving").
            "driving": bool(last.driving) if last is not None else None,
            "x": pose.x if has_pose else None,
            "y": pose.y if has_pose else None,
            "theta": pose.theta if has_pose else None,
            "has_pose": has_pose,
            "action_states": [
                {
                    "action_id": s.action_id,
                    "action_type": s.action_type,
                    "action_status": s.action_status.name,
                    "result_description": s.result_description,
                }
                for s in vda_action_states
            ],
            "jack_state": _resolve_jack_state(robot_id, mfg, vda_action_states),
            # Master-side outbound order queue depth (orders awaiting delivery
            # behind a stitch point or departure blocker). Surfaced live so the
            # UI queue panel can flag a stuck/zombie queue.
            "pending_order_count": agv.get_pending_order_count() if agv else 0,
            "order_active": bool(last is not None and len(last.node_states) > 0),
            "battery_percent": _battery_cache.get(robot_id, {}).get("battery_percent"),
        })
    return {"type": "state", "agvs": agvs}


@app.get("/map", tags=["2 · Status"], summary="Loaded topology map (map_id, nodes, edges)")
def get_map():
    """The topology map the master validates and dispatches against. The
    scheduler's live (dry_run=false) path snaps robot poses to these nodes."""
    return {"map_id": state.map_id, "revision": state.map_revision, **state.map_data}


@app.post(
    "/map/lif",
    tags=["1 · Control"],
    summary="Replace the active topology map from a LIF document",
)
def update_map_from_lif(payload: dict):
    """Load a LIF document into the live VDA5050 master map.

    The RMF2 map server calls this after the LIF editor submits a new map. The
    update is applied immediately to /map, /map/grid, order validation, and the
    visualiser WebSocket feed. The canonical LIF file is also updated when the
    configured MASTER_MAP_FILE is writable.
    """
    master = _require_master()
    payload = _merge_active_rack_stations(payload)
    try:
        layout = _lif_document_to_layout(payload)
    except Exception as exc:  # noqa: BLE001 — report user payload errors cleanly
        raise HTTPException(400, f"Invalid LIF document: {exc}") from exc

    try:
        _apply_layout(layout, master=master)
    except Exception as exc:  # noqa: BLE001 — VDA5050 core validation failure
        raise HTTPException(422, f"VDA5050 map load failed: {exc}") from exc

    persisted = True
    warning = None
    try:
        map_path = _persist_lif_document(payload)
    except OSError as exc:
        persisted = False
        warning = f"Live map updated, but {MASTER_MAP_FILE} could not be written: {exc}"
        map_path = _resolve_path(MASTER_MAP_FILE)

    return {
        "status": "ok",
        "message": "VDA5050 map updated",
        "map_id": state.map_id,
        "revision": state.map_revision,
        "nodes": len(state.map_data.get("nodes", [])),
        "edges": len(state.map_data.get("edges", [])),
        "persisted": persisted,
        "path": str(map_path),
        "warning": warning,
    }


@app.get(
    "/map/grid",
    tags=["2 · Status"],
    summary="Planner grid view (node ids, undirected edges, blocked_edges, starts)",
)
def get_map_grid():
    """The MAPF planner's topology view, derived from the same canonical layout
    as /map: undirected ``edges``, ``blocked_edges``, and the ``nodes`` id list.
    The planner derives its integer grid by parsing the "col,row" node ids."""
    return {"mode": "real", "map_id": state.map_id, **state.grid}


@app.get(
    "/map/image/meta",
    tags=["2 · Status"],
    summary="Master-frame map image metadata (resolution, origin, pixel size)",
)
def get_map_image_meta():
    """ROS map_server metadata for the occupancy image served at /map/image:
    ``resolution`` (m/px), ``origin`` ([x, y, theta] of the image's lower-left
    corner in the master frame) and pixel ``width``/``height``. The UI uses
    these to drape the PNG behind the topology so node coords land on the image.
    404 when no image is configured."""
    if not state.map_image:
        raise HTTPException(404, "no map image configured")
    return {
        "resolution": state.map_image["resolution"],
        "origin": state.map_image["origin"],
        "width": state.map_image["width"],
        "height": state.map_image["height"],
        "url": "/map/image",
    }


@app.get(
    "/map/image",
    tags=["2 · Status"],
    summary="Master-frame occupancy map image (PNG)",
)
def get_map_image():
    """The master-frame occupancy map as a PNG. 404 when no image configured."""
    if not state.map_image:
        raise HTTPException(404, "no map image configured")
    return FileResponse(state.map_image["path"], media_type="image/png")


@app.get(
    "/transform",
    tags=["2 · Status"],
    summary="Frame transforms (maps/map_transforms.yaml)",
)
def get_transform():
    """Per-adapter robot-frame -> master-frame similarity transforms. Consumed
    by the scheduler's direct-navigate path (and any frame-aware client)."""
    return state.transform


@app.get(
    "/robots",
    tags=["2 · Status"],
    summary="Canonical robot config (identity + per-mode routes)",
)
def get_robots():
    """Unified robot config for the stack: identity (robot_id / manufacturer /
    serial_number / onboard_map / endpoint), the full per-mode ``routes``,
    plus the active-mode ``route`` and ``home`` convenience slices.

    ``robot_id`` is the single id used everywhere (scheduler/MAPF/UI/MQTT-state);
    ``endpoint`` is the robot's direct REST base/host (single source of truth for
    direct-navigate + jack control)."""
    out = []
    for r in ROBOTS:
        routes = r.get("routes", {})
        entry = {
            "robot_id": r["robot_id"],
            "manufacturer": r["manufacturer"],
            "serial_number": r["serial_number"],
            "endpoint": r.get("endpoint", ""),
            "onboard_map": r.get("onboard_map", {}),
            "routes": routes,
            "route": routes.get("real", {}),
            # Optional config convenience only; the MAPF planner does not use this
            # for idle-blocker evacuation in the real demo.
            "home": routes.get("real", {}).get("home"),
        }
        out.append(entry)
    return {"mode": "real", "robots": out}


@app.post(
    "/actions/jack/{robot_id}/{direction}",
    tags=["4 · Jack Control"],
    summary="Send jack up or down command to a robot",
    description=(
        "``robot_id`` is the unified robot id (e.g. ``autoxing-1``, ``reeman-2-blue``). "
        "``direction`` is ``up`` or ``down``. "
        "Calls the robot's REST API when an IP is configured; otherwise updates in-memory state "
        "only (no-op). AutoXing only: ``POST /services/jack_up|down``."
    ),
)
def jack_control(robot_id: str, direction: str):
    if direction not in ("up", "down"):
        raise HTTPException(400, f"direction must be 'up' or 'down', got {direction!r}")

    robot = next(
        (r for r in ROBOTS if r["robot_id"] == robot_id),
        None,
    )
    if robot is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")

    rid = robot["robot_id"]
    mfg = robot["manufacturer"]
    if mfg == "Reeman":
        raise HTTPException(
            501,
            "Jack control is not enabled for Reeman in this demo — use AutoXing only.",
        )
    # Per-robot endpoint (robots.yaml is the single source of truth), so multiple
    # robots of the same type hit their own host.
    endpoint = (robot.get("endpoint") or "").strip()

    result: dict = {"robot_id": rid, "direction": direction}

    if mfg == "Autoxing" and endpoint:
        url = f"{endpoint.rstrip('/')}/services/jack_{direction}"
        r = _robot_jack_post(url)
        result.update(r)
        if r.get("success"):
            _jack_states[rid] = "jacking_up" if direction == "up" else "jacking_down"
    else:
        _jack_states[rid] = "up" if direction == "up" else "down"
        result["success"] = True
        result["note"] = f"No {mfg} endpoint configured — state updated in memory only (no-op)."

    print(
        f"Jack {direction}: {rid} ({mfg}) → "
        f"{'OK' if result.get('success') else 'FAILED'} "
        f"state={_jack_states.get(rid)!r}",
        file=sys.stderr,
    )
    return result


@app.post(
    "/actions/stop/{robot_id}",
    tags=["4 · Jack Control"],
    summary="Stop / cancel a robot's active movement",
    description=(
        "Cancels the robot's current navigation by calling its REST API directly "
        "(no MAPF/VDA5050 — same path as direct-navigate/jack). ``robot_id`` is the "
        "unified robot id (e.g. ``autoxing-1``, ``reeman-2-blue``). "
        "AutoXing: ``PATCH /chassis/moves/current {state: cancelled}``. "
        "Reeman: ``POST /cmd/cancel_goal``. With no endpoint configured (no-op) "
        "it is a no-op success."
    ),
)
def stop_robot(robot_id: str):
    robot = next((r for r in ROBOTS if r["robot_id"] == robot_id), None)
    if robot is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")

    result = _cancel_robot_move(robot)

    # Also clear the master-side outbound order queue so the stopped robot
    # doesn't pick up a queued/stitched order moments later (mirrors the
    # /plans/mapf/reset behaviour — stop should clear the queue too).
    master = state.master
    if master is not None:
        agv = master.get_agv(robot["manufacturer"], robot["serial_number"])
        if agv is not None and agv.get_pending_order_count():
            agv.cancel_pending_orders()

    print(
        f"Stop: {robot['robot_id']} ({robot['manufacturer']}) "
        f"→ {'OK' if result.get('success') else 'FAILED'}",
        file=sys.stderr,
    )
    return result


class DirectNavigateActionRequest(BaseModel):
    x: float
    y: float
    theta: float | None = None
    goal_node: str | None = None
    wait: bool = False


def _endpoint_base(robot: dict) -> str:
    endpoint = (robot.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(
            503,
            f"No endpoint configured for {robot['robot_id']!r} in robots.yaml.",
        )
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint.rstrip("/")
    return f"http://{endpoint}".rstrip("/")


def _endpoint_host_port(base: str) -> str:
    parsed = urlparse(base if "://" in base else f"http://{base}")
    if not parsed.hostname:
        raise HTTPException(503, f"Invalid robot endpoint {base!r}.")
    if parsed.port:
        return f"{parsed.hostname}:{parsed.port}"
    return parsed.hostname


def _spellbook_dir_for(robot: dict) -> tuple[str, Path]:
    demo_root = Path(__file__).resolve().parent
    mfg = (robot.get("manufacturer") or "").strip()
    if mfg == "Autoxing":
        return mfg, demo_root / "autoxing" / "autoxing_bridge" / "spellbook"
    if mfg == "Reeman":
        return mfg, demo_root / "reeman" / "reeman_bridge" / "spellbook"
    raise HTTPException(
        501,
        f"Direct navigate not implemented for manufacturer {mfg!r}.",
    )


def _purge_spellbook_modules() -> None:
    """The spellbooks use bare imports (credentials/api_client/navigate).

    Purge those names before switching robot families so one spellbook cannot
    reuse modules imported from the other family.
    """
    prefixes = (
        "credentials",
        "api_client",
        "cancel_move",
        "get_battery_state",
        "navigate",
        "ws_helper",
    )
    for name in list(sys.modules):
        if name in prefixes or any(name.startswith(f"{p}.") for p in prefixes):
            sys.modules.pop(name, None)


def _front_spellbook_path(spellbook_dir: Path) -> None:
    demo_root = str(Path(__file__).resolve().parent)
    spellbook = str(spellbook_dir)
    sys.path[:] = [
        p for p in sys.path
        if p not in {demo_root, spellbook}
    ]
    sys.path.insert(0, demo_root)
    sys.path.insert(0, spellbook)


def _activate_spellbook_for_robot(robot: dict, base: str) -> tuple[str, Path]:
    mfg, spellbook_dir = _spellbook_dir_for(robot)
    host_port = _endpoint_host_port(base)
    if mfg == "Autoxing":
        os.environ["AUTOXING_BASE_URL"] = base
        os.environ["AUTOXING_ROBOT_IP"] = host_port
    elif mfg == "Reeman":
        os.environ["REEMAN_BASE_URL"] = base
        os.environ["REEMAN_ROBOT_IP"] = host_port

    _purge_spellbook_modules()
    _front_spellbook_path(spellbook_dir)
    credentials = importlib.import_module("credentials")
    constants = getattr(credentials, "CONSTANTS", None)
    refresh = getattr(constants, "refresh", None)
    if callable(refresh):
        refresh()
    return mfg, spellbook_dir


def _load_spellbook_module(
    robot: dict,
    base: str,
    module_name: str,
) -> tuple[str, ModuleType]:
    mfg, spellbook_dir = _activate_spellbook_for_robot(robot, base)
    if not (spellbook_dir / f"{module_name}.py").is_file():
        raise HTTPException(
            503,
            f"Missing spellbook {module_name}.py: {spellbook_dir}",
        )
    try:
        return mfg, importlib.import_module(module_name)
    except Exception as exc:
        raise HTTPException(
            503,
            f"Could not load {mfg} {module_name} spellbook for "
            f"{robot['robot_id']}: {exc}",
        ) from exc


def _load_direct_navigate_spellbook(robot: dict, base: str) -> tuple[str, ModuleType]:
    return _load_spellbook_module(robot, base, "navigate")


def _to_robot_frame(robot_id: str, x: float, y: float, theta: float | None) -> tuple[float, float, float | None]:
    try:
        params = state.transform["adapters"][robot_id]["robot_to_master"]
        tx = float(params["tx"])
        ty = float(params["ty"])
        angle = float(params["theta"])
        scale = float(params.get("scale", 1.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            503,
            f"Transform for adapter {robot_id!r} unavailable from /transform: {exc}",
        ) from exc

    s = scale * math.cos(angle)
    r = scale * math.sin(angle)
    det = s * s + r * r
    dx = x - tx
    dy = y - ty
    rx = (s * dx + r * dy) / det
    ry = (-r * dx + s * dy) / det
    if theta is None:
        return rx, ry, None
    rtheta = math.atan2(math.sin(theta - angle), math.cos(theta - angle))
    return rx, ry, rtheta


def _set_manual_last_node(robot_id: str, req: DirectNavigateActionRequest) -> None:
    if req.goal_node and req.goal_node in state.map_nodes:
        node = req.goal_node
    else:
        node = _snap_to_map_node(req.x, req.y)
    if node is None:
        return
    cfg = _robot_cfg(robot_id)
    if cfg is None:
        return
    master = _require_master()
    agv = master.get_agv(cfg["manufacturer"], cfg["serial_number"])
    last = agv.get_last_state() if agv else None
    shadow = last.last_node_id if last is not None else ""
    state.manual_last_node[robot_id] = {"node_id": node, "shadow": shadow}


@app.post(
    "/actions/direct-navigate/{robot_id}",
    tags=["4 · Jack Control"],
    summary="Direct navigate via the robot spellbook selected from master /robots",
)
def direct_navigate_action(robot_id: str, req: DirectNavigateActionRequest):
    robot = _robot_cfg(robot_id)
    if robot is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")

    base = _endpoint_base(robot)
    arrived: bool | None = None
    robot_frame: dict | None = None
    dispatched = {"x": req.x, "y": req.y, "theta": req.theta}

    with _SPELLBOOK_IMPORT_LOCK:
        mfg, nav = _load_direct_navigate_spellbook(robot, base)
        if mfg == "Autoxing":
            robot_url = f"{base}/chassis/moves"
            result = nav.navigate(
                float(req.x),
                float(req.y),
                req.theta,
                creator="vda5050_master_direct_nav",
            )
            if req.wait:
                nav.monitor_move_after_dispatch()
                arrived = True
        else:
            rx, ry, rtheta = _to_robot_frame(robot_id, float(req.x), float(req.y), req.theta)
            robot_url = f"{base}/cmd/nav"
            result = nav.navigate(
                rx,
                ry,
                rtheta,
                creator="vda5050_master_direct_nav",
            )
            robot_frame = {"x": rx, "y": ry, "theta": rtheta}
            if req.wait:
                nav.monitor_reeman_move_after_dispatch()
                arrived = True

    if result is None:
        raise HTTPException(502, f"{mfg} spellbook navigate returned no response.")
    if not isinstance(result, dict):
        result = {"raw": result}

    _set_manual_last_node(robot_id, req)
    print(
        f"Direct navigate: {robot_id} ({mfg}) -> spellbook {robot_url} OK",
        file=sys.stderr,
    )
    return {
        "robot_id": robot_id,
        "goal": {
            "x": req.x,
            "y": req.y,
            "theta": req.theta,
            "goal_node": req.goal_node,
        },
        "dispatched": dispatched,
        "robot_frame": robot_frame,
        "robot_url": robot_url,
        "robot_response": result,
        "arrived": arrived,
        "wait": req.wait,
    }


class InstantActionRequest(BaseModel):
    actions: list[dict]
    """Each dict: action_type (str, required), action_id (str, auto-generated if absent),
    blocking_type (str: HARD/SOFT/NONE, default HARD), action_description (str, optional)."""


@app.post(
    "/actions/instant/{robot_id}",
    tags=["4 · Jack Control"],
    summary="Send VDA5050 instant actions to a robot",
    description=(
        "Dispatches instant actions to the named robot via ``assign_instant_actions`` "
        "over MQTT. ``robot_id`` is the unified robot id (e.g. ``autoxing-1``). "
        "**Note**: the adapter's C++ instant-action subscription is not yet wired — "
        "actions are queued and published but won't be received by the adapter until "
        "that C++ support lands. Use node actions (``actions`` field on ``PlanWaypoint``) "
        "for orchestrated jack operations."
    ),
)
def send_instant_actions(robot_id: str, req: InstantActionRequest):
    master = _require_master()
    robot = next(
        (r for r in ROBOTS if r["robot_id"] == robot_id),
        None,
    )
    if robot is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")

    ia = vda.InstantActions()
    ia.header.manufacturer = robot["manufacturer"]
    ia.header.serial_number = robot["serial_number"]
    ia.header.version = PROTOCOL_VERSION

    built = []
    for a in req.actions:
        act = vda.Action()
        act.action_type = a["action_type"]
        act.action_id = a.get("action_id") or str(uuid.uuid4())
        bt = a.get("blocking_type", "HARD").upper()
        act.blocking_type = (
            vda.BlockingType.HARD if bt == "HARD"
            else vda.BlockingType.SOFT if bt == "SOFT"
            else vda.BlockingType.NONE
        )
        if "action_description" in a:
            act.action_description = a["action_description"]
        built.append(act)
    ia.actions = built

    result = master.assign_instant_actions(robot["manufacturer"], robot["serial_number"], ia)
    print(
        f"Instant actions → {robot_id}: decision={result.decision.name} "
        f"actions={[a.action_type for a in built]}",
        file=sys.stderr,
    )
    return {
        "robot_id": robot["robot_id"],
        "decision": result.decision.name,
        "success": bool(result),
        "errors": [e.error_type for e in result.errors],
        "actions": [{"action_type": a.action_type, "action_id": a.action_id} for a in built],
    }


@app.get("/state", tags=["2 · Status"], summary="Live AGV poses (x, y, theta)")
def live_state():
    return _agv_state_snapshot()


@app.websocket("/ws/state")
async def ws_state(websocket: WebSocket):
    """Visualiser feed: map messages on connect/update, then live AGV poses.

    First message: ``{"type": "map", "nodes": [...], "edges": [...]}``.
    A fresh map message is sent whenever the active map is replaced.
    Then every ~300 ms: the /state snapshot (``{"type": "state", "agvs": […]}``).
    """
    await websocket.accept()
    sent_map_revision = -1
    try:
        while True:
            if sent_map_revision != state.map_revision:
                await websocket.send_json({
                    "type": "map",
                    "map_id": state.map_id,
                    "revision": state.map_revision,
                    **state.map_data,
                })
                sent_map_revision = state.map_revision
            try:
                snapshot = _agv_state_snapshot()
            except HTTPException as exc:  # master not initialized
                snapshot = {"type": "error", "detail": exc.detail, "agvs": []}
            await websocket.send_json(snapshot)
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """VDA5050 MQTT traffic feed: buffered history on connect, then live.

    Every message is sent as ``{"type": "log", ...entry}`` — first the
    buffered history (most-recent ``LogTap`` history_size entries), then new
    entries as they arrive on the broker. See ``log_tap.py`` for the entry
    schema.
    """
    await websocket.accept()
    queue = log_tap.register()
    try:
        for entry in log_tap.history():
            await websocket.send_json({"type": "log", **entry})
        while True:
            entry = await queue.get()
            await websocket.send_json({"type": "log", **entry})
    except WebSocketDisconnect:
        pass
    finally:
        log_tap.unregister(queue)


@app.get("/status", tags=["2 · Status"], summary="Master and AGV state")
def status():
    master = _require_master()
    agvs = []
    for mfg, serial in master.get_onboarded_agvs():
        agv = master.get_agv(mfg, serial)
        last = agv.get_last_state() if agv else None
        pose = last.agv_position if last is not None else None
        rid = _ROBOT_IDS.get((mfg, serial), f"{mfg}/{serial}")
        agvs.append({
            "robot_id": rid,
            "manufacturer": mfg,
            "serial_number": serial,
            "connection_status": agv.get_connection_status().name if agv else None,
            "operational_state": agv.get_operational_state().name if agv else None,
            "last_node_id": _effective_last_node(
                rid, last.last_node_id if last is not None else None
            ),
            "has_pose": bool(pose is not None and pose.position_initialized),
            "pending_order_count": agv.get_pending_order_count() if agv else 0,
            "order_active": bool(last is not None and len(last.node_states) > 0),
        })
    return {"broker_connected": master.is_connected(), "agvs": agvs}


@app.get(
    "/orders/queue",
    tags=["2 · Status"],
    summary="Per-robot master-side outbound order queue",
)
def orders_queue():
    """Snapshot of each AGV's master-side outbound order queue: how many orders
    are pending delivery (queued behind a stitch point or a departure blocker),
    plus the active order id/update and the current MAPF goal. Read-only — the
    same ``pending_order_count`` is also streamed on /ws/state so the UI panel
    above Direct Control updates live; this endpoint is for API/debug use."""
    master = _require_master()
    robots = []
    for mfg, serial in master.get_onboarded_agvs():
        agv = master.get_agv(mfg, serial)
        last = agv.get_last_state() if agv else None
        rid = _ROBOT_IDS.get((mfg, serial), f"{mfg}/{serial}")
        plan = state.mapf_plans.get(rid)
        dispatched = state.dispatched_orders.get(rid)
        robots.append({
            "robot_id": rid,
            "manufacturer": mfg,
            "serial_number": serial,
            "pending_order_count": agv.get_pending_order_count() if agv else 0,
            "active_order_id": (last.order_id or None) if last is not None else None,
            "active_order_update_id": (
                last.order_update_id if last is not None else None
            ),
            "last_node_id": _effective_last_node(
                rid, last.last_node_id if last is not None else None
            ),
            "order_active": bool(last is not None and len(last.node_states) > 0),
            "goal": plan.waypoints[-1].name if plan and plan.waypoints else None,
            "dispatched_order_id": (
                dispatched.get("order_id") if dispatched else None
            ),
        })
    return {"broker_connected": master.is_connected(), "robots": robots}


class LastNodeOverrideRequest(BaseModel):
    """Pin a robot's last_node_id to a map node: ``node_id`` directly, or
    master-frame ``x``/``y`` (snapped to the nearest node)."""

    node_id: str | None = None
    x: float | None = None
    y: float | None = None


@app.post(
    "/state/last-node/{robot_id}",
    tags=["2 · Status"],
    summary="Override a robot's last_node_id (e.g. after direct-navigate)",
    description=(
        "Pins ``robot_id``'s reported ``last_node_id`` to a map node. "
        "direct-navigate bypasses VDA5050, so the order-driven last_node_id "
        "would otherwise stay stale and the scheduler/MAPF planner would plan "
        "from the wrong node. The override is shadowed against the AGV's current "
        "last_node_id and dropped automatically once a real VDA5050 order "
        "advances the node. Provide ``node_id``, or master-frame ``x``/``y``."
    ),
)
def set_last_node(robot_id: str, req: LastNodeOverrideRequest):
    cfg = _robot_cfg(robot_id)
    if cfg is None:
        raise HTTPException(404, f"Robot {robot_id!r} not in robots.yaml")
    if req.node_id is not None:
        if req.node_id not in state.map_nodes:
            raise HTTPException(
                404, f"node_id {req.node_id!r} is not on the master map."
            )
        node = req.node_id
    elif req.x is not None and req.y is not None:
        node = _snap_to_map_node(req.x, req.y)
        if node is None:
            raise HTTPException(502, "No master map loaded; cannot snap pose.")
    else:
        raise HTTPException(422, "Provide node_id, or x and y.")

    master = _require_master()
    agv = master.get_agv(cfg["manufacturer"], cfg["serial_number"])
    last = agv.get_last_state() if agv else None
    shadow = last.last_node_id if last is not None else ""
    state.manual_last_node[robot_id] = {"node_id": node, "shadow": shadow}
    print(
        f"last_node override: {robot_id} → {node} (shadowing {shadow!r})",
        file=sys.stderr,
    )
    return {"robot_id": robot_id, "last_node_id": node, "shadowing": shadow}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


class _StatePollAccessLogFilter(logging.Filter):
    """Drop uvicorn access-log lines for high-frequency GET /state polls."""

    def filter(self, record: logging.LogRecord) -> bool:
        return 'GET /state ' not in record.getMessage()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disable-state-log",
        action="store_true",
        help="Suppress uvicorn access logs for GET /state poll spam.",
    )
    args = parser.parse_args()
    port = int(os.environ.get("VDA5050_PORT", "8000"))
    if args.disable_state_log:
        logging.getLogger("uvicorn.access").addFilter(_StatePollAccessLogFilter())
    uvicorn.run(app, host="0.0.0.0", port=port)

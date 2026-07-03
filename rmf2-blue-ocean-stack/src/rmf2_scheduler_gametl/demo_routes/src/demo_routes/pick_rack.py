# Copyright 2024-2026 ROS Industrial Consortium Asia Pacific
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""AutoXing pick_rack demo route — delegates to autoxing_bridge/spellbook/pick_rack.py."""

from __future__ import annotations

import json
import os
import threading
import time
from types import ModuleType
from typing import Annotated, Any, Literal
import urllib.error
import urllib.request

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from demo_routes.blue_ocean import _master_get, load_robot_config
from demo_routes.direct_navigate import _autoxing_base_url
from demo_routes.mission import (
    MissionLeg,
    MissionState,
    _finish_mission,
    _is_mission_cancelled,
    _mission_state,
    _missions,
    _missions_lock,
    _set_leg,
    _wait_for_jack,
)
from demo_routes.spellbook_loader import load_pick_rack

router = APIRouter(tags=["Demo"])

DETECT_SLEEP_S = 3.0

# UI display labels for onboard-map rack overlay names (spellbook ids).
RACK_UI_LABELS: dict[str, str] = {
    "Rack_East": "East",
    "Rack_FarNorth": "FarNorth",
    "Rack_South": "South",
    "Rack_West": "West",
}

# The active LIF stations carry rack x/y but no orientation, while AutoXing's
# rack move payload requires yaw. Keep these in sync with the known rack overlay
# calibration for the supported demo floors; unknown future racks fall back to 0.
RACK_ORI_DEFAULTS: dict[str, float] = {
    "Rack_1": 0.0175,
    "Rack_2": 3.1416,
    "Rack_3": 3.1416,
    "Rack_East": 4.6949,
    "Rack_FarNorth": 6.2657,
    "Rack_South": 3.1241,
    "Rack_West": 1.5533,
}

RACK_NODE_DEFAULTS: dict[str, str] = {
    "Rack_1": "3,4",
    "Rack_2": "5,4",
    "Rack_3": "6,6",
    "Rack_East": "4,6",
    "Rack_FarNorth": "4,10",
    "Rack_South": "3,4",
    "Rack_West": "2,5",
}


def _rack_label(rack_id: str) -> str:
    if rack_id in RACK_UI_LABELS:
        return RACK_UI_LABELS[rack_id]
    if rack_id.startswith("Rack_"):
        return rack_id.removeprefix("Rack_")
    return rack_id


class RackPoint(BaseModel):
    id: str
    label: str | None
    x: float
    y: float
    ori: float


class RackPointsResponse(BaseModel):
    points: list[RackPoint]


class PickRackRequest(BaseModel):
    robot_id: str
    mode: Literal["pickup", "dropoff", "full"] = Field(
        "full",
        description="pickup = detect+align+jack up; dropoff = unload+jack down; full = both",
    )
    pickup: str | None = Field(None, description="Rack overlay name for pickup, e.g. Rack_West")
    putdown: str | None = Field(
        None, description="Rack overlay name for putdown, e.g. Rack_FarNorth"
    )


def _autoxing_robot(robot_id: str) -> dict:
    config = {r["robot_id"]: r for r in load_robot_config()}
    robot_cfg = config.get(robot_id)
    if robot_cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown robot_id {robot_id!r} — valid: {sorted(config)}.",
        )
    if (robot_cfg.get("manufacturer") or "").strip().lower() != "autoxing":
        raise HTTPException(
            status_code=422,
            detail=f"pick_rack is AutoXing-only — {robot_id!r} is not AutoXing.",
        )
    return robot_cfg


def _spellbook_for_robot(robot_id: str) -> tuple[ModuleType, str]:
    robot_cfg = _autoxing_robot(robot_id)
    endpoint = _autoxing_base_url(robot_cfg)
    try:
        return load_pick_rack(endpoint), endpoint
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _fetch_rack_points(sb: ModuleType) -> dict[str, dict[str, Any]]:
    try:
        rack_points = sb.get_rack_points()
    except SystemExit as exc:
        raise RuntimeError(str(exc) or "get_rack_points failed") from exc
    for rack_id, pose in rack_points.items():
        if isinstance(pose, dict) and rack_id in RACK_NODE_DEFAULTS:
            pose.setdefault("node_id", RACK_NODE_DEFAULTS[rack_id])
    return rack_points


def _rack_station_float(station: dict, *keys: str) -> float | None:
    for key in keys:
        value = station.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _fetch_master_rack_points() -> dict[str, dict[str, Any]] | None:
    """Rack poses from the active VDA5050 master map.

    This is the normal UI path: the dashboard renders rack buttons from
    ``GET /map`` stations, so validation and dispatch should accept the same
    station ids without depending on the robot's ``GET /maps/`` endpoint.
    """
    try:
        data = _master_get("/map")
    except HTTPException:
        return None

    stations = data.get("stations", [])
    if not isinstance(stations, list):
        return None

    rack_points: dict[str, dict[str, Any]] = {}
    for station in stations:
        if not isinstance(station, dict):
            continue
        rack_id = str(station.get("station_name") or "")
        if not rack_id.startswith("Rack_"):
            continue

        x = _rack_station_float(station, "x", "target_x")
        y = _rack_station_float(station, "y", "target_y")
        if x is None or y is None:
            continue

        ori = _rack_station_float(station, "ori", "theta", "yaw")
        node_ids = station.get("node_ids", [])
        node_id = node_ids[0] if isinstance(node_ids, list) and node_ids else None
        rack_points[rack_id] = {
            "x": x,
            "y": y,
            "ori": ori if ori is not None else RACK_ORI_DEFAULTS.get(rack_id, 0.0),
            "node_id": node_id or RACK_NODE_DEFAULTS.get(rack_id),
        }

    return dict(sorted(rack_points.items())) or None


def _rack_points_for_robot(robot_id: str) -> dict[str, dict[str, Any]]:
    """Best rack point source for a UI request.

    Prefer the master map so pickup/dropoff stays available when AutoXing
    ``/maps/`` is unavailable. Fall back to the spellbook overlay parser for
    older deployments where the master map has no rack stations.
    """
    _autoxing_robot(robot_id)

    rack_points = _fetch_master_rack_points()
    if rack_points is not None:
        return rack_points

    sb, _ = _spellbook_for_robot(robot_id)
    return _fetch_rack_points(sb)


def _agv_pose(robot_id: str) -> tuple[float, float] | None:
    try:
        agvs = _master_get("/state").get("agvs", [])
    except HTTPException:
        return None
    for agv in agvs:
        if (
            isinstance(agv, dict)
            and agv.get("robot_id") == robot_id
            and agv.get("has_pose")
            and agv.get("x") is not None
            and agv.get("y") is not None
        ):
            try:
                return float(agv["x"]), float(agv["y"])
            except (TypeError, ValueError):
                return None
    return None


def _set_master_last_node_near_robot(
    robot_id: str,
    fallback_pose: dict[str, Any],
) -> None:
    """Pin master's effective last_node_id after rack moves that bypass VDA5050."""
    node_id = fallback_pose.get("node_id")
    body_obj: dict[str, Any]
    if node_id:
        body_obj = {"node_id": node_id}
    else:
        x, y = _agv_pose(robot_id) or (fallback_pose["x"], fallback_pose["y"])
        body_obj = {"x": x, "y": y}
    body = json.dumps(body_obj).encode()
    master_url = os.environ.get("MASTER_URL", "http://vda5050:8000").rstrip("/")
    req = urllib.request.Request(
        f"{master_url}/state/last-node/{robot_id}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except (urllib.error.URLError, TimeoutError, OSError):
        pass


def _run_pick_rack(
    robot_id: str,
    mode: str,
    pickup: str | None,
    putdown: str | None,
    rack_points: dict[str, dict[str, Any]],
) -> None:
    """Background thread: spellbook pick_rack steps for pickup / dropoff / full."""
    try:
        sb, _ = _spellbook_for_robot(robot_id)

        leg_idx = 0

        if mode in ("pickup", "full"):
            assert pickup is not None
            pickup_label = _rack_label(pickup)
            if _is_mission_cancelled(robot_id):
                _finish_mission(robot_id, "cancelled", "cancelled by user")
                return
            _set_leg(robot_id, leg_idx, status="navigating", detail="rack size detection")
            sb.start_rack_detection()
            time.sleep(DETECT_SLEEP_S)
            _set_leg(robot_id, leg_idx, status="done")
            leg_idx += 1

            if _is_mission_cancelled(robot_id):
                _finish_mission(robot_id, "cancelled", "cancelled by user")
                return
            _set_leg(robot_id, leg_idx, status="navigating", detail="align_with_rack")
            if sb.align_with_rack(pickup, rack_points) is None:
                raise RuntimeError("align_with_rack request failed")
            sb.monitor_move_after_dispatch()
            _set_master_last_node_near_robot(robot_id, rack_points[pickup])
            _set_leg(robot_id, leg_idx, status="done", detail=f"aligned at {pickup_label}")
            leg_idx += 1

            if _is_mission_cancelled(robot_id):
                _finish_mission(robot_id, "cancelled", "cancelled by user")
                return
            _set_leg(robot_id, leg_idx, status="jacking", jack="up", detail="jacking up")
            sb.jack_up()
            if not _wait_for_jack(robot_id, "up"):
                if _is_mission_cancelled(robot_id):
                    _finish_mission(robot_id, "cancelled", "cancelled by user")
                    return
                raise RuntimeError("timed out waiting for jack up (hold/up)")
            _set_leg(robot_id, leg_idx, status="done", jack="up", detail="jack up complete")
            leg_idx += 1

        if mode in ("dropoff", "full"):
            assert putdown is not None
            putdown_label = _rack_label(putdown)
            if _is_mission_cancelled(robot_id):
                _finish_mission(robot_id, "cancelled", "cancelled by user")
                return
            _set_leg(robot_id, leg_idx, status="navigating", detail="to_unload_point")
            if sb.move_to_unload_point(putdown, rack_points) is None:
                raise RuntimeError("move to unload point request failed")
            sb.monitor_move_after_dispatch()
            _set_master_last_node_near_robot(robot_id, rack_points[putdown])
            _set_leg(robot_id, leg_idx, status="done", detail=f"at unload {putdown_label}")
            leg_idx += 1

            if _is_mission_cancelled(robot_id):
                _finish_mission(robot_id, "cancelled", "cancelled by user")
                return
            _set_leg(robot_id, leg_idx, status="jacking", jack="down", detail="jacking down")
            sb.jack_down()
            if not _wait_for_jack(robot_id, "down"):
                if _is_mission_cancelled(robot_id):
                    _finish_mission(robot_id, "cancelled", "cancelled by user")
                    return
                raise RuntimeError("timed out waiting for jack down")
            _set_leg(robot_id, leg_idx, status="done", jack="down", detail="jack down complete")

        _finish_mission(robot_id, "completed")
    except Exception as exc:  # noqa: BLE001
        _finish_mission(robot_id, "error", f"pick_rack crashed: {exc}")


def _validate_pick_rack_request(
    req: PickRackRequest,
    rack_points: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None, str]:
    """Return (pickup, putdown, mission_detail) after mode-specific validation."""
    valid = sorted(rack_points)
    mode = req.mode

    if mode == "pickup":
        if not req.pickup:
            raise HTTPException(status_code=422, detail="pickup mode requires pickup.")
        if req.pickup not in rack_points:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown pickup {req.pickup!r} — valid: {valid}.",
            )
        label = _rack_label(req.pickup)
        return req.pickup, None, f"pick up @ {label}"

    if mode == "dropoff":
        if not req.putdown:
            raise HTTPException(status_code=422, detail="dropoff mode requires putdown.")
        if req.putdown not in rack_points:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown putdown {req.putdown!r} — valid: {valid}.",
            )
        label = _rack_label(req.putdown)
        return None, req.putdown, f"drop off @ {label}"

    if not req.pickup or not req.putdown:
        raise HTTPException(
            status_code=422,
            detail="full mode requires pickup and putdown.",
        )
    if req.pickup not in rack_points:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown pickup {req.pickup!r} — valid: {valid}.",
        )
    if req.putdown not in rack_points:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown putdown {req.putdown!r} — valid: {valid}.",
        )
    if req.pickup == req.putdown:
        raise HTTPException(
            status_code=422,
            detail="Pickup and putdown must be different.",
        )
    pickup_label = _rack_label(req.pickup)
    putdown_label = _rack_label(req.putdown)
    return req.pickup, req.putdown, f"{pickup_label} → {putdown_label}"


def _legs_for_mode(
    mode: str,
    pickup: str | None,
    putdown: str | None,
) -> list[dict]:
    legs: list[MissionLeg] = []
    if mode in ("pickup", "full"):
        assert pickup is not None
        pickup_label = _rack_label(pickup)
        legs.extend([
            MissionLeg(goal="detect", is_rack=True, detail="rack size detection"),
            MissionLeg(
                goal=f"align:{pickup_label}",
                is_rack=True,
                detail="align_with_rack",
            ),
            MissionLeg(goal="jack_up", is_rack=True, jack="up", detail="jacking up"),
        ])
    if mode in ("dropoff", "full"):
        assert putdown is not None
        putdown_label = _rack_label(putdown)
        legs.extend([
            MissionLeg(
                goal=f"unload:{putdown_label}",
                is_rack=True,
                detail="to_unload_point",
            ),
            MissionLeg(
                goal="jack_down",
                is_rack=True,
                jack="down",
                detail="jacking down",
            ),
        ])
    return [leg.model_dump() for leg in legs]


@router.get(
    "/pick-rack/points",
    response_model=RackPointsResponse,
    summary="Rack poses from the active demo map",
)
def list_pick_rack_points(
    robot_id: str = Query("autoxing-1", description="AutoXing robot to query"),
) -> RackPointsResponse:
    try:
        rack_points = _rack_points_for_robot(robot_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    points = [
        RackPoint(
            id=rack_id,
            label=_rack_label(rack_id),
            x=float(pose["x"]),
            y=float(pose["y"]),
            ori=float(pose["ori"]),
        )
        for rack_id, pose in sorted(rack_points.items())
    ]
    return RackPointsResponse(points=points)


@router.post(
    "/pick-rack",
    response_model=MissionState,
    status_code=202,
    summary="Pick rack [spellbook · direct robot REST · no MAPF]",
)
def pick_rack(
    req: Annotated[
        PickRackRequest,
        Body(openapi_examples={
            "pickup_west": {
                "summary": "Pick up only at West",
                "value": {
                    "robot_id": "autoxing-1",
                    "mode": "pickup",
                    "pickup": "Rack_West",
                },
            },
            "dropoff_farnorth": {
                "summary": "Drop off only at FarNorth",
                "value": {
                    "robot_id": "autoxing-1",
                    "mode": "dropoff",
                    "putdown": "Rack_FarNorth",
                },
            },
            "west_to_farnorth": {
                "summary": "Pick at West → drop at FarNorth",
                "value": {
                    "robot_id": "autoxing-1",
                    "mode": "full",
                    "pickup": "Rack_West",
                    "putdown": "Rack_FarNorth",
                },
            },
        }),
    ],
) -> MissionState:
    """Run spellbook ``pick_rack`` on AutoXing. Returns 202; poll
    ``GET /demo/mission/{robot_id}``. Cancel with ``POST /demo/cancel-navigation``."""
    try:
        rack_points = _rack_points_for_robot(req.robot_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _spellbook_for_robot(req.robot_id)

    pickup, putdown, mission_detail = _validate_pick_rack_request(req, rack_points)

    with _missions_lock:
        active = _missions.get(req.robot_id)
        if active is not None and active["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail=f"{req.robot_id} already has a running mission — wait for it "
                "to finish or cancel it.",
            )

    legs = _legs_for_mode(req.mode, pickup, putdown)
    with _missions_lock:
        _missions[req.robot_id] = {
            "robot_id": req.robot_id,
            "dry_run": False,
            "status": "running",
            "cancelled": False,
            "current_leg": 0,
            "legs": legs,
            "detail": mission_detail,
        }

    threading.Thread(
        target=_run_pick_rack,
        args=(req.robot_id, req.mode, pickup, putdown, rack_points),
        daemon=True,
    ).start()

    return _mission_state(req.robot_id)

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

"""Direct robot navigate — bypass MAPF/AMQP, but delegate robot REST to master.

The scheduler resolves UI-friendly goals to master-frame coordinates, then asks
the VDA5050 master to dispatch via the robot endpoint from its canonical
``/robots`` config.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Imported after blue_ocean module init; _master_get is the shared VDA5050
# master HTTP helper (the master is the single map/transform/robots authority).
from demo_routes.blue_ocean import _master_get, load_robot_config

router = APIRouter(tags=["Demo"])

HTTP_TIMEOUT_S = 10.0


class GoalCoords(BaseModel):
    x: float
    y: float
    theta: float | None = None
    goal_node: str | None = None


class DispatchedCoords(BaseModel):
    x: float
    y: float
    theta: float | None = None


class DirectNavigateRequest(BaseModel):
    robot_id: str
    goal_node: str | None = None
    x: float | None = None
    y: float | None = None
    theta: float | None = None
    wait: bool = False


class DirectNavigateResponse(BaseModel):
    robot_id: str
    goal: GoalCoords
    dispatched: DispatchedCoords
    robot_frame: DispatchedCoords | None = None
    robot_url: str
    robot_response: dict[str, Any]
    arrived: bool | None = None
    wait: bool


@lru_cache
def _master_map_nodes() -> dict[str, dict[str, float]]:
    """Master-frame node coords from the VDA5050 master /map (node_id -> {x,y}).

    Replaces the previously-bundled gametl_demo_map.real.json copy — the master
    is now the single source of map truth."""
    nodes = _master_get("/map").get("nodes", [])
    if not nodes:
        # Don't let lru_cache memoize an empty map (master up but not yet
        # loaded); raise so the next call retries once the map is available.
        raise HTTPException(status_code=502, detail="VDA5050 master returned an empty /map.")
    return {n["node_id"]: {"x": float(n["x"]), "y": float(n["y"])} for n in nodes}


def resolve_goal(req: DirectNavigateRequest) -> GoalCoords:
    has_node = req.goal_node is not None
    has_xy = req.x is not None and req.y is not None
    if has_node == has_xy:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one goal form: goal_node, or x and y.",
        )
    if req.goal_node is not None:
        node = _master_map_nodes().get(req.goal_node)
        if node is None:
            raise HTTPException(
                status_code=404,
                detail=f"goal_node {req.goal_node!r} is not on the master map "
                f"— valid: {sorted(_master_map_nodes())}.",
            )
        return GoalCoords(x=node["x"], y=node["y"], theta=req.theta, goal_node=req.goal_node)
    assert req.x is not None and req.y is not None
    return GoalCoords(x=req.x, y=req.y, theta=req.theta)


def _robot_endpoint(robot: dict) -> str:
    endpoint = (robot.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(
            status_code=503,
            detail=f"No `endpoint` set for {robot.get('robot_id')!r} in master /robots.",
        )
    return endpoint


def _autoxing_base_url(robot: dict) -> str:
    endpoint = _robot_endpoint(robot)
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint.rstrip("/")
    return f"http://{endpoint}".rstrip("/")


def _wait_timeout_s() -> float:
    return float(os.environ.get("DIRECT_NAV_WAIT_TIMEOUT_S", "300"))


@router.post(
    "/direct-navigate",
    response_model=DirectNavigateResponse,
    summary="Direct Navigate [VDA5050 master spellbook dispatch | No MAPF]",
)
def direct_navigate(req: DirectNavigateRequest) -> DirectNavigateResponse:
    """Navigate one robot through the VDA5050 master's spellbook action.

    Accepts master-frame ``goal_node`` or ``x``/``y``. Reeman goals are
    transformed to robot-frame coordinates by the master before dispatch.

    **Heading:** ``theta`` is optional. When omitted (the default for robot-card
    Reset and Direct Control x/y sends), dispatch delegates to spellbook
    navigation behavior. Pass ``theta`` only for an explicit manual heading
    override.

    ``robot_id`` may be the unified robot_id or the serial. The scheduler does
    not read robot REST endpoints; the master resolves them from its ``/robots``
    source of truth.
    """
    robots = load_robot_config()
    robot = next(
        (
            r
            for r in robots
            if req.robot_id in (r.get("robot_id"), r.get("master_id"), r.get("serial_number"))
        ),
        None,
    )
    if robot is None:
        valid = sorted(r.get("robot_id") for r in robots if r.get("robot_id"))
        raise HTTPException(
            status_code=404,
            detail=f"Unknown robot_id {req.robot_id!r} — valid robot_ids: {valid}.",
        )

    robot_id = robot["robot_id"]
    goal = resolve_goal(req)

    master_url = os.environ.get("MASTER_URL", "http://vda5050:8000").rstrip("/")
    try:
        resp = requests.post(
            f"{master_url}/actions/direct-navigate/{robot_id}",
            json=goal.model_dump() | {"wait": req.wait},
            timeout=_wait_timeout_s() + HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        detail = str(exc)
        if exc.response is not None:
            detail = f"{exc} — body: {exc.response.text[:500]}"
        raise HTTPException(
            status_code=502,
            detail=f"VDA5050 master direct navigate failed: {detail}",
        ) from exc

    return DirectNavigateResponse.model_validate(data)

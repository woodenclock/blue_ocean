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

"""Dry-run MAPF preview for the VDA visualiser — CBS without dispatch."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from demo_routes.blue_ocean import (
    _master_nodes,
    load_robot_config,
    resolve_live_start,
)

router = APIRouter(tags=["Demo"])


class MapfPreviewTask(BaseModel):
    robot_id: str
    goal_location: str


class MapfPreviewRequest(BaseModel):
    """Goals queued on the dashboard before Apply — live starts from /state."""

    tasks: list[MapfPreviewTask] = Field(..., min_length=1)
    dry_run: bool = False


class MapfPreviewResponse(BaseModel):
    plans: dict
    error: str | None = None


def _planner_preview_url() -> str:
    host = os.environ.get("MAPF_PLANNER_HOST", "mapf-planner")
    port = os.environ.get("MAPF_PREVIEW_PORT", "8092")
    return f"http://{host}:{port}/preview"


@router.post(
    "/mapf-preview",
    response_model=MapfPreviewResponse,
    summary="Preview CBS MAPF plans (no dispatch)",
)
def mapf_preview(req: MapfPreviewRequest) -> MapfPreviewResponse:
    """Run the real MAPF stack on queued goals without AMQP or VDA5050 dispatch.

    Starts come from live robot localization (or dry_run routes when
    ``dry_run=true``). Idle fleet robots are included automatically by the
    planner so corridor conflicts and idle yield moves match production."""
    config = {r["robot_id"]: r for r in load_robot_config()}
    nodes = _master_nodes()
    payload_tasks = []
    for task in req.tasks:
        robot_cfg = config.get(task.robot_id)
        if robot_cfg is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown robot_id {task.robot_id!r}.",
            )
        if not any(n["node_id"] == task.goal_location for n in nodes):
            raise HTTPException(
                status_code=404,
                detail=f"goal {task.goal_location!r} is not on the master map.",
            )
        if req.dry_run:
            from demo_routes.blue_ocean import _require_route

            start = _require_route(robot_cfg, "dry_run", "start")
        else:
            start = resolve_live_start(robot_cfg["master_id"], nodes)
        payload_tasks.append(
            {
                "robot_id": task.robot_id,
                "goal_location": task.goal_location,
                "start_location": start,
            }
        )

    url = _planner_preview_url()
    body = json.dumps({"tasks": payload_tasks}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise HTTPException(
            status_code=502,
            detail=f"MAPF planner preview failed ({exc.code}): {detail}",
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"MAPF planner preview unreachable at {url}: {exc}",
        ) from exc

    plans = data.get("plans") or {}
    if not plans:
        return MapfPreviewResponse(
            plans={},
            error=data.get("error") or "No collision-free MAPF plan found.",
        )
    return MapfPreviewResponse(plans=plans, error=data.get("error"))

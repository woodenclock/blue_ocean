import sys
import types
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

if "pika" not in sys.modules:
    fake_pika = types.ModuleType("pika")
    fake_pika.BlockingConnection = object
    fake_pika.ConnectionParameters = object
    fake_pika.exceptions = types.SimpleNamespace(AMQPError=Exception)
    sys.modules["pika"] = fake_pika
if "yaml" not in sys.modules:
    fake_yaml = types.ModuleType("yaml")
    fake_yaml.safe_load = lambda *_args, **_kwargs: {}
    sys.modules["yaml"] = fake_yaml
if "rich" not in sys.modules:
    fake_rich = types.ModuleType("rich")
    fake_rich_pretty = types.ModuleType("rich.pretty")
    fake_rich_pretty.pprint = lambda *_args, **_kwargs: None
    sys.modules["rich"] = fake_rich
    sys.modules["rich.pretty"] = fake_rich_pretty

import blue_ocean_planner_service as service_module

from res_mapf_planning.mapf_solve.mapf_solver_base import (
    MAPFAgent,
    MAPFSolverBase,
    Obstacle,
)
from res_mapf_planning.mapf_solve.models.models import (
    SolverLocation,
    SolverPlan,
    Step,
)


def _step(timestep: int, start: str, goal: str) -> Step:
    start_x, start_y = start.split(",", 1)
    goal_x, goal_y = goal.split(",", 1)
    return Step(
        timestep=timestep,
        step_from=SolverLocation(node=start, x=float(start_x), y=float(start_y)),
        step_to=SolverLocation(node=goal, x=float(goal_x), y=float(goal_y)),
        task_id="",
    )


def _plan(agent_name: str, nodes: list[str]) -> SolverPlan:
    return SolverPlan(
        agent_name=agent_name,
        steps=[
            _step(timestep, start, goal)
            for timestep, (start, goal) in enumerate(zip(nodes, nodes[1:]))
        ],
    )


class UnusedSolver(MAPFSolverBase):
    def solve(
        self,
        agents: Sequence[MAPFAgent],
        obstacles: Sequence[Obstacle],
    ) -> Sequence[SolverPlan]:
        raise AssertionError("fake coordinator should not call the solver")


def _make_service() -> service_module.PlannerService:
    service = service_module.PlannerService.__new__(service_module.PlannerService)
    service._preview_lock = service_module.threading.Lock()
    service._cbs_executor = ThreadPoolExecutor(max_workers=1)
    service.cbs_timeout_s = 5
    service.map_id = "test_map"
    service.graph = object()
    service.robot_profiles = {
        "mover": {
            "manufacturer": "MoverCo",
            "serial": "M001",
            "fleet_id": "mover",
        },
        "idle": {
            "manufacturer": "IdleCo",
            "serial": "I001",
            "fleet_id": "idle",
        },
    }
    service._fetch_master_agv_positions = lambda: {
        "mover": "1,1",
        "idle": "2,2",
    }
    service._resolve_agent_start = (
        lambda robot_id, live_positions: live_positions.get(robot_id, "")
    )
    return service


def test_preview_returns_stationary_idle_robot_when_generator_skips_empty_plan(
    monkeypatch,
) -> None:
    class FakeCoordinator:
        def __init__(self, context, _solver) -> None:
            self.context = context

        def solve(self, new_tasks, committed_locations, stationary_agents, obstacles):
            snapshot = self.context.get_agents_snapshot()
            assert set(snapshot) == {"mover", "idle"}
            assert {task.robot_id for task in new_tasks} == {"mover"}
            return [
                _plan("mover", ["1,1", "1,2"]),
                SolverPlan(agent_name="idle", steps=[]),
    ]

    monkeypatch.setattr(service_module, "MAPFCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        service_module,
        "MapConstrainedCBSAdapter",
        lambda _graph: UnusedSolver(),
    )
    service = _make_service()
    try:
        plans = service.preview(
            [{"robot_id": "mover", "goal_location": "1,2", "start_location": "1,1"}]
        )
    finally:
        service._cbs_executor.shutdown(wait=False, cancel_futures=True)

    assert set(plans) == {"mover", "idle"}
    assert [waypoint["name"] for waypoint in plans["mover"]["waypoints"]] == [
        "1,1",
        "1,2",
    ]
    assert plans["idle"]["preview"] is True
    assert plans["idle"]["waypoints"] == [
        {
            "name": "2,2",
            "x": 2.0,
            "y": 2.0,
            "progress": 0,
            "departure_blockers": [],
        }
    ]


def test_preview_keeps_idle_yield_plan_from_mapf(monkeypatch) -> None:
    class FakeCoordinator:
        def __init__(self, context, _solver) -> None:
            self.context = context

        def solve(self, new_tasks, committed_locations, stationary_agents, obstacles):
            snapshot = self.context.get_agents_snapshot()
            assert set(snapshot) == {"mover", "idle"}
            return [
                _plan("mover", ["1,1", "1,2"]),
                _plan("idle", ["2,2", "2,3", "2,2"]),
    ]

    monkeypatch.setattr(service_module, "MAPFCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        service_module,
        "MapConstrainedCBSAdapter",
        lambda _graph: UnusedSolver(),
    )
    service = _make_service()
    try:
        plans = service.preview(
            [{"robot_id": "mover", "goal_location": "1,2", "start_location": "1,1"}]
        )
    finally:
        service._cbs_executor.shutdown(wait=False, cancel_futures=True)

    assert set(plans) == {"mover", "idle"}
    assert [waypoint["name"] for waypoint in plans["idle"]["waypoints"]] == [
        "2,2",
        "2,3",
        "2,2",
    ]

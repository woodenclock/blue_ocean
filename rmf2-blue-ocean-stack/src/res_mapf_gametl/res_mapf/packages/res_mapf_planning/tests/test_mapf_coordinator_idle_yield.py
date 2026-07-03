# Copyright (C) 2026 ROS-Industrial Consortium Asia Pacific
# Advanced Remanufacturing and Technology Centre
# A*STAR Research Entities (Co. Registration No. 199702110H)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Sequence

from res_plan_server.models.task import Task

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
from res_mapf_planning.planning.mapf_coordinator import MAPFCoordinator
from res_mapf_planning.planning.multi_agent_context import MultiAgentContext


def _step(timestep: int, start: str, goal: str) -> Step:
    return Step(
        timestep=timestep,
        step_from=SolverLocation(node=start),
        step_to=SolverLocation(node=goal),
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


class IdleYieldSolver(MAPFSolverBase):
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def solve(
        self,
        agents: Sequence[MAPFAgent],
        obstacles: Sequence[Obstacle],
    ) -> Sequence[SolverPlan]:
        self.calls.append({agent.agent_id: agent.goal.name or "" for agent in agents})
        return [
            _plan("idle", ["A", "X", "A"]),
            _plan("mover", ["M", "G"]),
        ]


def test_idle_yield_return_plan_is_accepted_without_retargeting() -> None:
    context = MultiAgentContext()
    context.initialise_agent("idle", "A")
    context.initialise_agent("mover", "M")
    solver = IdleYieldSolver()
    coordinator = MAPFCoordinator(context, solver)

    plans = coordinator.solve(
        new_tasks=[Task(task_id="move", robot_id="mover", goal="G")],
        committed_locations={},
        stationary_agents=set(),
        obstacles=[],
    )

    assert solver.calls == [{"idle": "A", "mover": "G"}]
    idle_plan = next(plan for plan in plans if plan.agent_name == "idle")
    assert [step.step_to.node for step in idle_plan.steps] == ["X", "A"]

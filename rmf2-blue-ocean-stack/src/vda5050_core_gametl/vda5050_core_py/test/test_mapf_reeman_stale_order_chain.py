import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "demo-june-blue-ocean"
    / "example_master_webserver.py"
)


class _VdaObject:
    pass


class _Order:
    def __init__(self):
        self.header = _VdaObject()


class _BlockingType:
    HARD = "HARD"
    SOFT = "SOFT"
    NONE = "NONE"


def _install_vda_stub():
    vda_stub = types.ModuleType("vda5050_core_py")
    vda_stub.Order = _Order
    vda_stub.Node = _VdaObject
    vda_stub.Edge = _VdaObject
    vda_stub.NodePosition = _VdaObject
    vda_stub.Action = _VdaObject
    vda_stub.InstantActions = _VdaObject
    vda_stub.BlockingType = _BlockingType
    vda_stub.VDA5050Master = _VdaObject
    sys.modules["vda5050_core_py"] = vda_stub


def _load_master_module():
    _install_vda_stub()
    os.environ.setdefault(
        "MASTER_ROBOTS_FILE",
        str(Path(__file__).resolve().parents[4] / "maps" / "AMAV-X" / "robots.yaml"),
    )
    spec = importlib.util.spec_from_file_location("example_master_webserver", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


master_web = _load_master_module()


class _Decision:
    def __init__(self, name: str):
        self.name = name


class _Error:
    def __init__(self, error_type: str):
        self.error_type = error_type


class _AssignResult:
    def __init__(self, decision: str, assigned: bool, error_type: str | None = None):
        self.decision = _Decision(decision)
        self._assigned = assigned
        self.errors = [] if error_type is None else [_Error(error_type)]

    def __bool__(self):
        return self._assigned


class _LastState:
    def __init__(
        self,
        *,
        order_id: str = "",
        order_update_id: int = 0,
        last_node_id: str = "4,3",
        last_node_sequence_id: int = 6,
        node_states: list[object] | None = None,
        driving: bool = False,
    ):
        self.order_id = order_id
        self.order_update_id = order_update_id
        self.last_node_id = last_node_id
        self.last_node_sequence_id = last_node_sequence_id
        self.node_states = node_states or []
        self.driving = driving


class _FakeAgv:
    def __init__(self, state: _LastState | None):
        self._state = state
        self.cancelled_pending = False

    def get_last_state(self):
        return self._state

    def get_pending_order_count(self):
        return 0

    def cancel_pending_orders(self):
        self.cancelled_pending = True


class _FakeMaster:
    def __init__(
        self,
        agvs: dict[tuple[str, str], _FakeAgv],
        assign_result: _AssignResult | None = None,
    ):
        self.agvs = agvs
        self.assign_result = assign_result or _AssignResult("ASSIGNED", True)
        self.assigned_orders = []

    def get_agv(self, manufacturer: str, serial_number: str):
        return self.agvs.get((manufacturer, serial_number))

    def get_onboarded_agvs(self):
        return list(self.agvs)

    def onboard_agv(self, manufacturer: str, serial_number: str):
        self.agvs.setdefault((manufacturer, serial_number), _FakeAgv(None))

    def assign_order(self, manufacturer: str, serial_number: str, order):
        self.assigned_orders.append((manufacturer, serial_number, order))
        return self.assign_result


def _waypoint(name: str, progress: float):
    return master_web.PlanWaypoint(
        name=name,
        x=0.0,
        y=0.0,
        progress=progress,
        departure_blockers=[],
    )


def _plan(
    *,
    order_id: str = "mapf_reeman-2-blue_v6_future",
    plan_version: int = 6,
):
    return master_web.MapfPlanRequest(
        robot_id="reeman-2-blue",
        manufacturer="Reeman",
        serial_number="R002",
        order_id=order_id,
        map_id="test-map",
        plan_version=plan_version,
        waypoints=[_waypoint("4,3", 0.0), _waypoint("4,4", 1.0)],
    )


def _reset_state(fake_master: _FakeMaster, plans=None, dispatched=None):
    master_web.state.master = fake_master
    master_web.state.mapf_plans = plans or {}
    master_web.state.dispatched_orders = dispatched or {}
    master_web.state.map_nodes = {}
    master_web.state.map_id = "test-map"


@pytest.mark.xfail(
    strict=False,
    reason="pending cancelOrder/fresh-dispatch recovery",
)
def test_reset_with_stale_node_states_requires_fresh_order_boundary():
    old_order_id = "mapf_reeman-2-blue_v1_af337756"
    stale_state = _LastState(
        order_id=old_order_id,
        order_update_id=5,
        last_node_id="4,3",
        last_node_sequence_id=6,
        node_states=[object()],
        driving=False,
    )
    fake_master = _FakeMaster({("Reeman", "R002"): _FakeAgv(stale_state)})
    previous_plan = _plan(order_id=old_order_id, plan_version=1)
    _reset_state(
        fake_master,
        plans={"reeman-2-blue": previous_plan},
        dispatched={
            "reeman-2-blue": {
                "order_id": old_order_id,
                "update_id": 5,
                "released_until": 1,
                "seq_base": 0,
            }
        },
    )

    master_web.reset_mapf_plan_for_robot("reeman-2-blue")

    next_plan = _plan(order_id="mapf_reeman-2-blue_v6_future", plan_version=6)
    order_id, update_id, seq_base = master_web._resolve_order_identity(next_plan)

    assert order_id == next_plan.order_id
    assert update_id == 0
    assert seq_base == 0


@pytest.mark.xfail(
    strict=False,
    reason="pending cancelOrder/fresh-dispatch recovery",
)
def test_stitch_rejection_should_mark_robot_for_cancel_or_fresh_dispatch():
    old_order_id = "mapf_reeman-2-blue_v1_af337756"
    stale_state = _LastState(
        order_id=old_order_id,
        order_update_id=4,
        last_node_id="4,3",
        last_node_sequence_id=6,
        node_states=[object()],
        driving=False,
    )
    fake_master = _FakeMaster(
        {("Reeman", "R002"): _FakeAgv(stale_state)},
        assign_result=_AssignResult(
            "STITCH_REJECTED",
            False,
            error_type="orderUpdateError",
        ),
    )
    _reset_state(
        fake_master,
        plans={"reeman-2-blue": _plan(order_id=old_order_id, plan_version=1)},
        dispatched={
            "reeman-2-blue": {
                "order_id": old_order_id,
                "update_id": 4,
                "released_until": 1,
                "seq_base": 0,
            }
        },
    )

    response = master_web.receive_mapf_plan(
        _plan(order_id="mapf_reeman-2-blue_v5_future", plan_version=5)
    )

    assert response.decision == "CANCEL_REQUIRED"
    assert response.errors == ["orderUpdateError"]
    assert "reeman-2-blue" in master_web.state.mapf_recovery_boundaries

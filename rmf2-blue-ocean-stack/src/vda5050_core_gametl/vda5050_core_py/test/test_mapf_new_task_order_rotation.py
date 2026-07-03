"""Regression: a NEW task must not be stitched onto the previous order.

Reproduces the 2026-07-02 accumulation failure. An order (v4) completed, then
several independent new missions were each dispatched as another *update* of
v4 because the AGV's nodeStates still lingered (order_active heuristic):

    v4  5,4->4,4->4,5->5,5   COMPLETED at 5,5 (update 3)
    v5  5,5->4,5->3,5        update 4 of v4   (new task, wrongly stitched)
    v6  3,5->4,5->4,4        update 5 of v4   (new task, wrongly stitched)
    v7  5,4->4,4             update 6 of v4   -> "Stitch node content differs
                                                  from base (§6.6.2)"

Successive divergent routes accumulate on one order_id until a stitch node can
no longer be byte-identical to the base and every update is rejected. The fix
makes order identity TASK-SCOPED: a plan whose orchestrator task_id differs
from the tracked order's is dispatched under a FRESH order_id (and the prior
order's lifecycle is cleared so the fresh id isn't rejected as concurrent).

Uses the same lightweight stubs as the sibling stale-order tests.
"""

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
    def __init__(self, name):
        self.name = name


class _AssignResult:
    def __init__(self, decision="ASSIGNED", assigned=True):
        self.decision = _Decision(decision)
        self._assigned = assigned
        self.errors = []

    def __bool__(self):
        return self._assigned


class _LastState:
    def __init__(self, *, order_id="", order_update_id=0, last_node_id="5,5",
                 last_node_sequence_id=4, node_states=None, driving=False):
        self.order_id = order_id
        self.order_update_id = order_update_id
        self.last_node_id = last_node_id
        self.last_node_sequence_id = last_node_sequence_id
        self.node_states = node_states if node_states is not None else []
        self.driving = driving


class _FakeAgv:
    def __init__(self, state):
        self._state = state
        self._active = state is not None and len(state.node_states) > 0
        self.lifecycle_cleared = False
        self.pending_cancelled = False

    def get_last_state(self):
        return self._state

    def get_pending_order_count(self):
        return 0

    def cancel_pending_orders(self):
        self.pending_cancelled = True

    def has_active_order(self):
        return self._active

    def clear_order_lifecycle(self):
        self.lifecycle_cleared = True
        self._active = False


class _FakeMaster:
    def __init__(self, agvs, assign_result=None):
        self.agvs = agvs
        self.assign_result = assign_result or _AssignResult()
        self.assigned = []

    def get_agv(self, mfg, serial):
        return self.agvs.get((mfg, serial))

    def get_onboarded_agvs(self):
        return list(self.agvs)

    def onboard_agv(self, mfg, serial):
        self.agvs.setdefault((mfg, serial), _FakeAgv(None))

    def assign_order(self, mfg, serial, order):
        self.assigned.append(order)
        return self.assign_result


def _plan(*, order_id, task_id, goal="4,4", start="5,4"):
    return master_web.MapfPlanRequest(
        robot_id="autoxing-1",
        manufacturer="Autoxing",
        serial_number="A001",
        order_id=order_id,
        map_id="AMAV-X",
        plan_version=7,
        task_id=task_id,
        waypoints=[
            master_web.PlanWaypoint(name=start, x=0.0, y=0.0, progress=0.0),
            master_web.PlanWaypoint(name=goal, x=1.0, y=0.0, progress=1.0),
        ],
    )


# The lingering-but-completed v4 order: nodeStates still populated, robot idle.
def _completed_v4_state():
    return _LastState(
        order_id="mapf_autoxing-1_v4_100f6822",
        order_update_id=3,
        last_node_id="5,5",
        last_node_sequence_id=4,
        node_states=[object()],  # zombie: order done but state lingers
        driving=False,
    )


def _install_state(agv, *, prev_task_id):
    master = _FakeMaster({("Autoxing", "A001"): agv})
    master_web.state.master = master
    master_web.state.mapf_plans = {}
    master_web.state.map_nodes = {}
    master_web.state.map_id = "AMAV-X"
    master_web.state.mapf_recovery_boundaries = set()
    master_web.state.dispatched_orders = {
        "autoxing-1": {
            "order_id": "mapf_autoxing-1_v4_100f6822",
            "update_id": 3,
            "released_until": 1,
            "seq_base": 0,
            "task_id": prev_task_id,
        }
    }
    return master


def test_is_new_task_detects_distinct_task_ids():
    prev = {"task_id": "urn:task:AAA"}
    assert master_web._is_new_task(_plan(order_id="x", task_id="urn:task:BBB"), prev)
    assert not master_web._is_new_task(_plan(order_id="x", task_id="urn:task:AAA"), prev)
    # Unknown ids never misfire (legacy record / preview with no task_id).
    assert not master_web._is_new_task(_plan(order_id="x", task_id=""), prev)
    assert not master_web._is_new_task(_plan(order_id="x", task_id="urn:task:BBB"), {})


def test_new_task_on_idle_robot_dispatches_fresh():
    """The v7 case: new task, robot idle, v4 nodeStates linger -> FRESH, no stitch."""
    _install_state(_FakeAgv(_completed_v4_state()), prev_task_id="urn:task:v4")
    new_plan = _plan(order_id="mapf_autoxing-1_v7_0beba220", task_id="urn:task:v7")

    order_id, update_id, seq_base = master_web._resolve_order_identity(new_plan)

    assert order_id == "mapf_autoxing-1_v7_0beba220"  # fresh, NOT the v4 chain
    assert update_id == 0
    assert seq_base == 0


def test_same_task_replan_still_stitches():
    """Routine same-task re-solve on an in-flight order must still stitch — the
    departure-blocker release watcher depends on it."""
    _install_state(_FakeAgv(_completed_v4_state()), prev_task_id="urn:task:v4")
    replan = _plan(order_id="mapf_autoxing-1_v9_new", task_id="urn:task:v4")

    order_id, update_id, seq_base = master_web._resolve_order_identity(replan)

    assert order_id == "mapf_autoxing-1_v4_100f6822"  # stitched onto active order
    assert update_id == 4  # bumped past tracked update 3


def test_new_task_while_driving_still_stitches():
    """A mid-drive supersede (robot still driving) falls back to stitching —
    cleanly aborting a moving robot's order needs VDA5050 cancelOrder, which is
    out of scope here. Documents the remaining gap."""
    driving_state = _completed_v4_state()
    driving_state.driving = True
    _install_state(_FakeAgv(driving_state), prev_task_id="urn:task:v4")
    new_plan = _plan(order_id="mapf_autoxing-1_v7_0beba220", task_id="urn:task:v7")

    order_id, _, _ = master_web._resolve_order_identity(new_plan)
    assert order_id == "mapf_autoxing-1_v4_100f6822"  # still stitches while moving


def test_receive_plan_new_task_clears_prior_lifecycle_and_dispatches_fresh():
    """End to end through receive_mapf_plan: new task on an idle robot clears the
    prior order's master lifecycle and dispatches a fresh order id."""
    agv = _FakeAgv(_completed_v4_state())
    _install_state(agv, prev_task_id="urn:task:v4")
    new_plan = _plan(order_id="mapf_autoxing-1_v7_0beba220", task_id="urn:task:v7")

    resp = master_web.receive_mapf_plan(new_plan)

    assert agv.lifecycle_cleared is True
    assert resp.order_id == "mapf_autoxing-1_v7_0beba220"  # fresh
    assert master_web.state.dispatched_orders["autoxing-1"]["task_id"] == "urn:task:v7"

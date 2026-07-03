"""Regression: pickup orders must survive a same-goal stitch re-solve.

Reproduces the 2026-07-02 demo failure: a pickup order (goal node carrying a
``jackUp`` action) re-solves while in flight and is dispatched as a stitch
update of the active order. The VDA5050 stitch validator requires the stitch
node to be BYTE-IDENTICAL to the base (§6.6.2 stitch-node immutability,
order_lifecycle_manager.cpp). ``build_plan_order`` used to mint a fresh random
``action_id`` (uuid4) on every build, so the rebuilt goal node never matched
the base's — the master logged:

    Order validation failed ... orderUpdateError
      desc=Stitch node content differs from base (§6.6.2: stitch node must be
      identical)

and (because the rejection is async) reported the misleading
``dispatched=True (ASSIGNED)`` while the robot never picked up.

The fix makes ``action_id`` deterministic (keyed on the resolved order_id), so a
re-solve dispatched under the same order_id rebuilds an identical goal node.

Uses the same lightweight stubs as test_mapf_reeman_stale_order_chain.py.
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


def _pickup_plan(order_id: str):
    """A 2-waypoint pickup plan: transit 3,4 -> goal 4,4 carrying a jackUp."""
    return master_web.MapfPlanRequest(
        robot_id="autoxing-1",
        manufacturer="Autoxing",
        serial_number="A001",
        order_id=order_id,
        map_id="AMAV-X",
        plan_version=6,
        waypoints=[
            master_web.PlanWaypoint(name="3,4", x=0.0, y=0.0, progress=0.0),
            master_web.PlanWaypoint(
                name="4,4",
                x=1.0,
                y=0.0,
                progress=1.0,
                actions=[{"action_type": "jackUp"}],
            ),
        ],
    )


@pytest.fixture(autouse=True)
def _empty_map(monkeypatch):
    # Empty topology → node ids fall back to the waypoint name; keeps the build
    # deterministic without loading a real map.
    monkeypatch.setattr(master_web.state, "map_nodes", {}, raising=False)
    monkeypatch.setattr(master_web.state, "map_id", "AMAV-X", raising=False)


def _goal_action_id(order):
    goal = order.nodes[-1]
    assert goal.actions, "goal node should carry the jackUp action"
    return goal.actions[0].action_id


def test_same_order_rebuild_is_action_id_stable():
    """The heart of the fix: the fresh dispatch and a later stitch re-solve are
    both built under the SAME resolved order_id, so the goal node's action_id
    (and thus the whole stitch node) is identical → §6.6.2 stitch passes."""
    order_id = "mapf_autoxing-1_v6_ee42d48d"

    # Fresh dispatch (update 0) and a same-goal stitch re-solve (update 3) both
    # resolve to the same order_id — mirrors _resolve_order_identity reusing the
    # active order's id.
    fresh = master_web.build_plan_order(
        _pickup_plan(order_id), order_id, 0, released_until=1, seq_base=0
    )
    stitch = master_web.build_plan_order(
        _pickup_plan(order_id), order_id, 3, released_until=1, seq_base=0
    )

    assert _goal_action_id(fresh) == _goal_action_id(stitch)


def test_action_id_is_deterministic_not_random():
    order_id = "mapf_autoxing-1_v6_ee42d48d"
    a = _goal_action_id(
        master_web.build_plan_order(_pickup_plan(order_id), order_id, 0, seq_base=0)
    )
    b = _goal_action_id(
        master_web.build_plan_order(_pickup_plan(order_id), order_id, 0, seq_base=0)
    )
    assert a == b
    # Encodes the order_id so it stays stable across the order's stitch updates.
    assert order_id in a


def test_action_id_differs_across_distinct_orders():
    """Still globally unique per §6.6.2: a genuinely different order (different
    order_id) gets a different action_id, so ids never collide across orders."""
    a = _goal_action_id(
        master_web.build_plan_order(
            _pickup_plan("mapf_autoxing-1_v6_aaaa"), "mapf_autoxing-1_v6_aaaa", 0
        )
    )
    b = _goal_action_id(
        master_web.build_plan_order(
            _pickup_plan("mapf_autoxing-1_v7_bbbb"), "mapf_autoxing-1_v7_bbbb", 0
        )
    )
    assert a != b


def test_planner_supplied_action_id_is_respected():
    order_id = "mapf_autoxing-1_v6_ee42d48d"
    plan = _pickup_plan(order_id)
    plan.waypoints[-1].actions = [
        {"action_type": "jackUp", "action_id": "explicit-id-123"}
    ]
    order = master_web.build_plan_order(plan, order_id, 0, seq_base=0)
    assert _goal_action_id(order) == "explicit-id-123"

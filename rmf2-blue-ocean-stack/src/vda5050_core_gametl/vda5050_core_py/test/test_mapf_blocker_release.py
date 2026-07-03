import importlib.util
import os
from pathlib import Path
import sys
import types


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


class _LastState:
    def __init__(self, node_id: str, sequence_id: int):
        self.last_node_id = node_id
        self.last_node_sequence_id = sequence_id


class _FakeAgv:
    def __init__(self, state: _LastState | None):
        self._state = state

    def get_last_state(self):
        return self._state


class _FakeMaster:
    def __init__(self, agvs: dict[tuple[str, str], _FakeAgv], publish_result=True):
        self.agvs = agvs
        self.publish_result = publish_result
        self.published = []

    def get_agv(self, manufacturer: str, serial_number: str):
        return self.agvs.get((manufacturer, serial_number))

    def publish_order(self, manufacturer: str, serial_number: str, order):
        self.published.append((manufacturer, serial_number, order))
        return self.publish_result


def _waypoint(name: str, progress: float, blockers=None):
    return master_web.PlanWaypoint(
        name=name,
        x=0.0,
        y=0.0,
        progress=progress,
        departure_blockers=blockers or [],
    )


def _blocker(robot_id: str, required_progress: float, plan_version: int):
    return master_web.DepartureBlocker(
        robot_id=robot_id,
        required_progress=required_progress,
        plan_version=plan_version,
    )


def _plan(robot_id: str, plan_version: int, waypoints):
    return master_web.MapfPlanRequest(
        robot_id=robot_id,
        manufacturer=f"M-{robot_id}",
        serial_number=f"S-{robot_id}",
        order_id=f"order-{robot_id}",
        map_id="test-map",
        plan_version=plan_version,
        waypoints=waypoints,
    )


def _reset_state(plans, fake_master):
    master_web.state.mapf_plans = plans
    master_web.state.dispatched_orders = {}
    master_web.state.master = fake_master
    master_web.state.map_nodes = {}
    master_web.state.map_id = "test-map"


def test_release_blocks_stale_blocker_plan_version():
    reeman = _plan("reeman", 1, [_waypoint("R0", 0.0), _waypoint("R1", 1.0)])
    autox = _plan(
        "autox",
        3,
        [
            _waypoint("A0", 0.0),
            _waypoint("A1", 1.0, [_blocker("reeman", 1.0, 3)]),
            _waypoint("A2", 2.0),
        ],
    )
    fake_master = _FakeMaster({
        ("M-reeman", "S-reeman"): _FakeAgv(_LastState("R1", 2)),
        ("M-autox", "S-autox"): _FakeAgv(_LastState("A1", 2)),
    })
    _reset_state({"reeman": reeman, "autox": autox}, fake_master)
    master_web.state.dispatched_orders["autox"] = {
        "order_id": "order-autox",
        "update_id": 0,
        "released_until": 1,
        "seq_base": 0,
    }

    master_web._advance_releases()

    assert fake_master.published == []
    assert master_web.state.dispatched_orders["autox"]["released_until"] == 1


def test_release_waits_for_held_robot_to_reach_hold_node():
    reeman = _plan("reeman", 3, [_waypoint("R0", 0.0), _waypoint("R1", 1.0)])
    autox = _plan(
        "autox",
        3,
        [
            _waypoint("A0", 0.0),
            _waypoint("A1", 1.0, [_blocker("reeman", 1.0, 3)]),
            _waypoint("A2", 2.0),
        ],
    )
    fake_master = _FakeMaster({
        ("M-reeman", "S-reeman"): _FakeAgv(_LastState("R1", 2)),
        ("M-autox", "S-autox"): _FakeAgv(_LastState("A0", 0)),
    })
    _reset_state({"reeman": reeman, "autox": autox}, fake_master)
    master_web.state.dispatched_orders["autox"] = {
        "order_id": "order-autox",
        "update_id": 0,
        "released_until": 1,
        "seq_base": 0,
    }

    master_web._advance_releases()

    assert fake_master.published == []
    assert master_web.state.dispatched_orders["autox"]["released_until"] == 1


def test_release_publishes_when_blocker_and_self_arrival_are_satisfied():
    reeman = _plan("reeman", 3, [_waypoint("R0", 0.0), _waypoint("R1", 1.0)])
    autox = _plan(
        "autox",
        3,
        [
            _waypoint("A0", 0.0),
            _waypoint("A1", 1.0, [_blocker("reeman", 1.0, 3)]),
            _waypoint("A2", 2.0),
        ],
    )
    fake_master = _FakeMaster({
        ("M-reeman", "S-reeman"): _FakeAgv(_LastState("R1", 2)),
        ("M-autox", "S-autox"): _FakeAgv(_LastState("A1", 2)),
    })
    _reset_state({"reeman": reeman, "autox": autox}, fake_master)
    master_web.state.dispatched_orders["autox"] = {
        "order_id": "order-autox",
        "update_id": 0,
        "released_until": 1,
        "seq_base": 0,
    }

    master_web._advance_releases()

    assert len(fake_master.published) == 1
    assert master_web.state.dispatched_orders["autox"]["update_id"] == 1
    assert master_web.state.dispatched_orders["autox"]["released_until"] == 2


def test_publish_failure_does_not_advance_local_release_state():
    reeman = _plan("reeman", 3, [_waypoint("R0", 0.0), _waypoint("R1", 1.0)])
    autox = _plan(
        "autox",
        3,
        [
            _waypoint("A0", 0.0),
            _waypoint("A1", 1.0, [_blocker("reeman", 1.0, 3)]),
            _waypoint("A2", 2.0),
        ],
    )
    fake_master = _FakeMaster({
        ("M-reeman", "S-reeman"): _FakeAgv(_LastState("R1", 2)),
        ("M-autox", "S-autox"): _FakeAgv(_LastState("A1", 2)),
    }, publish_result=False)
    _reset_state({"reeman": reeman, "autox": autox}, fake_master)
    master_web.state.dispatched_orders["autox"] = {
        "order_id": "order-autox",
        "update_id": 0,
        "released_until": 1,
        "seq_base": 0,
    }

    master_web._advance_releases()

    assert len(fake_master.published) == 1
    assert master_web.state.dispatched_orders["autox"]["update_id"] == 0
    assert master_web.state.dispatched_orders["autox"]["released_until"] == 1

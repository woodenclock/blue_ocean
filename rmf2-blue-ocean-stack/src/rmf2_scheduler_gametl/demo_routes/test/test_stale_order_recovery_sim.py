"""End-to-end *simulation* of the MAPF stale-order vicious cycle and its fix.

Reproduces the failure captured in the 2026-07-01 demo logs (see
docs/2026-07-01-reeman-mapf-stale-order-chain.md):

  1. A robot is left in a force-fresh recovery boundary while the ADAPTER still
     holds the previous order (``node_states`` non-empty → ``order_active=true``).
  2. Every new MAPF plan is dispatched as a FRESH order id (``update 0``) — but
     the stitch validator rejects it as a concurrent order because the adapter's
     old order is still live. The rejection is async, so the master reports the
     misleading ``dispatched=True (ASSIGNED)`` and never auto-recovers.
  3. ``/plans/mapf/reset/{robot}`` clears planner/master bookkeeping but does NOT
     clear the adapter's ``node_states`` — so it can't break the cycle. Mashing
     demo-reset does nothing.

The fix: ``reset_robot_demo_state`` now escalates to
``POST /actions/clear-order-state/{robot}`` when the robot is left idle-but-stale
(``order_active=true`` while ``driving=false``). That clears the adapter's
``node_states``, so the next fresh order is ACCEPTED and the cycle ends.

This drives the REAL ``reset_robot_demo_state`` against a fake master that models
``node_states`` + a stitch validator behaving exactly as the logs show. No
Docker, MQTT/AMQP, or live master required.
"""

import pytest

from demo_routes import mapf_reset, mission


class FakeMaster:
    """In-memory model of the VDA5050 master's order state for one robot.

    ``node_states`` is the adapter-published order execution state. It stays
    populated (order_active) until an explicit /actions/clear-order-state call —
    NOT touched by /actions/stop or /plans/mapf/reset (the real trap)."""

    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        # A previous order is still live on the adapter — the stale-order state.
        self.node_states = ["stale-node"]
        self.driving = False
        self.clear_order_state_calls = 0
        self.dispatched: list[str] = []
        self.rejected: list[str] = []

    @property
    def order_active(self) -> bool:
        return len(self.node_states) > 0

    # --- endpoint models ---------------------------------------------------

    def get(self, path: str) -> dict:
        if path == "/state":
            return {
                "agvs": [
                    {
                        "robot_id": self.robot_id,
                        "order_active": self.order_active,
                        "driving": self.driving,
                    }
                ]
            }
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str) -> dict:
        if path.startswith("/actions/stop/"):
            self.driving = False
            return {"success": True}
        if path.startswith("/plans/mapf/reset"):
            # Clears planner/master bookkeeping only — deliberately does NOT
            # clear the adapter's node_states (the reason plain reset can't fix
            # the cycle).
            return {
                "cleared_robots": [self.robot_id],
                "pending_cancelled": 0,
                "had_active_dispatch": True,
            }
        if path.startswith("/actions/clear-order-state/"):
            self.clear_order_state_calls += 1
            self.node_states = []  # adapter.reset_order_state() drains node_states
            return {"robot_id": self.robot_id, "force_fresh_order": True}
        raise AssertionError(f"unexpected POST {path}")

    # --- planner-side dispatch model (the stitch validator) ---------------

    def dispatch_fresh_order(self, order_id: str) -> bool:
        """Simulate the planner pushing a fresh order (update 0) to /plans/mapf.

        Returns True if accepted, False if stitch-rejected. A fresh order is
        rejected while the adapter still has an active order (node_states)."""
        if self.order_active:
            self.rejected.append(order_id)
            return False
        self.dispatched.append(order_id)
        self.node_states = ["fresh-node"]  # the new order becomes active
        return True


@pytest.fixture
def fake_master(monkeypatch):
    fm = FakeMaster("autoxing-1")
    # Route both modules' master helpers through the fake.
    monkeypatch.setattr(mission, "_master_get", fm.get)
    monkeypatch.setattr(mission, "_master_post", fm.post)
    monkeypatch.setattr(mapf_reset, "_master_post", fm.post)
    monkeypatch.setattr(mapf_reset, "_publish_mapf_reset", lambda *a, **k: None)
    # Keep the recovery poll fast.
    monkeypatch.setattr(mission, "RESET_RECOVERY_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(mission, "RESET_RECOVERY_POLL_INTERVAL_S", 0.01)
    return fm


def test_plain_reset_alone_does_not_break_the_cycle(fake_master):
    """Baseline: without adapter clear, a fresh order stays rejected forever."""
    assert fake_master.dispatch_fresh_order("mapf_autoxing-1_v14") is False
    # Even the master-side reset endpoint can't help — node_states untouched.
    fake_master.post("/plans/mapf/reset/autoxing-1")
    assert fake_master.dispatch_fresh_order("mapf_autoxing-1_v15") is False
    assert fake_master.order_active is True
    assert fake_master.rejected == ["mapf_autoxing-1_v14", "mapf_autoxing-1_v15"]
    assert fake_master.dispatched == []


def test_demo_reset_recovery_breaks_the_cycle(fake_master):
    """With the fix, one demo-reset clears the adapter and the next fresh order
    is accepted — the vicious cycle is broken."""
    # Pre-condition: robot wedged with a stale live order.
    assert fake_master.order_active is True
    assert fake_master.dispatch_fresh_order("mapf_autoxing-1_v14") is False

    result = mission.reset_robot_demo_state("autoxing-1", reason="demo_reset")

    # The recovery fired exactly once and drained the adapter's node_states.
    assert result.order_state_recovered is True
    assert fake_master.clear_order_state_calls == 1
    assert fake_master.order_active is False

    # The next fresh MAPF plan is now ACCEPTED, not stitch-rejected.
    assert fake_master.dispatch_fresh_order("mapf_autoxing-1_v15") is True
    assert fake_master.dispatched == ["mapf_autoxing-1_v15"]


def test_recovery_is_idempotent_when_already_clean(fake_master):
    """A robot with no stale order is left alone — no wasted recovery call."""
    fake_master.node_states = []  # already idle/clean
    result = mission.reset_robot_demo_state("autoxing-1", reason="demo_reset")
    assert result.order_state_recovered is False
    assert fake_master.clear_order_state_calls == 0

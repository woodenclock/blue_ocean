"""Regression coverage for the stale-order-state recovery fallback that
reset_robot_demo_state() runs after stop/reset (see
docs/2026-07-01-reeman-mapf-stale-order-chain.md). This exercises
mission._recover_stale_order_state_if_needed() directly, hermetically —
no Docker, MQTT/AMQP, or live master required."""

import pytest

from demo_routes import mission


@pytest.fixture(autouse=True)
def _fast_recovery_window(monkeypatch):
    # Real default is a few seconds; keep tests fast without changing behavior.
    monkeypatch.setattr(mission, "RESET_RECOVERY_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(mission, "RESET_RECOVERY_POLL_INTERVAL_S", 0.01)


def _agv(order_active, driving):
    return {
        "robot_id": "reeman-2-blue",
        "order_active": order_active,
        "driving": driving,
    }


def _stub_master(monkeypatch, agvs, post_calls):
    monkeypatch.setattr(mission, "_master_get", lambda path: {"agvs": agvs})
    monkeypatch.setattr(
        mission, "_master_post", lambda path: post_calls.append(path) or {}
    )


def test_order_active_false_skips_recovery(monkeypatch):
    calls = []
    _stub_master(monkeypatch, [_agv(order_active=False, driving=False)], calls)

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False
    assert calls == []


def test_idle_stale_order_triggers_recovery(monkeypatch):
    calls = []
    _stub_master(monkeypatch, [_agv(order_active=True, driving=False)], calls)

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is True
    assert calls == ["/actions/clear-order-state/reeman-2-blue?reason=demo_reset"]


def test_driving_true_never_triggers_recovery(monkeypatch):
    # Robot legitimately still executing an order — must never be cancelled.
    calls = []
    _stub_master(monkeypatch, [_agv(order_active=True, driving=True)], calls)

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False
    assert calls == []


def test_driving_none_never_triggers_recovery(monkeypatch):
    # driving=None means "no state yet" per the master's own /state docs —
    # must be treated the same as still-moving, not as idle-stale.
    calls = []
    _stub_master(monkeypatch, [_agv(order_active=True, driving=None)], calls)

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False
    assert calls == []


def test_unreachable_master_aborts_cleanly(monkeypatch):
    calls = []

    def _boom(path):
        raise RuntimeError("master unreachable")

    monkeypatch.setattr(mission, "_master_get", _boom)
    monkeypatch.setattr(
        mission, "_master_post", lambda path: calls.append(path) or {}
    )

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False
    assert calls == []


def test_robot_not_onboarded_aborts_cleanly(monkeypatch):
    calls = []
    _stub_master(monkeypatch, [], calls)  # robot missing from /state agvs list

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False
    assert calls == []


def test_recovery_post_failure_returns_false(monkeypatch):
    monkeypatch.setattr(
        mission,
        "_master_get",
        lambda path: {"agvs": [_agv(order_active=True, driving=False)]},
    )

    def _fail(path):
        raise RuntimeError("clear-order-state failed")

    monkeypatch.setattr(mission, "_master_post", _fail)

    result = mission._recover_stale_order_state_if_needed(
        "reeman-2-blue", "demo_reset"
    )

    assert result is False

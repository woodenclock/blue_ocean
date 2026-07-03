#!/usr/bin/env python3
"""Get battery state via Reeman REST ``GET /reeman/base_encode``."""

import sys

import requests

from api_client import base_url, print_json


def get_reeman_battery_state(
    base: str | None = None, timeout: float | None = None
) -> dict | None:
    """Get Reeman battery state via REST API.

    ``base`` is the robot's REST endpoint (host, ``host:port``, or full URL)
    from robots.yaml. When omitted, falls back to the active ``CONSTANTS.yml``
    robot. Passing it explicitly is what keeps each robot's poll pinned to its
    own IP with no shared global state.
    """
    try:
        resp = requests.get(
            f"{base_url(base)}/reeman/base_encode",
            timeout=timeout or 5,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "percentage": data.get("battery"),
            "battery": data.get("battery"),
            "charge_flag": data.get("chargeFlag"),
            "emergency_button": data.get("emergencyButton"),
        }
    except Exception as e:
        print(f"Reeman REST error: {e}")
        return None


def get_battery_state(
    base: str | None = None, timeout: float | None = None
) -> dict | None:
    """Get battery state (Reeman FlyBoat REST only)."""
    return get_reeman_battery_state(base=base, timeout=timeout)


if __name__ == "__main__":
    state = get_battery_state()
    if state is not None:
        print_json(state)
    else:
        sys.exit(1)

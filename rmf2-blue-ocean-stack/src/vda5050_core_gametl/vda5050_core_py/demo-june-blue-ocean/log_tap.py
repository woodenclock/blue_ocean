"""Tap the VDA5050 broker for every topic and fan it out to websocket clients.

Unlike ``order_tracker.py`` (which tracks one topic for one purpose — last-node
detection), this module subscribes to the whole VDA5050 topic tree for every
onboarded AGV and keeps a bounded in-memory history so a client connecting to
``/ws/logs`` can catch up, then receives new entries live. It is independently
best-effort: if the broker is unreachable, the webserver stays up and the log
just stays empty until the broker becomes reachable.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections import deque
from typing import Any

import paho.mqtt.client as mqtt

from order_tracker import broker_host_port


def parse_topic(topic: str) -> tuple[str, str, str, str] | None:
    """Split ``{interface}/v{major}/{manufacturer}/{serial}/{topicType}`` into
    its four semantic parts, or ``None`` if the topic doesn't have that shape."""
    parts = topic.split("/")
    if len(parts) != 5:
        return None
    interface, _version, manufacturer, serial, topic_type = parts
    return interface, manufacturer, serial, topic_type


def _is_cancel_order(message_type: str, payload: dict[str, Any] | None) -> bool:
    if message_type != "instantActions" or not isinstance(payload, dict):
        return False
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(a, dict) and a.get("actionType") == "cancelOrder" for a in actions
    )


def _put_nowait_drop_oldest(q: asyncio.Queue, entry: dict[str, Any]) -> None:
    """Enqueue ``entry``, dropping the oldest queued item first if full — bounds
    a per-connection queue against a stalled/slow client instead of blocking."""
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass


class LogTap:
    """Best-effort MQTT traffic recorder + live fan-out for one broker."""

    def __init__(
        self,
        *,
        broker: str,
        interface: str,
        protocol_version: str,
        client_id: str,
        history_size: int = 500,
        robot_lookup: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._host, self._port = broker_host_port(broker)
        major = protocol_version.split(".", 1)[0]
        self._wildcard_topic = f"{interface}/v{major}/#"
        self._robot_lookup = robot_lookup or {}

        self._lock = threading.Lock()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"{client_id}_log_tap"
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def start(self) -> None:
        """Connect and begin listening on a background thread (best-effort)."""
        try:
            self._client.connect(self._host, self._port)
        except OSError as exc:
            print(
                f"[log-tap] connect to {self._host}:{self._port} failed: {exc}; "
                "MQTT log feed disabled (broker unreachable)",
                file=sys.stderr,
            )
            return
        self._client.loop_start()
        print(
            f"[log-tap] listening on {self._wildcard_topic} "
            f"({self._host}:{self._port})",
            file=sys.stderr,
        )

    def stop(self) -> None:
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except OSError:
            pass

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop so paho's own thread can safely hand
        entries to asyncio subscriber queues via ``call_soon_threadsafe``."""
        self._loop = loop

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    # ── paho callbacks (run on paho's network thread) ──────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        client.subscribe(self._wildcard_topic)

    def _on_message(self, client, userdata, msg) -> None:
        parsed = parse_topic(msg.topic)
        if parsed is None:
            manufacturer, serial, message_type = "", "", "unknown"
        else:
            _interface, manufacturer, serial, message_type = parsed
            if message_type not in (
                "order",
                "state",
                "instantActions",
                "connection",
                "visualization",
                "factsheet",
            ):
                message_type = "unknown"

        payload: dict[str, Any] | None
        parse_error = False
        try:
            payload = json.loads(msg.payload)
            if not isinstance(payload, dict):
                payload = None
                parse_error = True
        except (ValueError, TypeError):
            payload = None
            parse_error = True

        entry: dict[str, Any] = {
            "ts": time.time(),
            "topic": msg.topic,
            "manufacturer": manufacturer,
            "serial_number": serial,
            "robot_id": self._robot_lookup.get((manufacturer, serial)),
            "message_type": message_type,
            "payload": payload,
            "is_cancel_order": _is_cancel_order(message_type, payload),
        }
        if parse_error:
            entry["parse_error"] = True

        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._history.append(entry)
            subs = list(self._subscribers)

        if self._loop is not None:
            for q in subs:
                self._loop.call_soon_threadsafe(_put_nowait_drop_oldest, q, entry)

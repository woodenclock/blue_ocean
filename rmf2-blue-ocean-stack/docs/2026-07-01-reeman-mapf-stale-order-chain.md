# 2026-07-01 - Issue: Reeman MAPF stale order chain after reset

## Symptom

In the AMAV-X MAPF demo, Reeman can be physically idle while the VDA5050 master
still reports:

```json
{
  "robot_id": "reeman-2-blue",
  "pending_order_count": 0,
  "order_active": true,
  "driving": false
}
```

The next MAPF mission may then appear to ignore the latest requested route. The
robot is not blocked by a master outbound queue (`pending_order_count=0`); it is
stuck on an adapter-published VDA5050 order state (`node_states` still
populated), so new MAPF plans are stitched onto an old order chain.

## Observed chain

The failure seen on 2026-07-01 had this shape:

1. Reeman accepted a MAPF base order such as
   `mapf_reeman-2-blue_v1_af337756`.
2. Later replans reused that same order id as VDA5050 order updates.
3. A v4 update was rejected with `orderUpdateError` because it attempted to
   alter an already released base node.
4. v5/v6 continued on the same old order id, so the stale chain remained the
   dispatch boundary.
5. `demo_reset` cleared MAPF planner/master bookkeeping and pending queues, but
   the adapter still had non-empty `node_states`.
6. `_resolve_order_identity()` read the live adapter state and chose to stitch
   the next MAPF plan onto the old order id instead of using the new MAPF
   `order_id`.

Representative reset logs:

```text
MapfReset (demo_reset): dropped task for reeman-2-blue
MAPF reset (reeman-2-blue): cancelled 0 pending outbound order(s), had_active_dispatch=True
[AGV] Cleared pending outbound queues for Reeman/R002
```

After a container restart, Reeman returned to `order_active=false`, which
confirmed the stuck state was in the VDA5050 adapter/master order lifecycle, not
in the MAPF task batch alone.

## Root cause

`order_active` is derived from the adapter's last VDA5050 `State`:

```python
"order_active": bool(last is not None and len(last.node_states) > 0)
```

`POST /plans/mapf/reset` clears:

- `state.mapf_plans`
- `state.dispatched_orders`
- master-side pending outbound orders

It does not send a VDA5050 `cancelOrder` instant action to the adapter. If the
adapter never drains its current order by acknowledging or cancelling the
remaining `node_states`, the master can still see `order_active=true`.

The next MAPF plan then takes the stale-order branch in
`_resolve_order_identity()`:

```python
if agv_order_active:
    return last.order_id, base_update + 1, last.last_node_sequence_id
```

That is correct for a healthy in-flight order, but it is wrong after a rejected
stitch/update or after reset has intentionally abandoned the MAPF task. In that
case, continuing v5/v6 on the old v1 order id makes the failure lifelong.

## Preferred lifelong fix

Implement VDA5050 `cancelOrder` end to end, or an equivalent explicit recovery
boundary with the same effect:

1. Detect a rejected stitch/update or a reset while the adapter still reports
   `order_active=true`.
2. Cancel the active VDA5050 order on the adapter.
3. Wait until the adapter reports `node_states=[]` and `driving=false`, or until
   a cancel action state reports failure/timeout.
4. Clear master order lifecycle/stitch tracking for that robot.
5. Dispatch the next MAPF plan as a fresh order id, not as another update of the
   stale chain.

With real `cancelOrder`, the policy should be:

- Normal replan while the order is healthy: stitch update onto the active order.
- Rejected stitch/update: cancel active order, wait for idle, then fresh
  dispatch.
- Demo reset: cancel active order first, verify idle, then clear MAPF planner
  state.

## Acceptance criteria

- After a rejected Reeman order update, the system records a cancel/fresh-dispatch
  boundary instead of continuing v5/v6 on the old v1 order id.
- After `demo_reset`, `/state` reports `order_active=false`,
  `pending_order_count=0`, and `driving=false` before new MAPF dispatch is
  accepted.
- The next MAPF mission after reset uses its own fresh `order_id`.
- The regression test for this issue can run without Docker, MQTT, AMQP, live
  robots, or network access.

## Out of scope

- Direct navigate / spellbook `502` behavior.
- Home/charger evacuation policy.
- Changing CBS path generation.

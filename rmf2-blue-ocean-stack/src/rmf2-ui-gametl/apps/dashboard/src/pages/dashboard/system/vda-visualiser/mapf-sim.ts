// Build a unified CBS timestep timeline from stored MAPF plans (GET /plans/mapf).
import type { MapfPlan, MapfPlansByRobot, MapfSimFrame } from './types';

function waypointProgress(progress: number | null | undefined, index: number) {
  return progress ?? index;
}

/** Merge per-robot CBS progress values into ordered simulation frames. */
export function buildMapfTimeline(plans: MapfPlansByRobot): {
  times: number[];
  frames: MapfSimFrame[];
} {
  const robotIds = Object.keys(plans);
  if (robotIds.length === 0) return { times: [], frames: [] };

  const schedules = robotIds.map((robotId) => {
    const plan = plans[robotId];
    const waypoints = plan.waypoints.map((wp, index) => ({
      ...wp,
      progress: waypointProgress(wp.progress, index),
    }));
    return { robotId, waypoints, planVersion: plan.plan_version ?? 0 };
  });

  const times = [
    ...new Set(
      schedules.flatMap(({ waypoints }) => waypoints.map((wp) => wp.progress)),
    ),
  ].sort((a, b) => a - b);

  const frames: MapfSimFrame[] = times.map((time, step) => {
    const robots: MapfSimFrame['robots'] = {};
    for (const { robotId, waypoints } of schedules) {
      let idx = 0;
      for (let i = 0; i < waypoints.length; i++) {
        if (waypoints[i].progress <= time) idx = i;
      }
      const wp = waypoints[idx];
      const next = waypoints[idx + 1];
      const blockers = wp.departure_blockers ?? [];
      const holding =
        blockers.length > 0 && (next == null || next.name === wp.name);
      robots[robotId] = {
        nodeId: wp.name,
        holding,
        blockerNote:
          blockers.length > 0
            ? blockers
                .map((b) => `${b.robot_id} ≥ p${b.required_progress}`)
                .join(', ')
            : undefined,
      };
    }
    return {
      step,
      time,
      maxStep: times.length - 1,
      planVersion: Math.max(...schedules.map((s) => s.planVersion)),
      robots,
    };
  });

  return { times, frames };
}

export function formatMapfPlanPath(plan: MapfPlan): string {
  const tokens: string[] = [];
  for (const wp of plan.waypoints) {
    const prev = tokens[tokens.length - 1];
    if (prev === wp.name || prev === `${wp.name}(wait)`) {
      tokens[tokens.length - 1] = `${wp.name}(wait)`;
    } else {
      tokens.push(wp.name);
    }
  }
  return tokens.join(' → ');
}

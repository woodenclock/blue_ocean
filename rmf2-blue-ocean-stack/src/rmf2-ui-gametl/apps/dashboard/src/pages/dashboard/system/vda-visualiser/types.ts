// Shared types for the VDA5050 live visualiser.

export interface MapNode {
  node_id: string;
  x: number;
  y: number;
}

export interface MapEdge {
  edge_id: string;
  start_node_id: string;
  end_node_id: string;
}

export interface MapStation {
  station_id: string;
  station_name: string;
  node_ids: string[];
  x: number;
  y: number;
}

export interface MapData {
  map_id?: string;
  nodes: MapNode[];
  edges: MapEdge[];
  stations: MapStation[];
}

export interface AgvActionState {
  action_id: string;
  action_type: string;
  action_status: string;
  result_description: string;
}

export interface AgvState {
  robot_id: string;
  manufacturer: string;
  serial_number: string;
  connection_status: string | null;
  last_node_id: string | null;
  x: number | null;
  y: number | null;
  theta: number | null;
  has_pose: boolean;
  action_states: AgvActionState[] | null;
  jack_state: string | null;
  /** Master-side outbound order queue depth (orders awaiting delivery behind a
   *  stitch point or departure blocker). Streamed live on /ws/state. */
  pending_order_count?: number;
  /** True when the AGV currently has an active order (non-empty node_states). */
  order_active?: boolean;
  /** Live battery percentage (0–100) from spellbook poll on robot endpoint. */
  battery_percent?: number | null;
}

// Master-frame occupancy map image draped behind the topology. `origin` is the
// world pose [x, y] (metres) of the image's lower-left corner; `resolution` is
// metres per pixel; `width`/`height` are the PNG's pixel dimensions.
export interface MapImageMeta {
  url: string;
  resolution: number;
  origin: [number, number];
  width: number;
  height: number;
}

export type SocketStatus = 'connecting' | 'connected' | 'disconnected';

// Robot id accepted by the scheduler's /demo/* endpoints. Now unified with the
// live /state robot_id (e.g. "autoxing-1", "reeman-1", "reeman-2-blue"), so it's
// just a string — no fixed two-robot enum.
export type SchedulerRobotId = string;

// A point-and-click goal for one robot; `applied` once dispatched to MAPF.
export interface RobotGoal {
  node: string;
  applied: boolean;
  /** Set for spellbook pick_rack missions (not MAPF node ids). */
  pickRackMode?: 'pickup' | 'dropoff' | 'full';
}

export type RobotGoals = Record<SchedulerRobotId, RobotGoal>;

// Rack node_id -> approach node id (from GET /demo/racks). Empty if unknown.
export type RackMap = Record<string, string | null>;

export interface MapfDepartureBlocker {
  robot_id: string;
  required_progress: number;
}

export interface MapfWaypoint {
  name: string;
  x: number;
  y: number;
  progress?: number | null;
  departure_blockers?: MapfDepartureBlocker[];
}

/** Latest stored plan for one robot (GET /plans/mapf). */
export interface MapfPlan {
  robot_id: string;
  manufacturer?: string;
  serial_number?: string;
  order_id?: string;
  map_id?: string;
  plan_version?: number;
  task_id?: string;
  waypoints: MapfWaypoint[];
}

export type MapfPlansByRobot = Record<string, MapfPlan>;

/** One tick of the MAPF timestep simulator. */
export interface MapfSimFrame {
  step: number;
  /** CBS progress value for this tick (may differ per robot; unified by max reached). */
  time: number;
  maxStep: number;
  planVersion: number;
  robots: Record<
    string,
    {
      nodeId: string;
      holding: boolean;
      blockerNote?: string;
    }
  >;
}

// Raw VDA5050 MQTT traffic, streamed over /ws/logs (see log_tap.py).
export type VdaLogMessageType =
  | 'order'
  | 'state'
  | 'instantActions'
  | 'connection'
  | 'visualization'
  | 'factsheet'
  | 'unknown';

export interface VdaLogEntry {
  seq: number;
  ts: number;
  topic: string;
  manufacturer: string;
  serial_number: string;
  robot_id: string | null;
  message_type: VdaLogMessageType;
  payload: Record<string, unknown> | null;
  is_cancel_order: boolean;
  parse_error?: boolean;
}

export type LogDisplayMode = 'pretty' | 'concise';

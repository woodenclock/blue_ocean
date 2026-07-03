import { BrokerStatusConfig } from '@/clients';
import type { LifDocument, RobotsPayload } from './types';
import type { MapData } from '../vda-visualiser/types';

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
  'http://localhost:8008';
const VDA_BASE_URL = BrokerStatusConfig.BASE ?? 'http://localhost:8000';

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function stringValue(value: unknown): string | null {
  if (typeof value === 'string' && value.trim() !== '') return value;
  if (typeof value === 'number') return String(value);
  return null;
}

function normalizeVdaMap(payload: unknown): MapData {
  const data = isRecord(payload) ? payload : {};
  const mapId = stringValue(data.map_id ?? data.mapId);
  const rawNodes = Array.isArray(data.nodes) ? data.nodes : [];
  const rawEdges = Array.isArray(data.edges) ? data.edges : [];
  const rawStations = Array.isArray(data.stations) ? data.stations : [];

  const nodes = rawNodes.flatMap((raw) => {
    if (!isRecord(raw)) return [];
    const position = isRecord(raw.nodePosition) ? raw.nodePosition : raw;
    const nodeId = stringValue(raw.node_id ?? raw.nodeId ?? raw.id);
    const x = numberValue(position.x);
    const y = numberValue(position.y);
    if (!nodeId || x == null || y == null) return [];
    return [{ node_id: nodeId, x, y }];
  });

  const edges = rawEdges.flatMap((raw, index) => {
    if (!isRecord(raw)) return [];
    const start = stringValue(
      raw.start_node_id ?? raw.startNodeId ?? raw.source,
    );
    const end = stringValue(raw.end_node_id ?? raw.endNodeId ?? raw.target);
    if (!start || !end) return [];
    return [
      {
        edge_id:
          stringValue(raw.edge_id ?? raw.edgeId ?? raw.id) ??
          `${start}_TO_${end}_${index}`,
        start_node_id: start,
        end_node_id: end,
      },
    ];
  });

  const stations = rawStations.flatMap((raw) => {
    if (!isRecord(raw)) return [];
    const position = isRecord(raw.stationPosition) ? raw.stationPosition : raw;
    const stationId = stringValue(raw.station_id ?? raw.stationId ?? raw.id);
    const stationName = stringValue(
      raw.station_name ?? raw.stationName ?? raw.name,
    );
    const x = numberValue(position.x);
    const y = numberValue(position.y);
    if (!stationId || !stationName || x == null || y == null) return [];
    const rawNodeIds = raw.node_ids ?? raw.interactionNodeIds;
    const nodeIds = Array.isArray(rawNodeIds)
      ? rawNodeIds.flatMap((nodeId) => stringValue(nodeId) ?? [])
      : [];
    return [
      {
        station_id: stationId,
        station_name: stationName,
        node_ids: nodeIds,
        x,
        y,
      },
    ];
  });

  if (nodes.length === 0) {
    throw new Error('VDA map response did not include any usable nodes.');
  }

  return { map_id: mapId ?? undefined, nodes, edges, stations };
}

export async function fetchLif(): Promise<LifDocument> {
  const res = await fetch(`${API_BASE_URL}/api/lif`);
  if (!res.ok) throw new Error(`Failed to fetch LIF: ${res.status}`);
  return res.json() as Promise<LifDocument>;
}

export async function fetchVdaMap(): Promise<MapData> {
  const res = await fetch(`${VDA_BASE_URL}/map`);
  if (!res.ok) throw new Error(`Failed to fetch VDA map: ${res.status}`);
  return normalizeVdaMap(await res.json());
}

export async function postLif(doc: LifDocument): Promise<LifDocument> {
  const res = await fetch(`${API_BASE_URL}/api/lif`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to submit LIF (${res.status}): ${text}`);
  }
  return res.json() as Promise<LifDocument>;
}

export async function postNewMap(
  doc: LifDocument,
): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE_URL}/newmap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to post new map (${res.status}): ${text}`);
  }
  return res.json() as Promise<{ status: string; message: string }>;
}

export async function fetchRobots(): Promise<RobotsPayload> {
  const res = await fetch(`${API_BASE_URL}/api/robots`);
  if (!res.ok) throw new Error(`Failed to fetch robots: ${res.status}`);
  return res.json() as Promise<RobotsPayload>;
}

export async function saveRobots(
  payload: RobotsPayload,
): Promise<RobotsPayload> {
  const res = await fetch(`${API_BASE_URL}/api/robots`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to save robots (${res.status}): ${text}`);
  }
  return res.json() as Promise<RobotsPayload>;
}
